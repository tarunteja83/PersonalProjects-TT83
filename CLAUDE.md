# CLAUDE.md: project context for new sessions

The repo owner (GitHub `tarunteja83`) is building **faceless YouTube channels as a second income**. The main project is
`youtube-automation-agents/`, a multi-agent Python pipeline that posts Shorts and long videos daily via GitHub Actions.
Read `youtube-automation-agents/execution/execution.md` (full user guide) and the `PIPELINE ARCHITECTURE` section of
`youtube-automation-agents/BRAIN.md` before changing anything.

## Channels (live, created by the owner under a different Google account than the Cloud project)
| Key | Channel | Link | Posting (IST) |
|---|---|---|---|
| `geopolitics_war` | Frontline Ledger TT83 | youtube.com/@frontline-ledger-tt83 | fetch 06:00; 6 Shorts hourly 07:00 to 12:00; 2 long (5 to 7 min) at 14:00, 20:00 |
| `kids_edu` | WonderSprout Kids TT83 (made for kids) | youtube.com/channel/UCf9u1ZaejKPPA-oOD8oXVfg | fetch 08:00; 8 Shorts hourly 09:00 to 16:00; 2 long (4 to 6 min max) at 17:00, 19:00 |

## How it works (short)
- **One entry point:** `execution/execution.py`. With no arguments it runs every channel with `run: true`. GitHub uses
  `--scheduled --cron`. `--check` validates config and rewrites the workflow cron lines. Other options: `--ask`,
  `--authorize KEY`, `--format short|long|both`, `--dry-run`.
- **Config:**
  - `execution/channels.yaml`: identity (name, `channel_url`, made_for_kids, watermark, category).
  - `execution/questions.yaml`: Q1 to Q24 per channel, with options written as comments.
  - `BRAIN.md`: global rules. Everything after `## AGENT INSTRUCTIONS` is sent to Gemini, so put human notes above it.
- **Run order:** long videos first, then Shorts. Per format: scout (trending topic via the YouTube API), then
  script + voice (Gemini, then edge-tts with sentence timings, a length check and ~15 s scenes), then footage (one
  Pixabay clip per scene from Gemini's per-scene terms), then the FFmpeg editor, then the publisher (channel check,
  resumable chunked upload, `publishAt`). Then cleanup and a Telegram alert.
- **Manifest:** `GDrive_Mount/YouTube_Automation/01_manifests/trends.json`. Status flow: discovered, voiced,
  ready_to_edit, edited, published (also expired, download_failed).
- **GitHub runs:** use temporary folders. Logins and the manifest sync via rclone to Google Drive
  (`YouTube_Automation/`).
- **Laptop runs:** write video files to `H:/My Drive/YouTube_Auto/<YYYY-MM-DD>/download|upload` (Google Drive for
  Desktop), and day folders older than 2 days are deleted.

## Key decisions (and why)
- **Stock footage (Pixabay), not YouTube downloads.** YouTube blocks yt-dlp from GitHub's cloud IPs ("Sign in to
  confirm you're not a bot"). It also avoids copyright claims. Pexels is supported in code, but its key issuance was
  paused, so the workflow passes only `PIXABAY_API_KEY`.
- **Footage synced to narration.** The script is written first, then one clip per scene. Frontline may name public
  figures (government representatives) in search terms; the kids channel never names real people.
- **AI voices, not voice cloning:** edge-tts. `en-US-ChristopherNeural` for Frontline; `en-US-AnaNeural` (child
  voice) for WonderSprout. Supported languages: English, Hindi, Telugu.
- **Audio and branding:** no original audio (`original_audio_volume: 0`), a name-only LIKE then SUBSCRIBE end card
  (`show_logo: false`), and a watermark.
- **WonderSprout content:** entertainment plus education. Original funny stories (no copyrighted characters) and
  `footage_style: "mixed"` (Pixabay animation alternating with real footage).
- **Rendering:** FFmpeg does the rendering for stock videos (MoviePy per-frame was 30+ min for a long video).
  MoviePy remains only for `footage.source: "youtube"`.
- **Upload safety:** `channels.yaml` `channel_url` is checked against the logged-in channel before saving a login and
  before every upload.
- **Gemini:** `gemini-2.5-flash` is retired for this key. `ai_models` in BRAIN.md falls back across 3.8 → 3.7 → 3.5
  → flash-latest, and retries dropped connections.

## Environment and secrets
- **Local Python:** `C:\Users\USERSS\venvs\yt\Scripts\python.exe` (Python 3.14). It's a short path because of
  Windows' long-path limit, and the owner has no admin rights. `winget` isn't available. rclone is at
  `C:\Users\USERSS\tools\rclone`, on the user PATH.
- **Python versions:** GitHub uses **Python 3.11**. Don't use 3.12+ syntax, such as reusing a quote inside an f-string
  expression.
- **`youtube-automation-agents/.env`** (git-ignored) holds the real keys: YOUTUBE, GEMINI, PIXABAY, TELEGRAM, HF_TOKEN
  (kept for future use). **Never overwrite it or `.env.example` without reading it first;** the owner once put real
  keys in `.env.example`. Never print key values; mask them.
- **GitHub secrets:** YOUTUBE_API_KEY, GEMINI_API_KEY, PIXABAY_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID,
  CLIENT_SECRET_JSON, RCLONE_CONFIG_GDRIVE (rclone uses the owner's own OAuth client; the env var must not be named
  `RCLONE_CONFIG`).
- **Google Cloud project** `thepersonelyoutubeproject-tt83`: OAuth app published (so tokens don't expire after 7 days),
  scopes youtube.upload + youtube.readonly, Drive API enabled. YouTube logins are in Google Drive
  `YouTube_Automation/00_tokens/` (and locally in `GDrive_Mount/.../00_tokens`).
- **Limits:**
  - YouTube: 100 uploads and 100 searches a day per project.
  - Pixabay: 100 requests a minute; searches are paced.
  - GitHub Actions: 2,000 free minutes a month on a private repo; `--check` estimates usage, currently ~1,600.

## Conventions
- **Committing:** commit on a new descriptively named branch, push it, then fast-forward `main` and push. The owner
  approves commits and pushes.
- **After code or config changes:**
  - run `execution.py --check` (it validates and syncs the workflow cron)
  - regenerate `setup_project.py` (it embeds every project file)
  - check the Python 3.11 compatibility of the syntax
- **Verify before committing:** use offline tests with stubs where possible, then a real local test run with
  `--videos 1 --post-now --visibility unlisted`. Daily runs post **public** videos.
- **Write for the owner in plain terms:** explain where to click, and give options with a recommendation.

## Status and open items (as of 2026-10-21)
- **Shipped:** the full pipeline is live on `main`, including scene-synced footage, long videos and the GitHub
  hardening.
- **Not yet confirmed:** that full scheduled days complete on GitHub. Ask the owner for the Actions results (green
  ticks, the step 8 RESULT section) and their billing minutes.
- **Optional clean-up:** some early test videos on YouTube are unlisted (a few have old audio or the 11-minute kids
  video); the owner was advised to delete or publish them in Studio.
- **Possible future work:** more channels (add to both YAML files and run `--authorize`), and tuning scene-matching
  quality from sync reports.
