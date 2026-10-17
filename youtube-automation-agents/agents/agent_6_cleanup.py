import os
import json
from datetime import datetime, timezone

class CleanupAgent:
    def __init__(self, config):
        self.config = config
        self.paths = config["storage_paths"]
        self.cleanup_rules = config.get("cleanup_rules", {
            "delete_raw_after_publish": True,
            "delete_voiceover_after_publish": True,
            "delete_edited_from_previous_dates": True
        })

    def _safe_remove(self, file_path):
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                print(f"[Agent 6: Cleanup] Deleted file: {file_path}")
                return True
            except Exception as e:
                print(f"[Agent 6: Cleanup] Failed to delete {file_path}: {e}")
        return False

    def run(self):
        manifest_path = self.paths["manifest_file"]
        if not os.path.exists(manifest_path):
            return

        print("\n[Agent 6: Cleanup] Scanning manifest for published & previous-date videos...")
        with open(manifest_path, "r", encoding="utf-8") as f:
            trends = json.load(f)

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cleaned_count = 0

        for item in trends:
            if item.get("status") == "published":
                discovered_date = item.get("discovered_at", "")[:10]
                is_previous_date = (discovered_date != "" and discovered_date < today_str)

                if self.cleanup_rules.get("delete_raw_after_publish", True):
                    if self._safe_remove(item.get("raw_video_path")):
                        item["raw_video_path"] = None
                        cleaned_count += 1

                if self.cleanup_rules.get("delete_voiceover_after_publish", True):
                    if self._safe_remove(item.get("voiceover_path")):
                        item["voiceover_path"] = None

                if self.cleanup_rules.get("delete_edited_from_previous_dates", True) and is_previous_date:
                    if self._safe_remove(item.get("edited_video_path")):
                        item["edited_video_path"] = None

            elif item.get("status") == "download_failed":
                discovered_date = item.get("discovered_at", "")[:10]
                if discovered_date and discovered_date < today_str:
                    self._safe_remove(item.get("raw_video_path"))
                    item["raw_video_path"] = None

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(trends, f, indent=4, ensure_ascii=False)

        print(f"[Agent 6: Cleanup] Cleanup complete. Purged {cleaned_count} published raw video sets.")
