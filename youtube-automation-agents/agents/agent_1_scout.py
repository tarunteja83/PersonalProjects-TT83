import os
import re
import html
import json
import random
import unicodedata
from datetime import datetime, timedelta, timezone

import requests
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from agents.agent_8_analyst import order_phrases
from agents.inbox import boosted_phrases, waiting_requests

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


# Hashtags that say nothing about the topic; YouTube treats piles of them as spam
GENERIC_HASHTAGS = {"#shorts", "#short", "#viral", "#trending", "#trend", "#youtube", "#youtubeshorts", "#ytshorts",
                    "#fyp", "#foryou", "#foryoupage", "#explore", "#explorepage", "#viralvideo", "#viralshorts",
                    "#reels", "#subscribe", "#like", "#shortsfeed", "#shortvideo", "#shortsvideo", "#new", "#video",
                    "#usashorts", "#viralshort", "#trendingshorts", "#feed", "#reel"}
TRENDING_POOL = 15
TRENDING_KEEP = 8
# Search phrases handed to the script AI and the upload tags: rising videos' keyword tags and YouTube's autocomplete
KEYWORDS_KEEP = 10
SUGGESTIONS_KEEP = 8


def trending_hashtags(videos, now=None):
    """The hashtags the fastest-rising videos on a search use, weighted by their views per hour.

    videos: [(video details from videos.list, views per hour)] of same-language sources. Generic tags, other
    channels' names and tags not written in Latin letters are left out.
    """
    weights, spelling = {}, {}
    for video, speed in sorted(videos, key=lambda v: -v[1])[:TRENDING_POOL]:
        snippet = video["snippet"]
        brand = re.sub(r"\W", "", snippet.get("channelTitle", "").lower())
        seen = set()
        for tag in re.findall(r"#\w+", f"{snippet.get('title', '')} {snippet.get('description', '')}"):
            key = tag.lower()
            other_script = not all(is_latin(c) for c in tag[1:] if c.isalpha())
            if (key in seen or key in GENERIC_HASHTAGS or len(key) < 4 or other_script
                    or (brand and (brand in key or key.lstrip("#") in brand))):
                continue
            seen.add(key)
            weights[key] = weights.get(key, 0) + 1 + speed
            spelling.setdefault(key, tag)
    return [spelling[k] for k in sorted(weights, key=lambda k: -weights[k])[:TRENDING_KEEP]]


def trending_keywords(videos):
    """The keyword tags (YouTube's hidden search tags) the fastest-rising videos use, weighted by views per hour.

    Same pool and filters as trending_hashtags; single words and very long tags are left out, since search
    phrases of 2 to 6 words are what viewers type.
    """
    weights, spelling = {}, {}
    for video, speed in sorted(videos, key=lambda v: -v[1])[:TRENDING_POOL]:
        snippet = video["snippet"]
        brand = re.sub(r"\W", "", snippet.get("channelTitle", "").lower())
        for tag in dict.fromkeys(t.strip() for t in snippet.get("tags") or []):
            key = " ".join(tag.lower().split())
            if (not 2 <= len(key.split()) <= 6 or len(key) > 50 or "#" + key.replace(" ", "") in GENERIC_HASHTAGS
                    or not all(is_latin(c) for c in key if c.isalpha())
                    or (brand and brand in key.replace(" ", ""))):
                continue
            weights[key] = weights.get(key, 0) + 1 + speed
            spelling.setdefault(key, key)
    return [spelling[k] for k in sorted(weights, key=lambda k: -weights[k])[:KEYWORDS_KEEP]]


