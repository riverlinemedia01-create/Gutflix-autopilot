"""Command line: python -m autopost {validate,plan,run,retry}"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import ConfigError, Secrets, load_settings
from .platforms import PublishError
from .posts import load_posts, parse_time
from .runner import run, validate_all
from .state import State


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="autopost", description="Scheduled posting to YouTube, X and Instagram")
    ap.add_argument("--root", default=".", help="project folder (default: current folder)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="check every post and account for problems")
    v.add_argument("--check-secrets", action="store_true", help="also check the needed secrets are set")
    sub.add_parser("plan", help="list every scheduled post and its status")
    r = sub.add_parser("run", help="post everything that is due now")
    r.add_argument("--dry-run", action="store_true", help="show what would post, post nothing")
    r.add_argument("--now", help="pretend it is this time (for testing)")
    ds = sub.add_parser("drive-sync", help="schedule today's video from the Google Drive folder")
    ds.add_argument("--now", help="pretend it is this time (for testing)")
    dp = sub.add_parser("drive-preview", help="list the upcoming Drive Shorts and their titles")
    dp.add_argument("--days", type=int, default=60)
    rt = sub.add_parser("retry", help="let a failed or missed post try again")
    rt.add_argument("key", help="POST_ID to retry all its targets, or POST_ID::ACCOUNT")

    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    try:
        settings = load_settings(root)
        state = State(root / "state" / "posted.json")

        if a.cmd == "validate":
            errors, warnings = validate_all(settings, Secrets() if a.check_secrets else None, state)
            for w in warnings:
                print(f"warning: {w}")
            for e in errors:
                print(f"error: {e}")
            if errors:
                print(f"{len(errors)} problem(s) found")
                return 1
            n = sum(len(p.targets) for p in load_posts(settings))
            print(f"OK: {n} scheduled post target(s) look good")
            return 0

        if a.cmd == "plan":
            posts = load_posts(settings)
            if not posts:
                print("No posts in posts/ yet.")
            for p in posts:
                local = p.publish_at.astimezone(settings.timezone).strftime("%a %d %b %Y %H:%M %Z")
                print(f"{local}  {p.id}")
                for t in p.targets:
                    st = state.get(t.key)
                    extra = st.get("url") or st.get("last_error", "")
                    when = "" if t.publish_at == p.publish_at else \
                        f" at {t.publish_at.astimezone(settings.timezone):%d %b %H:%M}"
                    print(f"    {state.status(t.key):9} {t.account}{when}  {extra[:100]}")
            return 0

        if a.cmd == "run":
            now = parse_time(a.now, settings.timezone) if a.now else datetime.now(timezone.utc)
            return run(settings, Secrets(), state, now=now, dry_run=a.dry_run)

        if a.cmd == "drive-sync":
            from .drive import sync
            now = parse_time(a.now, settings.timezone) if a.now else datetime.now(timezone.utc)
            return sync(settings, Secrets(), state, now)

        if a.cmd == "drive-preview":
            from .drive import preview
            preview(settings, Secrets(), a.days)
            return 0

        if a.cmd == "retry":
            keys = [k for k in state.data if k == a.key or k.startswith(a.key + "::")]
            if not keys:
                print(f"No state for '{a.key}'.")
                return 1
            for k in keys:
                state.record(k, status="retrying", attempts=0)
                print(f"{k} will be tried again on the next run (if still within max_late_hours).")
            return 0
    except (ConfigError, PublishError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
