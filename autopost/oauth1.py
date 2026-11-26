"""Minimal OAuth 1.0a (HMAC-SHA1) signing, as X uses for user-context requests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl


def _enc(s) -> str:
    return quote(str(s), safe="~-._")


def signature(method: str, url: str, params: dict, consumer_secret: str, token_secret: str = "") -> str:
    parts = urlsplit(url)
    base_url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))
    all_params = list(params.items()) + parse_qsl(parts.query, keep_blank_values=True)
    # sort on the percent-encoded pairs, join, then encode again into the base string
    pairs = sorted((_enc(k), _enc(v)) for k, v in all_params)
    norm = "&".join(f"{k}={v}" for k, v in pairs)
    base = "&".join([method.upper(), _enc(base_url), _enc(norm)])
    key = f"{_enc(consumer_secret)}&{_enc(token_secret)}"
    digest = hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def auth_header(method: str, url: str, consumer_key: str, consumer_secret: str,
                token: str = "", token_secret: str = "", form: dict | None = None,
                extra_oauth: dict | None = None, nonce: str | None = None,
                timestamp: str | None = None) -> str:
    """Authorization header value. Pass `form` only for x-www-form-urlencoded bodies."""
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_version": "1.0",
    }
    if token:
        oauth["oauth_token"] = token
    oauth.update(extra_oauth or {})
    sig_params = {**oauth, **(form or {})}
    oauth["oauth_signature"] = signature(method, url, sig_params, consumer_secret, token_secret)
    return "OAuth " + ", ".join(f'{_enc(k)}="{_enc(v)}"' for k, v in sorted(oauth.items()))
