# Execution Guide

Everything you need to run the YouTube automation lives in this folder:

| File | Purpose |
|---|---|
| `execution.py` | **The only file you run.** Works from GitHub, any IDE, a terminal, or Copilot. |
| `execution.md` | This guide: setup, rules and how to run. |
| `questions.yaml` | **The answers.** What and when each channel posts: genre, language, voice, how many videos, how often. Options are written beside each question. |
| `channels.yaml` | **The channel page.** Who each channel is: name, made for kids, watermark, category. |

`BRAIN.md` (one folder up) holds the global rules every channel shares: editing style, storage folders and AI writing instructions. You rarely need to touch it.

---

## Golden Rules

1. **Create each YouTube channel yourself**, then list it in `channels.yaml` and answer its questions in `questions.yaml` under the **same key**.
2. **`execution.py` always reads `questions.yaml`.** To change what a channel does, change the answers there, not the code.
3. **Copy options exactly** from the comments: quotes, spaces and capital letters matter (`"1 hour"`, `"Telugu"`, `"-5%"`).
4. **After any change to either file, run** `python execution/execution.py --check`, **then push to GitHub.** The check validates both files, shows each channel's posting plan and daily YouTube usage, and updates the GitHub schedule.
5. **Every channel needs a YouTube login** (`token_<key>.json`) before it can upload. See *Adding a Channel*, step 4. Sign in with **the Google account that owns the channel**. This can be a different account from the one that owns the Google Cloud project; if the OAuth consent screen is still in Testing mode, add that account under **Test users**.
6. **Test first:** set `test_run: true` for a new channel. It makes the videos without uploading them.
7. **Stay within YouTube's daily limits:** 100 uploads and 100 searches per day, shared by all channels. `--check` shows your total.
8. **Never commit secrets.** `.env`, `client_secret.json` and `token_*.json` are git-ignored.

---

## How Posting Works

For each channel, every day it is scheduled (`days`):

1. **At `fetch_time`**, GitHub starts the pipeline. It finds trending Shorts for your `genre` and `search_phrases`, writes a script in your `language`, narrates it with your `voice` at your `voice_speed`, and edits each video.
2. **It uploads all `videos_per_day` videos at once**, each with its own go-live time: the first at `first_post_time`, then one every `post_frequency`.
3. **YouTube publishes each video exactly at its time**, even though GitHub has already finished.

Example: `fetch_time "06:00"`, `first_post_time "07:00"`, `post_frequency "1 hour"`, `videos_per_day 12` → videos go live at 07:00, 08:00, 09:00 … 18:00.

| `post_frequency` | 12 videos starting 07:00 go live |
|---|---|
| `"15 mins"` | 07:00, 07:15 … 09:45 |
| `"30 mins"` | 07:00, 07:30 … 12:30 |
| `"1 hour"` | 07:00, 08:00 … 18:00 |
| `"3 hours"` | 07:00, 10:00 … 16:00 the next day (too long, so `--check` warns you) |

**Every video looks and sounds the same way:**
- **Audio:** only the AI narration; the source video's own sound is removed.
- **Watermark:** in the bottom-right corner.
- **End card:** a 3-second closing card with the channel name in large text, a **LIKE** button first, then **SUBSCRIBE**.
  - Change the card's length or wording, or turn it off, under `end_card` in `BRAIN.md`.
  - To also show a logo, set `show_logo: true` there. The logo is the channel's YouTube profile picture, saved in `GDrive_Mount/YouTube_Automation/00_branding/`, or the image set as `logo_file` in `channels.yaml`.

If preparing the videos takes longer than planned, the next free slot is used automatically. Allow at least 30 minutes between `fetch_time` and `first_post_time` (about 2 to 3 minutes per video).

---

## The Questions (`questions.yaml`)

