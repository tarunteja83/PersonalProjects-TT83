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

cleanup_rules:
  delete_raw_after_publish: true
  delete_voiceover_after_publish: true
  delete_edited_from_previous_dates: true
```

## AGENT INSTRUCTIONS
* **Agent 1 (Scout):** Prioritize high-velocity Short videos. Store clean metadata in human-readable JSON.
* **Agent 3 (Script & Voice):** Write a transformative commentary script on the trending topic, sized to the channel's `target_duration_seconds`, in the channel's language and tone. Short, clear sentences that read naturally aloud. **Open with a hook in the first sentence (first 3 seconds)**: a surprising fact, a bold question, or a "you won't believe" moment that makes the viewer keep watching, then pay it off before the end. For news and war channels, strictly avoid graphic or violent terms so the video remains advertiser-friendly; use strategic terminology ("escalating tensions", "frontline shifts", "defense posture"). Name the countries, places and public figures involved (heads of state, ministers and other government representatives), describing their public actions and statements factually, without insults or unproven claims. For kids channels, write as an excited child talking to friends: very simple words, playful wonder, a "Guess what?" or "Whoa!" style hook, and end with a fun question that invites them to watch the next video. Keep it entertaining, not only educational: when the topic is a cartoon, story or silly animal, tell a short ORIGINAL funny story with a clear beginning, a surprise and a happy ending (never retell copyrighted characters such as Peppa Pig or Disney characters); when it is a fact, explain it with wonder and a joke.
* **Agent 4 (Editor):** Apply a 1.06x crop zoom, remove the original audio completely (original_audio_volume 0), use only the AI voiceover, place the channel watermark in the bottom-right corner, and end every video with the end card (channel name, LIKE first, then SUBSCRIBE).
* **Agent 5 (Publisher):** Generate a high-CTR title under 65 characters. Always include plain-text credit in the description WITHOUT using the `@` mention symbol: for stock footage, a link to Pexels and the videographers' names; for re-used videos, the original source channel.
