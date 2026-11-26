"""Used by the weekly GitHub workflow: refreshes every Instagram account's token
and saves the new one back into the repo's secrets with the gh CLI.

Instagram long-lived tokens last 60 days and can be refreshed once they're a day
old, so refreshing weekly keeps them alive indefinitely.
Needs GH_TOKEN (a fine-grained token with "Secrets: read and write" on this repo).
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests  # noqa: E402

from autopost.config import Secrets, load_settings  # noqa: E402


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root)
    secrets = Secrets()
    bad = 0
    for acct in settings.accounts.values():
        if acct.platform != "instagram":
            continue
        name = f"{acct.secret_prefix}_ACCESS_TOKEN"
        token = secrets.get(name, required=False)
        if not token:
            print(f"{acct.name}: no {name} secret yet, skipping")
            continue
        j = requests.get("https://graph.instagram.com/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=60).json()
        if "access_token" not in j:
            print(f"{acct.name}: refresh failed: {j.get('error', j)}")
            bad += 1
            continue
        subprocess.run(["gh", "secret", "set", name, "--body", j["access_token"]], check=True)
        print(f"{acct.name}: token refreshed, valid ~{j.get('expires_in', 0) // 86400} more days")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
