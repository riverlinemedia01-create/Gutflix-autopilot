"""Daily Shorts from Google Drive folders.

Each day `python -m autopost drive-sync`:
  1. lists the configured sources (folders, and optionally their Shorts subfolders),
  2. picks the next video that hasn't been used yet, in source order,
  3. writes a normal post file for it in posts/drive/<date>.yaml, and
  4. downloads any Drive video whose post is due now, into media/drive/.

Folders must be shared as "Anyone with the link can view". The tool reads them
with a Google API key (secret GOOGLE_API_KEY) that has the Drive API enabled.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import requests
import yaml

from .config import Secrets, Settings
from .platforms.base import PublishError, http, local_path
from .platforms.x import weighted_length
from .posts import load_posts
from .state import State

API = "https://www.googleapis.com/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"
DONE = ("posted", "missed", "failed")
X_MAX_SECONDS = 140  # longer videos need X Premium

DEFAULTS = {
    "time": "18:30",
    "max_seconds": 180,          # YouTube Shorts limit; longer videos are skipped
    "max_size_mb": 200,          # guards against full episodes landing in a Shorts folder
    "generic_title": "{series} Ep {episode} | Part {part}",
    "youtube_title": "{title} #Shorts",
    "youtube_description": "{text}\n\n{hashtags} #Shorts",
    "youtube_tags": [],
    "youtube_privacy": "public",
    "instagram_caption": "{text}\n\n{hashtags} #reels",
    "x_text": "{title} {hashtags}",
}

# File names straight off a phone or editor carry no meaning as a title.
GENERIC_NAME = re.compile(
    r"^(vid|img|inshot|pxl|mov|dsc|video|clip|shorts?|screen[ _-]?recording|cap ?cut)[ _-]*[\d_ -]*(bsl)?$",
    re.I)


class _Safe(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def natural_key(name: str):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def episode_of(folder_name: str):
    m = re.search(r"ep(?:isode)?\s*\.?\s*(\d+)", folder_name, re.I) or re.search(r"(\d+)\s*$", folder_name)
    return int(m.group(1)) if m else None


def clean_name(filename: str) -> str:
    stem = filename.strip()
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    stem = stem.strip()
    if stem.endswith("_"):          # Drive/phones turn a trailing "?" into "_"
        stem = stem[:-1].rstrip() + "?"
    return re.sub(r"\s+", " ", stem.replace("_", " ")).strip()


def is_generic(filename: str) -> bool:
    stem = filename.strip().rsplit(".", 1)[0].strip()
    return bool(GENERIC_NAME.match(stem))


def _ext(name: str, mime: str) -> str:
    name = name.strip()
    if "." in name and len(name.rsplit(".", 1)[1]) <= 4:
        return "." + name.rsplit(".", 1)[1].lower()
    return {"video/quicktime": ".mov", "video/webm": ".webm"}.get(mime, ".mp4")


def list_children(folder_id: str, key: str) -> list[dict]:
    files, token = [], None
    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id, name, mimeType, size, createdTime, "
                      "videoMediaMetadata(width, height, durationMillis))",
            "pageSize": 1000, "key": key,
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
        }
        if token:
            params["pageToken"] = token
        j = http("GET", API, params=params).json()
        files += j.get("files", [])
        token = j.get("nextPageToken")
        if not token:
            return files


def _folder_name(folder_id: str, key: str) -> str:
    try:
        return http("GET", f"{API}/{folder_id}", params={"fields": "name", "key": key,
                                                          "supportsAllDrives": "true"}).json().get("name", "")
    except PublishError:
        return ""


def collect(cfg: dict, key: str, log=print) -> list[dict]:
    """All candidate videos, in posting order. Each gets series/episode/part/folder info."""
    out, seen = [], set()

    def add_folder(folder_id, name, src, children=None):
        children = children if children is not None else list_children(folder_id, key)
        videos = sorted((f for f in children if f.get("mimeType", "").startswith("video/")),
                        key=lambda f: natural_key(f["name"].strip()))
        texts = {f["name"].strip().rsplit(".", 1)[0].strip().lower(): f for f in children
                 if f["name"].strip().lower().endswith(".txt") or f.get("mimeType") == "text/plain"}
        ep = episode_of(name)
        for i, v in enumerate(videos, 1):
            if v["id"] in seen:
                continue
            seen.add(v["id"])
            v = dict(v, series=src.get("series", ""), hashtags=src.get("hashtags", ""),
                     episode=ep if ep is not None else "", part=i, folder=name,
                     sidecar=texts.get(v["name"].strip().rsplit(".", 1)[0].strip().lower()))
            out.append(v)

    for src in cfg.get("sources") or []:
        fid = src["folder_id"]
        scan = src.get("scan", "files")
        try:
            if scan == "subfolders":
                children = list_children(fid, key)
                want = str(src.get("subfolder_filter", "")).lower()
                subs = [f for f in children if f.get("mimeType") == FOLDER and want in f["name"].lower()]
                subs.sort(key=lambda f: (episode_of(f["name"]) or 10 ** 6, natural_key(f["name"])))
                for sub in subs:
                    add_folder(sub["id"], sub["name"], src)
            else:
                add_folder(fid, src.get("folder_name") or _folder_name(fid, key) or src.get("series", ""), src)
        except PublishError as e:
            log(f"WARNING: couldn't read '{src.get('series', fid)}' folder {fid}. Is it shared as "
                f"'Anyone with the link'? ({str(e)[:200]})")
        for extra in src.get("also_folders") or []:  # used when the main folder can't be read
            try:
                add_folder(extra, _folder_name(extra, key) or src.get("series", ""), src)
            except PublishError as e:
                log(f"WARNING: couldn't read folder {extra}: {str(e)[:200]}")
    return out


def download(file_id: str, key: str, dest: Path, log=print) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last = None
    for _ in range(3):
        try:
            with requests.get(f"{API}/{file_id}", params={"alt": "media", "key": key,
                              "supportsAllDrives": "true"}, stream=True, timeout=300) as r:
                if r.status_code != 200:
                    raise PublishError(f"Drive download {file_id} -> {r.status_code}: {r.text[:300]}")
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(1024 * 1024):
                        fh.write(chunk)
            os.replace(tmp, dest)
            log(f"  downloaded {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
            return
        except (requests.RequestException, PublishError) as e:
            last = e
    raise PublishError(f"couldn't download Drive file {file_id}: {last}")


def _fit_x(text: str) -> str:
    while weighted_length(text) > 280:
        text = text[:-2].rstrip() + "…"
    return text


def _duration(v: dict):
    ms = (v.get("videoMediaMetadata") or {}).get("durationMillis")
    return int(ms) / 1000 if ms else None


def problem(v: dict, cfg: dict) -> str | None:
    """Why a video can't be used as a Short, or None."""
    size = int(v.get("size") or 0)
    if size > cfg["max_size_mb"] * 1e6:
        return f"{size / 1e6:.0f} MB is over max_size_mb ({cfg['max_size_mb']}), looks like a full episode"
    d = _duration(v)
    if d and d > cfg["max_seconds"]:
        return f"{d:.0f}s is longer than max_seconds ({cfg['max_seconds']})"
    return None


