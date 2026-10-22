import os
import re
import html
import json
import random
import unicodedata
from datetime import datetime, timedelta, timezone
from googleapiclient.discovery import build

from agents.agent_8_analyst import order_phrases

# YouTube's relevanceLanguage codes, so results favour videos in the channel's narration language
LANGUAGE_CODES = {"English": "en", "Hindi": "hi", "Telugu": "te"}
# Don't reuse a source channel that supplied any of this channel's last N videos (unless nothing else is left)
RECENT_SOURCES_WINDOW = 5
# Letters of each narration language's own script; a source title written in another script is another audience
SCRIPTS = {"Hindi": (0x0900, 0x097F), "Telugu": (0x0C00, 0x0C7F)}
# Everyday Hindi words that rarely appear in English titles; two or more mark a Hinglish video
HINGLISH_WORDS = {"hai", "hain", "ke", "ki", "ka", "ko", "se", "mein", "aur", "kya", "nahi", "wala", "wali", "wale",
                  "gaya", "gayi", "bhi", "kahani", "kahaniyan", "bachpan", "bachpana", "dost", "pani", "jadu", "jaadu",
                  "bacche", "bachon", "masti", "apna", "apni", "hum", "tum", "kaise", "kyun", "ghar", "gaon"}

# A title naming its language ("Tamil Kids Story") is for that language's audience
OTHER_LANGUAGES = {"hindi", "telugu", "tamil", "bengali", "bangla", "urdu", "malayalam", "kannada", "marathi",
                   "gujarati", "punjabi", "odia", "arabic", "indonesian", "spanish", "portuguese"}


def is_latin(char):
    """Latin letters, including accented ones and the 'bold' or 'italic' maths letters used in fancy titles."""
    return ord(char) < 0x250 or unicodedata.name(char, "").startswith("MATHEMATICAL")


def views_per_hour(video, now=None):
    """How fast a video is gaining views: views since upload divided by hours since upload (at least 1)."""
    published = datetime.fromisoformat(video["snippet"]["publishedAt"].replace("Z", "+00:00"))
    hours = max(((now or datetime.now(timezone.utc)) - published).total_seconds() / 3600, 1)
    return int(video.get("statistics", {}).get("viewCount", 0)) / hours


