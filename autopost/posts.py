"""Reads the post queue: one YAML file per post in posts/."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path

import yaml

from .config import ConfigError, Settings


@dataclass
class Target:
    post_id: str
    account: str
    platform: str
    publish_at: datetime
    fields: dict
    source: Path

    @property
    def key(self) -> str:
        return f"{self.post_id}::{self.account}"


@dataclass
class Post:
    id: str
    publish_at: datetime
    targets: list[Target] = field(default_factory=list)
    source: Path | None = None


def parse_time(value, tz) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as e:
            raise ConfigError(f"bad time '{value}': use e.g. 2026-12-01 18:30") from e
    else:
        raise ConfigError(f"bad time value: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def _load_file(path: Path, settings: Settings) -> list[Post]:
    try:
        docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path.name}: YAML error: {e}") from e
    posts = []
    for i, doc in enumerate(d for d in docs if d):
        if not isinstance(doc, dict):
            raise ConfigError(f"{path.name}: each post must be a mapping")
        if doc.get("enabled", True) is False:
            continue
        pid = str(doc.get("id") or (path.stem if i == 0 else f"{path.stem}-{i}"))
        if "publish_at" not in doc:
            raise ConfigError(f"{path.name}: post '{pid}' needs publish_at")
        when = parse_time(doc["publish_at"], settings.timezone)
        post = Post(pid, when, source=path)
        shared = doc.get("defaults") or {}
        for t in doc.get("targets") or []:
            if not isinstance(t, dict) or "account" not in t:
                raise ConfigError(f"{path.name}: every target needs an 'account'")
            fields = {**shared, **{k: v for k, v in t.items() if k != "account"}}
            t_when = parse_time(fields.pop("publish_at"), settings.timezone) if "publish_at" in fields else when
            for acct in settings.resolve_targets(t["account"]):
                platform = settings.accounts[acct].platform
                post.targets.append(Target(pid, acct, platform, t_when, dict(fields), path))
        if not post.targets:
            raise ConfigError(f"{path.name}: post '{pid}' has no targets")
        posts.append(post)
    return posts


def load_posts(settings: Settings, posts_dir: Path | None = None) -> list[Post]:
    posts_dir = posts_dir or settings.root / "posts"
    posts: list[Post] = []
    seen: dict[str, Path] = {}
    files = sorted(p for p in posts_dir.rglob("*") if p.suffix in (".yaml", ".yml"))
    for f in files:
        for post in _load_file(f, settings):
            if post.id in seen:
                raise ConfigError(f"duplicate post id '{post.id}' in {f.name} and {seen[post.id].name}")
            seen[post.id] = f
            posts.append(post)
    posts.sort(key=lambda p: p.publish_at)
    return posts
