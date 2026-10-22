"""Agent 8, Analyst: records how every uploaded video performs and feeds it back into topic choice.

After each run it reads the public view, like and comment counts of the channel's recent uploads (1 YouTube quota
unit per 50 videos) and stores them in the manifest. Each video's views at its first check after 48 hours
(views_48h) score the search phrase that found its topic, and the Scout tries the best-scoring phrases first.
Once a day it posts a short performance report to Telegram.
"""
import html
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Views are tracked for this many days after upload
TRACK_DAYS = 14
# A video's views are scored once it has been live this long
SCORE_HOURS = 48
# A phrase not used for this long is tried again first, so a phrase that did badly once gets another chance
STALE_DAYS = 3
# Only a phrase's most recent scored videos count, so its score follows current trends
SCORE_WINDOW = 8
REPORT_DAYS = 7


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def live_since(item):
    """When the video went public: its scheduled go-live time, or its upload time if it went live at once."""
    return parse_time(item.get("publish_at")) or parse_time(item.get("uploaded_at"))


def phrase_scores(items):
    """{phrase: average views_48h of its latest scored videos}."""
    scored = {}
    for item in sorted(items, key=lambda i: i.get("discovered_at", "")):
        if item.get("views_48h") is not None:
            scored.setdefault(item.get("genre"), []).append(item["views_48h"])
    return {phrase: sum(v[-SCORE_WINDOW:]) / len(v[-SCORE_WINDOW:]) for phrase, v in scored.items()}


def order_phrases(phrases, items, now=None):
    """Search phrases in the order the Scout should use them.

    Phrases that are untried, unscored or unused for STALE_DAYS come first (least recently used first), so every
    phrase keeps getting tested as trends change; then the rest, best average views first.
    """
    now = now or datetime.now(timezone.utc)
    scores = phrase_scores(items)
    last_used = {}
    for item in items:
        found = parse_time(item.get("discovered_at"))
        if found and (item.get("genre") not in last_used or found > last_used[item.get("genre")]):
            last_used[item.get("genre")] = found
    oldest = datetime.min.replace(tzinfo=timezone.utc)

    def explore(phrase):
        return phrase not in scores or now - last_used.get(phrase, oldest) > timedelta(days=STALE_DAYS)

    fresh = sorted((p for p in phrases if explore(p)), key=lambda p: last_used.get(p, oldest))
    proven = sorted((p for p in phrases if not explore(p)), key=lambda p: -scores[p])
    return fresh + proven


class AnalystAgent:
    def __init__(self, config):
        self.config = config
        self.paths = config["storage_paths"]
        self.state_file = os.path.join(os.path.dirname(self.paths["manifest_file"]), "analyst_state.json")

    def fetch_stats(self, video_ids):
        from googleapiclient.discovery import build
        youtube = build("youtube", "v3", developerKey=os.getenv("YOUTUBE_API_KEY"))
        stats = {}
        for start in range(0, len(video_ids), 50):
            response = youtube.videos().list(part="statistics", id=",".join(video_ids[start:start + 50])).execute()
            stats.update({v["id"]: v.get("statistics", {}) for v in response.get("items", [])})
        return stats

    def update(self, items, now=None):
        """Store current stats on every tracked video; returns how many were updated."""
        now = now or datetime.now(timezone.utc)
        tracked = {}
        for item in items:
            since = live_since(item)
            if (item.get("status") == "published" and item.get("uploaded_youtube_id") and since
                    and since <= now and now - since <= timedelta(days=TRACK_DAYS)):
                tracked[item["uploaded_youtube_id"]] = item
        if not tracked:
            return 0
        stats = self.fetch_stats(list(tracked))
        for yt_id, item in tracked.items():
            if yt_id not in stats:
                continue  # private, removed, or not processed yet
            s = stats[yt_id]
            hours = round((now - live_since(item)).total_seconds() / 3600, 1)
            item["stats"] = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                             "comments": int(s.get("commentCount", 0)), "hours_live": hours,
                             "checked_at": now.isoformat()}
            if hours >= SCORE_HOURS and item.get("views_48h") is None:
                item["views_48h"] = item["stats"]["views"]
        return len(tracked)

    def report(self, items, profiles, now=None):
        """Telegram text: last REPORT_DAYS days per channel, best video, best and weakest search phrase."""
        now = now or datetime.now(timezone.utc)
        lines = [f"📊 <b>Daily report</b> (videos live in the last {REPORT_DAYS} days)"]
        for key, profile in profiles.items():
            recent = [i for i in items if i.get("profile") == key and i.get("stats")
                      and now - live_since(i) <= timedelta(days=REPORT_DAYS)]
            if not recent:
                continue
            views = sum(i["stats"]["views"] for i in recent)
            best = max(recent, key=lambda i: i["stats"]["views"])
            lines.append(f"\n<b>{html.escape(profile['channel_name'])}</b>: {len(recent)} videos, {views:,} views "
                         f"(avg {views // len(recent):,})")
            title = html.escape((best.get("uploaded_title") or best.get("original_title") or "")[:60])
            lines.append(f"🏆 {title}: "
                         f"{best['stats']['views']:,} views ({best.get('format', 'short')})")
            scores = phrase_scores([i for i in items if i.get("profile") == key])
            if len(scores) >= 2:
                top, low = max(scores, key=scores.get), min(scores, key=scores.get)
                name = {p: html.escape(p.replace("#shorts", "").strip()) for p in (top, low)}
                lines.append(f"📈 Best topic: {name[top]} ({scores[top]:.0f} views at 48h)")
                lines.append(f"📉 Weakest: {name[low]} ({scores[low]:.0f})")
        return "\n".join(lines) if len(lines) > 1 else None

    def run(self, keys, now=None):
        now = now or datetime.now(timezone.utc)
        path = self.paths["manifest_file"]
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            items = json.load(f)
        try:
            count = self.update([i for i in items if i.get("profile") in keys], now)
        except Exception as e:
            print(f"[Agent 8: Analyst] Could not read video statistics: {e}")
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=4, ensure_ascii=False)
        print(f"[Agent 8: Analyst] Updated views for {count} recent video(s).")

        # One report a day, from the first run of the day in the channels' timezone
        profiles = {k: self.config["profiles"][k] for k in keys}
        tz = ZoneInfo(next(iter(profiles.values()))["schedule"]["timezone"])
        today = now.astimezone(tz).date().isoformat()
        state = {}
        if os.path.exists(self.state_file):
            with open(self.state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        if state.get("last_report") == today:
            return
        text = self.report(items, profiles, now)
        if text:
            from agents.agent_7_notifier import send_telegram
            send_telegram(text)
        state["last_report"] = today
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)
