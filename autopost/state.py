"""Remembers what has been posted (state/posted.json, committed back by the workflow)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {}
        if path.exists() and path.read_text(encoding="utf-8").strip():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def get(self, key: str) -> dict:
        return self.data.get(key, {})

    def status(self, key: str) -> str:
        return self.get(key).get("status", "pending")

    def record(self, key: str, **fields) -> None:
        entry = self.data.setdefault(key, {})
        entry.update(fields)
        entry["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, self.path)
