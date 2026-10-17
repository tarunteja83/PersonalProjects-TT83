# PersonalProjects-TT83

A collection of my own personal projects. Each project lives in its own folder with its own dependencies and configuration.

| Project | Description |
|---|---|
| [youtube-automation-agents](youtube-automation-agents/) | A multi-agent Python pipeline that discovers trending YouTube Shorts, writes original commentary with Gemini, narrates it with a natural-sounding AI voice, edits the video, uploads it to YouTube and reports the result on Telegram. Supports any number of channels, each on its own posting schedule. Runs from any IDE or from GitHub Actions through a single execution file. |

---

## YouTube Automation Agents

### Overview

This project automates the full lifecycle of faceless YouTube Shorts channels. Seven specialised agents run in sequence, each doing one job and handing off to the next through a shared JSON manifest.

Everything is driven by two pages and one file:
- **[execution/channels.yaml](youtube-automation-agents/execution/channels.yaml):** who each channel is. You create channels on YouTube yourself and list them here.
- **[execution/questions.yaml](youtube-automation-agents/execution/questions.yaml):** what and when each channel posts. It holds the answers to 19 questions per channel, with the allowed options written beside each one.
- **[execution/execution.py](youtube-automation-agents/execution/execution.py):** the one file that runs everything. It reads the answers every time, whether started by GitHub, an IDE, a terminal or Copilot.

**Start with [execution/execution.md](youtube-automation-agents/execution/execution.md)**: it has setup, rules and every command.

Each day, at a channel's fetch time, the pipeline prepares that day's videos and uploads them with staggered go-live times. YouTube then publishes one every 15 mins, 30 mins, 1 hour or 3 hours. Narration supports English, Hindi and Telugu.

| Channel key | Channel | Audience | Language and voice | Posting (IST) |
|---|---|---|---|---|
| `geopolitics_war` | Frontline Ledger | General | English, `en-US-ChristopherNeural` | Fetch 06:00, 12 videos hourly from 07:00 |
| `kids_edu` | WonderSprout | Children aged 6 to 10 (marked Made for Kids) | English, `en-US-EmmaNeural` | Fetch 15:00, 4 videos hourly from 16:00 |

### How It Works

```
  channels.yaml (who)  +  questions.yaml (what & when)  +  BRAIN.md (global rules)
                                   │
                                   ▼
             execution/execution.py  →  orchestrator.py
                                   │
  ┌──────────┬──────────┬──────────┼──────────┬──────────┬──────────┐
  ▼          ▼          ▼          ▼          ▼          ▼          ▼
Agent 1   Agent 2    Agent 3    Agent 4    Agent 5    Agent 6    Agent 7
Scout ──▶ Download ─▶ Script & ─▶ Editor ──▶ Publisher ▶ Cleanup ─▶ Telegram
                      Voiceover                                     Notifier
  │          │          │          │          │          │          │
  └──────────┴──────────┴──── trends.json manifest ──────┴──────────┘
```

Every video tracked in the manifest moves through these statuses:

`discovered` → `downloaded` → `voiced` → `edited` → `published`

(`download_failed` is recorded if a download fails.) Because each agent only picks up items in the status it expects, a failed or interrupted run can safely be re-run and will continue where it left off.

### The Agents

| # | Agent | File | What it does |
|---|---|---|---|
| 1 | **Trend Scout** | [agent_1_scout.py](youtube-automation-agents/agents/agent_1_scout.py) | Uses the YouTube Data API v3 to search the channel's search phrases, in its language, for the most-viewed Shorts from the last `lookback_days`. Rotates across phrases, takes at most one video per source channel, avoids recently used sources and any in `exclude_sources`, and adds up to `videos_per_day` new entries. |
| 2 | **Footage** | [agent_2_downloader.py](youtube-automation-agents/agents/agent_2_downloader.py) | Downloads free stock clips from Pixabay (or Pexels) that match each topic, using search terms picked by Gemini ([stock_footage.py](youtube-automation-agents/agents/stock_footage.py)). Optionally re-uses the trending video itself with `yt-dlp`, but only from a laptop, because YouTube blocks downloads from cloud servers. |
| 3 | **Script & Voiceover** | [agent_3_voiceover.py](youtube-automation-agents/agents/agent_3_voiceover.py) | Asks Gemini 2.5 Flash to write an original commentary script sized to the channel's target video length, in its language and tone, avoiding any banned terms. Narrates it with the channel's Microsoft neural voice and speed via `edge-tts` (free, no API key). |
| 4 | **Video Editor** | [agent_4_editor.py](youtube-automation-agents/agents/agent_4_editor.py) | Uses MoviePy to trim or loop the clip to match the voiceover, apply a slight crop zoom, replace the original audio with the AI narration (set by `original_audio_volume` in BRAIN.md, currently 0), overlay the channel watermark, and add a 3-second end card (channel name, LIKE first, then SUBSCRIBE). Graphics are drawn with Pillow, so ImageMagick isn't needed. |
| 5 | **Publisher** | [agent_5_publisher.py](youtube-automation-agents/agents/agent_5_publisher.py) | Generates an SEO title, description and hashtags with Gemini, adds a plain-text credit to the original creator, and uploads to the correct YouTube channel via OAuth. Sets visibility, Made for Kids and synthetic-media disclosure, and schedules each video's go-live time on YouTube according to `first_post_time` and `post_frequency`. |
| 6 | **Cleanup** | [agent_6_cleanup.py](youtube-automation-agents/agents/agent_6_cleanup.py) | Deletes raw downloads and voiceovers after publishing, and removes edited videos from previous days to keep storage small. |
| 7 | **Telegram Notifier** | [agent_7_notifier.py](youtube-automation-agents/agents/agent_7_notifier.py) | Posts a summary to a Telegram channel with links to the newly published Shorts, or a "nothing new" status if no video was published. |

