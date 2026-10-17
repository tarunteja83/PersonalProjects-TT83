"""Runs agents 1 to 7 in order for one channel. Started by execution/execution.py, not run directly."""
import copy
import json
import os

from agents.agent_1_scout import TrendScoutAgent
from agents.agent_2_downloader import DownloaderAgent
from agents.agent_3_voiceover import VoiceoverAgent, stock_mode
from agents.agent_4_editor import VideoEditorAgent
from agents.agent_5_publisher import PublisherAgent
from agents.agent_6_cleanup import CleanupAgent
from agents.agent_7_notifier import TelegramNotifierAgent
from brain import item_matches

# Footage can't always be found (no matching stock clips, or a restricted video), so look again a few times
FIND_ATTEMPTS = 3
# Long videos are prepared first, so they are certainly ready well before their go-live times
FORMATS = ("long", "short")


def format_profile(profile, fmt):
    """The channel's settings for one format: Shorts as answered, or the long-video answers (Q22 to Q24)."""
    settings = copy.deepcopy(profile)
    settings["format"] = fmt
    if fmt == "long":
        settings["max_videos_per_run"] = profile["long"]["count"]
        settings["publish_plan"] = profile.get("long_publish_plan")
    return settings


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

    def manifest_items(self, channel_key, profile=None):
        path = self.config["storage_paths"]["manifest_file"]
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            items = json.load(f)
        if profile is None:
            return [item for item in items if item.get("profile") == channel_key]
        return [item for item in items if item_matches(item, channel_key, profile)]

    def run_format(self, config, channel_key, dry_run):
        """Find footage, narrate, edit and (unless a test run) upload one format's videos."""
        profile = config["profiles"][channel_key]
        paths = config["storage_paths"]
        wanted = profile["max_videos_per_run"]
        print(f"\n---------- {channel_key}: {wanted} {profile['format']} video(s) ----------")

        scout = TrendScoutAgent(config, self.instructions)
        downloader = DownloaderAgent(paths["manifest_file"], paths["raw_downloads"], config)
        voiceover = VoiceoverAgent(config, self.instructions)
        if stock_mode(config, profile):
            # Stock footage: write and narrate the script first, then fetch a clip that matches each scene
            ready_statuses, in_progress = ("ready_to_edit", "edited"), ("discovered", "voiced")
            steps = [voiceover.run, lambda: downloader.run(channel_key)]
        else:
            # Re-used trending video: download it first, then narrate
            ready_statuses, in_progress = ("downloaded", "voiced", "edited"), ("discovered",)
            steps = [lambda: downloader.run(channel_key), voiceover.run]

        for attempt in range(FIND_ATTEMPTS):
            items = self.manifest_items(channel_key, profile)
            ready = sum(1 for i in items if i["status"] in ready_statuses)
            pending = sum(1 for i in items if i["status"] in in_progress)
            if ready >= wanted:
                break
            if attempt:
                print(f"\n[Orchestrator] {ready} of {wanted} video(s) ready; looking for more (attempt {attempt + 1}).")
            if ready + pending < wanted:
                scout.run(limit=wanted - ready - pending)
            for step in steps:
                step()

        VideoEditorAgent(config).run()
        if not dry_run:
            PublisherAgent(config, self.instructions).run()

    def execute_pipeline(self, channel_key, dry_run=False, formats=FORMATS):
        """Run every agent for one channel (Shorts, then long videos) and return the number of videos uploaded."""
        base = self.config["profiles"][channel_key]
        published_before = {i["video_id"] for i in self.manifest_items(channel_key) if i["status"] == "published"}
        print("====================================================")
        print(f" ORCHESTRATOR STARTED | CHANNEL: {channel_key}" + (" | TEST RUN" if dry_run else ""))
        print("====================================================")

        for fmt in formats:
            if fmt == "long" and not base["long"]["count"]:
                continue
            config = copy.deepcopy(self.config)
            config["active_profile"] = channel_key
            config["profiles"][channel_key] = format_profile(base, fmt)
            self.run_format(config, channel_key, dry_run)

        if dry_run:
            print("\n[Test run] Skipping upload, cleanup and Telegram alert.")
            print(f"[Test run] Finished videos are in: {self.config['storage_paths']['processed_videos']}")
            return 0

        config = copy.deepcopy(self.config)
        config["active_profile"] = channel_key
        CleanupAgent(config).run()
        TelegramNotifierAgent(config).run()

        uploaded = [i for i in self.manifest_items(channel_key)
                    if i["status"] == "published" and i["video_id"] not in published_before]
        print("\n====================================================")
        print(f" {channel_key}: {len(uploaded)} video(s) uploaded")
        for item in uploaded:
            link = "shorts/" if item.get("format", "short") == "short" else "watch?v="
            print(f"   https://youtube.com/{link}{item.get('uploaded_youtube_id')}  ({item.get('format', 'short')})")
        print("====================================================")
        return len(uploaded)
