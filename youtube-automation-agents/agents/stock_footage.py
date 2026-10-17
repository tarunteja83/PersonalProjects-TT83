"""Free stock footage from Pixabay or Pexels, used instead of re-using trending YouTube videos.

YouTube blocks video downloads from cloud servers such as GitHub's, while these APIs work everywhere and their clips
are free to use. Both ask that you show where the footage came from, so each description credits the site.

footage.provider in BRAIN.md: "pixabay", "pexels", or "auto" (whichever has a key: PIXABAY_API_KEY, then PEXELS_API_KEY).
"""
import os

import requests

from agents.ai import generate_text

PROVIDERS = {
    "pixabay": {"name": "Pixabay", "site": "https://pixabay.com", "env": "PIXABAY_API_KEY"},
    "pexels": {"name": "Pexels", "site": "https://www.pexels.com", "env": "PEXELS_API_KEY"},
}
PEXELS_URLS = ["https://api.pexels.com/videos/search", "https://api.pexels.com/v1/videos/search"]
PIXABAY_URL = "https://pixabay.com/api/videos/"
TARGET_HEIGHT = 1920
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024


def choose_provider(setting="auto"):
    """Return (provider, api_key) for the configured provider, or (None, None) if no key is set."""
    order = [setting] if setting in PROVIDERS else ["pixabay", "pexels"]
    for provider in order:
        key = os.getenv(PROVIDERS[provider]["env"])
        if key:
            return provider, key
    return None, None


def search_terms(ai_client, item, profile, models, count):
    """Ask Gemini for short visual search terms matching the trending topic."""
    prompt = (
        f"Trending video title: {item['original_title']}\n"
        f"Context: {item['original_description'][:300]}\n"
        f"Channel niche: {profile.get('niche', '')}\n"
        + ("The audience is young children, so keep every term child-friendly.\n" if profile.get("made_for_kids") else "")
        + f"Give {count} different stock-footage search terms in English (1 to 3 words each) for visuals that "
        "match this topic. Avoid graphic violence. Output ONLY the terms, comma-separated."
    )
    try:
        text = generate_text(ai_client, prompt, models)
        terms = [t.strip().strip('"').strip() for t in text.replace("\n", ",").split(",") if t.strip()]
    except Exception as e:
        print(f"[Stock footage] Could not get search terms from Gemini ({e}); using the channel niche.")
        terms = []
    fallback = [w for w in profile.get("niche", "").replace(",", " ").split() if len(w) > 3]
    return (terms + fallback + ["city", "nature", "people"])[:count]


# ---------- Pexels ----------

def pick_file(video):
    """Pexels: choose the MP4 rendition closest to 1080x1920 without being smaller than 720p."""
    files = [f for f in video.get("video_files", [])
             if f.get("file_type") == "video/mp4" and f.get("height") and f.get("link")]
    usable = [f for f in files if f["height"] >= 1280] or files
    return min(usable, key=lambda f: abs(f["height"] - TARGET_HEIGHT)) if usable else None


def find_pexels(api_key, term, used_ids, kids):
    params = {"query": term, "orientation": "portrait", "size": "medium", "per_page": 15}
    for url in PEXELS_URLS:
        response = requests.get(url, headers={"Authorization": api_key}, params=params, timeout=30)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        for video in response.json().get("videos", []):
            if video["id"] not in used_ids and video.get("duration", 0) >= 3 and pick_file(video):
                return video["id"], pick_file(video)["link"], video.get("user", {}).get("name", "Pexels")
        return None
    return None


# ---------- Pixabay ----------

def pick_rendition(renditions):
    """Pixabay: portrait -> closest to 1920 tall; landscape -> the tallest version under the size cap (cropped later)."""
    usable = [r for r in renditions.values() if r.get("url") and r.get("height")]
    if not usable:
        return None
    if any(r["height"] > r["width"] for r in usable):
        return min(usable, key=lambda r: abs(r["height"] - TARGET_HEIGHT))
    small_enough = [r for r in usable if 0 < r.get("size", 0) <= MAX_DOWNLOAD_BYTES] or usable
    return max(small_enough, key=lambda r: r["height"])


def find_pixabay(api_key, term, used_ids, kids):
    params = {"key": api_key, "q": term[:100], "per_page": 30, "order": "popular",
              "safesearch": "true" if kids else "false"}
    response = requests.get(PIXABAY_URL, params=params, timeout=30)
    response.raise_for_status()
    hits = [h for h in response.json().get("hits", []) if h["id"] not in used_ids and h.get("duration", 0) >= 3]
    # Prefer clips that are already vertical; landscape ones are centre-cropped by the editor
    hits.sort(key=lambda h: 0 if (h["videos"].get("medium", {}).get("height", 0)
                                 > h["videos"].get("medium", {}).get("width", 1)) else 1)
    for hit in hits:
        rendition = pick_rendition(hit["videos"])
        if rendition:
            return hit["id"], rendition["url"], hit.get("user", "Pixabay")
    return None


FINDERS = {"pexels": find_pexels, "pixabay": find_pixabay}


def fetch_clips(provider, api_key, terms, destination_dir, prefix, kids=False):
    """Download one clip per search term. Returns (file paths, contributor credits)."""
    os.makedirs(destination_dir, exist_ok=True)
    paths, credits, used_ids = [], [], set()
    for index, term in enumerate(terms):
        try:
            found = FINDERS[provider](api_key, term, used_ids, kids)
        except requests.RequestException as e:
            print(f"[Stock footage] {PROVIDERS[provider]['name']} search failed for '{term}': {e}")
            continue
        if not found:
            print(f"[Stock footage] No clip found for '{term}'.")
            continue
        clip_id, url, credit = found
        used_ids.add(clip_id)
        target = os.path.join(destination_dir, f"{prefix}_clip{index + 1}.mp4")
        with requests.get(url, stream=True, timeout=180) as download:
            download.raise_for_status()
            with open(target, "wb") as f:
                for chunk in download.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        paths.append(target)
        credits.append(credit)
        print(f"[Stock footage] '{term}' -> {PROVIDERS[provider]['name']} video {clip_id} by {credit}")
    return paths, credits
