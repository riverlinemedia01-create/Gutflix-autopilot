"""YouTube uploads through the YouTube Data API v3 (resumable upload)."""

from __future__ import annotations

import json
import os

from .base import (Context, Publisher, PublishError, Result, http, kind_of, local_path,
                   media_list, media_present, mime_of)

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMB_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
CHUNK = 8 * 1024 * 1024  # must be a multiple of 256 KB


class YouTube(Publisher):
    platform = "youtube"

    def secrets_needed(self, account):
        p = account.secret_prefix
        return [(f"{p}_CLIENT_ID", "YOUTUBE_CLIENT_ID"),
                (f"{p}_CLIENT_SECRET", "YOUTUBE_CLIENT_SECRET"),
                (f"{p}_REFRESH_TOKEN",)]

    def validate(self, t, settings):
        f, errs = t.fields, []
        media = media_list(f)
        if len(media) != 1:
            errs.append("YouTube needs exactly one 'video' file")
        else:
            if kind_of(media[0]) != "video":
                errs.append(f"'{media[0]}' doesn't look like a video file")
            elif not media_present(settings, media[0]):
                errs.append(f"video file not found: {media[0]}")
        title = str(f.get("title", ""))
        if not title.strip():
            errs.append("YouTube needs a 'title'")
        if len(title) > 100:
            errs.append(f"title is {len(title)} characters; YouTube allows 100")
        if "<" in title or ">" in title:
            errs.append("title can't contain < or >")
        desc = str(f.get("description", ""))
        if len(desc.encode("utf-8")) > 5000:
            errs.append("description is over YouTube's 5000-byte limit")
        tags = f.get("tags") or []
        if sum(len(str(x)) + 2 for x in tags) > 500:
            errs.append("tags add up to more than YouTube's 500 characters")
        privacy = f.get("privacy", "public")
        if privacy not in ("public", "unlisted", "private"):
            errs.append("privacy must be public, unlisted or private")
        thumb = f.get("thumbnail")
        if thumb and not local_path(settings, str(thumb)).exists():
            errs.append(f"thumbnail not found: {thumb}")
        return errs

    def _access_token(self, ctx: Context) -> str:
        p = ctx.account.secret_prefix
        s = ctx.secrets
        r = http("POST", TOKEN_URL, data={
            "client_id": s.get(f"{p}_CLIENT_ID", "YOUTUBE_CLIENT_ID"),
            "client_secret": s.get(f"{p}_CLIENT_SECRET", "YOUTUBE_CLIENT_SECRET"),
            "refresh_token": s.get(f"{p}_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        })
        return r.json()["access_token"]

    def publish(self, t, ctx):
        f = t.fields
        path = local_path(ctx.settings, media_list(f)[0])
        size = os.path.getsize(path)
        mime = mime_of(str(path))
        token = self._access_token(ctx)
        auth = {"Authorization": f"Bearer {token}"}

        status = {
            "privacyStatus": f.get("privacy", "public"),
            "selfDeclaredMadeForKids": bool(f.get("made_for_kids", False)),
        }
        if f.get("contains_synthetic_media") is not None:
            status["containsSyntheticMedia"] = bool(f["contains_synthetic_media"])
        body = {
            "snippet": {
                "title": str(f["title"]),
                "description": str(f.get("description", "")),
                "tags": [str(x) for x in f.get("tags") or []],
                "categoryId": str(f.get("category_id", "22")),
            },
            "status": status,
        }
        notify = "true" if f.get("notify_subscribers", True) else "false"
        r = http("POST", f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status&notifySubscribers={notify}",
                 headers={**auth, "Content-Type": "application/json; charset=UTF-8",
                          "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": mime},
                 data=json.dumps(body))
        session = r.headers["Location"]

        ctx.log(f"    uploading {path.name} ({size / 1e6:.1f} MB)")
        video = None
        with open(path, "rb") as fh:
            offset = 0
            while offset < size:
                chunk = fh.read(CHUNK)
                end = offset + len(chunk) - 1
                r = http("PUT", session, ok=(200, 201, 308), data=chunk,
                         headers={**auth, "Content-Length": str(len(chunk)),
                                  "Content-Range": f"bytes {offset}-{end}/{size}"})
                if r.status_code in (200, 201):
                    video = r.json()
                    break
                rng = r.headers.get("Range")  # e.g. "bytes=0-8388607"
                offset = int(rng.split("-")[1]) + 1 if rng else 0
                fh.seek(offset)
        if not video:
            raise PublishError("YouTube upload finished without returning a video")

        vid = video["id"]
        note = ""
        got = video.get("status", {}).get("privacyStatus")
        if got == "private" and status["privacyStatus"] != "private":
            note = ("YouTube kept this video private. Your Google Cloud project hasn't passed "
                    "YouTube's API audit yet; see README, 'YouTube: public uploads'.")

        thumb = f.get("thumbnail")
        if thumb:
            tp = local_path(ctx.settings, str(thumb))
            try:
                http("POST", f"{THUMB_URL}?videoId={vid}&uploadType=media",
                     headers={**auth, "Content-Type": mime_of(str(tp))}, data=tp.read_bytes())
            except PublishError as e:  # custom thumbnails need a verified channel
                note = (note + " " if note else "") + f"Thumbnail not set: {e}"
        return Result(vid, f"https://youtu.be/{vid}", note)