| # | Question | Field | Options |
|---|---|---|---|
| Q1 | Channel to post to | the key (e.g. `kids_edu`) | Any key from `channels.yaml` |
| Q2 | Include this channel? | `run` | `true` \| `false` |
| Q3 | Genre to source trending videos for | `genre` | Any text |
| Q4 | YouTube searches that find trending Shorts | `search_phrases` | 1 to 5 lines; add `#shorts` to each |
| Q5 | Narration language | `language` | `"English"` \| `"Hindi"` \| `"Telugu"` |
| Q6 | Narrator voice | `voice` | See voices below; must match the language |
| Q7 | Speaking speed | `voice_speed` | `"-15%"` slow and very clear \| `"-5%"` clear (recommended) \| `"+0%"` natural \| `"+10%"` brisk |
| Q8 | Narration style | `tone` | Any text |
| Q9 | Length of each video | `video_length_seconds` | `15` \| `30` \| `45` \| `60` |
| Q10 | Words the script must never use | `avoid_words` | `[]` or `["word1", "word2"]` |
| Q11 | Use trending videos from the last N days | `lookback_days` | `1` to `30` |
| Q12 | When to fetch and prepare videos | `fetch_time` | `"HH:MM"` (24-hour) |
| Q13 | When the first video goes live | `first_post_time` | `"HH:MM"` |
| Q14 | Gap between videos | `post_frequency` | `"15 mins"` \| `"30 mins"` \| `"1 hour"` \| `"3 hours"` |
| Q15 | Videos to post each day | `videos_per_day` | `1` to `50` |
| Q16 | Which days | `days` | `"daily"` \| `"weekdays"` \| `"weekends"` \| `[mon, wed, fri]` |
| Q17 | Timezone for all times | `timezone` | `"Asia/Kolkata"`, `"America/New_York"`, `"Europe/London"`, … |
| Q18 | Who can see the videos | `visibility` | `"public"` \| `"unlisted"` \| `"private"` |
| Q19 | Make videos without uploading | `test_run` | `true` \| `false` |
| Q20 | YouTube channels never to take videos from | `exclude_sources` | `[]` or `["Channel Name 1", "Channel Name 2"]` |

**How source videos are picked:** each run starts from the next search phrase in turn and takes the most-viewed recent Short from each phrase in rotation. Searches favour videos in the channel's language. It takes **at most one video per source channel per run**, avoids channels used for any of that channel's last 5 videos, and never uses channels in `exclude_sources`. This keeps one big channel from supplying everything.

Delete a line to use its default. Defaults: English, a natural voice for the language, `-5%` speed (`-15%` for kids), 30-second videos, last 5 days, 1 video, `"1 hour"`, daily, `Asia/Kolkata`, public, uploads on. If `first_post_time` is left out, videos go live as soon as they are uploaded.

**Voices:**

| Language | Voice | Style |
|---|---|---|
| English | `en-US-AndrewNeural` | Male, warm, all-rounder |
| English | `en-US-ChristopherNeural` | Male, authoritative, news |
| English | `en-US-GuyNeural` | Male, energetic, sports |
| English | `en-US-AriaNeural` | Female, confident, news |
| English | `en-US-EmmaNeural` | Female, cheerful, kids and education |
| English | `en-US-AnaNeural` | **Child voice** (young girl); best for kids channels |
| English | `en-US-JennyNeural` | Female, friendly, calm |
| English | `en-IN-PrabhatNeural` / `en-IN-NeerjaNeural` | Indian English, male / female |
| Hindi | `hi-IN-MadhurNeural` / `hi-IN-SwaraNeural` | Male / female |
| Telugu | `te-IN-MohanNeural` / `te-IN-ShrutiNeural` | Male / female |

For Hindi and Telugu, scripts, titles and descriptions are written in the native script. Write `search_phrases` in whichever language finds the best trending videos for your genre.

---

## The Channel Page (`channels.yaml`)

| Field | Required | Options |
|---|---|---|
| *key* | yes | Lowercase letters, numbers, underscores; starts with a letter; unique |
| `channel_name` | yes | Exactly as on YouTube |
| `channel_url` | yes | The channel link: `https://www.youtube.com/@handle` or `https://www.youtube.com/channel/UC...`. Sign-in and every upload check that the YouTube login is this exact channel, so videos never go to the wrong one. |
| `made_for_kids` | yes | `true` \| `false` (`true` only for children under 13: legal COPPA setting, limits ads) |
| `watermark_text` | no | Default `@` + channel name |
| `logo_file` | no | Image for the end card, e.g. `"execution/logos/my_logo.png"`. Default: the channel's YouTube profile picture |
| `category_id` | no | `"24"` Entertainment (default), `"25"` News & Politics, `"27"` Education (default for kids), `"17"` Sports, `"28"` Science & Technology. The full list is in the file. |