def build_post(settings: Settings, cfg: dict, video: dict, sidecar: str | None, day: str, n: int) -> dict:
    name = clean_name(video["name"])
    base_vals = dict(name=name, date=day, n=n, filename=video["name"].strip(),
                     series=video.get("series", ""), episode=video.get("episode", ""),
                     part=video.get("part", ""), hashtags=video.get("hashtags", ""))
    fallback = name if not is_generic(video["name"]) else cfg["generic_title"].format_map(_Safe(base_vals))
    lines = [l.strip() for l in (sidecar or "").splitlines() if l.strip()]
    title = lines[0] if lines else fallback
    text = (sidecar or "").strip() or title
    vals = _Safe(base_vals, title=title, text=text)
    ref = f"drive:{video['id']}{_ext(video['name'], video.get('mimeType', ''))}"
    dur = _duration(video)
    meta = video.get("videoMediaMetadata") or {}

    targets, notes = [], []
    for acct in settings.resolve_targets(cfg.get("accounts") or list(settings.accounts)):
        platform = settings.accounts[acct].platform
        if platform == "youtube":
            yt_title = re.sub(r"[<>]", "", cfg["youtube_title"].format_map(vals))
            yt_title = re.sub(r"\s+", " ", yt_title).strip()
            if len(yt_title) > 100:
                yt_title = yt_title[:99].rstrip() + "…"
            t = {"account": acct, "video": ref, "title": yt_title,
                 "description": cfg["youtube_description"].format_map(vals).strip()[:4900],
                 "privacy": cfg["youtube_privacy"]}
            if cfg.get("youtube_tags"):
                t["tags"] = list(cfg["youtube_tags"])
        elif platform == "instagram":
            t = {"account": acct, "type": "reel", "media": [ref],
                 "caption": cfg["instagram_caption"].format_map(vals).strip()[:2200]}
        else:
            if dur and dur > X_MAX_SECONDS:
                notes.append(f"{acct} left out: {dur:.0f}s is over X's {X_MAX_SECONDS}s limit without Premium")
                continue
            t = {"account": acct, "text": _fit_x(re.sub(r"[ \t]+", " ", cfg["x_text"].format_map(vals)).strip()),
                 "media": [ref]}
        targets.append(t)
    if meta.get("width") and meta.get("height") and int(meta["width"]) > int(meta["height"]):
        notes.append("video is horizontal, so YouTube will post it as a regular video, not a Short")

    post = {"id": f"drive-{day}", "publish_at": f"{day} {cfg['time']}",
            "drive_file": {"id": video["id"], "name": video["name"].strip(),
                           "folder": video.get("folder", ""), "series": video.get("series", "")},
            "targets": targets}
    if notes:
        post["notes"] = notes
    return post


