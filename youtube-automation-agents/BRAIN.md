# MASTER BRAIN & MULTI-AGENT CONFIGURATION
Global rules shared by every channel. Modify the YAML block or the Agent Instructions below to change how all agents behave.
Channels are listed on the channel page: `execution/channels.yaml`. How to run everything: `execution/execution.md`.

```yaml
# Gemini models for scripts and titles, tried in order (a busy or retired model falls through to the next)
ai_models: ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-flash-latest"]
# Free backups used when every Gemini model is out of quota or busy, tried in order. A provider whose key isn't set
# (GROQ_API_KEY, OPENROUTER_API_KEY in .env and GitHub secrets) is skipped. Groq's free tier: about 1,000 requests a
# day per model. Add {provider: "openrouter", models: ["<model>:free"]} for a second backup.
ai_fallbacks:
  - provider: "groq"
    models: ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]

# GitHub starts scheduled runs late (4 to 5 hours in October 2026). The GitHub schedule fires this many minutes
# BEFORE each fetch_time, so runs actually start near fetch_time. 0 = no shift. The Telegram start message says how
# early or late each run started; adjust this if they drift.
github_schedule_lead_minutes: 265
# true when the GitHub repository is public (unlimited Actions minutes, so --check skips the minutes warning)
github_repo_public: true

storage_paths:
  base_dir: "./GDrive_Mount/YouTube_Automation"
  tokens_dir: "./GDrive_Mount/YouTube_Automation/00_tokens"
  branding_dir: "./GDrive_Mount/YouTube_Automation/00_branding"   # cached channel logos for the end card
  manifest_file: "./GDrive_Mount/YouTube_Automation/01_manifests/trends.json"
  raw_downloads: "./GDrive_Mount/YouTube_Automation/02_raw_videos"
  audio_renders: "./GDrive_Mount/YouTube_Automation/02_audio_voiceovers"
  processed_videos: "./GDrive_Mount/YouTube_Automation/03_edited_videos"
  archive_dir: "./GDrive_Mount/YouTube_Automation/04_published_archive"

# Runs on your laptop or IDE put video files here instead, one folder per day:
#   YouTube_Auto/<YYYY-MM-DD>/download  (source videos)  and  /upload  (narration + final videos)
# Day folders older than keep_days are deleted automatically. GitHub runs keep using storage_paths above.
local_media:
  enabled: true
  root: "H:/My Drive/YouTube_Auto"   # your Google Drive (Drive for Desktop); "~/YouTube_Auto" = your user folder instead
  keep_days: 2

# Where each video's pictures come from:
#   "stock"   = free stock clips matching the topic (works everywhere, including GitHub)
#   "youtube" = re-use the trending video itself (laptop only: YouTube blocks downloads from cloud servers)
footage:
  source: "stock"
  provider: "auto"        # "pixabay" | "pexels" | "auto" (whichever key is set: PIXABAY_API_KEY, then PEXELS_API_KEY)
  clips_per_video: 6            # scenes per Short (a new clip about every 5 to 6 seconds)
  long_scene_seconds: 8         # long videos change clip about this often (was 15: too slow to hold attention)

editing_rules:
  zoom_factor: 1.06
  original_audio_volume: 0      # 0 = only the AI voice is heard; 0.05 = original sound faintly underneath
  watermark_fontsize: 36
  watermark_color: "white"
  watermark_opacity: 0.75
  end_card:                     # closing card on every video: channel name, LIKE first, then SUBSCRIBE
    enabled: true
    seconds: 3
    show_logo: false            # true = also show the channel's logo above the name
    like_text: "LIKE"
    subscribe_text: "SUBSCRIBE"

upload_rules:
  privacy_status: "public" # Default when a channel has no privacy_status of its own
  synthetic_media_disclosure: true

cleanup_rules:                  # GitHub runs; laptop runs keep files in YouTube_Auto day folders instead
  delete_raw_after_publish: true
  delete_voiceover_after_publish: true
  delete_edited_after_publish: true
  delete_edited_from_previous_dates: true
```

## PIPELINE ARCHITECTURE
(Reference for people. This section is NOT sent to the AI; only the AGENT INSTRUCTIONS below are.)

