"""Instagram posts through the Instagram API with Instagram Login (graph.instagram.com).

Meta fetches each image or video from a public link, so media files must be
reachable at media_base_url (or given as full https links).
"""

from __future__ import annotations

import time

from .base import (Context, Publisher, PublishError, Result, http, is_drive, is_url, kind_of, local_path,
                   media_list, public_url)

TYPES = ("image", "carousel", "reel", "story")


def infer_type(fields: dict) -> str:
    if fields.get("type"):
        return str(fields["type"]).lower()
    media = media_list(fields)
    if len(media) > 1:
        return "carousel"
    return "reel" if media and kind_of(media[0]) == "video" else "image"


class Instagram(Publisher):
    platform = "instagram"

    def secrets_needed(self, account):
        return [(f"{account.secret_prefix}_ACCESS_TOKEN",)]

    def validate(self, t, settings):
        f, errs = t.fields, []
        kind = infer_type(f)
        media = media_list(f)
        if kind not in TYPES:
            return [f"type must be one of {', '.join(TYPES)}"]
        if not media:
            return ["Instagram posts need 'media'"]
        kinds = [kind_of(m) for m in media]
        if kind == "carousel":
            if not 2 <= len(media) <= 10:
                errs.append("a carousel needs 2 to 10 media files")
        elif len(media) != 1:
            errs.append(f"an Instagram {kind} takes exactly one media file")
        if kind == "reel" and kinds[0] != "video":
            errs.append("a reel needs a video file")
        if kind == "image" and kinds[0] != "image":
            errs.append("an image post needs an image (use type: reel for video)")
        for m, k in zip(media, kinds):
            if k == "image" and not m.split("?")[0].lower().endswith((".jpg", ".jpeg")):
                errs.append(f"{m}: Instagram's API only accepts JPEG images")
            if k == "unknown":
                errs.append(f"{m}: not an image or video")
            if not is_url(m) and not is_drive(m):
                if not local_path(settings, m).exists():
                    errs.append(f"media file not found: {m}")
                if not settings.media_base_url:
                    errs.append("local media needs media_base_url set in config/accounts.yaml")
        caption = str(f.get("caption", ""))
        if kind == "story" and caption:
            errs.append("stories don't take a caption")
        if len(caption) > 2200:
            errs.append(f"caption is {len(caption)} characters; Instagram allows 2200")
        if caption.count("#") > 30:
            errs.append("Instagram allows at most 30 hashtags")
        return errs

    # -----------------------------------------------------------------------
    def _base(self, ctx):
        return f"https://graph.instagram.com/{ctx.settings.instagram_api_version}"

    def _user_id(self, ctx, token) -> str:
        uid = ctx.secrets.get(f"{ctx.account.secret_prefix}_USER_ID", required=False)
        if uid:
            return uid
        r = http("GET", f"{self._base(ctx)}/me", params={"fields": "user_id,username", "access_token": token})
        j = r.json()
        return str(j.get("user_id") or j["id"])

    def _container(self, ctx, uid, token, params) -> str:
        r = http("POST", f"{self._base(ctx)}/{uid}/media", data={**params, "access_token": token})
        return r.json()["id"]

    def _wait(self, ctx, cid, token, timeout=900):
        deadline = time.time() + timeout
        while True:
            r = http("GET", f"{self._base(ctx)}/{cid}",
                     params={"fields": "status_code,status", "access_token": token})
            j = r.json()
            code = j.get("status_code")
            if code in ("FINISHED", "PUBLISHED"):
                return
            if code in ("ERROR", "EXPIRED"):
                raise PublishError(f"Instagram couldn't process the media: {j.get('status', code)}",
                                   retriable=False)
            if time.time() > deadline:
                raise PublishError("Instagram took too long to process the media")
            time.sleep(10)

    def publish(self, t, ctx):
        f = t.fields
        token = ctx.secrets.get(f"{ctx.account.secret_prefix}_ACCESS_TOKEN")
        uid = self._user_id(ctx, token)
        kind = infer_type(f)
        urls = [public_url(ctx.settings, m, ctx.secrets) for m in media_list(f)]
        caption = str(f.get("caption", ""))
        extra = {}
        if f.get("is_ai_generated"):
            extra["is_ai_generated"] = "true"

        def media_params(url, child=False):
            if kind_of(url) == "video":
                p = {"media_type": "VIDEO" if child else "REELS", "video_url": url}
            else:
                p = {"image_url": url}
            if child:
                p["is_carousel_item"] = "true"
            return p

        if kind == "carousel":
            children = []
            for u in urls:
                cid = self._container(ctx, uid, token, media_params(u, child=True))
                children.append(cid)
            for cid in children:
                self._wait(ctx, cid, token)
            container = self._container(ctx, uid, token, {
                "media_type": "CAROUSEL", "children": ",".join(children), "caption": caption, **extra})
        elif kind == "story":
            p = media_params(urls[0])
            p["media_type"] = "STORIES"
            container = self._container(ctx, uid, token, p)
        else:
            p = {**media_params(urls[0]), "caption": caption, **extra}
            if kind == "reel":
                p["share_to_feed"] = "true" if f.get("share_to_feed", True) else "false"
                if f.get("cover"):
                    p["cover_url"] = public_url(ctx.settings, str(f["cover"]), ctx.secrets)
                if f.get("thumb_offset_ms") is not None:
                    p["thumb_offset"] = str(int(f["thumb_offset_ms"]))
            if f.get("alt_text") and kind == "image":
                p["alt_text"] = str(f["alt_text"])
            container = self._container(ctx, uid, token, p)

        ctx.log("    waiting for Instagram to process the media")
        self._wait(ctx, container, token)
        r = http("POST", f"{self._base(ctx)}/{uid}/media_publish",
                 data={"creation_id": container, "access_token": token})
        mid = r.json()["id"]
        link = ""
        try:
            link = http("GET", f"{self._base(ctx)}/{mid}",
                        params={"fields": "permalink", "access_token": token}).json().get("permalink", "")
        except PublishError:
            pass
        return Result(mid, link or f"instagram media {mid}")
