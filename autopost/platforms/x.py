"""X (Twitter) posts through the X API v2, signed with OAuth 1.0a user tokens."""

from __future__ import annotations

import json
import os
import re
import time

from .. import oauth1
from .base import (Context, Publisher, PublishError, Result, http, kind_of, local_path,
                   media_list, media_present, mime_of)

API = "https://api.x.com/2"
CHUNK = 4 * 1024 * 1024
URL_RE = re.compile(r"https?://\S+", re.I)
LIMITS = {"image": 5 * 1024 * 1024, "gif": 15 * 1024 * 1024, "video": 512 * 1024 * 1024}


def weighted_length(text: str) -> int:
    """X's character count: links count 23, most non-Latin characters and emoji count 2."""
    n = 0
    for part in URL_RE.split(text):
        for ch in part:
            cp = ord(ch)
            light = (cp <= 4351 or 8192 <= cp <= 8205 or 8208 <= cp <= 8223 or 8242 <= cp <= 8247)
            n += 1 if light else 2
    return n + 23 * len(URL_RE.findall(text))


def _media_kind(ref: str) -> str:
    if ref.lower().endswith(".gif"):
        return "gif"
    return kind_of(ref)


class X(Publisher):
    platform = "x"

    KEYS = ("API_KEY", "API_SECRET", "ACCESS_TOKEN", "ACCESS_SECRET")

    def _names(self, account, key) -> tuple[str, ...]:
        """Secret names to try for one key. `secret_names` in accounts.yaml can point a key at
        any GitHub secret name, e.g. ACCESS_SECRET: ACCESS_TOKEN_SECRET."""
        custom = (account.options.get("secret_names") or {}).get(key)
        if custom:
            return (str(custom),)
        p = account.secret_prefix
        return (f"{p}_{key}", f"X_{key}") if key.startswith("API_") else (f"{p}_{key}",)

    def secrets_needed(self, account):
        return [self._names(account, k) for k in self.KEYS]

    def validate(self, t, settings):
        f, errs = t.fields, []
        text = str(f.get("text", ""))
        media = media_list(f)
        if not text.strip() and not media:
            errs.append("X post needs 'text' or 'media'")
        for i, part in enumerate([text] + [str(x) for x in f.get("thread") or []]):
            n = weighted_length(part)
            if n > 280:
                where = "text" if i == 0 else f"thread item {i}"
                errs.append(f"{where} is {n} characters; X allows 280 (more only with X Premium)")
        kinds = [_media_kind(m) for m in media]
        if any(k == "unknown" for k in kinds):
            errs.append("X media must be images (jpg/png/webp/gif) or a video")
        if ("video" in kinds or "gif" in kinds) and len(media) > 1:
            errs.append("X allows one video or GIF per post, or up to 4 images")
        if len(media) > 4:
            errs.append("X allows up to 4 images per post")
        for m, k in zip(media, kinds):
            p = local_path(settings, m)
            if not media_present(settings, m):
                errs.append(f"media file not found: {m}")
            elif p.exists() and k in LIMITS and p.stat().st_size > LIMITS[k]:
                errs.append(f"{m} is over X's {LIMITS[k] // (1024 * 1024)} MB limit for a {k}")
        return errs

    def warnings(self, t) -> list[str]:
        texts = [str(t.fields.get("text", ""))] + [str(x) for x in t.fields.get("thread") or []]
        if any(URL_RE.search(x) for x in texts):
            return ["contains a link: X bills link posts at a much higher rate than plain posts"]
        return []

    # --- signing -----------------------------------------------------------
    def _creds(self, ctx: Context):
        return tuple(ctx.secrets.get(*self._names(ctx.account, k)) for k in self.KEYS)

    def _auth(self, creds, method, url):
        ck, cs, tk, ts = creds
        return {"Authorization": oauth1.auth_header(method, url, ck, cs, tk, ts)}

    # --- media -------------------------------------------------------------
    def _upload(self, creds, path, kind, ctx) -> str:
        size = os.path.getsize(path)
        category = {"image": "tweet_image", "gif": "tweet_gif", "video": "tweet_video"}[kind]
        url = f"{API}/media/upload/initialize"
        r = http("POST", url, headers={**self._auth(creds, "POST", url), "Content-Type": "application/json"},
                 data=json.dumps({"media_type": mime_of(str(path)), "total_bytes": size,
                                  "media_category": category}))
        media_id = r.json()["data"]["id"]

        with open(path, "rb") as fh:
            seg = 0
            while chunk := fh.read(CHUNK):
                url = f"{API}/media/upload/{media_id}/append"
                http("POST", url, headers=self._auth(creds, "POST", url),
                     files={"media": ("chunk", chunk, "application/octet-stream")},
                     data={"segment_index": str(seg)})
                seg += 1

        url = f"{API}/media/upload/{media_id}/finalize"
        info = (http("POST", url, headers=self._auth(creds, "POST", url)).json().get("data") or {}
                ).get("processing_info")
        deadline = time.time() + 600
        while info and info.get("state") in ("pending", "in_progress"):
            if time.time() > deadline:
                raise PublishError("X took too long to process the video")
            time.sleep(max(1, int(info.get("check_after_secs", 5))))
            url = f"{API}/media/upload?command=STATUS&media_id={media_id}"
            info = (http("GET", url, headers=self._auth(creds, "GET", url)).json().get("data") or {}
                    ).get("processing_info")
        if info and info.get("state") == "failed":
            raise PublishError(f"X couldn't process {path.name}: {info.get('error')}", retriable=False)
        return media_id

    def _tweet(self, creds, body) -> str:
        url = f"{API}/tweets"
        r = http("POST", url, headers={**self._auth(creds, "POST", url), "Content-Type": "application/json"},
                 data=json.dumps(body))
        return r.json()["data"]["id"]

    def publish(self, t, ctx):
        creds = self._creds(ctx)
        f = t.fields
        ids = []
        for m in media_list(f):
            p = local_path(ctx.settings, m)
            ctx.log(f"    uploading {p.name}")
            ids.append(self._upload(creds, p, _media_kind(m), ctx))

        body = {"text": str(f.get("text", ""))}
        if ids:
            body["media"] = {"media_ids": ids}
        first = self._tweet(creds, body)

        prev = first
        for i, part in enumerate(f.get("thread") or [], 1):
            try:
                prev = self._tweet(creds, {"text": str(part), "reply": {"in_reply_to_tweet_id": prev}})
            except PublishError as e:
                # The main post is live, so don't retry the whole thing and double-post.
                return Result(first, f"https://x.com/i/status/{first}",
                              f"Thread stopped at item {i}: {e}")
        return Result(first, f"https://x.com/i/status/{first}")
