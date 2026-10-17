import os
import re
import json
import shutil
from datetime import datetime, timedelta, timezone

# Videos still waiting for a later step; if their files are gone they can never finish
UNFINISHED = ("downloaded", "voiced", "ready_to_edit", "edited")
FILE_FOR_STATUS = {"downloaded": "raw_video_path", "voiced": "voiceover_path",
                   "ready_to_edit": "raw_video_path", "edited": "edited_video_path"}

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

    def remove_old_date_folders(self):
        """Local runs: delete whole YouTube_Auto/<YYYY-MM-DD> folders older than local_media.keep_days."""
        media = self.config.get("local_media") or {}
        root = self.config.get("media_root")
        keep_days = int(media.get("keep_days", 2))
        if not root or not os.path.isdir(root):
            return 0
        oldest_kept = (datetime.now() - timedelta(days=keep_days - 1)).strftime("%Y-%m-%d")
        removed = 0
        for name in sorted(os.listdir(root)):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name) and name < oldest_kept:
                try:
                    shutil.rmtree(os.path.join(root, name))
                    print(f"[Agent 6: Cleanup] Deleted folder older than {keep_days} days: {os.path.join(root, name)}")
                    removed += 1
                except Exception as e:
                    print(f"[Agent 6: Cleanup] Failed to delete {name}: {e}")
        return removed

    def run(self):
        manifest_path = self.paths["manifest_file"]
        if not os.path.exists(manifest_path):
            return

        local = self.config.get("storage_mode") == "local"
        print("\n[Agent 6: Cleanup] " + ("Removing date folders past the keep period..." if local
                                         else "Scanning manifest for published & previous-date videos..."))
        removed_folders = self.remove_old_date_folders() if local else 0

        with open(manifest_path, "r", encoding="utf-8") as f:
            trends = json.load(f)

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cleaned_count = expired = 0

        for item in trends:
            status = item.get("status")
            if local:
                # Files are kept in the date folders until those are deleted as a whole
                for field in ("raw_video_path", "voiceover_path", "edited_video_path"):
                    if item.get(field) and not os.path.exists(item[field]):
                        item[field] = None
                if status in UNFINISHED and not item.get(FILE_FOR_STATUS[status]):
                    item["status"] = "expired"
                    expired += 1
                continue

            if status == "published":
                discovered_date = item.get("discovered_at", "")[:10]
                is_previous_date = (discovered_date != "" and discovered_date < today_str)

                if self.cleanup_rules.get("delete_raw_after_publish", True):
                    for clip in item.get("footage_paths") or []:
                        self._safe_remove(clip)
                    if self._safe_remove(item.get("raw_video_path")) or item.get("footage_paths"):
                        item["raw_video_path"] = None
                        item["footage_paths"] = []
                        cleaned_count += 1

                if self.cleanup_rules.get("delete_voiceover_after_publish", True):
                    if self._safe_remove(item.get("voiceover_path")):
                        item["voiceover_path"] = None

                if self.cleanup_rules.get("delete_edited_from_previous_dates", True) and is_previous_date:
                    if self._safe_remove(item.get("edited_video_path")):
                        item["edited_video_path"] = None

            elif status == "download_failed":
                discovered_date = item.get("discovered_at", "")[:10]
                if discovered_date and discovered_date < today_str:
                    self._safe_remove(item.get("raw_video_path"))
                    item["raw_video_path"] = None

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(trends, f, indent=4, ensure_ascii=False)

        if local:
            print(f"[Agent 6: Cleanup] Cleanup complete. Removed {removed_folders} old date folder(s); "
                  f"{expired} unfinished video(s) expired with them.")
        else:
            print(f"[Agent 6: Cleanup] Cleanup complete. Purged {cleaned_count} published raw video sets.")
