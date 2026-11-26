"""Instagram token helper.

    python tools/instagram_token.py check TOKEN
    python tools/instagram_token.py exchange SHORT_LIVED_TOKEN   (asks for the Instagram app secret)
    python tools/instagram_token.py refresh TOKEN

Tokens from Meta's dashboard "Generate token" button are usually already
long-lived (60 days); `check` confirms which account a token belongs to.
The GitHub workflow refreshes saved tokens weekly so they never expire.
"""

import getpass
import sys

import requests

G = "https://graph.instagram.com"


def show(j):
    if "error" in j:
        sys.exit(f"Instagram said: {j['error'].get('message', j)}")
    return j


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("check", "exchange", "refresh"):
        sys.exit(__doc__)
    cmd, token = sys.argv[1], sys.argv[2]
    if cmd == "exchange":
        secret = getpass.getpass("Instagram app secret: ")
        j = show(requests.get(f"{G}/access_token", params={
            "grant_type": "ig_exchange_token", "client_secret": secret, "access_token": token}, timeout=60).json())
        token = j["access_token"]
        print(f"Long-lived token (valid ~{j.get('expires_in', 0) // 86400} days):\n{token}\n")
    elif cmd == "refresh":
        j = show(requests.get(f"{G}/refresh_access_token", params={
            "grant_type": "ig_refresh_token", "access_token": token}, timeout=60).json())
        token = j["access_token"]
        print(f"Refreshed token (valid ~{j.get('expires_in', 0) // 86400} days):\n{token}\n")
    me = show(requests.get(f"{G}/me", params={
        "fields": "user_id,username,account_type", "access_token": token}, timeout=60).json())
    print(f"Token works for @{me.get('username')} ({me.get('account_type')}), user id {me.get('user_id')}")
    print("Save it as the GitHub secret <PREFIX>_ACCESS_TOKEN, e.g. GUTFLIX_IG_ACCESS_TOKEN.")


if __name__ == "__main__":
    main()
