"""Loads config/accounts.yaml and the secrets each account needs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

PLATFORMS = ("youtube", "x", "instagram")


class ConfigError(Exception):
    pass


@dataclass
class Account:
    name: str
    platform: str
    secret_prefix: str
    options: dict = field(default_factory=dict)


@dataclass
class Settings:
    root: Path
    timezone: ZoneInfo
    media_base_url: str
    max_late_hours: float
    max_attempts: int
    instagram_api_version: str
    accounts: dict[str, Account]
    groups: dict[str, list[str]]
    drive_daily: dict = field(default_factory=dict)

    def resolve_targets(self, ref) -> list[str]:
        """Turn an account name, group name or list of them into account names."""
        refs = ref if isinstance(ref, list) else [ref]
        out: list[str] = []
        for r in refs:
            if r in self.accounts:
                names = [r]
            elif r in self.groups:
                names = self.groups[r]
            else:
                raise ConfigError(f"unknown account or group '{r}'")
            for n in names:
                if n not in out:
                    out.append(n)
        return out


def load_settings(root: Path) -> Settings:
    path = root / "config" / "accounts.yaml"
    if not path.exists():
        raise ConfigError(f"missing {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    accounts: dict[str, Account] = {}
    for name, spec in (raw.get("accounts") or {}).items():
        spec = spec or {}
        platform = str(spec.get("platform", "")).lower()
        if platform == "twitter":
            platform = "x"
        if platform not in PLATFORMS:
            raise ConfigError(f"account '{name}': platform must be one of {PLATFORMS}")
        prefix = spec.get("secret_prefix") or name.upper().replace("-", "_")
        opts = {k: v for k, v in spec.items() if k not in ("platform", "secret_prefix")}
        accounts[name] = Account(name, platform, prefix, opts)

    groups: dict[str, list[str]] = {}
    for gname, members in (raw.get("groups") or {}).items():
        if gname in accounts:
            raise ConfigError(f"group '{gname}' has the same name as an account")
        members = members or []
        for m in members:
            if m not in accounts:
                raise ConfigError(f"group '{gname}' lists unknown account '{m}'")
        groups[gname] = list(members)

    base = raw.get("media_base_url") or ""
    if base and not base.endswith("/"):
        base += "/"

    return Settings(
        root=root,
        timezone=ZoneInfo(raw.get("timezone", "UTC")),
        media_base_url=base,
        max_late_hours=float(raw.get("max_late_hours", 12)),
        max_attempts=int(raw.get("max_attempts", 3)),
        instagram_api_version=str(raw.get("instagram_api_version", "v27.0")),
        accounts=accounts,
        groups=groups,
        drive_daily=raw.get("drive_daily") or {},
    )


class Secrets:
    """Reads secrets from environment variables.

    In GitHub Actions the workflow passes every repository secret as one JSON
    blob (SECRETS_JSON), so adding an account never means editing the workflow.
    """

    def __init__(self, env: dict | None = None):
        env = dict(os.environ if env is None else env)
        blob = env.get("SECRETS_JSON")
        if blob:
            try:
                for k, v in json.loads(blob).items():
                    env.setdefault(k, v)
            except json.JSONDecodeError as e:
                raise ConfigError(f"SECRETS_JSON is not valid JSON: {e}") from e
        self._env = env

    def get(self, *names: str, required: bool = True) -> str | None:
        for n in names:
            v = self._env.get(n)
            if v:
                return v
        if required:
            raise ConfigError("missing secret: " + " or ".join(names))
        return None

    def has(self, *names: str) -> bool:
        return any(self._env.get(n) for n in names)
