import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

from autopost import oauth1
from autopost.config import ConfigError, Secrets, load_settings
from autopost.platforms import PUBLISHERS
from autopost.platforms.base import Context, PublishError, Result
from autopost.platforms.x import weighted_length
from autopost.posts import load_posts
from autopost.runner import run, validate_all
from autopost.state import State

CONFIG = """
timezone: Asia/Kolkata
media_base_url: https://cdn.example.com/media/
max_late_hours: 12
max_attempts: 2
accounts:
  yt: {platform: youtube, secret_prefix: YT}
  xa: {platform: x, secret_prefix: XA}
  xb: {platform: twitter, secret_prefix: XB}
  ig: {platform: instagram, secret_prefix: IG}
groups:
  all-x: [xa, xb]
"""


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "accounts.yaml").write_text(CONFIG)
    (tmp_path / "posts").mkdir()
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "clip.mp4").write_bytes(b"\0" * 1000)
    (tmp_path / "media" / "a.jpg").write_bytes(b"\xff\xd8" + b"\0" * 100)
    (tmp_path / "media" / "b.jpg").write_bytes(b"\xff\xd8" + b"\0" * 100)
    return tmp_path


def write_post(proj, name, text):
    (proj / "posts" / name).write_text(text)


SECRETS = Secrets({"SECRETS_JSON": json.dumps({
    "YOUTUBE_CLIENT_ID": "cid", "YOUTUBE_CLIENT_SECRET": "cs", "YT_REFRESH_TOKEN": "rt",
    "X_API_KEY": "k", "X_API_SECRET": "s", "XA_ACCESS_TOKEN": "t", "XA_ACCESS_SECRET": "ts",
    "XB_ACCESS_TOKEN": "t2", "XB_ACCESS_SECRET": "ts2", "IG_ACCESS_TOKEN": "igt", "IG_USER_ID": "17841"})})


# --- OAuth 1.0a: the test vector from the OAuth Core 1.0 spec (Appendix A.5) ------
def test_oauth1_signature_matches_spec_example():
    sig = oauth1.signature(
        "GET", "http://photos.example.net/photos?file=vacation.jpg&size=original",
        {"oauth_consumer_key": "dpf43f3p2l4k3l03", "oauth_token": "nnch734d00sl2jdk",
         "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": "1191242096",
         "oauth_nonce": "kllo9940pd9333jh", "oauth_version": "1.0"},
        "kd94hf93k423kf44", "pfkkdhi9sl3r4s00")
    assert sig == "tR3+Ty81lMeYAr/Fid0kMTYa/WM="


def test_oauth1_header_shape():
    h = oauth1.auth_header("POST", "https://api.x.com/2/tweets", "ck", "cs", "tk", "ts",
                           nonce="n", timestamp="1")
    assert h.startswith("OAuth ") and 'oauth_token="tk"' in h and "oauth_signature=" in h


def test_x_weighted_length():
    assert weighted_length("hello") == 5
    assert weighted_length("see https://example.com/a/very/long/path/that/is/long") == 4 + 23
    assert weighted_length("😀") == 2  # emoji count double


# --- loading ---------------------------------------------------------------
def test_groups_timezone_and_staggered_targets(proj):
    write_post(proj, "p.yaml", """
id: p1
publish_at: 2026-12-01 18:30
targets:
  - account: all-x
    text: hi
  - account: yt
    publish_at: 2026-12-01T20:00:00Z
    video: media/clip.mp4
    title: T
""")
    s = load_settings(proj)
    (post,) = load_posts(s)
    assert [t.account for t in post.targets] == ["xa", "xb", "yt"]
    assert post.publish_at == datetime(2026, 12, 1, 13, 0, tzinfo=timezone.utc)
    assert post.targets[2].publish_at == datetime(2026, 12, 1, 20, 0, tzinfo=timezone.utc)
    assert post.targets[1].platform == "x"


