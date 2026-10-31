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
  story_books: "./GDrive_Mount/YouTube_Automation/Story_Books"   # book channels: each book's text, plan, pictures, progress

# Runs on your laptop or IDE put video files here instead, one folder per day:
#   YouTube_Auto/<YYYY-MM-DD>/download  (source videos)  and  /upload  (narration + final videos)
# Day folders older than keep_days are deleted automatically. GitHub runs keep using storage_paths above.
local_media:
  enabled: true
  root: "H:/My Drive/YouTube_Auto"   # your Google Drive (Drive for Desktop); "~/YouTube_Auto" = your user folder instead
  keep_days: 2
  story_books: "H:/My Drive/YouTube_Automation/Story_Books"   # the Drive folder GitHub syncs, so both share progress

# Where each video's pictures come from:
#   "stock"   = free stock clips matching the topic (works everywhere, including GitHub)
#   "youtube" = re-use the trending video itself (laptop only: YouTube blocks downloads from cloud servers)
footage:
  source: "stock"
  provider: "auto"        # "pixabay" | "pexels" | "auto" (whichever key is set: PIXABAY_API_KEY, then PEXELS_API_KEY)
  clips_per_video: 6            # scenes per Short (a new clip about every 5 to 6 seconds)
  long_scene_seconds: 8         # long videos change clip about this often (was 15: too slow to hold attention)

# Book storytelling channels (a `story:` section in questions.yaml; agents/storyteller.py)
story:
  picture_model: "@cf/black-forest-labs/flux-1-schnell"   # Cloudflare Workers AI (free daily allowance)
  picture_steps: 4              # FLUX schnell is made for 1 to 4; each step costs allowance (6 steps: ~95 pictures a day)
  retell_words: [1000, 1350]    # words per episode: about 7 to 9.5 minutes of narration
  split_words: 4000             # chapters longer than this become Part 1 and Part 2
  short_chapter_words: 725      # under about 5 minutes: never merged, its day gets a second video instead
  check_pictures: true          # Gemini compares every picture with its sentence; failures are drawn again
  pictures_per_check: 10        # pictures per Gemini request
  picture_attempts: 3           # drawings per sentence before a last try with a simpler prompt
  make_ahead_days: 1            # episodes are made this many days before they go live

