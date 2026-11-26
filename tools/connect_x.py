"""Connect an X account: run on your own computer, once per X profile.

    python tools/connect_x.py

Uses your X app's API key and secret (README, "X setup"). Sign in to the X
profile you want to connect in your browser first, then follow the prompts.
"""

import os
import sys
import urllib.parse
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests  # noqa: E402

from autopost import oauth1  # noqa: E402

BASE = "https://api.x.com"


def main():
    ck = os.environ.get("X_API_KEY") or input("X API key (consumer key): ").strip()
    cs = os.environ.get("X_API_SECRET") or input("X API key secret: ").strip()

    url = f"{BASE}/oauth/request_token"
    r = requests.post(url, headers={"Authorization": oauth1.auth_header(
        "POST", url, ck, cs, extra_oauth={"oauth_callback": "oob"})}, timeout=60)
    if r.status_code != 200:
        sys.exit(f"X refused the request ({r.status_code}): {r.text}\n"
                 "Check the key and secret, and that the app has 'Read and write' permission.")
    req = dict(urllib.parse.parse_qsl(r.text))

    auth_url = f"{BASE}/oauth/authorize?oauth_token={req['oauth_token']}"
    print("\nMake sure you're signed in to X as the profile you want to connect, then approve here:")
    print(auth_url + "\n")
    webbrowser.open(auth_url)
    pin = input("Enter the PIN X shows you: ").strip()

    url = f"{BASE}/oauth/access_token"
    r = requests.post(url, headers={"Authorization": oauth1.auth_header(
        "POST", url, ck, cs, req["oauth_token"], req["oauth_token_secret"],
        extra_oauth={"oauth_verifier": pin})}, timeout=60)
    if r.status_code != 200:
        sys.exit(f"X refused the PIN ({r.status_code}): {r.text}")
    acc = dict(urllib.parse.parse_qsl(r.text))

    prefix = input(f"\nConnected @{acc.get('screen_name')}. Secret prefix (e.g. GUTFLIX_X): ").strip()
    print("\nAdd these GitHub secrets (repo Settings > Secrets and variables > Actions):\n")
    print(f"  {prefix}_ACCESS_TOKEN  = {acc['oauth_token']}")
    print(f"  {prefix}_ACCESS_SECRET = {acc['oauth_token_secret']}\n")
    print("Also add X_API_KEY and X_API_SECRET once, if you haven't yet.")


if __name__ == "__main__":
    main()
