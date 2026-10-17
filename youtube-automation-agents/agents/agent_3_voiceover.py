import os
import json
import asyncio
import edge_tts
from google import genai

from agents.ai import generate_text
from brain import item_matches

# Roughly 150 spoken words per minute, a pace most listeners follow comfortably
WORDS_PER_SECOND = 2.5
# Scripts more than 20% over their target word count are shortened
LENGTH_TOLERANCE = 1.2

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
        if item.get("format") == "long":
            task = (
                f"Task: Write an original, engaging narration script of about {target_words} words (about "
                f"{round(target_seconds / 60)} minutes spoken) for a regular YouTube video, not a Short. Open with a "
                "strong hook, then 3 to 5 clearly separated parts (a story with a beginning, middle and end, or an "
                "explanation that builds step by step), keep a lively pace throughout, and end with a short recap and "
                "a question inviting viewers to comment.\n"
            )
        else:
            task = (
                f"Task: Write an original, high-retention narration script of about {target_words} words about this "
                "topic for a YouTube Short: commentary, explanation, or a short funny story, whichever fits the "
                "channel's tone and the master instructions.\n"
            )
        avoid_terms = self.profile.get("avoid_terms") or []

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
        prompt += (f"Stay within {target_words} words; a little shorter is fine, longer is not. "
                   "Output ONLY the spoken narration text. No stage directions, no brackets, no intro labels.")

        script = generate_text(self.ai_client, prompt, self.config.get("ai_models"))
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
        out_mp3 = os.path.join(self.paths["audio_renders"], f"vo_{self.active_key}_{video_id}.mp3")
        print(f"[Agent 3: Voiceover] Narrating video {video_id} with {self.voice} ({self.voice_rate})...")
        communicate = edge_tts.Communicate(script_text, self.voice, rate=self.voice_rate)
        asyncio.run(communicate.save(out_mp3))
        return out_mp3

    def run(self):
        manifest_path = self.paths["manifest_file"]
        with open(manifest_path, "r", encoding="utf-8") as f:
            trends = json.load(f)

        for item in trends:
            if item["status"] == "downloaded" and item_matches(item, self.active_key, self.profile):
                vid_id = item["video_id"]
                try:
                    script = self.generate_script(item)
                    vo_path = self.synthesize_voice(script, vid_id)
                    item["generated_script"] = script
                    item["voiceover_path"] = vo_path
                    item["status"] = "voiced"
                except Exception as e:
                    print(f"[Agent 3: Voiceover] Failed on {vid_id}: {e}")

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(trends, f, indent=4, ensure_ascii=False)
