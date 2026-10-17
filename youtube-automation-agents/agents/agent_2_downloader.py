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
    def __init__(self, manifest_path, download_destination_dir):
        self.manifest_path = manifest_path
        self.download_dir = download_destination_dir

    def run(self, active_profile):
        print(f"\n[Agent 2: Downloader] Checking manifest at {self.manifest_path}...")
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            trends = json.load(f)

        for item in trends:
            if item["status"] == "discovered" and item["profile"] == active_profile:
                vid_id = item["video_id"]
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

        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(trends, f, indent=4, ensure_ascii=False)