def test_unknown_account_and_duplicate_ids(proj):
    write_post(proj, "a.yaml", "id: same\npublish_at: 2026-12-01 10:00\ntargets: [{account: nope, text: x}]")
    with pytest.raises(ConfigError, match="unknown account"):
        load_posts(load_settings(proj))
    write_post(proj, "a.yaml", "id: same\npublish_at: 2026-12-01 10:00\ntargets: [{account: xa, text: x}]")
    write_post(proj, "b.yaml", "id: same\npublish_at: 2026-12-01 11:00\ntargets: [{account: xa, text: y}]")
    with pytest.raises(ConfigError, match="duplicate"):
        load_posts(load_settings(proj))


def test_validation_catches_platform_rules(proj):
    write_post(proj, "bad.yaml", f"""
id: bad
publish_at: 2026-12-01 10:00
targets:
  - account: xa
    text: "{'a' * 300}"
  - account: ig
    type: image
    media: [media/clip.mp4]
  - account: ig
    type: carousel
    media: [media/a.jpg]
  - account: yt
    video: media/missing.mp4
""")
    errors, _ = validate_all(load_settings(proj))
    text = "\n".join(errors)
    assert "300 characters" in text
    assert "needs an image" in text
    assert "2 to 10" in text
    assert "video file not found" in text
    assert "needs a 'title'" in text


def test_secret_check(proj):
    write_post(proj, "ok.yaml", "id: ok\npublish_at: 2026-12-01 10:00\ntargets: [{account: xa, text: hi}]")
    errors, _ = validate_all(load_settings(proj), Secrets({}))
    assert any("XA_ACCESS_TOKEN" in e for e in errors)
    errors, _ = validate_all(load_settings(proj), SECRETS)
    assert errors == []


# --- runner ----------------------------------------------------------------
class FakePub:
    platform = "x"

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def validate(self, t, s):
        return []

    def publish(self, t, ctx):
        self.calls.append(t.key)
        if self.fail:
            raise self.fail
        return Result("1", "https://x.com/i/status/1")


def test_run_posts_due_skips_future_and_late(proj, monkeypatch):
    write_post(proj, "p.yaml", """
id: due
publish_at: 2026-12-01 10:00
targets: [{account: xa, text: a}]
---
id: future
publish_at: 2026-12-02 10:00
targets: [{account: xa, text: b}]
---
id: stale
publish_at: 2026-11-28 10:00
targets: [{account: xa, text: c}]
""")
    fake = FakePub()
    monkeypatch.setitem(PUBLISHERS, "x", fake)
    s = load_settings(proj)
    st = State(proj / "state" / "posted.json")
    now = datetime(2026, 12, 1, 5, 0, tzinfo=timezone.utc)  # 10:30 IST
    assert run(s, SECRETS, st, now=now, log=lambda *a: None) == 0
    assert fake.calls == ["due::xa"]
    assert st.status("due::xa") == "posted"
    assert st.status("stale::xa") == "missed"
    assert st.status("future::xa") == "pending"
    # second run doesn't double-post
    run(s, SECRETS, st, now=now, log=lambda *a: None)
    assert fake.calls == ["due::xa"]


def test_retries_then_fails(proj, monkeypatch):
    write_post(proj, "p.yaml", "id: p\npublish_at: 2026-12-01 10:00\ntargets: [{account: xa, text: a}]")
    fake = FakePub(fail=PublishError("boom"))
    monkeypatch.setitem(PUBLISHERS, "x", fake)
    s, st = load_settings(proj), State(proj / "state" / "posted.json")
    now = datetime(2026, 12, 1, 5, 0, tzinfo=timezone.utc)
    assert run(s, SECRETS, st, now=now, log=lambda *a: None) == 1
    assert st.status("p::xa") == "retrying"
    run(s, SECRETS, st, now=now, log=lambda *a: None)
    assert st.status("p::xa") == "failed"
    run(s, SECRETS, st, now=now, log=lambda *a: None)
    assert len(fake.calls) == 2  # max_attempts: 2


def test_dry_run_posts_nothing(proj, monkeypatch):
    write_post(proj, "p.yaml", "id: p\npublish_at: 2026-12-01 10:00\ntargets: [{account: xa, text: a}]")
    fake = FakePub()
    monkeypatch.setitem(PUBLISHERS, "x", fake)
    s, st = load_settings(proj), State(proj / "state" / "posted.json")
    run(s, SECRETS, st, now=datetime(2026, 12, 2, tzinfo=timezone.utc), dry_run=True, log=lambda *a: None)
    assert fake.calls == [] and st.status("p::xa") == "pending"