def sync(settings: Settings, secrets: Secrets, state: State, now: datetime, log=print) -> int:
    cfg = {**DEFAULTS, **settings.drive_daily}
    if not settings.drive_daily or not cfg.get("enabled", True) or not cfg.get("sources"):
        log("Drive daily posting is off (set drive_daily in config/accounts.yaml).")
        return 0
    key = secrets.get("GOOGLE_API_KEY")
    root = settings.root
    dstate_path = root / "state" / "drive.json"
    dstate = json.loads(dstate_path.read_text()) if dstate_path.exists() else {}
    used: dict = dstate.setdefault("used", {})
    days: dict = dstate.setdefault("days", {})
    skipped: dict = dstate.setdefault("skipped", {})
    rc = 0

    today = now.astimezone(settings.timezone).date().isoformat()
    if today not in days:
        videos = collect(cfg, key, log)
        chosen = None
        for v in videos:
            if v["id"] in used or v["id"] in skipped:
                continue
            why = problem(v, cfg)
            if why:
                log(f"Skipping '{v['name'].strip()}' in {v.get('folder')}: {why}")
                skipped[v["id"]] = {"name": v["name"].strip(), "reason": why}
                continue
            chosen = v
            break
        left = sum(1 for v in videos if v["id"] not in used and v["id"] not in skipped)
        log(f"Drive: {len(videos)} videos found, {left} not posted yet (including today's)")
        if not chosen:
            log("WARNING: no unposted Shorts left in the Drive folders; nothing scheduled today.")
            days[today] = None
            rc = 1
        else:
            sidecar = None
            if chosen.get("sidecar"):
                r = http("GET", f"{API}/{chosen['sidecar']['id']}", params={"alt": "media", "key": key})
                sidecar = r.content.decode("utf-8", errors="replace")
            post = build_post(settings, cfg, chosen, sidecar, today, len(used) + 1)
            out = root / "posts" / "drive" / f"{today}.yaml"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("# Made automatically from Google Drive. Edit it before "
                           f"{cfg['time']} to change today's wording, or set enabled: false to skip.\n"
                           + yaml.safe_dump(post, sort_keys=False, allow_unicode=True, width=1000),
                           encoding="utf-8")
            used[chosen["id"]] = {"post": post["id"], "name": chosen["name"].strip(), "day": today,
                                  "folder": chosen.get("folder", "")}
            days[today] = post["id"]
            log(f"Scheduled '{chosen['name'].strip()}' ({chosen.get('folder')}) for {today} {cfg['time']}")
            for note in post.get("notes", []):
                log(f"  note: {note}")
        dstate_path.parent.mkdir(parents=True, exist_ok=True)
        dstate_path.write_text(json.dumps(dstate, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                               encoding="utf-8")

    # Download Drive media for anything due now and not yet posted.
    drive_dir = root / "posts" / "drive"
    if drive_dir.exists():
        for post in load_posts(settings, drive_dir):
            for t in post.targets:
                if t.publish_at > now or state.status(t.key) in DONE:
                    continue
                refs = t.fields.get("media") or [t.fields.get("video")]
                for ref in [r for r in refs if r and str(r).startswith("drive:")]:
                    dest = local_path(settings, ref)
                    if not dest.exists():
                        download(ref[len("drive:"):].rsplit(".", 1)[0], key, dest, log)
    return rc


def preview(settings: Settings, secrets: Secrets, days: int = 30, log=print) -> None:
    """Print the upcoming queue with the titles each video will get."""
    cfg = {**DEFAULTS, **settings.drive_daily}
    key = secrets.get("GOOGLE_API_KEY")
    dstate_path = settings.root / "state" / "drive.json"
    dstate = json.loads(dstate_path.read_text()) if dstate_path.exists() else {}
    used = dstate.get("used", {})
    n = len(used)
    for v in collect(cfg, key, log):
        if v["id"] in used:
            continue
        why = problem(v, cfg)
        if why:
            log(f"   skip  {v['name'].strip()}  ({why})")
            continue
        n += 1
        post = build_post(settings, cfg, v, None, "preview", n)
        t0 = post["targets"][0] if post["targets"] else {}
        shown = t0.get("title") or t0.get("text") or str(t0.get("caption", "")).split("\n")[0]
        extra = f"   ({'; '.join(post['notes'])})" if post.get("notes") else ""
        log(f"{n:4}.  [{v.get('folder')}] {v['name'].strip()}  ->  {shown}{extra}")
        if n - len(used) >= days:
            break