editing_rules:
  zoom_factor: 1.06
  original_audio_volume: 0      # 0 = only the AI voice is heard; 0.05 = original sound faintly underneath
  watermark_fontsize: 36
  watermark_color: "white"
  watermark_opacity: 0.75
  audio_mastering:              # Audio leveling standards calibrated 2026-10-31
    target_mean_loudness_db: -20.0 # the level the values below were tuned to (FFmpeg volumedetect); not read by the code
    hero_volume_factor: 0.28    # Attenuate raw 0 dBFS (-9.1 dB) intro/outro hero clips to match narration
    voiceover_volume: 1.15      # Narration speech track boost in amix (yields -20.5 dB speech mean)
    amix_normalize: 0           # MUST be 0 to prevent amix dividing speech volume by input count
    bgm_volume: 0.12            # Ducked background music under narration (1.5s fade-in, 2.0s fade-out)
    sfx_volume: 0.35            # cartoon sound effects (assets/audio/kids_sfx) at the script's [BOING] / [TADA] tags
  mascot:                       # the WonderSprout superhero host, for channels with mascot: "wondersprout" (channels.yaml)
    intro_long: true            # long videos open with her flying in and saying intro_line (about 5 s)
    intro_shorts: true          # Shorts: her intro video (intro_short.mp4 or intro.mp4, ~7.6 s) plays first; false = she swoops into her corner instead
    host: true                  # in a corner throughout, expressions following the script, mouth moving with the voice
    outro: true                 # she says outro_line, waves and flies away before the LIKE / SUBSCRIBE card (about 5 s)
    intro_line: "Hey kids! This is Wonder Sprout, your fun and learning superhero!"
    outro_line: "Wonder Sprout signing off, kids! We'll meet next time!"
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
| `execution/view_techniques.md` | What gets views: its ACTIVE RULES are added to the AI instructions below for every video; it also logs every analysed source video (`--learn` or a link posted in Telegram) |
| `execution/video_requests.json` | Videos the owner asked for in Telegram (a shared example or a topic); written by the Telegram inbox (`agents/inbox.py`), made by each channel's next run |
| `BRAIN.md` (this file) | Rules shared by all channels: AI models and the free backup AI (`ai_fallbacks`, Groq), storage, footage source, editing and end card, upload and cleanup, AI writing instructions |
| `.github/workflows/daily_youtube_agents.yml` | When GitHub runs (generated from each channel's `fetch_time` by `execution.py --check`, firing `github_schedule_lead_minutes` early because GitHub starts scheduled runs hours late) |
| `.github/workflows/telegram_inbox.yml` | Hourly: reads the owner's Telegram posts (`execution.py --inbox`) and commits adopted rules and queued requests |
| `execution/execution.py` | The only entry point; reads the three files above and runs the agents |

### What one run does, in order
1. **Choose channels**: a scheduled run picks the channels whose `fetch_time` (one time or a list) matches; a manual run uses the channel typed in. With several fetch times a day, each run makes only the videos whose go-live times fall before the channel's next run.
2. **Clear lost work**: unfinished videos whose files are gone (an interrupted earlier run) are marked `expired`.
3. **Long videos first, then Shorts.** For each format:
   1. **Agent 1, Scout**: searches YouTube for trending videos on the channel's search phrases (Shorts topics from Shorts, long topics from 4 to 20 minute videos), ranks them by views per hour, skips other-language sources, uses the best-performing phrases first (from Agent 8) and rotates source channels. The owner's Telegram requests for this channel and format are made first (in normal slots), and their search phrases are searched first for 7 days. It also collects the search phrases people type (YouTube autocomplete, rising videos' keyword tags).
   2. **Agent 3, Script & Voice**: Gemini (or, when every Gemini model is out of quota or busy, the backup AI: Groq) writes an original script for the topic (following the owner's notes for a requested video), and in the same request the main search keyword (from the real search phrases; it leads the title and the first description sentence) and related phrases, the upload title, description, hashtags and footage terms (hook first; long videos get 3 to 5 parts and a recap). The voice narrates it sentence by sentence with varied pitch, speed and volume (`voice_expression`: lively for kids, measured for news; `agents/expressive_voice.py`), sentence timings are recorded, and if the narration runs longer than allowed the script is shortened and narrated again. The narration is split into scenes (about `clips_per_video` per Short, one every `long_scene_seconds` in long videos).
   3. **Agent 2, Footage**: a specific and a backup search term for each scene (for Shorts, written in the script request; for long videos, the AI reads each scene's words in a second request); with `footage_style: "real"` (Frontline) the specific term is first looked up as real footage of the actual people and events on Wikimedia Commons (public domain, CC0 or CC BY only; only the needed seconds are fetched), then as a real photo of them (shown whole with a slow zoom), otherwise one stock clip per scene comes from Pixabay (or Pexels), keeping only clips whose tags match the scene's subject (style words like "cartoon" don't count); on kids channels a matching Pixabay cartoon illustration (slow zoom) is used when no video matches.
   4. **Agent 4, Editor**: on channels with `mascot: "wondersprout"`, adds the superhero host (`agents/mascot.py`; her art in `assets/wondersprout/` replaces the drawn version): a voiced flying intro for long videos (`intro_line`), the host in a corner with expressions following the sentence timings and her mouth following the voice, and a voiced fly-off outro (`outro_line`) before the end card; kids channels also get quiet background music and the script's `[SOUND]` tags as cartoon sound effects, with the intro and outro levelled to the narration (`audio_mastering`). FFmpeg cuts each clip to its scene's narration time, fits it to 1080x1920 (Short) or 1920x1080 (long; real footage in Shorts is shown whole on a blurred copy of itself), joins them, adds the watermark and narration (no original audio), and appends the LIKE then SUBSCRIBE end card.
   5. **Agent 5, Publisher**: confirms the YouTube login is the right channel, uses the title and description written with the script (the source video's title if no AI was available), adds up to 12 hashtags (first the niche's currently trending hashtags from the Scout's rising videos and the strongest topic hashtag, then `#shorts`, the AI's topic hashtags and the source video's; also used as search tags), hidden search tags led by the video's search keyword and real search phrases that fit it, and the credits for real Commons footage (author, licence and link; no Pixabay or music credits), and uploads in resumable chunks with its go-live time (Shorts: `short_post_times`, or `first_post_time` then every `post_frequency`; long: `long_post_times`; a time already passed goes live soon after upload). It uploads only as many videos as the run has go-live times; extra finished videos wait for the next run.
4. **Agent 6, Cleanup**: deletes files no longer needed (GitHub) or day folders older than `keep_days` (laptop).
5. **Agent 7, Telegram**: a "run started" message (with how early or late GitHub started it), then one message per channel with each uploaded video's title, link, go-live time, search keyword and hashtags (📌 for a requested video), and how many failed.
6. **Agent 8, Analyst** (after all channels): saves each recent upload's views, scores search phrases by views after 48 hours, and sends a daily Telegram report.
7. **Between runs, Telegram inbox** (hourly workflow): the owner's posts in the alerts channel. A YouTube link is watched by Gemini: advice becomes proposed rules (`approve S5` adds them to ACTIVE RULES), a liked video is queued for a video like it; typed requests are queued too.
8. **Result**: the log ends with each channel's uploads out of planned. A channel that uploaded nothing marks the GitHub run failed (red); one that uploaded some but not all adds a yellow warning to a green run.

### Video status flow (in the manifest, `01_manifests/trends.json`)
`discovered` → `voiced` → `ready_to_edit` → `edited` → `published`
(`download_failed` or `expired` when a step can't be completed; re-used trending videos use `discovered` → `downloaded` → `voiced` → `edited` → `published`.)

## AGENT INSTRUCTIONS
* **Agent 1 (Scout):** Prioritize high-velocity Short videos. Store clean metadata in human-readable JSON.
* **Agent 3 (Script & Voice):** Write a transformative commentary script on the trending topic, sized to the channel's `target_duration_seconds`, in the channel's language and tone. Short, clear sentences that read naturally aloud. **Open with a hook in the first sentence (first 3 seconds)**: a surprising fact, a bold question, or a "you won't believe" moment that makes the viewer keep watching, then pay it off before the end. For news and war channels, strictly avoid graphic or violent terms so the video remains advertiser-friendly; use strategic terminology ("escalating tensions", "frontline shifts", "defense posture"). Name the countries, places and public figures involved (heads of state, ministers and other government representatives), describing their public actions and statements factually, without insults or unproven claims. For kids channels (ages 4 to 9), write as an enthusiastic, joyful game-show host (WonderSprout, "I") talking straight to the kids watching, making it FUN first, highly interactive and educational:
  - **Hook in the first sentence:** A mysterious or mind-blowing challenge ("Whoa! Can you guess which crazy animal can sleep for THREE whole years?", "Whoa! Look at this creature with giant glowing eyes!"). Never a dry "Today we will learn".
  - **Sound Effect Cue Tags:** You MUST include sound effect cues in square brackets in the script: `[BOING]`, `[POP]`, `[DING]`, `[CHEER]`, `[TICK]`, `[TADA]`, `[WHOOSH]`, `[SPLASH]` right where the action or sound occurs. These trigger real cartoon sound effects; use only these tags, and never write any other words in square brackets.
  - **Visual Realism (CRITICAL FOR FOOTAGE MATCHING):** Never write about imaginary cartoon animals wearing human clothes or doing human jobs (no bunnies wearing diamond hats, no pigs at beauty salons). Always describe REAL creatures, real animal habits, real habitats, ocean giants, dinosaurs, and amazing animal superpowers (e.g. cheetah sprinting fast, chameleon changing color, baby elephant splashing in mud, octopus squirting ink, eagle soaring high). Real subjects are what stock footage can actually show. Everything is original: never Peppa Pig, Disney or other copyrighted characters, shows or songs.
  - **Long Videos (5 to 7 minutes - The 5-Round Game Show Format):** Long videos must NEVER be a single slow monologue. Structure every 5 to 7 minute video into **5 Fast-Paced Interactive Rounds** (~60 to 75 seconds each):
    - *Intro (15s):* WonderSprout announces the challenge: "Hey kids! Welcome to the Mega Animal Mystery Show! We have 5 wild rounds today! Can you get all 5? Let's find out! Round 1!"
    - *Round 1 to Round 5:* Each round gives 2 exciting clues, prompts the viewer to shout their guess ("What is it? Guess in 3... 2... 1... [TICK] [TICK] [TICK]!"), reveals the answer with celebration ("It's the mighty blue whale! [TADA] [CHEER]"), and shares one mind-blowing superpower fact with a sound cue (`[DING]`).
    - *Transition between rounds:* Quick energetic bridge ("Awesome job! Give yourself a high-five! Are you ready for Round 2? Here we go! [WHOOSH]").
    - *Outro (20s):* Celebration `[CHEER]`, high-fives, and asking them to comment their final score ("How many rounds did you guess right? Tell me in the comments below!").
  - **Shorts (the channel's Short length):** A single rapid-fire mystery or crazy superpower with a 3-second countdown ("Guess what animal has BLUE blood! 3.. 2.. 1.. [TICK] [TADA] It's an octopus!").
  - Simple, punchy words, sentences of 5 to 10 words, lots of "!" and "?" so the expressive voice jumps with excitement.
* **Agent 4 (Editor):** Apply a 1.06x crop zoom, remove the original audio completely (original_audio_volume 0), keep the volume even across intro, narration and outro (`audio_mastering`), overlay the channel watermark in the bottom-right corner, and end every video with the end card (channel name, LIKE first, then SUBSCRIBE).
* **Agent 5 (Publisher):** Generate a high-CTR title under 65 characters for Shorts (70 for long videos) that matches what the script actually says, with no clickbait the video doesn't pay off. Keep the description to the requested sentences with no credits, links or source names: the pipeline adds the Commons footage credit itself. Hashtags have no spaces, never use the `@` symbol, and never name other YouTube channels.
