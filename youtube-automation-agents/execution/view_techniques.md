# View Techniques

What we have learned about getting more views, and the rules every video follows because of it.

- **ACTIVE RULES** (below) are sent to Gemini with every script, title and hashtag request. Keep them short: every
  word is sent with every request.
- **HOW THE PIPELINE APPLIES THEM** lists the rules that are built into the code instead (topic choice, schedule,
  measurement). Changing those needs a code change.
- **SOURCES** records every video analysed, what it teaches, and what we adopted, did manually, or rejected.

**To analyse a new video:** `python execution/execution.py --learn <youtube link>` (laptop). It reads the video's
title, description, statistics and transcript, asks Gemini for the techniques, and adds an entry at the end of
SOURCES marked "proposed". Nothing changes until a proposal is reviewed: copy the good rules into ACTIVE RULES (or
ask Claude to), and mark the entry "reviewed". Never adopt anything that fakes engagement, misleads viewers or reuses
other people's videos; it breaks YouTube's policies and can get a channel removed.

## ACTIVE RULES
1. Ride the trend: open with what is new right now in this topic (today's development, the newest fact, the moment
   people are talking about), and name it in the title. A current topic beats a polished evergreen one. [S1]
2. Stay inside the channel's niche and speak to its core audience every time. If the source topic is off-niche,
   take the angle that fits the niche; never drift to an unrelated subject. [S1]
3. Write for retention (target: viewers stay for at least 30% of a long video and most of a Short) [S1]:
   - No greeting, intro or channel name at the start; the first sentence is the hook.
   - Make a promise in the first 5 seconds and pay it off only near the end.
   - Long videos: every 30 to 45 seconds add a new fact, twist or question; tease what is coming ("in a moment...").
   - Shorts: one idea, no filler; the last line should lead naturally back into the first so the Short loops.
4. Title for clicks without misleading [S1]: put the topic keyword people search for in the first 3 to 5 words,
   add curiosity or a number, stay under 60 characters (70 for long videos), and only promise what the video
   delivers.

## HOW THE PIPELINE APPLIES THEM
| Technique | Where | What it does |
|---|---|---|
| Trends [S1] | Agent 1, Scout | Ranks trending source videos by **views per hour since upload** (what is rising now), not total views. |
| One audience per channel [S1] | Agent 1, Scout | Skips source videos that aren't in the channel's language (an English channel no longer takes Hindi topics), unless nothing else is found. |
| Consistency [S1] | Schedule | Fixed daily go-live times in `questions.yaml`; finished videos that miss their run are uploaded in the next one. |
| Learn from results [S1] | Agent 8, Analyst | Records every uploaded video's views. Each channel's search phrases are ordered by the views their videos got after 48 hours (phrases not tried for 3 days are tried again first), and a daily Telegram report shows what works. |
| Retention and title rules [S1] | ACTIVE RULES above | Sent to Gemini with every script and title. |
| Real, current footage [S2] | Agent 2, Footage | Frontline (`footage_style: "real"`) shows real footage of the actual people, places and events from Wikimedia Commons (public domain, CC0, CC BY only), shown whole in Shorts. |
| Picture matches the words [S2] | Agent 2, Footage | Stock clips are only used if their tags match the scene's subject (not just "cartoon"); kids scenes with no matching video get a matching cartoon illustration; otherwise the backup term is tried. |
| Faster cuts [S2] | BRAIN.md `footage` | About 6 clips per Short (was 3) and a new clip about every 8 seconds in long videos (was 15). |
| A human-sounding voice [S2] | Agent 3, `expressive_voice.py` | Each sentence gets its own pitch, speed and volume (`voice_expression`: lively for kids, measured for news). |
| Fun, hooky kids stories [S2] | BRAIN.md Agent 3 instructions | Named heroes with a funny trait, a silly problem, three escalating tries, a twist; sound words; the viewer is asked something every 20 to 30 seconds. |
| A recognisable host for kids [S2] | Agent 4, `mascot.py` | WonderSprout, a woman superhero, narrates and reacts on screen (intro, expressions, outro); a recurring character gives kids a reason to come back. |
| Trending hashtags for reach [S2] | Agents 1 and 5 | The hashtags the fastest-rising niche videos use right now lead each description, followed by the topic's own (up to 12). |

**Not automated yet (owner):**
- Custom thumbnails strongly affect click-through, but YouTube only allows them on phone-verified channels
  (youtube.com/verify). Verify first; generating thumbnails could be added after.
- Average view duration and click-through rate are only in YouTube Analytics (Studio → Analytics), which this
  pipeline's login can't read yet. Check them weekly against the targets: about 30% average view duration on long
  videos and 5 to 10% click-through rate.
- Patience: the source's channel took 9 months of consistent uploads before it grew.

## SOURCES

### S1. "Why Your Views STOP After 48 Hours (learn how to fix it)" by Danny Why
- Link: https://youtu.be/-bIvqlrCBNI
- When analysed: 2026-10-22, by Claude from the full transcript (11:37 long; 1.2M views, 36K likes, 8.6K comments;
  published 2025-12-27)
- Status: reviewed

**What it teaches:**
1. **Trends beat polish.** His video with a lower click-through rate and shorter view duration got 3x the views of
   a "better" one, because it was about something people were talking about that week.
   → Adopted: ACTIVE RULES 1, Scout ranks by views per hour.
2. **One niche per channel.** YouTube shows a new video first to the audience of your previous videos; a different
   topic reaches the wrong people, gets low watch time and stops being pushed.
   → Adopted: ACTIVE RULES 2, Scout skips other-language sources. Note: WonderSprout mixes stories, animal facts and
   science facts; all are for the same kids audience, so it stays one niche.
3. **Consistency builds trust** with viewers and the algorithm (the "shop that is always open").
   → Already done: fixed daily schedule.
4. **Benchmarks** (his experience, not a YouTube rule): a 10-minute video needs about 30% average view duration to
   keep growing; once past about 100K impressions, click-through rate must stay at 5 to 10%.
   → Adopted as targets: ACTIVE RULES 3 and 4; the owner checks them in Studio (see "Not automated yet").
5. **Give it time;** growth often comes after months of steady uploads.
   → Noted for the owner.
6. Sponsor segments (a clipping tool, a paid community) are adverts, not techniques. → Skipped.

### S2. Owner's feedback from real viewers (2026-10-24)
- Source: the owner showed the channels to real viewers and reported their feedback; YouTube stats at the time:
  WonderSprout Shorts weren't engaging, a few WonderSprout long videos reached about 200 views, Frontline had almost
  no views.
- Status: reviewed

**What it teaches:**
1. **Frontline isn't interactive or "real-time":** narration over generic stock scenery doesn't feel like news, and
   the pictures often didn't match the words.
   → Adopted: real footage of the actual people and events (Wikimedia Commons), tag-matched stock clips, faster cuts.
   Copyrighted news-agency footage is not used (Content ID claims, copyright strikes).
2. **The voice sounds robotic** (one flat tone).
   → Adopted: sentence-by-sentence expression; voice samples in Google Drive `YouTube_Auto/voice_samples` for the
   owner to choose more natural voices (Ava, Emma, Andrew, Brian).
3. **Kids need fun and hooks, and long videos work better than Shorts** on WonderSprout.
   → Adopted: new kids writing rules; WonderSprout moved to 3 long videos and 2 Shorts a day (was 2 and 6).
4. **More reach from trending hashtags** in each niche.
   → Adopted: trending niche hashtags first in every description.
