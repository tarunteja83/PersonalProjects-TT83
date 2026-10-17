import os
import html
import json
import random
from datetime import datetime, timedelta, timezone
from googleapiclient.discovery import build

# YouTube's relevanceLanguage codes, so results favour videos in the channel's narration language
LANGUAGE_CODES = {"English": "en", "Hindi": "hi", "Telugu": "te"}
# Don't reuse a source channel that supplied any of this channel's last N videos (unless nothing else is left)
RECENT_SOURCES_WINDOW = 5

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
            self.search_cache[query] = self.youtube.search().list(**params).execute().get("items", [])
        return self.search_cache[query]

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

        queries = self.profile["search_queries"]
        # Start from a different phrase each run so single-video runs cover every topic over time
        offset = len(mine) % len(queries)
        queries = queries[offset:] + queries[:offset]
        candidates = {query: self.search(query, published_after) for query in queries}

        new_items, used_sources = [], set()
        for allow_recent in (False, True):
            # Round-robin: best unused video from phrase 1, then phrase 2, ... until enough are picked
            progress = True
            while len(new_items) < max_per_run and progress:
                progress = False
                for query in queries:
                    if len(new_items) >= max_per_run:
                        break
                    pick = self.next_candidate(candidates[query], existing_ids, used_sources, excluded,
                                               set() if allow_recent else recent_sources)
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
    def next_candidate(items, existing_ids, used_sources, excluded, recent_sources):
        for item in items:
            source = item["snippet"]["channelTitle"].lower()
            if (item["id"]["videoId"] in existing_ids or source in used_sources
                    or source in excluded or source in recent_sources):
                continue
            return item
        return None

    def entry(self, item, query):
        vid_id = item["id"]["videoId"]
        entry_format = self.profile.get("format", "short")
        minutes = self.profile.get("long", {}).get("minutes", 6)
        if entry_format == "long":
            # "5-7" gives every long video its own length between 5 and 7 minutes
            target_seconds = random.randint(300, 420) if minutes == "5-7" else int(minutes) * 60
        else:
            target_seconds = self.profile.get("target_duration_seconds", 30)
        return {
            "video_id": vid_id,
            "profile": self.active_key,
            "format": entry_format,
            "target_seconds": target_seconds,
            "genre": query,
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
