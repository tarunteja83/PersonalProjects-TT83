# CLAUDE.md: project context for new sessions

The repo owner (GitHub `tarunteja83`) is building **faceless YouTube channels as a second income**. The main project is
`youtube-automation-agents/`, a multi-agent Python pipeline that posts Shorts and long videos daily via GitHub Actions.
Read `youtube-automation-agents/execution/execution.md` (full user guide) and the `PIPELINE ARCHITECTURE` section of
`youtube-automation-agents/BRAIN.md` before changing anything.

## Channels (live, created by the owner under a different Google account than the Cloud project)
| Key | Channel | Link | Posting (IST) |
|---|---|---|---|
| `geopolitics_war` | Frontline Ledger TT83 | youtube.com/@frontline-ledger-tt83 | Shorts 08:00, 10:00, 14:00, 16:00, 18:00, 19:00; long (5 to 7 min) 12:00, 16:30 |
| `kids_edu` | WonderSprout Kids TT83 (made for kids) | youtube.com/channel/UCf9u1ZaejKPPA-oOD8oXVfg | Shorts 09:00, 14:30; long (4 to 6 min max) 11:30, 16:00, 18:30 |

Both channels run together three times a day (`fetch_time: ["06:00", "13:00", "17:00"]`). Frontline: morning and
afternoon 1 long + 2 Shorts, evening 2 Shorts. WonderSprout (since 2026-10-24, owner's request: long videos do better):
morning and afternoon 1 long + 1 Short, evening 1 long. 13 videos a day. Each run owns the go-live slots
between its fetch time and the next one (`plan_run` in `execution.py`). The cron fires `github_schedule_lead_minutes`
(BRAIN.md, 265) early because GitHub starts scheduled runs 4 to 5 hours late. The owner plans to add more channels.

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
- **AI voices, not voice cloning:** edge-tts with expressive sentence-by-sentence narration. Chosen by the owner on
  2026-10-24 from samples: `en-US-BrianMultilingualNeural` (measured) for Frontline, `en-US-AvaMultilingualNeural`
  (lively) for WonderSprout. Supported languages: English, Hindi, Telugu.
- **Audio and branding:** no original audio (`original_audio_volume: 0`), a name-only LIKE then SUBSCRIBE end card
  (`show_logo: false`), and a watermark.
- **WonderSprout content:** entertainment plus education. Original funny stories (no copyrighted characters) and
  `footage_style: "mixed"` (Pixabay animation alternating with real footage).
- **Rendering:** FFmpeg does the rendering for stock videos (MoviePy per-frame was 30+ min for a long video).
  MoviePy remains only for `footage.source: "youtube"`.
- **Upload safety:** `channels.yaml` `channel_url` is checked against the logged-in channel before saving a login and
  before every upload.
- **Backup AI (Groq):** `agents/ai.py` sends a request to Groq (`ai_fallbacks` in BRAIN.md: gpt-oss-120b, then
  qwen3.8-27b; free, ~1,000 requests/day/model, 8K tokens/min) as soon as every Gemini model fails, before the
  65 s wait. Verified 2026-10-22: with Gemini out of quota, Groq wrote a full Short (script, title, scenes).
  GROQ_API_KEY is in .env and was added as a GitHub secret by the owner (the token can't list secrets; the run log
  line "Backup AI ... groq (key set)" confirms it).
- **Gemini quota:** runs on 20 and 21 Oct 2026 failed (0 uploaded) when every model returned 429 mid-run. Now the
  script request also returns title, description, hashtags and footage terms (Shorts also get their per-scene
  terms there: 1 request per Short, 2 per long video, ~20 a day). The key is on the free tier: 20 requests a day per
  model, reset at 12:30 IST (midnight Pacific). The publisher falls back to the source video's title, so rendered
  videos still upload. Partial uploads give a yellow `::warning` on the run (still green).
- **GitHub schedule delay:** scheduled runs started 4 to 5 hours after the cron time, so the cron is shifted earlier
  (see above). Passed go-live slots go live soon after upload; the Telegram start message shows how early or late.
- **Telegram:** each run sends a start message, then one message per channel (videos, links, go-live times, hashtags).
- **Hashtags:** Gemini's topic hashtags plus the trending source video's own, up to 8, also used as search tags.
- **Views (from `execution/view_techniques.md`, source S1 "Why Your Views STOP After 48 Hours"):** ACTIVE RULES
  (trend first, stay in niche, retention, honest click-worthy titles) are appended to the Gemini instructions by
  `brain.view_rules()`. The Scout ranks sources by views per hour and skips other-language sources (other scripts,
  Hinglish words, titles naming another language; WonderSprout had been taking Hindi topics). Agent 8 (Analyst,
  `agents/agent_8_analyst.py`) stores views on manifest items (`stats`, `views_48h`), orders each channel's search
  phrases by views after 48 h (re-trying phrases unused for 3 days), and posts a daily Telegram report
  (state in `01_manifests/analyst_state.json`).
- **Engagement overhaul (2026-10-24, owner's viewer feedback, source S2 in view_techniques.md):**
  - Real footage: Frontline `footage_style: "real"` searches Wikimedia Commons for real video of each scene's named
    person/place/event (`stock_footage.commons_candidates`): only public domain, CC0, CC BY; every key word of the
    term must be in the file title (longer terms retried with the first two words); only the needed seconds are
    fetched with FFmpeg over HTTP; a used video is reused from a later moment; credits (author, licence,
    `commons.wikimedia.org/?curid=`) go in the description. Real clips are named `*_real.mp4`; the editor shows them
    whole on a blurred copy in Shorts. Copyrighted news-agency footage is deliberately not used.
  - Pixabay clips must have a tag matching the term (`tags_match`), which fixed scenery that didn't match the words.
  - Faster cuts: `clips_per_video: 6`, `long_scene_seconds: 8` (BRAIN.md `footage`).
  - Expressive voice: `agents/expressive_voice.py` narrates sentence by sentence with per-kind pitch/rate/volume
    (`voice_expression`: lively kids, measured news, off). Samples are in Google Drive `YouTube_Auto/voice_samples`;
    the owner chose Ava (kids) and Brian (news).
  - Kids writing rules rewritten in BRAIN.md Agent 3 (named hero, silly problem, three tries, twist, sound words,
    talk to the viewer every 20 to 30 s).
  - Trending hashtags: the Scout counts hashtags of the ~15 fastest-rising same-language videos per search
    (`trending_hashtags`, generic and channel tags removed); the publisher puts 2 of them + the top topic tag first,
    up to 12 in total.
- **Gemini:** `gemini-2.5-flash` is retired for this key. `ai_models` in BRAIN.md falls back across 3.8 → 3.7 → 3.5
  → flash-latest, and retries dropped connections.

## Environment and secrets
- **Local Python:** `C:\Users\USERSS\venvs\yt\Scripts\python.exe` (Python 3.14). It's a short path because of
  Windows' long-path limit, and the owner has no admin rights. `winget` isn't available. rclone is at
  `C:\Users\USERSS\tools\rclone`, on the user PATH.
- **Python versions:** GitHub uses **Python 3.11**. Don't use 3.12+ syntax, such as reusing a quote inside an f-string
  expression.
- **`youtube-automation-agents/.env`** (git-ignored) holds the real keys: YOUTUBE, GEMINI, PIXABAY, TELEGRAM, HF_TOKEN
  (kept for future use) and GITHUB_TOKEN (fine-grained, this repo only, Actions read and write: used to read run logs
  and start the workflow through the API). **Never overwrite it or `.env.example` without reading it first;** the owner once put real
  keys in `.env.example`. Never print key values; mask them.
- **GitHub secrets:** YOUTUBE_API_KEY, GEMINI_API_KEY, GROQ_API_KEY, PIXABAY_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID,
  CLIENT_SECRET_JSON, RCLONE_CONFIG_GDRIVE (rclone uses the owner's own OAuth client; the env var must not be named
  `RCLONE_CONFIG`).
- **Google Cloud project** `thepersonelyoutubeproject-tt83`: OAuth app published (so tokens don't expire after 7 days),
  scopes youtube.upload + youtube.readonly, Drive API enabled. YouTube logins are in Google Drive
  `YouTube_Automation/00_tokens/` (and locally in `GDrive_Mount/.../00_tokens`).
- **Limits:**
  - YouTube: 100 uploads and 100 searches a day per project.
  - Pixabay: 100 requests a minute; searches are paced.
  - GitHub Actions: the repo was made **public** on 2026-10-21 (unlimited minutes; `github_repo_public: true` in
    BRAIN.md). Run logs are public, so never print secrets. YouTube searches: ~30 a day per channel, so the 100/day
    search limit caps the project at about 3 channels on this schedule.

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
- **When the owner sends a YouTube link about getting views:** analyse it and record it in
  `youtube-automation-agents/execution/view_techniques.md`, following entry S1 as the model.
  1. Run `python execution/execution.py --learn <link>` (laptop; it uses 1 Gemini request and adds a "proposed"
     entry). If Gemini or subtitles fail, read the metadata with the YouTube API and the subtitles with yt-dlp
     (`--skip-download --write-auto-subs --sub-langs "en.*"`) and write the entry yourself.
  2. Review every proposal critically: keep only techniques the video gives evidence for, reject anything that fakes
     engagement, misleads viewers or reuses others' content, and check it against what the pipeline already does.
  3. Adopt: short writing rules go into ACTIVE RULES (sent to Gemini with every request, so keep them brief and
     tagged [S<n>]); code-level techniques go into the code and the "HOW THE PIPELINE APPLIES THEM" table; owner
     tasks go under "Not automated yet". Mark the entry "reviewed" with a → verdict per technique.
  4. Then the usual checks (--check, setup_project.py, 3.11 syntax, tests) and tell the owner what changed.

## Status and open items (as of 2026-10-21)
- **Shipped (commit f0f8d7e on `main`, branch `three-daily-runs-telegram-hashtags`):** three runs a day for both
  channels, the cron lead, Telegram start and per-channel messages, merged Gemini calls, title fallback and hashtags.
- **Run history:**
  - 18 to 20 Oct: scheduled runs succeeded, starting 4h22m to 5h28m after their cron times.
  - 20 Oct (kids) and 21 Oct (Frontline): failed with 0 uploaded. Gemini returned 429 on every model mid-run, and
    titles couldn't be written (the kids run had already uploaded its 2 long videos).
  - 21 Oct 14:56 IST, manual run 39576955716 on f0f8d7e: success, 5 of 6 uploaded in 13 min. Passed slots went live
    about 15 min after upload; the title fallback was used once (Gemini still 429). One kids Short failed with
    YouTube's "exceeded the number of videos they may upload" (the channel's own daily limit).
  - First scheduled run on the new schedule: the 06:00 run on 22 Oct (cron 20:05 UTC = 01:35 IST). Not yet checked.
- **Open items:**
  - Ask the owner for the next days' Telegram messages or Actions results: check that runs start near
    06:00/13:00/17:00 (tune `github_schedule_lead_minutes` if not) and that all 16 videos upload.
  - YouTube daily upload limit: the owner was advised to phone-verify both channels at youtube.com/verify.
  - Leftover finished videos from the failed runs are uploaded first by the next runs (Frontline had 1 long + 4
    Shorts waiting). A test Short made on the laptop is "edited" in the local manifest and will upload on a local run.
  - The owner's Gmail is public as the author of every commit; they were told about GitHub's "Keep my email addresses
    private" setting. History was not rewritten.
- **Optional clean-up:** some early test videos on YouTube are unlisted (a few have old audio or the 11-minute kids
  video); the owner was advised to delete or publish them in Studio.
- **Possible future work:** more channels (add to both YAML files and run `--authorize`; about 3 channels fit the
  YouTube search limit on this schedule), and tuning scene-matching quality from sync reports.