---

## One-Time Setup

| # | Step | How |
|---|---|---|
| 1 | Python environment | `python -m venv C:\Users\<you>\venvs\yt`, then `C:\Users\<you>\venvs\yt\Scripts\python.exe -m pip install -r requirements.txt`. A short path avoids Windows' long-path error, and no admin rights are needed. |
| 2 | API keys | Copy `.env.example` to `.env` and fill in `YOUTUBE_API_KEY`, `GEMINI_API_KEY`, and optionally the Telegram values. |
| 3 | Upload permission | Save your Google Cloud **OAuth Desktop client** file as `client_secret.json` in `youtube-automation-agents`. On the OAuth consent screen click **Publish app**; otherwise logins expire every 7 days. |
| 4 | Google Drive | Create the folder `YouTube_Automation/00_tokens/`. |
| 5 | rclone | `rclone config` and create a remote named exactly **`gdrive`** (type: drive). |
| 6 | GitHub secrets | Repo → Settings → Secrets and variables → Actions. Add the 6 secrets below. |

| Secret | Contents |
|---|---|
| `YOUTUBE_API_KEY` | YouTube Data API v3 key |
| `GEMINI_API_KEY` | Gemini API key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHANNEL_ID` | Telegram channel ID (starts with `-100`) |
| `CLIENT_SECRET_JSON` | Full contents of `client_secret.json` |
| `RCLONE_CONFIG_GDRIVE` | Full contents of the file shown by `rclone config file` |

---

## Adding a Channel

1. **Create the channel on YouTube:** [youtube.com/channel_switcher](https://www.youtube.com/channel_switcher) → **Create a channel**.
2. **`channels.yaml`:** copy the TEMPLATE, paste it under `channels:`, remove the `# `, and fill in the 5 lines.
3. **`questions.yaml`:** copy the TEMPLATE, paste it under `channels:` with the **same key**, remove the `# `, and answer each question by copying an option from its comment.
4. **Sign in to YouTube:** `python execution/execution.py --authorize <key>`. Upload the token file it prints to Google Drive `YouTube_Automation/00_tokens/`.
5. **Check:** `python execution/execution.py --check`
6. **Test:** `python execution/execution.py --channel <key> --dry-run`
7. **Push to GitHub** so the schedule goes live: `git add -A`, `git commit -m "Add <key>"`, `git push`.

To pause a channel, set `run: false`. To remove one, delete it from both files. Then run `--check` and push.

---

## Ways to Run

### GitHub (automatic)

Nothing to do. Each channel runs at its `fetch_time` on its `days`.

### GitHub (manual)

GitHub → **Actions** → **Daily YouTube Multi-Agent Pipeline** → **Run workflow**. Leave any field blank to use `questions.yaml`.

| Field | Meaning |
|---|---|
| `channel` | `all` (every channel with `run: true`) or one channel key |
| `genre` | One-off genre |
| `videos` | One-off number of videos |
| `first_post_time` | One-off first go-live time (`HH:MM`) |
| `frequency` | One-off gap: 15 mins, 30 mins, 1 hour or 3 hours |
| `dry_run` | Tick to make videos without uploading |

### IDE, terminal or Copilot

Open `execution.py` and press **Run**, or run this from the `youtube-automation-agents` folder:

```bash
C:\Users\<you>\venvs\yt\Scripts\python.exe execution/execution.py
```

It runs **every channel with `run: true`** using its answers in `questions.yaml`. To have Copilot (or any assistant) run it, say:

> Run `python execution/execution.py` from the `youtube-automation-agents` folder using `C:\Users\<you>\venvs\yt\Scripts\python.exe`.

### Interactive one-off (`--ask`)

`python execution/execution.py --ask` asks the questions on screen, starting from your `questions.yaml` answers, and runs once. `questions.yaml` itself is not changed.

