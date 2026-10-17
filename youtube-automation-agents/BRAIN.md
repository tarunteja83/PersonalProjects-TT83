# MASTER BRAIN & MULTI-AGENT CONFIGURATION
Global rules shared by every channel. Modify the YAML block or the Agent Instructions below to change how all agents behave.
Channels are listed on the channel page: `execution/channels.yaml`. How to run everything: `execution/execution.md`.

```yaml
# Gemini models for scripts and titles, tried in order (a busy or retired model falls through to the next)
ai_models: ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-flash-latest"]

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
  clips_per_video: 3

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
| `BRAIN.md` (this file) | Rules shared by all channels: AI models, storage, footage source, editing and end card, upload and cleanup, AI writing instructions |
| `.github/workflows/daily_youtube_agents.yml` | When GitHub runs (generated from each channel's `fetch_time` by `execution.py --check`) |
| `execution/execution.py` | The only entry point; reads the three files above and runs the agents |

### What one run does, in order
1. **Choose channels**: a scheduled run picks the channels whose `fetch_time` matches; a manual run uses the channel typed in.
2. **Clear lost work**: unfinished videos whose files are gone (an interrupted earlier run) are marked `expired`.
3. **Long videos first, then Shorts.** For each format:
   1. **Agent 1, Scout**: searches YouTube for trending videos on the channel's search phrases (Shorts topics from Shorts, long topics from 4 to 20 minute videos), in the channel's language, rotating phrases and source channels.
   2. **Agent 3, Script & Voice**: Gemini writes an original script for the topic (hook first; long videos get 3 to 5 parts and a recap). The voice narrates it, sentence timings are recorded, and if the narration runs longer than allowed the script is shortened and narrated again. The narration is split into scenes (3 per Short, ~15 s each in long videos).
   3. **Agent 2, Footage**: Gemini picks a specific and a backup search term from each scene's words; one stock clip per scene is downloaded from Pixabay (or Pexels).
   4. **Agent 4, Editor**: FFmpeg cuts each clip to its scene's narration time, fits it to 1080x1920 (Short) or 1920x1080 (long), joins them, adds the watermark and narration (no original audio), and appends the LIKE then SUBSCRIBE end card.
   5. **Agent 5, Publisher**: confirms the YouTube login is the right channel, has Gemini write the title, description and hashtags (with stock footage credit), and uploads in resumable chunks with its go-live time (Shorts: `first_post_time` then every `post_frequency`; long: `long_post_times`).
4. **Agent 6, Cleanup**: deletes files no longer needed (GitHub) or day folders older than `keep_days` (laptop).
5. **Agent 7, Telegram**: posts links to everything uploaded.

### Video status flow (in the manifest, `01_manifests/trends.json`)
`discovered` → `voiced` → `ready_to_edit` → `edited` → `published`
(`download_failed` or `expired` when a step can't be completed; re-used trending videos use `discovered` → `downloaded` → `voiced` → `edited` → `published`.)

## AGENT INSTRUCTIONS
* **Agent 1 (Scout):** Prioritize high-velocity Short videos. Store clean metadata in human-readable JSON.
* **Agent 3 (Script & Voice):** Write a transformative commentary script on the trending topic, sized to the channel's `target_duration_seconds`, in the channel's language and tone. Short, clear sentences that read naturally aloud. **Open with a hook in the first sentence (first 3 seconds)**: a surprising fact, a bold question, or a "you won't believe" moment that makes the viewer keep watching, then pay it off before the end. For news and war channels, strictly avoid graphic or violent terms so the video remains advertiser-friendly; use strategic terminology ("escalating tensions", "frontline shifts", "defense posture"). Name the countries, places and public figures involved (heads of state, ministers and other government representatives), describing their public actions and statements factually, without insults or unproven claims. For kids channels, write as an excited child talking to friends: very simple words, playful wonder, a "Guess what?" or "Whoa!" style hook, and end with a fun question that invites them to watch the next video. Keep it entertaining, not only educational: when the topic is a cartoon, story or silly animal, tell a short ORIGINAL funny story with a clear beginning, a surprise and a happy ending (never retell copyrighted characters such as Peppa Pig or Disney characters); when it is a fact, explain it with wonder and a joke.
* **Agent 4 (Editor):** Apply a 1.06x crop zoom, remove the original audio completely (original_audio_volume 0), use only the AI voiceover, place the channel watermark in the bottom-right corner, and end every video with the end card (channel name, LIKE first, then SUBSCRIBE).
* **Agent 5 (Publisher):** Generate a high-CTR title under 65 characters. Always include plain-text credit in the description WITHOUT using the `@` mention symbol: for stock footage, a link to Pexels and the videographers' names; for re-used videos, the original source channel.
