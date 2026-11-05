# CLAUDE.md: project context for new sessions

The repo owner (GitHub `tarunteja83`) is building **faceless YouTube channels as a second income**. The main project is
`youtube-automation-agents/`, a multi-agent Python pipeline that posts Shorts and long videos daily via GitHub Actions.
Read `youtube-automation-agents/execution/execution.md` (full user guide) and the `PIPELINE ARCHITECTURE` section of
`youtube-automation-agents/BRAIN.md` before changing anything.

## Channels (live, created by the owner under a different Google account than the Cloud project)
| Key | Channel | Link | Posting (IST) |
|---|---|---|---|
| `geopolitics_war` | Frontline Ledger TT83 | youtube.com/@frontline-ledger-tt83 | Short 06:00; long (5 to 7 min) 12:00 |
| `kids_edu` | WonderSprout Kids TT83 (made for kids) | youtube.com/channel/UCf9u1ZaejKPPA-oOD8oXVfg | Short 06:00; long (5 to 7 min) 11:30 |
| `story_books` | Inkbound Tales (general audience, same Google account) | youtube.com/@InkboundTalesTT83 | 1 illustrated book episode (5 to 10 min) + teaser Short, both 15:00 |

Since 2026-11-05 (owner: "only 2 videos a day per channel, but good and well synced") Frontline and WonderSprout
each make 1 Short (06:00) + 1 long video in ONE run at midnight (`fetch_time: "00:00"`); Inkbound keeps three runs
(`["00:00", "13:00", "17:00"]`) for its picture allowance. 6 uploads a day in all (was 13). Each run owns the go-live slots
between its fetch time and the next one (`plan_run` in `execution.py`). The cron fires `github_schedule_lead_minutes`
early, per fetch time (BRAIN.md: 00:00 235, 13:00 445, 17:00 445 minutes; crons 20:05, 05:35, 09:35 IST), because
GitHub starts scheduled runs 3 to 10 hours late depending on the time of day (measured runs #27 to #56). Starting
early is harmless (`run_window` takes the nearest occurrence of the fetch time; verified for 22:45 to 02:30). The owner plans to add more channels.

## How it works (short)
- **One entry point:** `execution/execution.py`. With no arguments it runs every channel with `run: true`. GitHub uses
  `--scheduled --cron`. `--check` validates config and rewrites the workflow cron lines. Other options: `--ask`,
  `--authorize KEY`, `--format short|long|both`, `--dry-run`, `--learn LINK`, `--inbox` (Telegram posts).
- **Config:**
  - `execution/channels.yaml`: identity (name, `channel_url`, made_for_kids, watermark, category).
  - `execution/questions.yaml`: Q1 to Q24 per channel (plus `short_post_times`, `voice_expression`), with
    options written as comments.
  - `execution/view_techniques.md`: what gets views; its ACTIVE RULES go to the AI with every video.
  - `execution/video_requests.json`: videos the owner asked for in Telegram (written and committed by the inbox bot).
  - `BRAIN.md`: global rules. Everything after `## AGENT INSTRUCTIONS` is sent to Gemini, so put human notes above it.
- **Run order:** a Telegram start message, then per channel long videos first, then Shorts. Per format: scout
  (the owner's Telegram requests first, then a rising, same-language trending topic via the YouTube API, plus real
  search phrases), then script + voice (Gemini, or Groq as backup, writes script + search keyword + title +
  hashtags + footage terms; kids scripts carry [SOUND] tags; expressive edge-tts with sentence timings; ~6 scenes per Short, ~8 s
  scenes in long videos), then footage (Frontline: real Commons video/photo first; kids: matching cartoon/real
  Pixabay clip or cartoon illustration), then the FFmpeg editor (kids: WonderSprout host, intro, outro, music, sound effects), then the
  publisher (channel check, resumable chunked upload, `publishAt`). Then cleanup, a Telegram message per channel,
  and the Analyst (views, phrase scores, daily report).
- **Manifest:** `GDrive_Mount/YouTube_Automation/01_manifests/trends.json`. Status flow: discovered, voiced,
  ready_to_edit, edited, published (also expired, download_failed).
- **GitHub runs:** use temporary folders. Logins and the manifest sync via rclone to Google Drive
  (`YouTube_Automation/`).
- **Laptop runs:** write video files to `H:/My Drive/YouTube_Auto/<YYYY-MM-DD>/download|upload` (Google Drive for
  Desktop), and day folders older than 2 days are deleted.

## Inkbound Tales, the book storytelling channel (built 2026-10-31, owner's request)
- `agents/storyteller.py`, used instead of the orchestrator for any channel with a `story:` section in
  questions.yaml (`run_channels` branches on `profile["story"]`). Full description: execution.md "Inkbound Tales".
- Owner's decisions: public-domain books only (Gutendex lookup, strict title match; a modern book is refused);
  first book The Hound of the Baskervilles (owner preferred Jungle Book; trend reasoning chose Hound, Jungle Book
  later side by side); one episode a day of 5 to 10 min, live 15:00 IST with a teaser Short at 15:00; chapters over
  4,000 words become two parts; a chapter under ~5 min is NOT merged, that day gets two videos; condensed faithful
  retelling; a picture for every sentence was the first "strict rule", changed by the owner on 2026-11-01 to one
  picture per scene of 2 to 3 sentences (`lines_per_picture: 3`, option B; the free allowance covers only ~40 to
  60 pictures a day; Workers Paid at ~$7 to $10 a month was the alternative), pencil sketch style (`picture_style: color` exists; the
  owner asked for a future "colour animation of the pencil pictures" option: archived pictures + prompts are kept
  for it, nothing built yet: free text-to-image can't recolour the same drawing consistently); a voice per
  character by gender/age; spoken viewer-discretion card for violence, sexual themes, alcohol/drugs, language,
  frightening scenes (explicit passages toned down); made a day ahead (each run makes what's due by tomorrow).
- Pictures: Cloudflare Workers AI `@cf/black-forest-labs/flux-1-schnell` (square 1024; only prompt + steps
  accepted), free 10,000 neurons/day, reset 00:00 UTC (05:30 IST). Measured 2026-10-31: at 6 steps only ~95 to 100
  pictures a day (Chapter 1 has 105 lines), so picture_steps is 4 (~170 a day expected, not yet measured).
  Safety-filter refusals ("NSFW", code 8007) hit harmless prompts (~3 in 77): plainer wordings are tried.
  Pencil pictures are saved black and white (the model sometimes adds colour). Character consistency is
  imperfect (Holmes varied between pictures in Chapter 1's test). Pollinations (no key: 1 per
  45 s then 503) and HF (token lacks inference permission) failed on 2026-10-31. Gemini checks pictures in batches
  of 10 (`ai.look_at_pictures`). Keys CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN in .env and GitHub secrets
  (owner added both).
- Story channel login adds `youtube.force-ssl` (playlists); `brain.channel_scopes` keeps other channels' scopes so
  their tokens stay valid. The owner added the scope in the OAuth consent screen (Data Access) on 2026-10-31.
- Storage: Drive `YouTube_Automation/Story_Books/<book>/` (book.json, text/, characters.json, progress.json,
  episodes/<key>/, archive/<key>/), `channel_story_books.json` (last scheduled date, finished books). Laptop runs use
  `H:/My Drive/YouTube_Automation/Story_Books` (the same Drive folder), GitHub syncs it (archive/ not downloaded).
- Telegram: `book <title>` queues a book (kept until told). The Short's "Related video" link can only be set in
  Studio: Telegram reminds the owner after each upload. Multi-language audio tracks (Hindi, Tamil, Telugu,
  Marathi, Spanish; owner's wish) are phase 2: the Data API can't attach audio tracks (unverified, no official
  doc found), so the plan is translated narration files in Drive for the owner to add in Studio, plus
  localized titles/descriptions via the API.

## Key decisions (and why)
- **Stock footage (Pixabay), not YouTube downloads.** YouTube blocks yt-dlp from GitHub's cloud IPs ("Sign in to
  confirm you're not a bot"). It also avoids copyright claims. Pexels is supported in code, but its key issuance was
  paused, so the workflow passes only `PIXABAY_API_KEY`.
- **Footage synced to narration.** The script is written first, then one clip per scene, chosen by the footage
  check (`stock_footage.scene_candidates` + `check_footage`: Gemini sees each candidate's thumbnail next to the
  scene's words, 8 scenes a request; text-only backup judge; `archive_material` filter; none fits -> neighbouring
  clip). Run #54 showed why: "forest trench" gave a 1914 photo, "Kremlin" an airliner-crash meeting, and kids
  scripts read "Transition" and "Outro (20 seconds)" aloud; `agent_3_voiceover.clean_script` now strips such
  labels and markdown, and long scripts under 85% of their words are lengthened (`lengthen`). Frontline may name public
  figures (government representatives) in search terms; the kids channel never names real people.
- **AI voices, not voice cloning:** edge-tts with expressive sentence-by-sentence narration. Chosen by the owner on
  2026-10-24 from samples: `en-US-BrianMultilingualNeural` (measured) for Frontline, `en-US-AvaMultilingualNeural`
  (lively) for WonderSprout. Supported languages: English, Hindi, Telugu.
- **Audio standards and leveling (2026-10-31 calibration):**
  - **Broadcast loudness standard:** Uniform mean volume of ~ -20 dB (±0.5 dB) across all video segments (intro, narration, outro) to eliminate jarring volume spikes or ear fatigue. Verified via FFmpeg `volumedetect`.
  - **Hero clips attenuation (`hero_volume_factor: 0.28`):** Raw 3D hero assets (`intro.mp4`, `outro.mp4`) peak at 0 dBFS (-9.1 dB mean) and are ~11-15 dB louder than speech. `VideoEditorAgent.custom_hero_clip` attenuates them using `-af volume=0.28`, bringing them to -20.0 dB mean.
  - **FFmpeg `amix` normalization (`normalize=0`):** FFmpeg's `amix` filter divides audio by 1/N unless `normalize=0` is set. When mixing narration, BGM, and SFX, `amix` MUST use `normalize=0` with narration at `volume=1.15` so speech is not suppressed down to -30 dB.
  - **Background Music (BGM):** Cheerful royalty-free music (`assets/audio/kids_bgm/`) ducked under speech at `volume=0.12` with 1.5s fade-in and 2.0s fade-out.
  - **Sound Effects (SFX):** Cartoon SFX (`assets/audio/kids_sfx/`: boing, pop, whoosh, ding, tick, tada, splash, cheer) leveled at `volume=0.35`, triggered by script cues `[BOING]`, `[POP]`, `[TICK]`, `[TADA]`, etc. parsed by `expressive_voice.py`.
- **Branding:** no original audio (`original_audio_volume: 0`), a name-only LIKE then SUBSCRIBE end card
  (`show_logo: false`), and a watermark.
- **WonderSprout content:** entertainment plus education. Original funny stories (no copyrighted characters),
  narrated by the WonderSprout superhero host, and `footage_style: "mixed"` (Pixabay animation alternating with
  real footage, cartoon illustrations when no clip matches).
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
- **Telegram inbox (2026-10-31, owner's request):** `agents/inbox.py`, run hourly by `.github/workflows/telegram_inbox.yml`
  (`execution.py --inbox`). Reads `channel_post` updates of TELEGRAM_CHANNEL_ID via getUpdates (bot is an admin there;
  no webhook; each post is confirmed after handling). A YouTube link: Gemini watches it (`ai.watch_video`, file_data
  YouTube URL, low media resolution, ~60K tokens; title/description fallback) and replies KIND ADVICE (techniques ->
  proposed view_techniques entry; owner replies `approve S5 [1,3]` / `reject S5` -> `learner.review`) or EXAMPLE
  (channel, topic, why it works, search phrases -> `execution/video_requests.json`). Text requests ("make a kids
  video about ...") are interpreted by the AI and queued too; `queue` / `cancel Q3`. The Scout makes waiting
  requests first in normal slots (`waiting_requests`, `request_entry`; done = video_id already in the manifest;
  topic ids are `request-Q<n>`), and their search phrases lead its queries for 7 days (`boosted_phrases`); Agent 3
  gets `reference_notes`; Telegram upload messages show 📌. The workflow commits both files to main (setup_project.py
  then lags for view_techniques.md until the next repack). Owner chose: approve rules in Telegram, links in the
  existing channel, YouTube only for now (Instagram needs a login; possible later: send the video file to the bot).
- **Hashtags:** up to 12: two currently trending niche hashtags and the top topic hashtag first, then #shorts, the
  AI's topic hashtags, more trending ones and the source video's; also used as search tags (details below).
- **Search keywords (2026-10-31, owner's observation S3):** the Scout gets real search phrases per niche phrase
  (`search_suggestions`: YouTube autocomplete via suggestqueries.google.com, unofficial, free, no API quota,
  other-language phrases dropped; plus `trending_keywords` from rising videos' snippet.tags) into
  `item["search_keywords"]`. The script request also returns KEYWORD (`seo_keyword`, used in the title's first words
  and the first description sentence) and KEYWORDS (`seo_keywords`). `search_tags` leads with them, then
  `relevant_keywords` (Scout phrases whose every 4+ letter word is in the video). Telegram shows the keyword (🔎).
- **Views (from `execution/view_techniques.md`, source S1 "Why Your Views STOP After 48 Hours"):** ACTIVE RULES
  (trend first, stay in niche, retention, honest click-worthy titles) are appended to the Gemini instructions by
  `brain.view_rules()`. The Scout ranks sources by views per hour and skips other-language sources (other scripts,
  Hinglish words, titles naming another language; WonderSprout had been taking Hindi topics). Agent 8 (Analyst,
  `agents/agent_8_analyst.py`) stores views on manifest items (`stats`, `views_48h`), orders each channel's search
  phrases by views after 48 h (re-trying phrases unused for 3 days), and posts a daily Telegram report
  (state in `01_manifests/analyst_state.json`).
- **Engagement overhaul (2026-10-24, owner's viewer feedback, source S2 in view_techniques.md):**
  - Real footage: Frontline `footage_style: "real"` searches Wikimedia Commons for real video, then a real photo
    (`commons_photo_candidates`, whole photo on blur with a slow zoom), of each scene's named person/place/event (`stock_footage.commons_candidates`): only public domain, CC0, CC BY; every key word of the
    term must be in the file title (longer terms retried with the first two words); only the needed seconds are
    fetched with FFmpeg over HTTP; a used video is reused from a later moment; credits (author, licence,
    `commons.wikimedia.org/?curid=`) go in the description. Real clips are named `*_real.mp4`; the editor shows them
    whole on a blurred copy in Shorts. Copyrighted news-agency footage is deliberately not used.
  - Pixabay clips must have a tag matching the term's subject (`tags_match` ignores STYLE_WORDS such as cartoon,
    funny, cute: owner saw random stars/bubbles clips in kids videos). Kids styles (animation/mixed) fall back to
    a matching Pixabay cartoon illustration (`pixabay_illustrations` + `photo_clip`, up to 4 tried: Pixabay's
    image server refuses bursts with 429). The owner decided against AI-drawn scenes (2026-10-24): use web cartoon
    images played with a slow zoom instead.
  - Faster cuts: `clips_per_video: 6`, `long_scene_seconds: 8` (BRAIN.md `footage`).
  - Expressive voice: `agents/expressive_voice.py` narrates sentence by sentence with per-kind pitch/rate/volume
    (`voice_expression`: lively kids, measured news, off). Samples are in Google Drive `YouTube_Auto/voice_samples`;
    the owner chose Ava (kids) and Brian (news).
  - Kids writing rules rewritten in BRAIN.md Agent 3 (named hero, silly problem, three tries, twist, sound words,
    talk to the viewer every 20 to 30 s).
  - WonderSprout host (owner's idea): `agents/mascot.py`. Corner host track (qtrle .mov, 15 fps): `expression_for`
    picks a pose per sentence (`sentence_timings` from Agent 3), `mouth_track` opens the mouth on loud frames.
    Voiced intro (long videos) and outro: she flies through a cloud sky (`FlyingSky`) and speaks `intro_line` /
    `outro_line` (BRAIN.md) in the channel voice via expressive_voice, mixed with wind, whoosh and chime made by
    FFmpeg (`effect_sources`); about 6 s each, ~1 min each to render. Shorts also play the intro (`intro_shorts`).
    Character: the owner wants the real character from their reference image (green/light-blue suit, "WS" logo,
    dark hair with green streaks, gold belt; copy in Google Drive `YouTube_Auto/wondersprout_preview/`). The
    Gemini key's free tier has no image generation (limit 0), so the owner makes 9 poses by hand
    (`assets/wondersprout/PROMPTS.md`) and imports them with `--hero-art` (magenta background cut out). Until
    then the drawn cartoon is used; once any art exists it is used for every pose (closest pose as fallback).
    The owner offered to provide their own intro/outro videos and expression pictures: drop folder Google Drive
    `YouTube_Auto/wondersprout_assets/` (spec `WHAT_TO_PROVIDE.md` = assets/wondersprout/PROMPTS.md). Import with
    `--hero-art`, then commit assets/wondersprout. `VideoEditorAgent.custom_hero_clip` uses intro.mp4/outro.mp4
    (and *_short.mp4) when present, fitted on blur, silence added if they have no sound.
    Imported 2026-10-24: the owner's 9 poses (3D-animated, made with Grok, transparent PNG with a "Grok" logo
    and magenta fringe) and intro/outro (474x642, 7.6 s, with sound, "Grok" logo bottom-right). `clean_cutout`
    keeps only her shape (flood fill; drops the logo) and despills the magenta edge; `cover_corner_logo` puts a
    "WS" badge over the videos' logo (owner asked to remove or cover it). Note: Pillow 13 images made with
    Image.fromarray can be read-only, so floodfill needs `.copy()`.
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
    BRAIN.md). Run logs are public, so never print secrets.
  - YouTube searches (100/day per project, reset at midnight Pacific = 12:30 IST): since 2026-10-24 the Scout
    searches phrases lazily (about one search per video, ~13/day for both channels) and, if the limit is used up,
    logs it and continues with finished videos instead of failing. Local test runs also spend searches: on
    2026-10-24 testing used up the day's limit (GitHub test run 40071143012, kids Short failed).

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
  1. Run `python execution/execution.py --learn <link>` (1 Gemini request; it reads the subtitles, or Gemini watches
     the video when they can't be downloaded, and adds a "proposed" entry). If the owner posts the link in Telegram
     instead, the inbox bot does this itself and the owner approves rules there. If Gemini or subtitles fail, read the metadata with the YouTube API and the subtitles with yt-dlp
     (`--skip-download --write-auto-subs --sub-langs "en.*"`) and write the entry yourself.
  2. Review every proposal critically: keep only techniques the video gives evidence for, reject anything that fakes
     engagement, misleads viewers or reuses others' content, and check it against what the pipeline already does.
  3. Adopt: short writing rules go into ACTIVE RULES (sent to Gemini with every request, so keep them brief and
     tagged [S<n>]); code-level techniques go into the code and the "HOW THE PIPELINE APPLIES THEM" table; owner
     tasks go under "Not automated yet". Mark the entry "reviewed" with a → verdict per technique.
  4. Then the usual checks (--check, setup_project.py, 3.11 syntax, tests) and tell the owner what changed.

## Status and context for next session (as of 2026-10-31)
- **Next steps (2026-10-31):**
  - Telegram inbox (`agents/inbox.py`, `telegram_inbox.yml`) is merged but not yet run on GitHub: run the Telegram
    Inbox workflow once by hand, have the owner post one YouTube link and one request ("make a kids video about
    ..."), and check the bot's replies, the commit to main (needs Settings → Actions → Workflow permissions: read
    and write, if the push fails) and that the next daily run makes the request (📌 in its Telegram message).
  - Check from the next runs' Telegram messages: the search keyword (🔎) in titles, kids music and sound effects,
    and the Shorts intro's effect on retention.
  - Music licence: both BGM tracks look like CC BY and get no credit (owner's choice); offer to swap them for
    tracks that need no credit.
- **Immediate owner priority (CRITICAL FOR NEXT SESSION):**
  - **Video clips are still bad:** The owner explicitly noted *"the video clips are still bad"*. Pixabay stock search frequently returns generic, low-interest or poorly matched clips. The next agent/session must focus heavily on improving visual quality, clip matching, or alternative footage sources/curation for kids entertainment.
  - **Video length requirement:** Kids long videos must be **at least 5 to 7 minutes** without lagging.
  - **Two approved approaches:**
    1. *Approach 1 (Interactive Animal Mystery Show):* 5 fast-paced rounds (~60-75s each) with 2 clues, 3-second countdown (`[TICK]`), answer reveal (`[TADA] [CHEER]`), and an animal superpower fact (`[DING]`). Documented in `BRAIN.md`.
    2. *Approach 2 (Trending Kids Content):* Curating popular trending kids concepts and using transformative commentary/fair use to prevent copyright claims.
       Not built. Caution: fair use is not a defence YouTube applies automatically; reusing others' kids clips risks
       Content ID claims and "reused content" demonetisation, and a Made for Kids channel gets stricter review. Only the
       topic should come from trending videos (as today), never their footage, characters or songs.
- **Completed and verified on 2026-10-31:**
  - **Audio soundtrack & cartoon SFX:** Added Creative Commons BGM (`assets/audio/kids_bgm/`) and 8 cartoon sound effects (`assets/audio/kids_sfx/`). `expressive_voice.py` parses script cues (`[BOING]`, `[POP]`, `[TICK]`, `[TADA]`, `[CHEER]`, `[WHOOSH]`, etc.) into `sfx_cues` for the editor.
  - **Review fixes (2026-10-31, before merging):** tags are never spoken (tag-only sentences are skipped; the one-pass
    fallback strips them; `generated_script` is stored without them); a cue plays where its tag sits in the sentence and
    repeats are 0.6 s apart; spoken sound words match whole words only ("holding" no longer fires `ding`); music and
    SFX apply to any `made_for_kids` channel, not just the key `kids_edu`; tracks rotate per video.
  - **Description credits (owner's decision, 2026-10-31):** only real Wikimedia Commons footage is credited
    (`credit_block`: author, licence, link; in practice Frontline). No Pixabay/Pexels, "Commentary produced by" or
    music lines; kids descriptions are text + hashtags. Risk the owner accepted: both BGM tracks look like CC BY
    (Happy Ukulele by Jason Shaw per its tags; carefree.mp3 likely Kevin MacLeod's "Carefree"), which asks for
    attribution; safer option offered: music that needs no credit (e.g. Pixabay Music).
  - **Broadcast audio leveling standard:** Calibrated to uniform **-20.0 dB mean** (±0.5 dB) across intro, narration, and outro. Hero clips are attenuated with `volume=0.28` (raw clips peak at 0 dBFS, -9.1 dB mean). FFmpeg `amix` MUST use `:normalize=0` with voiceover at `volume=1.15` (without `:normalize=0`, FFmpeg suppresses narration down to -34 dB).
  - **Reference test video:** Verified at `H:\My Drive\YouTube_Auto\2026-10-31\upload\final_kids_edu_ic2S-zIJBI0_leveled.mp4` (intro: -20.0 dB, narration: -20.5 dB, outro: -20.2 dB).
  - **Hero entry on Shorts:** `intro_shorts: true` in `BRAIN.md` (owner's choice, kept after review): the WonderSprout
    intro clip (~7.6 s) plays at the start of Shorts. Watch Shorts retention in Studio.
  - **Footage style:** Set `footage_style: "film"` in `execution/questions.yaml`: real-life Pixabay stock only (no cartoon
    clips or cartoon illustrations), matching the new "real animals" scripts.
  - **Validation:** `execution.py --check` passes cleanly; `setup_project.py` is re-packed.
  - **Search keywords** (`9aebfaf`): see "Search keywords" under Key decisions; verified with one real AI request
    (keyword "guess the animal challenge" from autocomplete, leading the title).
  - **Telegram inbox:** see "Telegram inbox" under Key decisions. Verified locally: Gemini watched a real kids Short
    (NB3OQ2IEM4w, 21 s) and queued it for WonderSprout; approve/reject/queue/cancel and the Scout's request handling
    tested offline with stubs. Gemini watching a video was first confirmed on S1 (~63K tokens at low resolution).

## 5 Nov 2026: midnight run, per-run lead times, no more silent hangs
- Later the same day (owner's request): 1 long + 1 Short a day per trend channel, the footage check and script
  cleaning above, and Inkbound's storyteller voice: `voice_expression: "storyteller"` at `voice_speed: "-8%"`
  (measured 143 words a minute, was 176 with "measured" at -5%; viewers said it "just reads the story"), 0.5 s
  more silence where the picture changes, and the retelling explains unfamiliar things in plain words (`explain`
  lines, grouped into their scene so they cost no picture). Footage check and kid/Frontline downloads were tested
  on run #54's problem scenes (Groq text judge; Gemini was out of quota that day, so the picture-based check and a
  retelling with explain lines are not yet seen live).
- Run #56 (05:45 IST start) found its first topic at 06:16 then hung in Agent 3 (script/voice) until GitHub's
  240-minute limit cancelled it: no videos that morning, and no log (Python buffered its output). Fixed: Gemini
  requests have a 180 s limit (`ai.REQUEST_TIMEOUT_SECONDS`, 600 s to watch a video) and a timed-out model is
  skipped at once; each edge-tts sentence has 60 s (`expressive_voice.SPEAK_TIMEOUT_SECONDS`, retried), one-pass
  narration 600 s; both workflows set PYTHONUNBUFFERED so the log streams.
- Schedule (owner's request): first run at midnight IST, both channels' first Short at 06:00 (Frontline 08:00 ->
  06:00, WonderSprout 09:00 -> 06:00); `github_schedule_lead_minutes` became per fetch time (`brain.lead_for`).

## Runs of 31 Oct / 1 Nov 2026 (first runs with Inkbound Tales)
- Runs #44 (04:38 IST) and #45 (15:50 IST) were red: story_books uploaded 0 because Cloudflare said the free
  allowance was used up, with 0 pictures drawn. #44 was before the 05:30 IST reset (expected), but #45 and a manual
  test at 18:54 IST on 1 Nov were also refused although nothing of ours drew since the reset: either something else
  uses the account (Antigravity had the .env open; owner asked to check the Cloudflare dashboard) or the window is
  not a 00:00 UTC reset. The API token can't read usage (GraphQL "not authorized").
- Fixed: a story episode waiting for pictures no longer fails the run (`StoryChannel.waiting`, yellow ::warning).
- Fixed: Frontline lost one long video per run when a Commons segment download "succeeded" with no picture (seek
  past the real end); `stock_footage.has_picture` now rejects it and the scene falls back to stock footage.
- Chapter 1 moved to 2 Nov 15:00 (run #45 started after 15:00 on 1 Nov).
- Step 9's rclone "directory not found" lines are old noise from deleting 02_raw_videos (harmless, `|| true`).
- 2 Nov: still no Chapter 1. Run #47 drew the pictures, but the Gemini checker kept rejecting 9 of them (mostly the
  small artist signature FLUX puts in a corner of "sketch" pictures, and minor missing details); run #48 left one
  (picture 104) failing, and one picture blocked the whole episode. Fixed: `clean_corners` paints out a bottom
  corner that is blank paper with a few marks (measured: signature corners < 2% marked pixels, drawn corners 12%+);
  the checker fails only wrong characters/place, contradictions, large readable writing, unsafe content or wrong
  style; a picture failing every try is used anyway (`*_rejected.jpg` kept, reported in Telegram).
- Local renders froze: temporary clips written into H:/My Drive (Drive for Desktop locked a file; FFmpeg hung on clip
  37 for 40 min, the same command took 2.7 s on local disk). Work folders are now in the system temp folder.
- 2 to 3 Nov DUPLICATES (my mistake): I uploaded Chapter 1 from the laptop (dgLZgTMoiOM + Short mes6p7xtpns,
  playlist PLJDefp6dWP_k) at ~22:30 IST while GitHub run #49 (started 22:21) was running; it had pulled Story_Books
  before my upload was saved, so it uploaded Chapter 1 again for 3 Nov 15:00 (yX-jxfHW3_g + aYVFG8Ehq0Q, playlist
  PLKjIsnYicKpo) and its progress.json overwrote mine. Owner: no such mistakes again, and be smart even for laptop
  uploads. Fixed: `plan()` first runs `reconcile()` (episodes already on the channel by title count as uploaded,
  with their date and saved summary, so the next one is picked); a laptop run doesn't upload while a GitHub run is
  queued or in progress (`github_run_active`, GitHub API with GITHUB_TOKEN); the uploads list is re-read right
  before each upload; an existing playlist with the same title is reused. Duplicates kept (owner: delete nothing).
- 3 Nov: Chapter 2 (6:29) waited all day because Gemini's free quota (shared with the other channels) was used up
  for the picture check. Fixed: unchecked pictures are used (noted in Telegram) instead of holding the episode.
- Verified locally 2 Nov (dry run): Chapter 1 8:48 (1920x1080), teaser Short 42 s, 36 pictures all passing the new
  check. Loudness was -21.7 dB at loudnorm -19, so the target is -17.5 LUFS (not re-measured yet).

## Historical status and run history
- **Shipped on `main`** (newest first): Telegram inbox (share a video or ask for one; 2026-10-31); `9aebfaf` search
  keywords, audio leveling, kids BGM and SFX cues, animal game-show scripts, description credits for Commons only;
  `8ca9d2f` WonderSprout's real character, intro/outro and stricter footage matching; `8fba579` WonderSprout host, Frontline real photos, lazy YouTube searches; `b0e5164` real footage,
  expressive voices (Ava, Brian), faster cuts, trending hashtags, kids 3 long + 2 Shorts; `e426569` view techniques,
  Analyst, `--learn`, Groq backup; `0736318` / `f0f8d7e` three runs a day, Telegram, title fallback.
- **Run history:**
  - 18 to 20 Oct: scheduled runs succeeded, starting 4h22m to 5h28m after their cron times.
  - 20 and 21 Oct: two runs uploaded nothing (Gemini 429 on every model); fixed by the title fallback, fewer Gemini
    requests and the Groq backup.
  - 21 Oct 14:56 IST manual run 39576955716: 5 of 6 uploaded; one kids Short hit YouTube's per-channel upload limit.
  - 22 Oct morning run (started 04:32 IST, ~3 h after its cron): Frontline 3 of 3, WonderSprout 1 of 3 (Gemini
    quota; the reason for the Groq backup and the yellow warning).
  - 24 Oct GitHub test run 40071143012 (test run, no uploads): real Commons footage and the new voices worked on
    GitHub; the kids Short failed because local testing had used up the day's 100 YouTube searches (since fixed).
    Its 3 finished test videos are "edited" in the Drive manifest and will be uploaded by the next real runs.
  - The WonderSprout host, Frontline photos and stricter matching were verified locally only; not yet seen in a
    GitHub run.
- **Open items:**
  - Ask the owner for Telegram messages / Actions results from the next runs: check the WonderSprout character,
    intro and outro, real photos on Frontline, start times near 00:00/13:00/17:00 (tune
    `github_schedule_lead_minutes` per fetch time), and that all 6 videos upload; read the footage-check lines and
    the sync report of the first runs after 5 Nov.
  - The owner's intro/outro are 474x642 (tall); in long videos they show on a blurred background. Suggested
    re-exporting them at 1920x1080.
  - YouTube daily upload limit: the owner was advised to phone-verify both channels at youtube.com/verify.
  - Views were near zero on 22 Oct; the owner was asked to check YouTube Studio → Analytics → Reach (impressions).
  - The owner's Gmail is public as the author of every commit; they were told about GitHub's "Keep my email
    addresses private" setting. History was not rewritten.
- **Optional clean-up:** some early test videos on YouTube are unlisted; the owner was advised to delete or publish
  them in Studio. Laptop test videos are "edited" in the local manifest only (they upload only on a laptop run).
- **Possible future work:** Instagram/TikTok examples by sending the video file to the bot (Gemini Files API), more
  channels (add to both YAML files and run `--authorize`; with ~1 search per video
  the YouTube search limit allows many more channels), YouTube Analytics scope for retention/CTR in the Analyst,
  and tuning scene matching from the sync reports.
