import os
import json
import shutil
import yt_dlp

def ffmpeg_location():
    # yt-dlp needs FFmpeg to merge video and audio; fall back to the copy bundled with MoviePy when none is installed
    if shutil.which("ffmpeg"):
        return None
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None

class DownloaderAgent:
    """Gets footage for each discovered topic.

    footage.source in BRAIN.md: "stock" downloads free Pexels clips matching the topic (works everywhere);
    "youtube" downloads the trending video itself (laptop only: YouTube blocks downloads from cloud servers).
    """
    def __init__(self, manifest_path, download_destination_dir, config=None):
        self.manifest_path = manifest_path
        self.download_dir = download_destination_dir
        self.config = config or {}
        self.footage = self.config.get("footage") or {}

    def run(self, active_profile):
        print(f"\n[Agent 2: Downloader] Checking manifest at {self.manifest_path}...")
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            trends = json.load(f)

        stock = self.footage.get("source", "stock") == "stock"
        if stock:
            from google import genai
            from agents import stock_footage
            provider, api_key = stock_footage.choose_provider(self.footage.get("provider", "auto"))
            ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            profile = self.config["profiles"][active_profile]

        for item in trends:
            if item["status"] == "discovered" and item["profile"] == active_profile:
                vid_id = item["video_id"]
                if stock:
                    self.fetch_stock(item, stock_footage, provider, api_key, ai_client, profile)
                else:
                    self.download_source(item, vid_id)

        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(trends, f, indent=4, ensure_ascii=False)

    def fetch_stock(self, item, stock_footage, provider, api_key, ai_client, profile):
        vid_id = item["video_id"]
        if not provider:
            print("[Agent 2: Downloader] No stock footage key: add PIXABAY_API_KEY (or PEXELS_API_KEY) "
                  "to .env / GitHub secrets.")
            item["status"] = "download_failed"
            return
        count = int(self.footage.get("clips_per_video", 3))
        terms = stock_footage.search_terms(ai_client, item, profile, self.config.get("ai_models"), count)
        print(f"[Agent 2: Downloader] Stock footage for '{item['original_title'][:60]}': {', '.join(terms)}")
        try:
            paths, credits = stock_footage.fetch_clips(provider, api_key, terms, self.download_dir,
                                                       f"stock_{vid_id}", kids=profile.get("made_for_kids", False))
        except Exception as e:
            print(f"[Agent 2: Downloader] Stock footage failed for {vid_id}: {e}")
            paths, credits = [], []
        if paths:
            item["footage_paths"] = paths
            item["footage_provider"] = provider
            item["footage_credits"] = sorted(set(credits))
            item["raw_video_path"] = paths[0]
            item["status"] = "downloaded"
        else:
            item["status"] = "download_failed"

    def download_source(self, item, vid_id):
        target_file = os.path.join(self.download_dir, f"raw_{vid_id}.mp4")
        ydl_opts = {
            "format": "bestvideo[ext=mp4][height<=1920]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": target_file,
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "noprogress": True
        }
        if ffmpeg_location():
            ydl_opts["ffmpeg_location"] = ffmpeg_location()

        try:
            print(f"[Agent 2: Downloader] Downloading {item['source_url']} -> {target_file}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([item["source_url"]])

            if os.path.exists(target_file):
                item["raw_video_path"] = target_file
                item["status"] = "downloaded"
        except Exception as e:
            print(f"[Agent 2: Downloader] Error downloading {vid_id}: {e}")
            item["status"] = "download_failed"