### Where everything is decided
| File | Decides |
|---|---|
| `execution/channels.yaml` | Who each channel is: name, link, made for kids, watermark, category |
| `execution/questions.yaml` | What and when each channel posts: genre, searches, language, voice, Shorts schedule, long videos, footage style |
| `execution/view_techniques.md` | What gets views: its ACTIVE RULES are added to the AI instructions below for every video; it also logs every analysed source video (`--learn`) |
| `BRAIN.md` (this file) | Rules shared by all channels: AI models and the free backup AI (`ai_fallbacks`, Groq), storage, footage source, editing and end card, upload and cleanup, AI writing instructions |
| `.github/workflows/daily_youtube_agents.yml` | When GitHub runs (generated from each channel's `fetch_time` by `execution.py --check`, firing `github_schedule_lead_minutes` early because GitHub starts scheduled runs hours late) |
| `execution/execution.py` | The only entry point; reads the three files above and runs the agents |

### What one run does, in order
1. **Choose channels**: a scheduled run picks the channels whose `fetch_time` (one time or a list) matches; a manual run uses the channel typed in. With several fetch times a day, each run makes only the videos whose go-live times fall before the channel's next run.
2. **Clear lost work**: unfinished videos whose files are gone (an interrupted earlier run) are marked `expired`.
3. **Long videos first, then Shorts.** For each format:
   1. **Agent 1, Scout**: searches YouTube for trending videos on the channel's search phrases (Shorts topics from Shorts, long topics from 4 to 20 minute videos), ranks them by views per hour, skips other-language sources, uses the best-performing phrases first (from Agent 8) and rotates source channels.
   2. **Agent 3, Script & Voice**: Gemini (or, when every Gemini model is out of quota or busy, the backup AI: Groq) writes an original script for the topic, and in the same request the upload title, description, hashtags and footage terms (hook first; long videos get 3 to 5 parts and a recap). The voice narrates it sentence by sentence with varied pitch, speed and volume (`voice_expression`: lively for kids, measured for news; `agents/expressive_voice.py`), sentence timings are recorded, and if the narration runs longer than allowed the script is shortened and narrated again. The narration is split into scenes (about `clips_per_video` per Short, one every `long_scene_seconds` in long videos).
   3. **Agent 2, Footage**: a specific and a backup search term for each scene (for Shorts, written in the script request; for long videos, the AI reads each scene's words in a second request); with `footage_style: "real"` (Frontline) the specific term is first looked up as real footage of the actual people and events on Wikimedia Commons (public domain, CC0 or CC BY only; only the needed seconds are fetched), otherwise one stock clip per scene comes from Pixabay (or Pexels), keeping only clips whose tags match the scene.
   4. **Agent 4, Editor**: FFmpeg cuts each clip to its scene's narration time, fits it to 1080x1920 (Short) or 1920x1080 (long; real footage in Shorts is shown whole on a blurred copy of itself), joins them, adds the watermark and narration (no original audio), and appends the LIKE then SUBSCRIBE end card.
   5. **Agent 5, Publisher**: confirms the YouTube login is the right channel, uses the title and description written with the script (the source video's title if no AI was available), adds up to 12 hashtags (first the niche's currently trending hashtags from the Scout's rising videos and the strongest topic hashtag, then `#shorts`, the AI's topic hashtags and the source video's; also used as search tags) and the footage credits (real footage with author, licence and link), and uploads in resumable chunks with its go-live time (Shorts: `short_post_times`, or `first_post_time` then every `post_frequency`; long: `long_post_times`; a time already passed goes live soon after upload). It uploads only as many videos as the run has go-live times; extra finished videos wait for the next run.
4. **Agent 6, Cleanup**: deletes files no longer needed (GitHub) or day folders older than `keep_days` (laptop).
5. **Agent 7, Telegram**: a "run started" message (with how early or late GitHub started it), then one message per channel with each uploaded video's title, link, go-live time and hashtags, and how many failed.
6. **Agent 8, Analyst** (after all channels): saves each recent upload's views, scores search phrases by views after 48 hours, and sends a daily Telegram report.
7. **Result**: the log ends with each channel's uploads out of planned. A channel that uploaded nothing marks the GitHub run failed (red); one that uploaded some but not all adds a yellow warning to a green run.

### Video status flow (in the manifest, `01_manifests/trends.json`)
`discovered` → `voiced` → `ready_to_edit` → `edited` → `published`
(`download_failed` or `expired` when a step can't be completed; re-used trending videos use `discovered` → `downloaded` → `voiced` → `edited` → `published`.)

## AGENT INSTRUCTIONS
* **Agent 1 (Scout):** Prioritize high-velocity Short videos. Store clean metadata in human-readable JSON.
* **Agent 3 (Script & Voice):** Write a transformative commentary script on the trending topic, sized to the channel's `target_duration_seconds`, in the channel's language and tone. Short, clear sentences that read naturally aloud. **Open with a hook in the first sentence (first 3 seconds)**: a surprising fact, a bold question, or a "you won't believe" moment that makes the viewer keep watching, then pay it off before the end. For news and war channels, strictly avoid graphic or violent terms so the video remains advertiser-friendly; use strategic terminology ("escalating tensions", "frontline shifts", "defense posture"). Name the countries, places and public figures involved (heads of state, ministers and other government representatives), describing their public actions and statements factually, without insults or unproven claims. For kids channels (ages 4 to 9), write as an excited child talking to friends, and make it FUN first, educational second:
  - Hook in the first sentence with something surprising, silly or mysterious ("Whoa! This cat can't stop sneezing glitter!"), never a plain "Today we will learn".
  - Stories are ORIGINAL (never Peppa Pig, Disney or other copyrighted characters): give the hero a fun name and one funny trait, a silly problem, three tries that get funnier, a surprise twist, and a happy ending. Facts are told like a surprise reveal, with one joke each.
  - Very simple words, sentences of 5 to 10 words, lots of "!" and "?" so the narrator's voice can go up and down.
  - Use fun sound words (Boing! Splash! Zoom! Achoo!) and talk to the viewer every 20 to 30 seconds ("Can you guess what happens next?", "Say it with me!", "Count with me: one, two, three!").
  - Describe bright, simple things that can be shown on screen: animals, colours, food, toys, weather, places.
  - Long videos: three or four mini-stories or a "Top 5 silliest..." countdown, each ending with a little cliffhanger into the next.
  - End with a giggle and a question that makes them want the next video.
* **Agent 4 (Editor):** Apply a 1.06x crop zoom, remove the original audio completely (original_audio_volume 0), use only the AI voiceover, place the channel watermark in the bottom-right corner, and end every video with the end card (channel name, LIKE first, then SUBSCRIBE).
* **Agent 5 (Publisher):** Generate a high-CTR title under 65 characters for Shorts (70 for long videos) that matches what the script actually says, with no clickbait the video doesn't pay off. Keep the description to the requested sentences with no credits, links or source names: the pipeline adds the footage credit itself. Hashtags have no spaces, never use the `@` symbol, and never name other YouTube channels.