# --- platform request flows against a fake HTTP server ----------------------
class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


def fake_http(monkeypatch, handler):
    calls = []

    def req(method, url, **kw):
        calls.append((method, url, kw))
        return handler(method, url, kw)
    monkeypatch.setattr(requests, "request", req)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return calls


def target(proj, text):
    write_post(proj, "p.yaml", text)
    s = load_settings(proj)
    return s, load_posts(s)[0].targets[0]


def test_x_video_post_with_thread(proj, monkeypatch):
    s, t = target(proj, """
id: p
publish_at: 2026-12-01 10:00
targets: [{account: xa, text: hi, media: [media/clip.mp4], thread: [two]}]
""")
    state = {"n": 0}

    def h(m, u, kw):
        if u.endswith("/initialize"):
            return Resp(200, {"data": {"id": "M1"}})
        if u.endswith("/append"):
            return Resp(204)
        if u.endswith("/finalize"):
            return Resp(200, {"data": {"processing_info": {"state": "pending", "check_after_secs": 1}}})
        if "command=STATUS" in u:
            return Resp(200, {"data": {"processing_info": {"state": "succeeded"}}})
        if u.endswith("/2/tweets"):
            state["n"] += 1
            return Resp(201, {"data": {"id": f"T{state['n']}"}})
        raise AssertionError(u)
    calls = fake_http(monkeypatch, h)
    res = PUBLISHERS["x"].publish(t, Context(s, SECRETS, s.accounts["xa"], lambda *a: None))
    assert res.url == "https://x.com/i/status/T1"
    tweets = [json.loads(kw["data"]) for m, u, kw in calls if u.endswith("/2/tweets")]
    assert tweets[0] == {"text": "hi", "media": {"media_ids": ["M1"]}}
    assert tweets[1]["reply"] == {"in_reply_to_tweet_id": "T1"}
    assert all(kw["headers"]["Authorization"].startswith("OAuth ") for _, _, kw in calls)


def test_instagram_carousel(proj, monkeypatch):
    s, t = target(proj, """
id: p
publish_at: 2026-12-01 10:00
targets: [{account: ig, media: [media/a.jpg, media/b.jpg], caption: hello}]
""")
    made = []

    def h(m, u, kw):
        if m == "POST" and u.endswith("/17841/media"):
            made.append(kw["data"])
            return Resp(200, {"id": f"C{len(made)}"})
        if m == "GET" and kw["params"].get("fields") == "status_code,status":
            return Resp(200, {"status_code": "FINISHED"})
        if u.endswith("/media_publish"):
            return Resp(200, {"id": "IG9"})
        if m == "GET" and kw["params"].get("fields") == "permalink":
            return Resp(200, {"permalink": "https://www.instagram.com/p/abc/"})
        raise AssertionError((m, u))
    fake_http(monkeypatch, h)
    res = PUBLISHERS["instagram"].publish(t, Context(s, SECRETS, s.accounts["ig"], lambda *a: None))
    assert res.url == "https://www.instagram.com/p/abc/"
    assert made[0]["image_url"] == "https://cdn.example.com/media/a.jpg"
    assert made[0]["is_carousel_item"] == "true"
    assert made[2]["media_type"] == "CAROUSEL" and made[2]["children"] == "C1,C2"
    assert made[2]["caption"] == "hello"


def test_youtube_upload_and_private_warning(proj, monkeypatch):
    s, t = target(proj, """
id: p
publish_at: 2026-12-01 10:00
targets: [{account: yt, video: media/clip.mp4, title: Hi, tags: [a]}]
""")

    def h(m, u, kw):
        if "oauth2" in u:
            return Resp(200, {"access_token": "AT"})
        if m == "POST" and "uploadType=resumable" in u:
            body = json.loads(kw["data"])
            assert body["snippet"]["title"] == "Hi" and body["status"]["privacyStatus"] == "public"
            return Resp(200, {}, {"Location": "https://upload.session/1"})
        if m == "PUT":
            assert kw["headers"]["Content-Range"] == "bytes 0-999/1000"
            return Resp(200, {"id": "VID", "status": {"privacyStatus": "private"}})
        raise AssertionError(u)
    fake_http(monkeypatch, h)
    res = PUBLISHERS["youtube"].publish(t, Context(s, SECRETS, s.accounts["yt"], lambda *a: None))
    assert res.url == "https://youtu.be/VID"
    assert "audit" in res.note