### Configuration

- **[execution/channels.yaml](youtube-automation-agents/execution/channels.yaml), the channel page:** channel name, made for kids, watermark and category.
- **[execution/questions.yaml](youtube-automation-agents/execution/questions.yaml), the answers:**
  - **Content:** genre, search phrases, tone, video length, banned words, how recent the source videos must be.
  - **Voice:** language (English, Hindi or Telugu), voice and speed.
  - **Schedule:** fetch time, first go-live time, frequency (15 mins, 30 mins, 1 hour or 3 hours), videos per day, days, timezone.
  - **Publishing:** visibility and test run.

  Options are written beside each question so you can copy and paste them.
- **[BRAIN.md](youtube-automation-agents/BRAIN.md), global rules:** storage folders, editing style, upload and cleanup rules, plus an `## AGENT INSTRUCTIONS` section in plain English that is passed directly into the Gemini prompts.

### Running

| Where | How |
|---|---|
| GitHub (automatic) | Each channel runs at its `fetch_time` from `questions.yaml`. |
| GitHub (manual) | Actions → **Daily YouTube Multi-Agent Pipeline** → **Run workflow**. Blank fields use `questions.yaml`. |
| Any IDE, terminal or Copilot | Run `execution/execution.py`. It runs every channel with `run: true` using `questions.yaml`. |
| Interactive one-off | `execution/execution.py --ask` asks the questions on screen for one run. |

Full instructions and rules: [execution/execution.md](youtube-automation-agents/execution/execution.md).

### Project Structure

```
youtube-automation-agents/
├── execution/
│   ├── execution.py         # THE file you run (IDE, terminal or GitHub)
│   ├── execution.md         # Setup, rules and every command
│   ├── questions.yaml       # The answers: what and when each channel posts
│   └── channels.yaml        # The channel page: who each channel is
├── BRAIN.md                 # Global rules and AI agent instructions
├── orchestrator.py          # Runs agents 1 to 7 for one channel
├── brain.py                 # Loads the config and works out posting schedules
├── agents/                  # One file per agent
├── setup_project.py         # Generator script that recreates the whole project
├── requirements.txt         # Python dependencies
└── .env.example             # Template for API keys
```

The scheduled workflow lives at the repository root in [.github/workflows/daily_youtube_agents.yml](.github/workflows/daily_youtube_agents.yml), because GitHub only runs workflows from there. It runs every step inside `youtube-automation-agents/`.

When run on a laptop or IDE, video files go to the Google Drive folder `H:/My Drive/YouTube_Auto/<YYYY-MM-DD>/download` and `/upload`, and day folders older than 2 days are deleted automatically (`local_media` in BRAIN.md). Shared state and GitHub runs use `GDrive_Mount/YouTube_Automation/` (ignored by Git), mirrored to the same folder in Google Drive:

```
00_tokens/               # YouTube login per channel (token_<channel_key>.json)
01_manifests/            # trends.json, the shared state between agents
02_raw_videos/           # Downloaded source clips
02_audio_voiceovers/     # Generated narration (.mp3)
03_edited_videos/        # Final videos ready to upload
04_published_archive/    # Archive folder
```

### Tech Stack

- **Python 3.11+**
- **YouTube Data API v3**: trend discovery and uploads
- **Google Gemini 2.5 Flash** (`google-genai`): script and SEO generation
- **edge-tts**: Microsoft neural text-to-speech voices
- **yt-dlp**: video download
- **MoviePy 1.0.3**, **FFmpeg**, **Pillow**: video editing and watermarks
- **Telegram Bot API**: notifications
- **GitHub Actions** + **rclone** + **Google Drive**: scheduled cloud runs with persistent storage

### Getting Started

Follow **One-Time Setup** in [execution/execution.md](youtube-automation-agents/execution/execution.md). In short:

1. **Python environment:** create one with Python 3.11+ and run `pip install -r requirements.txt`. On Windows, use a short path such as `C:\Users\<you>\venvs\yt` to avoid the long-path error; no admin rights are needed.
2. **API keys:** copy `.env.example` to `.env` and add your YouTube and Gemini keys. Telegram is optional.
3. **Upload permission:** save your Google OAuth Desktop client as `client_secret.json`, and publish the consent screen so logins don't expire after 7 days.
4. **YouTube logins:** sign in once per channel with `python execution/execution.py --authorize <channel_key>`, then upload each token file to Google Drive `YouTube_Automation/00_tokens/`.
5. **GitHub secrets:** add the 7 secrets `YOUTUBE_API_KEY`, `GEMINI_API_KEY`, `PIXABAY_API_KEY` (free at pixabay.com/api/docs), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `CLIENT_SECRET_JSON` and `RCLONE_CONFIG_GDRIVE`.
6. **Check and test:** `python execution/execution.py --check`, then `python execution/execution.py --channel geopolitics_war --dry-run`.

### Regenerating the Project

`setup_project.py` contains the full source of every file as embedded strings. Running it from inside `youtube-automation-agents/` recreates the project from scratch, including the workflow at the repository root:

```bash
python setup_project.py
```

> This overwrites existing files with the embedded versions, so any local edits will be lost.

### Responsible Use

This tool republishes clips from other creators alongside new commentary. Before running it on a real channel:

- Make sure your use is genuinely transformative and complies with YouTube's policies on reused content and with copyright law in your country.
- Keep the creator credit in descriptions and the synthetic-media disclosure enabled.
- Review kids channel output carefully; content marked Made for Kids is subject to COPPA and stricter YouTube rules.

### Security

`.env`, `client_secret*.json` and `token_*.json` are listed in `.gitignore`. Never commit them. If a key is ever exposed, revoke and regenerate it immediately.
