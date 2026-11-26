"""Decides what is due and posts it."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from .config import ConfigError, Secrets, Settings
from .platforms import PUBLISHERS, Context, PublishError
from .posts import Target, load_posts
from .state import State

DONE = ("posted", "missed", "failed")


def validate_all(settings: Settings, secrets: Secrets | None = None,
                 state: State | None = None) -> tuple[list[str], list[str]]:
    """Returns (errors, warnings) for everything still waiting to post."""
    errors, warnings = [], []
    try:
        posts = load_posts(settings)
    except ConfigError as e:
        return [str(e)], []
    for post in posts:
        for t in post.targets:
            if state is not None and state.status(t.key) in DONE:
                continue  # already handled; its media may have been cleaned up
            pub = PUBLISHERS[t.platform]
            where = f"{t.source.name} -> {t.account}"
            errors += [f"{where}: {e}" for e in pub.validate(t, settings)]
            if hasattr(pub, "warnings"):
                warnings += [f"{where}: {w}" for w in pub.warnings(t)]
    if secrets is not None:
        used = {t.account for p in posts for t in p.targets
                if state is None or state.status(t.key) not in DONE}
        dd = settings.drive_daily
        if dd and dd.get("enabled", True) and dd.get("sources"):
            used |= set(settings.resolve_targets(dd.get("accounts") or list(settings.accounts)))
            if not secrets.has("GOOGLE_API_KEY"):
                errors.append("Drive daily posting: missing secret GOOGLE_API_KEY")
        if not used:
            used = set(settings.accounts)
        for name in sorted(used):
            acct = settings.accounts[name]
            for alts in PUBLISHERS[acct.platform].secrets_needed(acct):
                if not secrets.has(*alts):
                    errors.append(f"account {name}: missing secret {' or '.join(alts)}")
    return errors, warnings


def _summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path and lines:
        with open(path, "a", encoding="utf-8") as f:
            f.write("## Social Autopilot run\n\n" + "\n".join(f"- {l}" for l in lines) + "\n")


def run(settings: Settings, secrets: Secrets, state: State, *, now: datetime | None = None,
        dry_run: bool = False, log=print) -> int:
    now = now or datetime.now(timezone.utc)
    posts = load_posts(settings)
    late_limit = timedelta(hours=settings.max_late_hours)
    report, failures = [], 0

    due: list[Target] = []
    for post in posts:
        for t in post.targets:
            if state.status(t.key) in DONE or t.publish_at > now:
                continue
            due.append(t)

    if not due:
        log("Nothing due.")
        return 0

    for t in due:
        label = f"{t.post_id} -> {t.account} ({t.platform})"
        if now - t.publish_at > late_limit:
            log(f"MISSED  {label}: more than {settings.max_late_hours:g}h past its time, skipping")
            if not dry_run:
                state.record(t.key, status="missed")
            report.append(f"⏭️ missed {label}")
            continue

        pub = PUBLISHERS[t.platform]
        errs = pub.validate(t, settings)
        if errs:
            failures += 1
            log(f"INVALID {label}: " + "; ".join(errs))
            if not dry_run:
                state.record(t.key, status="failed", last_error="; ".join(errs))
            report.append(f"❌ {label}: " + "; ".join(errs))
            continue

        if dry_run:
            log(f"WOULD POST {label}")
            continue

        acct = settings.accounts[t.account]
        missing = [alts[0] for alts in pub.secrets_needed(acct) if not secrets.has(*alts)]
        if missing:
            # Not connected yet: skip quietly rather than fail every run. It stays pending,
            # so it still posts if the keys are added before max_late_hours passes.
            log(f"WAITING {label}: account not connected yet (missing {', '.join(missing)})")
            report.append(f"⏸️ {label}: not connected yet (add {', '.join(missing)})")
            continue

        log(f"POSTING {label}")
        attempts = state.get(t.key).get("attempts", 0) + 1
        try:
            ctx = Context(settings, secrets, settings.accounts[t.account], log)
            res = pub.publish(t, ctx)
        except (PublishError, ConfigError, KeyError, ValueError) as e:
            retriable = getattr(e, "retriable", True)
            final = (not retriable) or attempts >= settings.max_attempts
            status = "failed" if final else "retrying"
            failures += 1
            log(f"  {status.upper()}: {e}")
            state.record(t.key, status=status, attempts=attempts, last_error=str(e)[:1000])
            report.append(f"❌ {label} ({status}, attempt {attempts}): {str(e)[:300]}")
            continue
        state.record(t.key, status="posted", attempts=attempts, id=res.id, url=res.url,
                     note=res.note, posted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        log(f"  POSTED {res.url}" + (f"  NOTE: {res.note}" if res.note else ""))
        report.append(f"✅ {label}: {res.url}" + (f" — ⚠️ {res.note}" if res.note else ""))

    _summary(report)
    return 1 if failures else 0