---

## Command Reference

| Command | What it does |
|---|---|
| `python execution/execution.py` | Run every channel with `run: true`, using `questions.yaml` |
| `... --channel KEY` | Run one channel |
| `... --channel all` | Same as no arguments |
| `... --genre "cricket highlights"` | One-off genre (search becomes `"<genre> #shorts"`) |
| `... --videos 6` | One-off number of videos |
| `... --first-post "09:00"` | One-off first go-live time |
| `... --frequency "30 mins"` | One-off gap: `"15 mins"`, `"30 mins"`, `"1 hour"` or `"3 hours"` |
| `... --post-now` | One-off: every video goes live as soon as it is uploaded |
| `... --visibility unlisted` | One-off visibility: `public`, `unlisted` or `private` |
| `... --dry-run` | Make videos, don't upload |
| `python execution/execution.py --ask` | Answer the questions on screen for a one-off run |
| `python execution/execution.py --check` | Validate both files, show plans and YouTube usage, update the GitHub schedule |
| `python execution/execution.py --list` | Show channels, plans and YouTube login status |
| `python execution/execution.py --authorize KEY` | Sign in to YouTube for a channel |

---

## Where Video Files Are Kept

| Run from | Downloaded videos | Narration and final (uploaded) videos | Deleted |
|---|---|---|---|
| Your laptop / IDE / Copilot | `H:\My Drive\YouTube_Auto\<YYYY-MM-DD>\download\` (Google Drive) | `H:\My Drive\YouTube_Auto\<YYYY-MM-DD>\upload\` | Whole day folders older than 2 days, automatically at the end of each run |
| GitHub | Temporary folders on GitHub's machine | Temporary folders on GitHub's machine | Right after upload; the machine is wiped after every run |

Change the folder or the number of days under `local_media` in `BRAIN.md` (`root`, `keep_days`). If Google Drive for Desktop isn't running (no `H:` drive), that run uses `C:\Users\<you>\YouTube_Auto` instead. Files in the Google Drive folder count toward your Drive storage, so the 2-day limit also keeps Drive from filling up. To clear space by hand, delete any day folder you no longer need; videos from that day that weren't uploaded yet are skipped automatically.

The manifest, YouTube logins and logos always stay in `GDrive_Mount/YouTube_Automation/`, because GitHub runs share them through Google Drive.

## Good to Know

- **GitHub may start a run 5 to 30 minutes after `fetch_time`.** Go-live times are still exact, because YouTube publishes them.
- **The workflow file stores times in UTC**, so 06:00 IST appears as `30 0 * * *`. `--check` handles this.
- **Daylight saving:** India has none. For timezones that do, run `--check` and push after each clock change.
- **GitHub free minutes:** private repos get 2,000 a month. Preparing each video takes about 2 to 3 minutes, so 12 videos a day uses roughly 900 to 1,100 minutes a month. Making the repository public removes the limit.
- **Test-run videos are kept** at "edited" and are uploaded by that channel's next real run.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Please fix these problems" | Each line names the file, channel and field. Copy the correct option from the comment beside it. |
| "No YouTube login file" | `--authorize <key>`, then upload the token to Drive `YouTube_Automation/00_tokens/`. |
| "You signed in to '...' but '<key>' is ..." | You picked a different channel in the browser. Run `--authorize <key>` again and choose the channel matching `channel_url`. |
| "Access blocked" / "app has not completed verification" when signing in | The account that owns the channel isn't a test user. Google Cloud Console → OAuth consent screen → add it under **Test users**, or click **Publish app**. |
| "login is missing or expired" on GitHub | Same as above. If it happens weekly, publish the OAuth consent screen (One-Time Setup, step 3). |
| "Cannot start: No .env file" | Copy `.env.example` to `.env` and fill in the keys. |
| `--check` warns about YouTube usage | Lower `videos_per_day` or the number of `search_phrases`, or request more quota in Google Cloud Console. |
| `pip` says "filename too long" | Use a short-path venv (One-Time Setup, step 1). |
| Narration fails | The free voice service occasionally changes. Run `pip install -U edge-tts`. Videos stay queued and retry on the next run. |
