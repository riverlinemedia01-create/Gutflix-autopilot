"""Shared pieces for the platform publishers."""

from __future__ import annotations

import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests

from ..config import Account, Secrets, Settings
from ..posts import Target

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}


class PublishError(Exception):
    """A post failed. retriable=False means trying again will not help."""

    def __init__(self, message: str, retriable: bool = True):
        super().__init__(message)
        self.retriable = retriable


@dataclass
class Context:
    settings: Settings
    secrets: Secrets
    account: Account
    log: callable = print


@dataclass
class Result:
    id: str
    url: str
    note: str = ""


def media_list(fields: dict) -> list[str]:
    m = fields.get("media")
    if m is None:
        m = fields.get("video")
    if m is None:
        return []
    return [str(x) for x in (m if isinstance(m, list) else [m])]


def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def is_drive(ref: str) -> bool:
    """Media written as drive:<file id>.<ext> comes from the Google Drive folder."""
    return ref.startswith("drive:")


def local_path(settings: Settings, ref: str) -> Path:
    if is_drive(ref):
        return settings.root / "media" / "drive" / ref[len("drive:"):]
    p = Path(ref)
    return p if p.is_absolute() else settings.root / p


def media_present(settings: Settings, ref: str) -> bool:
    """Drive files are downloaded just before posting, so they count as present."""
    return is_drive(ref) or local_path(settings, ref).exists()


def kind_of(ref: str) -> str:
    ext = Path(ref.split("?")[0]).suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    return "unknown"


def mime_of(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def public_url(settings: Settings, ref: str, secrets=None) -> str:
    if is_url(ref):
        return ref
    if is_drive(ref):
        name = ref[len("drive:"):]
        if settings.media_base_url:  # synced to R2 / your own host
            return settings.media_base_url + "drive/" + quote(name)
        # Otherwise Meta fetches the publicly shared file straight from Google Drive.
        key = secrets.get("GOOGLE_API_KEY") if secrets else ""
        file_id = name.rsplit(".", 1)[0]
        return f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&key={key}"
    if not settings.media_base_url:
        raise PublishError(
            f"'{ref}' is a local file, but Instagram needs a public link to it. "
            "Set media_base_url in config/accounts.yaml (see README).",
            retriable=False,
        )
    rel = ref[len("media/"):] if ref.startswith("media/") else ref
    return settings.media_base_url + quote(rel)


def http(method: str, url: str, *, retries: int = 3, ok=(200, 201, 202, 204), **kw) -> requests.Response:
    """requests with retry on network errors, 429 and 5xx."""
    kw.setdefault("timeout", 120)
    last = None
    for attempt in range(retries):
        try:
            r = requests.request(method, url, **kw)
        except requests.RequestException as e:
            last = PublishError(f"{method} {url}: {e}")
        else:
            if r.status_code in ok:
                return r
            body = r.text[:800]
            if r.status_code == 429 or r.status_code >= 500:
                last = PublishError(f"{method} {url} -> {r.status_code}: {body}")
            else:
                raise PublishError(f"{method} {url} -> {r.status_code}: {body}",
                                   retriable=r.status_code in (401, 408, 409))
        time.sleep(min(30, 3 * 2 ** attempt))
    raise last


class Publisher:
    platform = ""

    def validate(self, target: Target, settings: Settings) -> list[str]:
        raise NotImplementedError

    def secrets_needed(self, account: Account) -> list[tuple[str, ...]]:
        """Each tuple lists alternative names for one required secret."""
        raise NotImplementedError

    def publish(self, target: Target, ctx: Context) -> Result:
        raise NotImplementedError
