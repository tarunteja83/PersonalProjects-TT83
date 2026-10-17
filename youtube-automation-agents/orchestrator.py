"""Runs agents 1 to 7 in order for one channel. Started by execution/execution.py, not run directly."""
import copy
import json
import os

from agents.agent_1_scout import TrendScoutAgent
from agents.agent_2_downloader import DownloaderAgent
from agents.agent_3_voiceover import VoiceoverAgent
from agents.agent_4_editor import VideoEditorAgent
from agents.agent_5_publisher import PublisherAgent
from agents.agent_6_cleanup import CleanupAgent
from agents.agent_7_notifier import TelegramNotifierAgent

# Footage can't always be found (no matching stock clips, or a restricted video), so look again a few times
FIND_ATTEMPTS = 3
READY_STATUSES = ("downloaded", "voiced", "edited")


class OrchestratorAgent:
    def __init__(self, config, instructions):
        self.config = config
        self.instructions = instructions
        self.setup_directories()

    def setup_directories(self):
        paths = self.config["storage_paths"]
        os.makedirs(os.path.dirname(paths["manifest_file"]), exist_ok=True)
        os.makedirs(paths["raw_downloads"], exist_ok=True)
        os.makedirs(paths["audio_renders"], exist_ok=True)
        os.makedirs(paths["processed_videos"], exist_ok=True)
        os.makedirs(paths["archive_dir"], exist_ok=True)
        os.makedirs(paths["tokens_dir"], exist_ok=True)

    def manifest_items(self, channel_key):
        path = self.config["storage_paths"]["manifest_file"]
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            return [item for item in json.load(f) if item.get("profile") == channel_key]

    def execute_pipeline(self, channel_key, dry_run=False):
        """Run every agent for one channel and return the number of videos uploaded."""
        config = copy.deepcopy(self.config)
        config["active_profile"] = channel_key
        paths = config["storage_paths"]
        wanted = config["profiles"][channel_key]["max_videos_per_run"]
        published_before = {i["video_id"] for i in self.manifest_items(channel_key) if i["status"] == "published"}
        print("====================================================")
        print(f" ORCHESTRATOR STARTED | CHANNEL: {channel_key}" + (" | TEST RUN" if dry_run else ""))
        print("====================================================")

        scout = TrendScoutAgent(config, self.instructions)
        downloader = DownloaderAgent(paths["manifest_file"], paths["raw_downloads"], config)
        for attempt in range(FIND_ATTEMPTS):
            ready = sum(1 for i in self.manifest_items(channel_key) if i["status"] in READY_STATUSES)
            if ready >= wanted:
                break
            if attempt:
                print(f"\n[Orchestrator] {ready} of {wanted} video(s) ready; looking for more (attempt {attempt + 1}).")
            scout.run(limit=wanted - ready)
            downloader.run(channel_key)

        VoiceoverAgent(config, self.instructions).run()
        VideoEditorAgent(config).run()

        if dry_run:
            print("\n[Test run] Skipping upload, cleanup and Telegram alert.")
            print(f"[Test run] Finished videos are in: {paths['processed_videos']}")
            return 0

        PublisherAgent(config, self.instructions).run()
        CleanupAgent(config).run()
        TelegramNotifierAgent(config).run()

        uploaded = [i for i in self.manifest_items(channel_key)
                    if i["status"] == "published" and i["video_id"] not in published_before]
        print("\n====================================================")
        print(f" {channel_key}: {len(uploaded)} video(s) uploaded")
        for item in uploaded:
            print(f"   https://youtube.com/shorts/{item.get('uploaded_youtube_id')}")
        print("====================================================")
        return len(uploaded)
