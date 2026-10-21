import os
import html
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo


def send_telegram(html_text):
    """Post a message (Telegram HTML) to the channel in TELEGRAM_CHANNEL_ID; never stops the run on failure."""
    bot_token, channel_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHANNEL_ID")
    if not bot_token or not channel_id:
        print("[Agent 7: Telegram] Skipping alert: Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID.")
        return
    payload = {"chat_id": channel_id, "text": html_text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        resp = requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", json=payload, timeout=15)
        if resp.status_code == 200:
            print("[Agent 7: Telegram] Alert posted to Telegram channel!")
        else:
            print(f"[Agent 7: Telegram] API Error: {resp.text}")
    except Exception as e:
        # The exception text can contain the request URL, which includes the bot token
        print(f"[Agent 7: Telegram] Failed to send Telegram alert: {str(e).replace(bot_token, '***')}")


class TelegramNotifierAgent:
    def __init__(self, config):
        self.config = config
        self.active_key = config["active_profile"]
        self.profile = config["profiles"][self.active_key]
        self.paths = config["storage_paths"]

    def go_live_text(self, item):
        if not item.get("publish_at"):
            return "live now"
        tz = ZoneInfo(self.profile["schedule"]["timezone"])
        when = datetime.fromisoformat(item["publish_at"].replace("Z", "+00:00")).astimezone(tz)
        day = "today" if when.date() == datetime.now(tz).date() else f"{when:%d %b}"
        return f"goes live {day} {when:%H:%M} {when:%Z}"

    def run(self, uploaded, planned=None):
        """One message for this channel: the videos this run uploaded and when each one goes live."""
        uploaded = sorted(uploaded, key=lambda i: i.get("publish_at") or "")
        planned = planned if planned is not None else len(uploaded)
        icon = "✅" if uploaded and len(uploaded) >= planned else "⚠️"
        lines = [f"{icon} <b>{html.escape(self.profile['channel_name'])}</b>: "
                 f"{len(uploaded)} of {planned} video(s) uploaded and scheduled"]
        for idx, item in enumerate(uploaded, 1):
            long = item.get("format") == "long"
            yt_id = item.get("uploaded_youtube_id")
            url = f"https://youtube.com/{'watch?v=' if long else 'shorts/'}{yt_id}"
            title = item.get("uploaded_title") or item.get("original_title") or yt_id
            tags = " ".join(item.get("upload_hashtags") or [])
            lines.append(f"\n{idx}. {'🎬 Long video' if long else '📱 Short'}, {self.go_live_text(item)}\n"
                         f"<b>{html.escape(title)}</b>\n🔗 {url}" + (f"\n🏷 {html.escape(tags)}" if tags else ""))
        if len(uploaded) < planned:
            lines.append(f"\n{planned - len(uploaded)} video(s) were not uploaded. Check the GitHub run log, "
                         "step 8, for the [Agent ...] lines explaining why.")
        send_telegram("\n".join(lines))

        if not uploaded:
            return
        manifest_path = self.paths["manifest_file"]
        with open(manifest_path, "r", encoding="utf-8") as f:
            trends = json.load(f)
        ids = {item["video_id"] for item in uploaded}
        for item in trends:
            if item.get("video_id") in ids and item.get("profile") == self.active_key:
                item["notified_telegram"] = True
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(trends, f, indent=4, ensure_ascii=False)