def test_examples_are_valid_apart_from_missing_media():
    root = Path(__file__).resolve().parents[1]
    s = load_settings(root)
    posts = load_posts(s, root / "examples" / "posts")
    assert len(posts) == 4


# --- Google Drive daily: mirrors the real Myth Court / Gut Detective folders -------
MB = 1_000_000
DRIVE_TREE = {
    "MC": [  # Myth court (top level: full episodes + Shorts subfolders)
        {"id": "ep1full", "name": "Myth court ep1 .mp4", "mimeType": "video/mp4", "size": 1040 * MB},
        {"id": "ep2full", "name": "Myth court ep 2.mp4", "mimeType": "video/mp4", "size": 233 * MB},
        {"id": "S4", "name": "Shorts ep 4", "mimeType": "application/vnd.google-apps.folder"},
        {"id": "S2", "name": "Shorts ep 2", "mimeType": "application/vnd.google-apps.folder"},
        {"id": "S1", "name": "Shorts for ep 1", "mimeType": "application/vnd.google-apps.folder"},
    ],
    "S1": [
        {"id": "teaser", "name": "Teaser ep 1.mp4 ", "mimeType": "video/mp4", "size": 59 * MB},
        {"id": "s3", "name": "Shorts 3.mp4", "mimeType": "video/mp4", "size": 29 * MB},
        {"id": "s1", "name": "Shorts 1.mp4", "mimeType": "video/mp4", "size": 21 * MB},
    ],
    "S2": [
        {"id": "v807", "name": "VID_20261031_173525_807.mp4", "mimeType": "video/mp4", "size": 37 * MB,
         "videoMediaMetadata": {"width": 1080, "height": 1920, "durationMillis": "150000"}},
        {"id": "inshot", "name": "InShot_20261031_173003056.mp4", "mimeType": "video/mp4", "size": 72 * MB},
    ],
    "S4": [
        {"id": "hindi", "name": "क्या 16 घंटे fasting magic है या सिर्फ hype_.mp4", "mimeType": "video/mp4",
         "size": 33 * MB},
    ],
    "GD1": [
        {"id": "gd1", "name": "VID_20261031_061250_235_bsl.mp4", "mimeType": "video/mp4", "size": 15 * MB},
        {"id": "gd1txt", "name": "VID_20261031_061250_235_bsl.txt", "mimeType": "text/plain"},
    ],
}
NAMES = {"GD1": "Gut detective shorts ep1"}

DRIVE_CFG = """
drive_daily:
  time: "18:30"
  accounts: [yt, xa, ig]
  sources:
    - {series: Myth Court, hashtags: "#MythCourt", folder_id: MC, scan: subfolders, subfolder_filter: shorts}
    - {series: Gut Detective, hashtags: "#GutDetective", folder_id: PRIVATE, scan: subfolders,
       subfolder_filter: gut detective, also_folders: [GD1]}
"""


def drive_fake(monkeypatch):
    from autopost import drive

    def h(m, u, kw):
        if u == drive.API:
            fid = kw["params"]["q"].split("'")[1]
            if fid == "PRIVATE":
                return Resp(404, {"error": "File not found"})
            return Resp(200, {"files": DRIVE_TREE[fid]})
        fid = u.rsplit("/", 1)[1]
        if kw["params"].get("fields") == "name":
            return Resp(200, {"name": NAMES.get(fid, "")})
        if fid == "gd1txt":
            r = Resp(200)
            r.content = "Gut feeling ya gut science?\nPet ki sehat ka sach.".encode()
            return r
        raise AssertionError(u)
    fake_http(monkeypatch, h)

    class Stream:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def iter_content(self, n): yield b"video-bytes"
    downloads = []
    monkeypatch.setattr(drive.requests, "get", lambda url, **kw: downloads.append(url) or Stream())
    return drive, downloads


