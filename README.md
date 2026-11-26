# Social Autopilot

This tool posts to **YouTube, X (Twitter) and Instagram** on a schedule. It runs free on
GitHub Actions, so your computer can stay off. You write each post as a small text file,
push it to GitHub, and the post goes out at the time you set.

It is set up for Gutflix's three accounts, and adding more profiles takes three lines
plus their keys.

```
config/accounts.yaml     your accounts and groups
posts/*.yaml             the queue: one file per post (or several separated by ---)
media/                   videos and images the posts use
state/posted.json        what has been posted (the bot updates this itself)
examples/posts/          sample posts to copy (never posted)
tools/                   one-time helpers to connect each account
.github/workflows/       the schedule (every 30 min), weekly token refresh, checks
```

How it works: every 30 minutes GitHub runs `python -m autopost run`. It finds anything
whose `publish_at` has passed, posts it through each platform's official API, and records
the link in `state/posted.json`, so nothing ever posts twice. If a post is more than
`max_late_hours` late (default 12), it is skipped rather than posted at a strange time.
A failed post is retried up to `max_attempts` times. GitHub emails you when a run fails,
and each run's summary page lists the links to what went out.

**Daily Shorts from Google Drive** is already switched on. Every day at 18:30 IST, the next Short
from the Myth Court Shorts folders, then the Gut Detective folders, goes out as an **Instagram Reel**
and an **X video post**. To post them as YouTube Shorts too, add `gutflix-youtube` to
`drive_daily.accounts`. Setup is in section 5a.

---

## 1. Put it on GitHub (5 min)

1. Create a **private** repository on GitHub (for example `gutflix-autopilot`).
2. Upload everything in this folder to it, including the hidden `.github` folder.
   With git: `git init && git add . && git commit -m start && git remote add origin <url> && git push -u origin main`.
3. Under **Settings → Actions → General → Workflow permissions**, choose **Read and write permissions**.

You add keys under **Settings → Secrets and variables → Actions → New repository secret**.
Never put keys in the files themselves.

To run the helper scripts on your computer you need Python 3.10+ and
`pip install -r requirements.txt`.

---

## 2. Connect YouTube

1. Go to https://console.cloud.google.com, create a project, and enable the **YouTube Data API v3**.
2. Set up the **OAuth consent screen**: choose External, add your own Google account as a test
   user, then set the publishing status to **In production**. While the app stays in
   *Testing*, Google expires the login after 7 days and posting stops.
3. Under **Credentials → Create credentials → OAuth client ID**, choose **Desktop app**.
   Save the client ID and secret as the secrets `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET`.
4. On your computer, run `python tools/connect_youtube.py`, sign in, and pick the Gutflix
   channel. Google will warn that the app isn't verified; that's expected for your own app.
   Click Advanced and continue. Save the printed `GUTFLIX_YT_REFRESH_TOKEN` as a secret.

