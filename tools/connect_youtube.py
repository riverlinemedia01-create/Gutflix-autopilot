"""Connect a YouTube channel: run on your own computer, once per channel.

    python tools/connect_youtube.py

It opens Google's sign-in page, you pick the channel, and it prints the
refresh token to save as a GitHub secret. Needs the OAuth client ID and secret
of a "Desktop app" client from Google Cloud (README, "YouTube setup").
"""

import http.server
import json
import os
import secrets
import socket
import sys
import urllib.parse
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests  # noqa: E402

SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    cid = os.environ.get("YOUTUBE_CLIENT_ID") or input("OAuth client ID: ").strip()
    csec = os.environ.get("YOUTUBE_CLIENT_SECRET") or input("OAuth client secret: ").strip()
    port = free_port()
    redirect = f"http://127.0.0.1:{port}"
    state = secrets.token_urlsafe(16)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state})

    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Done. You can close this tab and go back to the terminal.</h2>")

        def log_message(self, *a):
            pass

    print("\nOpening your browser. If it doesn't open, visit:\n" + url + "\n")
    print("Sign in and choose the CHANNEL you want this tool to post to.")
    webbrowser.open(url)
    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    while "code" not in got and "error" not in got:
        server.handle_request()
    if got.get("error") or got.get("state") != state:
        sys.exit(f"Sign-in failed: {got.get('error', 'state mismatch')}")

    tok = requests.post("https://oauth2.googleapis.com/token", data={
        "code": got["code"], "client_id": cid, "client_secret": csec,
        "redirect_uri": redirect, "grant_type": "authorization_code"}, timeout=60).json()
    if "refresh_token" not in tok:
        sys.exit("Google didn't return a refresh token: " + json.dumps(tok))

    ch = requests.get("https://www.googleapis.com/youtube/v3/channels",
                      params={"part": "snippet", "mine": "true"},
                      headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=60).json()
    name = (ch.get("items") or [{}])[0].get("snippet", {}).get("title", "(unknown channel)")
    prefix = input(f"\nConnected to '{name}'. Secret prefix for this account (e.g. GUTFLIX_YT): ").strip()
    print("\nAdd this GitHub secret (repo Settings > Secrets and variables > Actions):\n")
    print(f"  {prefix}_REFRESH_TOKEN = {tok['refresh_token']}\n")
    print("Also add YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET once, if you haven't yet.")


if __name__ == "__main__":
    main()
