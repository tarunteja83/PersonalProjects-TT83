import os
import re
import json
import asyncio
import edge_tts
from google import genai

from agents import expressive_voice
from agents.ai import generate_text
from agents.stock_footage import TERM_PAIR_RULE, footage_rules, parse_scene_hints
from brain import item_matches

# Roughly 150 spoken words per minute, a pace most listeners follow comfortably
WORDS_PER_SECOND = 2.5
# Scripts more than 20% over their target word count are shortened
LENGTH_TOLERANCE = 1.2
# Narration longer than the video's maximum length (times this factor) is shortened and narrated again
DURATION_TOLERANCE = 1.0
# Stock footage changes scene about this often in long videos (footage.long_scene_seconds in BRAIN.md);
# Shorts use footage.clips_per_video scenes
LONG_SCENE_SECONDS = 8


def stock_mode(config, profile):
    """Stock-footage videos are scripted first, then footage is chosen to match each scene of the script."""
    return (config.get("footage") or {}).get("source", "stock") == "stock" or profile.get("format") == "long"


def group_scenes(sentences, total_seconds, scene_seconds):
    """Group timed sentences into scenes of about scene_seconds; each scene gets one stock clip.

    sentences: [(start_seconds, text)]. Returns [{"start", "end", "text"}] covering 0..total_seconds.
    """
    if not sentences:
        return [{"start": 0.0, "end": total_seconds, "text": ""}]
    scenes, current = [], {"start": 0.0, "text": []}
    for index, (start, text) in enumerate(sentences):
        if current["text"] and start - current["start"] >= scene_seconds:
            scenes.append({"start": current["start"], "end": start, "text": " ".join(current["text"])})
            current = {"start": start, "text": []}
        current["text"].append(text)
    scenes.append({"start": current["start"], "end": total_seconds, "text": " ".join(current["text"])})
    # Fold a very short final scene into the one before it
    if len(scenes) > 1 and scenes[-1]["end"] - scenes[-1]["start"] < scene_seconds / 3:
        last = scenes.pop()
        scenes[-1]["end"], scenes[-1]["text"] = last["end"], scenes[-1]["text"] + " " + last["text"]
    return scenes


META_FIELDS = {"TITLE": "seo_title", "DESCRIPTION": "seo_description", "HASHTAGS": "seo_hashtags",
               "FOOTAGE": "topic_terms", "SCENES": "scene_hints"}


