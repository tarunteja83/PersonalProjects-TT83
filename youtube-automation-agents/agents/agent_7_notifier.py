import os
import json
import requests
from datetime import datetime, timezone

class TelegramNotifierAgent:
    def __init__(self, config):
        self.config = config
        self.active_key = config["active_profile"]
        self.profile = config["profiles"][self.active_key]
        self.paths = config["storage_paths"]
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.channel_id = os.getenv("TELEGRAM_CHANNEL_ID")

    def send_message(self, html_text):
        if not self.bot_token or not self.channel_id:
            print("[Agent 7: Telegram] Skipping alert: Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID.")
            return

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.channel_id,
            "text": html_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                print("[Agent 7: Telegram] Alert posted to Telegram channel!")
            else:
                print(f"[Agent 7: Telegram] API Error: {resp.text}")
        except Exception as e:
            print(f"[Agent 7: Telegram] Failed to send Telegram alert: {e}")

    def run(self):
        manifest_path = self.paths["manifest_file"]
        if not os.path.exists(manifest_path):
            return

        with open(manifest_path, "r", encoding="utf-8") as f:
            trends = json.load(f)

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        published_today = [
            item for item in trends
            if item.get("profile") == self.active_key
            and item.get("status") == "published"
            and item.get("discovered_at", "")[:10] == today_str
            and not item.get("notified_telegram", False)
        ]

        if not published_today:
            msg = (
                f"ℹ️ <b>Pipeline Run Completed: {self.profile['channel_name']}</b>\n"
                f"• <b>Profile:</b> <code>{self.active_key}</code>\n"
                f"• <b>Status:</b> No new videos published in this cycle.\n"
                f"• <b>Storage Cleanup:</b> Old raw files purged ✅"
            )
            self.send_message(msg)
            return

        lines = [
            "🚀 <b>New YouTube Shorts Published!</b>",
            f"📺 <b>Channel:</b> {self.profile['channel_name']} (<code>{self.active_key}</code>)",
            f"📅 <b>Date:</b> {today_str}\n"
        ]

        for idx, item in enumerate(published_today, 1):
            yt_id = item.get("uploaded_youtube_id")
            yt_url = f"https://youtube.com/shorts/{yt_id}" if yt_id else item.get("source_url")
            clean_source = item.get("original_creator", "Unknown").replace("@", "")
            lines.append(
                f"<b>{idx}. {item.get('original_title')[:55]}...</b>\n"
                f"🔗 <a href='{yt_url}'>Watch Live Short on YouTube</a>\n"
                f"✍️ Credit Added: {clean_source}\n"
            )
            item["notified_telegram"] = True

        lines.append("🧹 <b>Storage Status:</b> Raw downloads & prior-date caches cleaned automatically.")
        self.send_message("\n".join(lines))

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(trends, f, indent=4, ensure_ascii=False)
