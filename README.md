# PersonalProjects-TT83

A collection of my own personal projects. Each project lives in its own folder with its own dependencies and configuration.

| Project | Description |
|---|---|
| [youtube-automation-agents](youtube-automation-agents/) | A multi-agent Python pipeline that runs faceless YouTube channels: finds rising topics, writes original scripts with Gemini, narrates them with an expressive AI voice, matches real or cartoon footage to every scene, edits, uploads on a schedule, reports on Telegram and learns which topics get views. Runs on GitHub Actions three times a day through a single execution file. |

--## YouTube Automation Agents

### Overview

This project runs faceless YouTube channels end to end. Eight specialised agents run in sequence for each channel,
handing work to each other through a shared JSON manifest, three times a day on GitHub Actions.

Everything is driven by three pages and one file:
- **[execution/channels.yaml](youtube-automation-agents/execution/channels.yaml):** who each channel is. You create channels on YouTube yourself and list them here.
- **[execution/questions.yaml](youtube-automation-agents/execution/questions.yaml):** what and when each channel posts (genre, searches, voice, schedule, long videos, footage style), with the allowed options written beside each answer.
- **[execution/view_techniques.md](youtube-automation-agents/execution/view_techniques.md):** what gets views. Its ACTIVE RULES are sent to the AI with every video; it also logs every video about growth that was analysed (`--learn`).
- **[execution/execution.py](youtube-automation-agents/execution/execution.py):** the one file that runs everything, from GitHub, an IDE, a terminal or Copilot.

**Start with [execution/execution.md](youtube-automation-agents/execution/execution.md)**: it has setup, rules and every command.

| Channel key | Channel | Audience | Voice | Daily (IST) |
|---|---|---|---|---|
| `geopolitics_war` | Frontline Ledger TT83 | General | `en-US-BrianMultilingualNeural`, measured | 6 Shorts and 2 long videos (5 to 7 min) with real footage of the people and events |
| `kids_edu` | WonderSprout Kids TT83 | Children (Made for Kids) | `en-US-AvaMultilingualNeural`, lively | 2 Shorts and 3 long videos (4 to 6 min), hosted by the WonderSprout superhero |

Runs start at 06:00, 13:00 and 17:00 IST; each run makes the videos due before the next run and schedules them on
YouTube. Narration supports English, Hindi and Telugu.

### How It Works

```
  channels.yaml (who) + questions.yaml (what & when) + BRAIN.md (global rules) + view_techniques.md (what gets views)
                                       │
                                       ▼
                 execution/execution.py  →  orchestrator.py   (Telegram: "run started")
                                       │
  ┌─────────┬──────────┬──────────┬────┴─────┬──────────┬──────────┬──────────┬──────────┐
  ▼         ▼          ▼          ▼          ▼          ▼          ▼          ▼
Agent 1   Agent 3    Agent 2    Agent 4    Agent 5    Agent 6    Agent 7    Agent 8
Scout ──▶ Script & ▶ Footage ─▶ Editor ──▶ Publisher ▶ Cleanup ─▶ Telegram ▶ Analyst
          Voice                                                   (per channel) (daily report)
  │         │          │          │          │          │          │          │
  └─────────┴──────────┴──────── trends.json manifest ─┴──────────┴──────────┘
```

Every video moves through `discovered` → `voiced` → `ready_to_edit` → `edited` → `published` (also `expired`,
`download_failed`). Each agent only picks up items in the status it expects, so an interrupted run continues where it
left off, and finished videos that missed their run are uploaded by the next one.

### The Agents

| # | Agent | File | What it does |
|---|---|---|---|
| 1 | **Trend Scout** | [agent_1_scout.py](youtube-automation-agents/agents/agent_1_scout.py) | Searches YouTube for trending videos on the channel's phrases (best-performing phrases first, only as many searches as needed), ranks them by views per hour, skips other-language sources, and notes the niche's trending hashtags. Only the topic is used. |
| 3 | **Script & Voice** | [agent_3_voiceover.py](youtube-automation-agents/agents/agent_3_voiceover.py), [expressive_voice.py](youtube-automation-agents/agents/expressive_voice.py) | Gemini (Groq as a free backup) writes an original script plus title, description, hashtags and footage terms in one request. `edge-tts` narrates it sentence by sentence with varied pitch, speed and volume, and the timings split it into scenes. |
| 2 | **Footage** | [agent_2_downloader.py](youtube-automation-agents/agents/agent_2_downloader.py), [stock_footage.py](youtube-automation-agents/agents/stock_footage.py) | One clip per scene. Frontline: real video, then a real photo, of the named person or event from Wikimedia Commons (free licences only), else Pixabay. WonderSprout: a Pixabay cartoon or real clip whose tags match the scene's subject, else a matching cartoon illustration. |
| 4 | **Editor** | [agent_4_editor.py](youtube-automation-agents/agents/agent_4_editor.py), [mascot.py](youtube-automation-agents/agents/mascot.py) | FFmpeg cuts each clip to its scene, adds the narration, watermark and LIKE / SUBSCRIBE end card. On WonderSprout it adds the superhero host in a corner (expressions follow the script), her intro and her outro. |
| 5 | **Publisher** | [agent_5_publisher.py](youtube-automation-agents/agents/agent_5_publisher.py) | Confirms the login is the right channel, adds up to 12 hashtags and the footage credits, and uploads in resumable chunks scheduled to go live at the channel's times. |
| 6 | **Cleanup** | [agent_6_cleanup.py](youtube-automation-agents/agents/agent_6_cleanup.py) | Deletes files no longer needed. |
| 7 | **Telegram** | [agent_7_notifier.py](youtube-automation-agents/agents/agent_7_notifier.py) | One message per channel: each video's title, link, go-live time and hashtags. |
| 8 | **Analyst** | [agent_8_analyst.py](youtube-automation-agents/agents/agent_8_analyst.py) | Records every upload's views, scores search phrases by views after 48 hours (the Scout uses the best first), and posts a daily report. |

