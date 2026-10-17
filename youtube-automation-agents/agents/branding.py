"""Fetches and caches each channel's YouTube profile picture, used as the logo on the end card."""
import os
from io import BytesIO

import requests
from PIL import Image
from googleapiclient.discovery import build

from brain import parse_channel_url


def channel_logo(profile, channel_key, branding_dir):
    """Return a local PNG of the channel's logo, or None if it can't be found.

    A logo_file set on the channel page wins; otherwise the channel's YouTube profile picture is
    downloaded once and cached (delete the cached file to refresh it after changing the picture).
    """
    custom = profile.get("logo_file")
    if custom and os.path.exists(custom):
        return custom

    cached = os.path.join(branding_dir, f"logo_{channel_key}.png")
    if os.path.exists(cached):
        return cached

    api_key = os.getenv("YOUTUBE_API_KEY")
    parsed = parse_channel_url(profile.get("channel_url", ""))
    if not api_key or not parsed:
        return None
    kind, value = parsed
    try:
        lookup = {"forHandle": value} if kind == "handle" else {"id": value}
        youtube = build("youtube", "v3", developerKey=api_key)
        items = youtube.channels().list(part="snippet", **lookup).execute().get("items", [])
        thumbnails = items[0]["snippet"]["thumbnails"] if items else {}
        url = next((thumbnails[size]["url"] for size in ("high", "medium", "default") if size in thumbnails), None)
        if not url:
            return None
        image = Image.open(BytesIO(requests.get(url, timeout=20).content)).convert("RGBA")
        os.makedirs(branding_dir, exist_ok=True)
        image.save(cached)
        return cached
    except Exception as e:
        print(f"[Branding] Could not fetch the logo for {channel_key}: {e}")
        return None