def search_suggestions(phrase, language="English"):
    """What people type into YouTube search starting with this phrase (YouTube's autocomplete), or [].

    Free and outside the YouTube Data API quota; it's an unofficial endpoint, so any failure just means no
    suggestions.
    """
    words = re.sub(r"#\w+", "", phrase).split()
    # A long phrase gets few suggestions, so its first three words are asked too
    queries = [" ".join(words)] + ([" ".join(words[:3])] if len(words) > 3 else [])
    other_languages = OTHER_LANGUAGES - {language.lower()}
    found = []
    for query in filter(None, queries):
        try:
            reply = requests.get("https://suggestqueries.google.com/complete/search",
                                 params={"client": "firefox", "ds": "yt", "q": query,
                                         "hl": LANGUAGE_CODES.get(language, "en")}, timeout=10)
            reply.raise_for_status()
            suggestions = reply.json()[1]
        except Exception as e:
            print(f"[Agent 1: Scout] No search suggestions for '{query}' ({str(e)[:80]})")
            continue
        # "... in hindi" or "... news tamil" is another language's audience
        found += [s for s in suggestions if isinstance(s, str) and s.lower() not in queries
                  and not set(s.lower().split()) & other_languages]
        if len(found) >= SUGGESTIONS_KEEP // 2:
            break
    return list(dict.fromkeys(found))[:SUGGESTIONS_KEEP]


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
        self.suggestion_cache = {}
        self.out_of_searches = False

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
            if self.out_of_searches:
                return []
            try:
                found = self.youtube.search().list(**params).execute().get("items", [])
            except HttpError as e:
                if e.resp.status not in (403, 429) or "quota" not in str(e).lower():
                    raise
                # YouTube's 100 searches a day are used up: carry on with the videos already found or finished
                print("[Agent 1: Scout] YouTube's daily search limit is used up (it resets at midnight Pacific "
                      "time); no new topics this run.")
                self.out_of_searches = True
                return []
            self.search_cache[query] = self.rank(found)
        return self.search_cache[query]

    def suggestions(self, query):
        # Cached per run, like the searches
        if query not in self.suggestion_cache:
            self.suggestion_cache[query] = search_suggestions(query, self.profile.get("language", "English"))
        return self.suggestion_cache[query]

    def rank(self, items):
        """Rising videos first (views per hour), in the channel's language first; costs 1 quota unit per 50 videos."""
        details = {}
        ids = [i["id"]["videoId"] for i in items]
        for start in range(0, len(ids), 50):
            response = self.youtube.videos().list(part="snippet,statistics", id=",".join(ids[start:start + 50])).execute()
            details.update({v["id"]: v for v in response.get("items", [])})
        language = self.profile.get("language", "English")
        ranked, pool = [], []
        for item in items:
            video = details.get(item["id"]["videoId"])
            if not video:
                continue
            item["views_per_hour"] = round(views_per_hour(video), 1)
            item["language_ok"] = language_matches(video, language) is not False
            ranked.append(item)
            if item["language_ok"]:
                pool.append((video, item["views_per_hour"]))
        tags, keywords = trending_hashtags(pool), trending_keywords(pool)
        for item in ranked:
            item["trending_hashtags"] = tags
            item["trending_keywords"] = keywords
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

        # Phrases whose videos got the most views come first; untried or long-unused phrases are tried again.
        # The owner's recent Telegram requests lead for a few days, so the next videos follow that trend too
        queries = list(dict.fromkeys(boosted_phrases(self.active_key) + order_phrases(
            self.profile["search_queries"],
            [i for i in mine if i.get("format", "short") == self.profile.get("format", "short")])))
        # Phrases are searched only when the round-robin reaches them (each search costs 1 of YouTube's 100 a
        # day), so a run that needs one or two topics usually searches just one or two phrases

        new_items, used_sources = [], set()
        # Videos the owner asked for in Telegram take the first slots (a shared example, or a topic)
        for request in waiting_requests(self.active_key, self.profile.get("format", "short"), existing_ids):
            if len(new_items) >= max_per_run:
                break
            new_items.append(self.request_entry(request))
            existing_ids.add(request["video_id"])
        # Rising, same-language sources first; a recently used source or another language only if nothing else
        for allow_recent, allow_language in ((False, False), (True, False), (True, True)):
            # Round-robin: best unused video from phrase 1, then phrase 2, ... until enough are picked
            progress = True
            while len(new_items) < max_per_run and progress:
                progress = False
                for query in queries:
                    if len(new_items) >= max_per_run:
                        break
                    pick = self.next_candidate(self.search(query, published_after), existing_ids, used_sources,
                                               excluded,
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

    def request_entry(self, request):
        """A manifest entry for one of the owner's Telegram requests (agents/inbox.py)."""
        phrase = (request.get("search_phrases") or [request.get("topic") or request.get("title", "")])[0]
        source = {"id": {"videoId": request["video_id"]},
                  "snippet": {"title": request.get("title") or request.get("topic", ""),
                              "description": request.get("description", ""),
                              "channelTitle": request.get("creator", "")}}
        entry = self.entry(source, phrase)
        entry.update(request_id=request["id"], source_url=request.get("link") or "",
                     # What to make: the topic, and for a shared example what makes that video work
                     reference_notes=request.get("notes", ""), request_kind=request.get("kind"))
        print(f"[Agent 1: Scout] Making the owner's request {request['id']}: {entry['original_title']}")
        return entry

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
            # What the fastest-rising videos on this search are tagged with right now
            "trending_hashtags": item.get("trending_hashtags") or [],
            # Search phrases viewers type for this niche: YouTube's autocomplete, then rising videos' keyword tags
            "search_keywords": list(dict.fromkeys(self.suggestions(query) + (item.get("trending_keywords") or []))),
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