def parse_script_reply(text):
    """Split the AI's reply into (narration, {seo_title, seo_description, seo_hashtags, topic_terms, scene_hints}).

    A field's value may continue on the following lines (some models list scenes or terms one per line). If the
    reply ignores the layout, the whole reply is the narration and the publisher writes the title later.
    """
    head, marker, body = text.partition("SCRIPT:")
    if not marker:
        return text.strip(), {}
    parts, field = {}, None
    for line in head.splitlines():
        label, colon, value = line.strip().lstrip("*# ").partition(":")
        if colon and label.strip("* ").upper() in META_FIELDS:
            field = META_FIELDS[label.strip("* ").upper()]
            parts[field] = [value]
        elif field and line.strip():
            # A continuation line: drop list numbering or bullets such as "1." or "-"
            parts[field].append(re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", line))
    joiners = {"scene_hints": " ; ", "topic_terms": ", "}
    meta = {}
    for field, values in parts.items():
        value = joiners.get(field, " ").join(v.strip().strip("*").strip().strip('"') for v in values if v.strip())
        if value:
            meta[field] = value
    if "topic_terms" in meta:
        meta["topic_terms"] = [t.strip().strip('"') for t in meta["topic_terms"].split(",") if t.strip()]
    if "scene_hints" in meta:
        meta["scene_hints"] = parse_scene_hints(meta["scene_hints"]) or None
        if not meta["scene_hints"]:
            del meta["scene_hints"]
    return body.strip().strip("*").strip(), meta


class VoiceoverAgent:
    def __init__(self, config, instructions):
        self.config = config
        self.instructions = instructions
        self.active_key = config["active_profile"]
        self.profile = config["profiles"][self.active_key]
        self.paths = config["storage_paths"]
        self.voice = self.profile.get("voice", "en-US-AndrewNeural")
        self.voice_rate = self.profile.get("voice_rate", "-5%")

        gemini_key = os.getenv("GEMINI_API_KEY")
        if not gemini_key:
            raise ValueError("Missing GEMINI_API_KEY in .env file.")
        self.ai_client = genai.Client(api_key=gemini_key)

    def generate_script(self, item):
        target_seconds = item.get("target_seconds") or self.profile.get("target_duration_seconds", 30)
        target_words = int(target_seconds * WORDS_PER_SECOND)
        avoid_terms = self.profile.get("avoid_terms") or []
        if item.get("format") == "long":
            task = (
                f"Task: Write an original, engaging narration script of about {target_words} words (about "
                f"{round(target_seconds / 60)} minutes spoken) for a regular YouTube video, not a Short. Open with a "
                "strong hook, then 3 to 5 clearly separated parts (a story with a beginning, middle and end, or an "
                "explanation that builds step by step), keep a lively pace throughout, and end with a short recap and "
                "a question inviting viewers to comment. Describe concrete, visual things (places, objects, animals, "
                "people, actions) so matching footage can be shown for every sentence.\n"
            )
        else:
            task = (
                f"Task: Write an original, high-retention narration script of about {target_words} words about this "
                "topic for a YouTube Short: commentary, explanation, or a short funny story, whichever fits the "
                "channel's tone and the master instructions.\n"
            )

        prompt = (
            f"Follow these master BRAIN instructions strictly:\n{self.instructions}\n\n"
            f"Profile: {self.active_key}\n"
            f"Channel Niche: {self.profile.get('niche', self.active_key)}\n"
            f"Required Tone: {self.profile['tone']}\n"
            f"Script Language: {self.profile.get('language', 'English')} (write in its native script)\n"
            f"Source Video Title: {item['original_title']}\n"
            f"Source Video Context: {item['original_description'][:300]}\n\n"
            + task
        )
        if avoid_terms:
            prompt += f"Never use these words or topics: {', '.join(avoid_terms)}.\n"
        long = item.get("format") == "long"
        prompt += (
            f"Stay within {target_words} words; a little shorter is fine, longer is not. The narration must be "
            "only spoken text: no stage directions, no brackets, no intro labels.\n\n"
            # The upload title and footage ideas come from this same request, so each video costs one call
            "Reply in exactly this layout:\n"
            f"TITLE: a {'click-worthy YouTube video' if long else 'viral YouTube Short'} title, at most "
            f"{70 if long else 60} characters, no quotes, in the script language\n"
            f"DESCRIPTION: a {3 if long else 2}-sentence SEO description in the script language, with no credits "
            "or source names (credits are added automatically)\n"
            "HASHTAGS: 5 hashtags separated by spaces that people actively search on YouTube for this topic right "
            "now: 2 broad, popular ones for the niche and 3 specific to this story (no spaces inside a hashtag"
            f"{', do not use #shorts' if long else ''})\n"
            "FOOTAGE: 5 English stock-footage search terms (1 to 3 words each, comma-separated) for visuals "
            "that match the whole topic"
            + (", child-friendly" if self.profile.get("made_for_kids") else "") + "\n"
        )
        if not long:
            # A Short has a few equal scenes, so footage for each is chosen here instead of in a second request
            clips = int((self.config.get("footage") or {}).get("clips_per_video", 6))
            prompt += (
                f"SCENES: split the narration into {clips} equal parts in order and, for each part, give "
                + TERM_PAIR_RULE + footage_rules(self.profile)
                + f" Write them on this one line as 'specific | backup' pairs separated by ' ; ' ({clips} pairs)\n"
            )
        prompt += "SCRIPT:\n<the narration>"

        script, meta = parse_script_reply(generate_text(self.ai_client, prompt, self.config.get("ai_models")))
        item.update(meta)
        return self.fit_length(script, target_words)

    def fit_length(self, script, target_words):
        """Keep the video near its target length: Gemini sometimes writes far more than asked."""
        limit = int(target_words * LENGTH_TOLERANCE)
        if len(script.split()) <= limit:
            return script
        print(f"[Agent 3: Voiceover] Script is {len(script.split())} words (target {target_words}); shortening...")
        try:
            script = generate_text(self.ai_client, (
                f"Shorten this narration script to at most {target_words} words. Keep the opening hook, the main "
                "story or explanation, and the ending (recap and question). Same language and tone. Output ONLY "
                f"the shortened narration text.\n\n{script}"
            ), self.config.get("ai_models"))
        except Exception as e:
            print(f"[Agent 3: Voiceover] Could not shorten with Gemini ({e}); trimming instead.")
        words = script.split()
        if len(words) <= limit:
            return script
        # Last resort: cut at the final sentence ending before the limit
        trimmed = " ".join(words[:limit])
        cut = max(trimmed.rfind(mark) for mark in (". ", "! ", "? ", "। "))
        return trimmed[:cut + 1] if cut > len(trimmed) // 2 else trimmed

    def synthesize_voice(self, script_text, video_id):
        """Narrate the script; returns (mp3 path, spoken seconds, [(sentence start seconds, sentence text)])."""
        out_mp3 = os.path.join(self.paths["audio_renders"], f"vo_{self.active_key}_{video_id}.mp3")
        expression = self.profile.get("voice_expression", "off")
        print(f"[Agent 3: Voiceover] Narrating video {video_id} with {self.voice} ({self.voice_rate}, "
              f"{expression} expression)...")
        if expression != "off":
            try:
                seconds, sentences = expressive_voice.narrate(script_text, self.voice, self.voice_rate, expression,
                                                              out_mp3)
                return out_mp3, seconds + 0.3, sentences
            except Exception as e:
                print(f"[Agent 3: Voiceover] Expressive narration failed ({e}); narrating in one pass instead.")

        async def narrate():
            communicate = edge_tts.Communicate(script_text, self.voice, rate=self.voice_rate,
                                               boundary="SentenceBoundary")
            sentences, end = [], 0.0
            with open(out_mp3, "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "SentenceBoundary":
                        start = chunk["offset"] / 1e7
                        sentences.append((start, chunk["text"]))
                        end = max(end, start + chunk["duration"] / 1e7)
            return sentences, end

        sentences, end = asyncio.run(narrate())
        return out_mp3, end + 0.5, sentences

    def narrate_within_limit(self, item, script):
        """Narrate, and if the spoken length runs past the video's maximum, shorten the script and narrate again."""
        max_seconds = item.get("max_seconds")
        vo_path, seconds, sentences = self.synthesize_voice(script, item["video_id"])
        if max_seconds and seconds > max_seconds * DURATION_TOLERANCE:
            words = len(script.split())
            # Aim for the video's target length (inside the allowed range), not just under the maximum
            goal_seconds = min(item.get("target_seconds") or max_seconds, max_seconds)
            target_words = int(words * goal_seconds / seconds)
            print(f"[Agent 3: Voiceover] Narration is {seconds / 60:.1f} min, over the {max_seconds / 60:.1f} min "
                  f"limit; shortening to about {target_words} words...")
            script = self.fit_length(script, target_words) if words > target_words * LENGTH_TOLERANCE else script
            if len(script.split()) > target_words:
                script = " ".join(script.split()[:target_words])
            vo_path, seconds, sentences = self.synthesize_voice(script, item["video_id"])
        return script, vo_path, seconds, sentences

    def run(self):
        manifest_path = self.paths["manifest_file"]
        with open(manifest_path, "r", encoding="utf-8") as f:
            trends = json.load(f)

        # Stock videos are scripted straight after discovery (footage is chosen afterwards to match the script);
        # re-used trending videos are scripted after their download
        stock = stock_mode(self.config, self.profile)
        wanted_status = "discovered" if stock else "downloaded"
        scene_seconds = float((self.config.get("footage") or {}).get("long_scene_seconds", LONG_SCENE_SECONDS))

        for item in trends:
            if item["status"] == wanted_status and item_matches(item, self.active_key, self.profile):
                vid_id = item["video_id"]
                try:
                    script = self.generate_script(item)
                    script, vo_path, seconds, sentences = self.narrate_within_limit(item, script)
                    item["generated_script"] = script
                    item["voiceover_path"] = vo_path
                    item["voice_seconds"] = round(seconds, 2)
                    if stock:
                        if item.get("format") != "long":
                            scene_seconds = seconds / int((self.config.get("footage") or {}).get("clips_per_video", 6))
                        item["scenes"] = group_scenes(sentences, seconds, scene_seconds)
                        print(f"[Agent 3: Voiceover] {seconds / 60:.1f} min of narration in {len(item['scenes'])} scenes")
                    item["status"] = "voiced"
                except Exception as e:
                    print(f"[Agent 3: Voiceover] Failed on {vid_id}: {e}")

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(trends, f, indent=4, ensure_ascii=False)