**YouTube: public uploads.** YouTube keeps every video uploaded through an *unaudited* API
project **private**. To post publicly, submit the free
[YouTube API Services audit form](https://support.google.com/youtube/contact/yt_api_form)
for your project and describe it as "an internal tool that uploads videos to channels I own."
Until it's approved, the tool still uploads; it marks each run with a note that the video
stayed private, and you can make it public in YouTube Studio with one click.
The default quota allows about 100 uploads a day per project.

## 3. Connect X (Twitter)

1. At https://developer.x.com, sign up and **add credits**. X bills per request now
   (roughly 1.5 cents per post, and much more for posts that contain a link; check
   their pricing page for current rates).
2. Create an app. Under **User authentication settings**, set permissions to **Read and write**
   (type: Web App). The callback URL can be anything, such as `https://example.com`.
3. Copy the app's **API Key** and **API Key Secret** into the secrets `X_API_KEY` and `X_API_SECRET`.
4. In your browser, sign in to X **as the profile you want to connect**. Then run
   `python tools/connect_x.py`, approve the app, and type the PIN. Save the two printed
   secrets (`GUTFLIX_X_ACCESS_TOKEN`, `GUTFLIX_X_ACCESS_SECRET`). These don't expire.

## 4. Connect Instagram

1. The Instagram account must be **Professional** (Business or Creator). You can switch
   for free in the Instagram app under Settings → Account type.
2. At https://developers.facebook.com, create an app (choose the Business type), then add the **Instagram** product
   and open **API setup with Instagram login**.
3. Under **App roles → Roles**, add the account as an **Instagram tester**, then accept the invite
   in Instagram (Settings → Website permissions → Apps and websites → Tester invites).
   For accounts you own, this means you don't need Meta's app review.
4. In API setup, click **Generate token** next to the account. Check the token with
   `python tools/instagram_token.py check <token>` and save it as `GUTFLIX_IG_ACCESS_TOKEN`.
5. These tokens last 60 days. The weekly **Refresh Instagram tokens** workflow renews them
   automatically. For that it needs one more secret, `GH_SECRETS_TOKEN`: create a
   [fine-grained GitHub token](https://github.com/settings/personal-access-tokens/new)
   for this repo only, with **Secrets: Read and write** permission.

### Instagram media hosting

Instagram doesn't accept file uploads. Meta downloads each image and video from a **public
link**, so the files in `media/` also need to be public somewhere. Pick one option:

- **Cloudflare R2 (recommended, automatic).** Create a free R2 bucket, turn on its public
  `r2.dev` URL, and create an R2 API token with Object Read & Write access. Add the secrets
  `R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY`, then set
  `media_base_url: https://pub-xxxx.r2.dev/` in `config/accounts.yaml`. Each run copies
  `media/` to the bucket before posting.
- **Any public host you already use.** Upload the same files there and set `media_base_url`
  to the folder's link. You can also give full `https://` links in a post's `media` list.

Instagram images must be **JPEG**. Other formats are rejected before anything posts.

---

## 5a. Daily Shorts from Google Drive

Settings live under `drive_daily:` in `config/accounts.yaml`: the sources, the time, which
accounts get the video, and the wording templates.

**What it uses, in this order:**
1. **Myth Court:** every subfolder of the Myth court folder with "shorts" in its name, in episode
   order ("Shorts for ep 1", "Shorts ep 2", ...). The full episodes at the top of the folder are
   never posted. A new "Shorts ep 5" folder is picked up automatically.
2. **Gut Detective:** "Gut detective ..." subfolders of the **Gutflix** folder. The Gutflix folder
   is private today, so for now only "Gut detective shorts ep1" is read. **Share the Gutflix folder
   as "Anyone with the link"** and new Gut Detective episode folders get picked up too.

As safety checks, it skips any video over 200 MB or over 3 minutes, so a full episode can't go
out by mistake. It also leaves X off any day whose video is longer than 2 min 20 s, because X
rejects longer videos without Premium. Run `python -m autopost drive-preview` (with
`GOOGLE_API_KEY` set) to see the whole upcoming queue with the titles each Short will get.

**Setup (5 min):**
1. In Google Drive, set each source folder to **Share → General access → Anyone with the link → Viewer**.
   Myth court and "Gut detective shorts ep1" are already shared this way. Gutflix isn't yet.
2. In the same Google Cloud project you used for YouTube, enable the **Google Drive API**.
   Then go to **Credentials → Create credentials → API key** and restrict the key to the Drive API.
3. Save the key as the secret `GOOGLE_API_KEY`.

Until that secret exists, the Autopost run reports the Drive step as failed, but everything
else still posts. If you don't want Drive posting, set `enabled: false`.

**How it picks and words each day's video:**
- It takes the next video that hasn't been used: sources in order, episode folders in order,
  and files by name within each folder, with numbers in order (`Shorts 2` comes before `Shorts 10`).
  Each video is used once, and `state/drive.json` records which ones have gone out.
- **Titles for camera file names:** names like `VID_20261031_173525_872` or `InShot_2026…`
  become "Myth Court Ep 2 | Part 1" (`generic_title`). Real names, such as the Hindi titles in
  "Shorts ep 4", are used as they are. A trailing `_` turns back into the `?` it replaced.
- **Captions:** if the folder has a `.txt` file with the same name as the video (`Myth 2.mp4` +
  `Myth 2.txt`), its first line becomes `{title}` and the whole text becomes `{text}`. Without
  one, the cleaned-up file name is used for both.
- On its first run each day, it writes that day's post to `posts/drive/<date>.yaml` and commits
  it. You can edit that file before the posting time to change the wording, or set
  `enabled: false` in it to skip the day.
- At posting time it downloads the video from Drive and posts it. If a day's time has already
  passed when the tool first runs (for example, on the day you set it up), that day's video
  goes out right away.
- When the folder runs out of new videos, the run fails once that day so GitHub emails you.
  Add more videos and the next day carries on.

**Video requirements:**
- **YouTube Shorts:** vertical (9:16) or square, and 3 minutes or shorter. Otherwise YouTube
  publishes it as a regular video.
- **Instagram Reels:** MP4 or MOV, under 15 minutes.
- **X:** videos longer than **2 minutes 20 seconds** need X Premium on the posting account.
  The tool leaves X out on those days automatically when Drive reports the video's length.
- **Instagram download:** if you haven't set up R2, Instagram downloads the video directly
  from Drive through the API key. If Instagram reports that it can't fetch the media, set up
  the R2 option in section 4. Synced Drive videos are then served from there.

## 5. Write posts

Copy a file from `examples/posts/` into `posts/`, change it, add its media to `media/`, and push.
The **Check posts** workflow validates it right away, so a typo shows up within a minute,
not at posting time.

```yaml
id: 2026-12-01-trailer            # unique; also used in state/posted.json
publish_at: 2026-12-01 18:30      # read in the config timezone (Asia/Kolkata); or add +05:30 / Z
targets:
  - account: gutflix-youtube      # an account, a group, or a list like [gutflix-x, brand2-x]
    video: media/trailer.mp4
    title: "Season 2 Trailer #Shorts"
  - account: gutflix-x
    publish_at: 2026-12-01 19:00  # optional: this target goes out at a different time
    text: "Trailer's out 🎬"
    media: [media/trailer.mp4]
  - account: gutflix-instagram
    type: reel
    media: [media/trailer.mp4]
    caption: "Season 2 🎬 #gutflix"
```

Set `enabled: false` on a post to pause it. You can put several posts in one file, separated by `---`.

| Platform | Fields |
|---|---|
| **YouTube** | `video` (required), `title` (required, ≤100 chars), `description`, `tags`, `privacy` (public/unlisted/private), `thumbnail`, `category_id` (default 22), `made_for_kids` (default false), `notify_subscribers`, `contains_synthetic_media`. A vertical video under 3 minutes becomes a Short automatically. |
| **X** | `text` (≤280 weighted chars), `media` (up to 4 images, or 1 video or GIF), `thread` (a list of follow-up replies) |
| **Instagram** | `type`: `image`, `carousel` (2–10 items), `reel`, or `story` (guessed from the media if left out); `media`; `caption` (≤2200 chars, ≤30 hashtags); reels also take `cover`, `thumb_offset_ms` and `share_to_feed`; images take `alt_text`; any type takes `is_ai_generated: true` |

## 6. Day to day

```bash
python -m autopost validate                 # check every waiting post
python -m autopost validate --check-secrets # also check keys (needs them set locally)
python -m autopost plan                     # everything scheduled, with status and links
python -m autopost run --dry-run            # what would post right now
python -m autopost retry 2026-12-01-trailer # try a failed/missed post again
```

On GitHub you can also go to **Actions → Autopost → Run workflow** to post immediately, or to do a dry run.

**Timing:** a post goes out on the first run after its `publish_at`, so usually within
30 minutes of that time (GitHub sometimes starts runs a little late). Each run takes about a
minute of GitHub's free 2,000 Actions minutes a month for private repos. To post closer to the
exact time, change the cron in `.github/workflows/autopost.yml` to `*/15 * * * *`, which needs
GitHub Pro's 3,000 minutes.

To keep the repo small, delete media from `media/` once it has posted (GitHub caps a single
file at 100 MB).

## 7. Adding the next 10 profiles

For each new profile:

1. Add three lines to `config/accounts.yaml`:
   ```yaml
   brand2-instagram:
     platform: instagram
     secret_prefix: BRAND2_IG
   ```
2. Connect it with the matching helper (or **Generate token** for Instagram) and save its
   secrets with that prefix. All X profiles share one X app, and all YouTube channels can
   share one Google project.
3. Optionally add it to a group (`all-instagram: [...]`) so a single post can target many accounts.

You don't need to change the workflows. They pass every secret through automatically.

**Stay within each platform's rules as you scale.** X's automation rules don't allow posting
identical or near-identical content across multiple accounts, and Instagram treats that kind
of pattern as inauthentic behaviour. Accounts can be banned for it. Give each profile its own
wording and content, and space out posts to shared topics. Groups are there to save you
typing, not to blast the same text everywhere.

## Limits at a glance (checked November 2026)

| | YouTube | X | Instagram |
|---|---|---|---|
| Cost | Free | Pay per request (about 1.5¢/post; link posts cost more) | Free |
| Daily cap | ~100 uploads/project (default quota) | Rate limits only | 100 API posts per account per 24h |
| Catch | Private until your project passes the audit | Buy credits first | Professional account + public media links; JPEG images only |

## Troubleshooting

- **A run failed:** open Actions → the failed run. The log and summary show the platform's
  exact error. Fix it, then `python -m autopost retry <post id>` and push `state/posted.json`.
- **YouTube `invalid_grant`:** the consent screen was still in Testing, so the login expired.
  Set it to In production and run `connect_youtube.py` again.
- **X `401` / `403`:** the app wasn't set to Read and write when you generated the tokens.
  Change the setting and run `connect_x.py` again. A `402` means you're out of credits.
- **Instagram "media could not be fetched":** the `media_base_url` link isn't public, or
  the file hasn't synced yet. Open the link in a private browser window to check.
- **Instagram token expired:** check that the Refresh Instagram tokens workflow is green and
  `GH_SECRETS_TOKEN` is set. Generate a new token and save it.

Run the tests with `pip install pytest && python -m pytest -q`.