def language_matches(video, language):
    """True or False if the source video is or isn't in the channel's language, None if it can't be told.

    Uses the language the uploader set, then the writing script of the title: an English channel shouldn't take
    topics from a Hindi video (a different audience, and its title is no use as a fallback title).
    """
    code = LANGUAGE_CODES.get(language)
    snippet = video["snippet"]
    declared = (snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage") or "").lower()
    if code and declared and not declared.startswith("zxx") and declared.split("-")[0] != code:
        return False
    # Uploaders often leave the language as English, so the title is checked too
    title = snippet.get("title", "")
    letters = [c for c in title if c.isalpha()]
    if not letters:
        return None
    if language in SCRIPTS:
        low, high = SCRIPTS[language]
        return any(low <= ord(c) <= high for c in letters) or None
    # English: no words in another script (Devanagari, Telugu, Bengali...), and not Hindi written in English
    # letters ("Hinglish"), spotted by its common words in the title and the start of the description
    if sum(not is_latin(c) for c in letters) >= 3:
        return False
    words = set(re.findall(r"[a-z]+", f"{title} {snippet.get('description', '')[:300]}".lower()))
    title_words = set(re.findall(r"[a-z]+", title.lower()))
    return len(words & HINGLISH_WORDS) < 2 and not title_words & OTHER_LANGUAGES

class TrendScoutAgent:
    def __init__(self, config, instructions):
        self.config = config
        self.instructions = instructions
        self.active_key = config["active_profile"]
        self.profile = config["profiles"][self.active_key]
        self.manifest_path = config["storage_paths"]["manifest_file"]
        self.search_cache = {}

        api_key = os.getenv("YOUTUBE_API_KEY")
        if not api_key:
            raise ValueError("Missing YOUTUBE_API_KEY in .env file.")
        self.youtube = build("youtube", "v3", developerKey=api_key)

    def search(self, query, published_after):
        # Cached per run: retries reuse the same results instead of spending more of the daily search quota
        if query not in self.search_cache:
            long_form = self.profile.get("format") == "long"
            params = dict(
                part="snippet",
                # Long videos take their topics from longer trending videos (4 to 20 minutes)
                q=query.replace("#shorts", "").strip() if long_form else query,
                type="video",
                videoDuration="medium" if long_form else "short",
                order="viewCount",
                publishedAfter=published_after,
                maxResults=50,  # same quota cost as 1, and leaves room to skip used or repeated sources
            )
            language = LANGUAGE_CODES.get(self.profile.get("language", "English"))
            if language:
                params["relevanceLanguage"] = language
            found = self.youtube.search().list(**params).execute().get("items", [])
            self.search_cache[query] = self.rank(found)
        return self.search_cache[query]

    def rank(self, items):
        """Rising videos first (views per hour), in the channel's language first; costs 1 quota unit per 50 videos."""
        details = {}
        ids = [i["id"]["videoId"] for i in items]
        for start in range(0, len(ids), 50):
            response = self.youtube.videos().list(part="snippet,statistics", id=",".join(ids[start:start + 50])).execute()
            details.update({v["id"]: v for v in response.get("items", [])})
        language = self.profile.get("language", "English")
        ranked = []
        for item in items:
            video = details.get(item["id"]["videoId"])
            if not video:
                continue
            item["views_per_hour"] = round(views_per_hour(video), 1)
            item["language_ok"] = language_matches(video, language) is not False
            ranked.append(item)
        return sorted(ranked, key=lambda i: (not i["language_ok"], -i["views_per_hour"]))

    def run(self, limit=None):
        """Add up to `limit` new videos (default: the channel's videos per day) to the manifest.

        Picks rotate across the search phrases, take at most one video per source channel, skip sources
        used for this channel recently, and never use channels listed in exclude_sources.
        """
        print(f"\n[Agent 1: Scout] Searching trending videos for profile: {self.active_key}...")
        published_after = (datetime.now(timezone.utc) - timedelta(days=self.profile.get("lookback_days", 5))).isoformat()

        existing_trends = []
        if os.path.exists(self.manifest_path):
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                try:
                    existing_trends = json.load(f)
                except json.JSONDecodeError:
                    existing_trends = []

        existing_ids = {item["video_id"] for item in existing_trends}
        mine = [item for item in existing_trends if item.get("profile") == self.active_key]
        recent_sources = {item.get("original_creator", "").lower() for item in mine[-RECENT_SOURCES_WINDOW:]}
        excluded = {name.lower() for name in self.profile.get("exclude_sources") or []}
        max_per_run = limit or self.profile["max_videos_per_run"]

        # Phrases whose videos got the most views come first; untried or long-unused phrases are tried again
        queries = order_phrases(self.profile["search_queries"],
                                [i for i in mine if i.get("format", "short") == self.profile.get("format", "short")])
        candidates = {query: self.search(query, published_after) for query in queries}

        new_items, used_sources = [], set()
        # Rising, same-language sources first; a recently used source or another language only if nothing else
        for allow_recent, allow_language in ((False, False), (True, False), (True, True)):
            # Round-robin: best unused video from phrase 1, then phrase 2, ... until enough are picked
            progress = True
            while len(new_items) < max_per_run and progress:
                progress = False
                for query in queries:
                    if len(new_items) >= max_per_run:
                        break
                    pick = self.next_candidate(candidates[query], existing_ids, used_sources, excluded,
                                               set() if allow_recent else recent_sources, allow_language)
                    if pick:
                        new_items.append(self.entry(pick, query))
                        existing_ids.add(pick["id"]["videoId"])
                        used_sources.add(pick["snippet"]["channelTitle"].lower())
                        progress = True
            if len(new_items) >= max_per_run:
                break

        all_trends = existing_trends + new_items
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(all_trends, f, indent=4, ensure_ascii=False)

        sources = ", ".join(f"{i['original_creator']} ({i['genre']})" for i in new_items) or "none"
        print(f"[Agent 1: Scout] Added {len(new_items)} new trending videos. Sources: {sources}")

    @staticmethod
    def next_candidate(items, existing_ids, used_sources, excluded, recent_sources, allow_language=True):
        for item in items:
            source = item["snippet"]["channelTitle"].lower()
            if (item["id"]["videoId"] in existing_ids or source in used_sources
                    or source in excluded or source in recent_sources
                    or not (allow_language or item.get("language_ok", True))):
                continue
            return item
        return None

    def entry(self, item, query):
        vid_id = item["id"]["videoId"]
        entry_format = self.profile.get("format", "short")
        minutes = str(self.profile.get("long", {}).get("minutes", 6))
        if entry_format == "long":
            # A range such as "5-6" gives every long video its own length, never longer than the upper bound
            low, _, high = minutes.partition("-")
            low, high = int(low), int(high or low)
            target_seconds = random.randint(low * 60, high * 60 - 20) if high > low else low * 60 - 10
            max_seconds = high * 60 - 5  # leaves room for the end card within the upper limit
        else:
            target_seconds = self.profile.get("target_duration_seconds", 30)
            max_seconds = int(target_seconds * 1.2)
        return {
            "video_id": vid_id,
            "profile": self.active_key,
            "format": entry_format,
            "target_seconds": target_seconds,
            "max_seconds": max_seconds,
            "genre": query,
            "source_views_per_hour": item.get("views_per_hour"),
            # The search API returns HTML-escaped text (e.g. &#39; and &amp;)
            "original_title": html.unescape(item["snippet"]["title"]),
            "original_description": html.unescape(item["snippet"]["description"]),
            "original_creator": html.unescape(item["snippet"]["channelTitle"]),
            "source_url": f"https://www.youtube.com/watch?v={vid_id}",
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "status": "discovered",
            "raw_video_path": None,
            "voiceover_path": None,
            "generated_script": None,
            "edited_video_path": None,
            "uploaded_youtube_id": None,
            "notified_telegram": False
        }