### Running

| Where | How |
|---|---|
| GitHub (automatic) | Three runs a day from the schedule in `questions.yaml`. |
| GitHub (manual) | Actions → **Daily YouTube Multi-Agent Pipeline** → **Run workflow**. |
| Any IDE, terminal or Copilot | `python execution/execution.py` (all channels), `--channel KEY`, `--dry-run`, `--check`, `--learn LINK`, `--hero-art FOLDER` and more: see [execution.md](youtube-automation-agents/execution/execution.md). |

### Project Structure

```
youtube-automation-agents/
├── execution/
│   ├── execution.py         # THE file you run (IDE, terminal or GitHub)
│   ├── execution.md         # Setup, rules and every command
│   ├── questions.yaml       # What and when each channel posts
│   ├── channels.yaml        # Who each channel is
│   └── view_techniques.md   # What gets views (rules sent to the AI, analysed sources)
├── assets/wondersprout/     # The WonderSprout character (9 poses) and her intro / outro videos
├── BRAIN.md                 # Global rules and AI agent instructions
├── orchestrator.py          # Runs the agents for one channel
├── brain.py                 # Loads the config and works out schedules
├── agents/                  # One file per agent, plus ai.py, expressive_voice.py, stock_footage.py, mascot.py, learner.py
├── setup_project.py         # Recreates every text file of the project
├── requirements.txt         # Python dependencies
└── .env.example             # Template for API keys
```

The workflow lives at the repository root in [.github/workflows/daily_youtube_agents.yml](.github/workflows/daily_youtube_agents.yml).
GitHub runs keep their state (logins, manifest, finished videos) in Google Drive `YouTube_Automation/` through rclone;
laptop runs write videos to `H:/My Drive/YouTube_Auto/<date>/`.

### Tech Stack

- **Python 3.11** (GitHub) / 3.14 (laptop)
- **YouTube Data API v3**: trends, uploads, view counts
- **Google Gemini** (`google-genai`, 3.x flash models) with **Groq** (`gpt-oss-120b`) as a free backup
- **edge-tts**: Microsoft neural voices, narrated sentence by sentence
- **Wikimedia Commons** (real footage and photos) and **Pixabay** (stock clips and cartoon illustrations)
- **FFmpeg** and **Pillow**: editing, the WonderSprout host, intro and outro
- **Telegram Bot API**, **GitHub Actions**, **rclone** + **Google Drive**

### Getting Started

Follow **One-Time Setup** in [execution/execution.md](youtube-automation-agents/execution/execution.md). In short:

1. **Python environment:** Python 3.11+ and `pip install -r requirements.txt` (on Windows, a short path such as `C:\Users\<you>\venvs\yt`).
2. **API keys:** copy `.env.example` to `.env` and add the YouTube, Gemini, Pixabay and (optional) Groq and Telegram keys.
3. **Upload permission:** save the Google OAuth Desktop client as `client_secret.json` and publish the consent screen.
4. **YouTube logins:** `python execution/execution.py --authorize <channel_key>` per channel, then upload the token to Google Drive `YouTube_Automation/00_tokens/`.
5. **GitHub secrets:** `YOUTUBE_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `PIXABAY_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `CLIENT_SECRET_JSON`, `RCLONE_CONFIG_GDRIVE`.
6. **Check and test:** `python execution/execution.py --check`, then `--channel <key> --dry-run`.

### Regenerating the Project

`setup_project.py` contains every text file as embedded strings. Running it from inside `youtube-automation-agents/`
recreates them (and the workflow at the repository root). Binary files (the WonderSprout pictures and videos in
`assets/`) aren't embedded: copy that folder too.

> It overwrites existing files with the embedded versions, so any local edits will be lost.

### Responsible Use

- **No reused videos:** trending videos supply only the topic. Footage is free stock (Pixabay) or freely licensed real
  footage and photos (Wikimedia Commons: public domain, CC0 and CC BY only), credited in every description.
  Copyrighted news-agency footage is never used.
- **Disclosure:** the synthetic-media flag is on for every upload (AI voice and AI-made host).
- **Kids:** WonderSprout is marked Made for Kids (COPPA and stricter YouTube rules); stories are original, never
  copyrighted characters, and footage searches use safe search.

### Security

`.env`, `client_secret*.json` and `token_*.json` are in `.gitignore`; never commit them. The repository is public:
GitHub secrets stay hidden and are masked in logs. If a key is ever exposed, revoke and regenerate it immediately.