def test_drive_order_titles_and_skips(proj, monkeypatch):
    (proj / "config" / "accounts.yaml").write_text(CONFIG + DRIVE_CFG)
    drive, downloads = drive_fake(monkeypatch)
    s, st = load_settings(proj), State(proj / "state" / "posted.json")
    sec, quiet = Secrets({"GOOGLE_API_KEY": "KEY"}), (lambda *a: None)

    order = [(v["id"], v["episode"], v["part"]) for v in drive.collect(
        {**drive.DEFAULTS, **s.drive_daily}, "KEY", quiet)]
    # episode order 1, 2, 4 then Gut Detective; numbers sorted naturally; full episodes never listed
    assert [o[0] for o in order] == ["s1", "s3", "teaser", "inshot", "v807", "hindi", "gd1"]

    titles = []
    for day in range(1, 8):
        drive.sync(s, sec, st, datetime(2026, 12, day, 3, 0, tzinfo=timezone.utc), quiet)
        post = [p for p in load_posts(s) if p.id == f"drive-2026-12-{day:02d}"][0]
        titles.append((post.targets[0].fields["title"], [t.account for t in post.targets]))
    assert titles[0] == ("Myth Court Ep 1 | Part 1 #Shorts", ["yt", "xa", "ig"])   # "Shorts 1" is generic
    assert titles[2][0] == "Teaser ep 1 #Shorts"                                   # real name kept
    assert titles[3] == ("Myth Court Ep 2 | Part 1 #Shorts", ["yt", "xa", "ig"])
    assert titles[4] == ("Myth Court Ep 2 | Part 2 #Shorts", ["yt", "ig"])        # 150s: X left out
    assert titles[5][0] == "क्या 16 घंटे fasting magic है या सिर्फ hype? #Shorts"     # trailing _ -> ?
    assert titles[6][0] == "Gut feeling ya gut science? #Shorts"                   # from the .txt
    ig = [p for p in load_posts(s) if p.id == "drive-2026-12-07"][0].targets[2].fields
    assert ig["caption"] == "Gut feeling ya gut science?\nPet ki sehat ka sach.\n\n#GutDetective #reels"
    assert validate_all(s, state=st)[0] == []

    # due -> downloaded; then out of videos -> warns once
    drive.sync(s, sec, st, datetime(2026, 12, 7, 13, 5, tzinfo=timezone.utc), quiet)
    assert (proj / "media" / "drive" / "gd1.mp4").exists()
    assert drive.sync(s, sec, st, datetime(2026, 12, 8, 3, 0, tzinfo=timezone.utc), quiet) == 1
    assert drive.sync(s, sec, st, datetime(2026, 12, 8, 4, 0, tzinfo=timezone.utc), quiet) == 0


def test_drive_skips_full_episode_in_shorts_folder(proj, monkeypatch):
    (proj / "config" / "accounts.yaml").write_text(CONFIG + DRIVE_CFG)
    DRIVE_TREE["S1"].insert(0, {"id": "big", "name": "Shorts 0.mp4", "mimeType": "video/mp4", "size": 900 * MB})
    try:
        drive, _ = drive_fake(monkeypatch)
        s, st = load_settings(proj), State(proj / "state" / "posted.json")
        drive.sync(s, Secrets({"GOOGLE_API_KEY": "K"}), st, datetime(2026, 12, 1, 3, tzinfo=timezone.utc),
                   lambda *a: None)
        assert load_posts(s)[0].targets[0].fields["video"] == "drive:s1.mp4"
    finally:
        DRIVE_TREE["S1"].pop(0)


def test_public_url_for_drive_media(proj):
    from autopost.platforms.base import public_url
    s = load_settings(proj)
    assert public_url(s, "drive:v2.mp4") == "https://cdn.example.com/media/drive/v2.mp4"
    s.media_base_url = ""
    assert public_url(s, "drive:v2.mp4", Secrets({"GOOGLE_API_KEY": "KEY"})) == \
        "https://www.googleapis.com/drive/v3/files/v2?alt=media&key=KEY"
