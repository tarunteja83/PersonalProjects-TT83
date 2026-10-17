"""Free stock footage from Pixabay or Pexels, used instead of re-using trending YouTube videos.

YouTube blocks video downloads from cloud servers such as GitHub's, while these APIs work everywhere and their clips
are free to use. Both ask that you show where the footage came from, so each description credits the site.

footage.provider in BRAIN.md: "pixabay", "pexels", or "auto" (whichever has a key: PIXABAY_API_KEY, then PEXELS_API_KEY).
"""
import os
import time

import requests

from agents.ai import generate_text

PROVIDERS = {
    "pixabay": {"name": "Pixabay", "site": "https://pixabay.com", "env": "PIXABAY_API_KEY"},
    "pexels": {"name": "Pexels", "site": "https://www.pexels.com", "env": "PEXELS_API_KEY"},
}
PEXELS_URLS = ["https://api.pexels.com/videos/search", "https://api.pexels.com/v1/videos/search"]
PIXABAY_URL = "https://pixabay.com/api/videos/"
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024
# The ideal clip height for each output shape: Shorts are 1080x1920, long videos 1920x1080
TARGET_HEIGHT = {"portrait": 1920, "landscape": 1080}


def safe_error(error, api_key):
    # Pixabay puts the key in the request URL, so error messages must never be printed with it
    return str(error).replace(api_key, "***") if api_key else str(error)


def with_retries(action, attempts=3):
    """Run action(), retrying dropped connections and rate limits with a growing pause."""
    for attempt in range(attempts):
        try:
            return action()
        except requests.RequestException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if attempt == attempts - 1 or (status is not None and status < 500 and status != 429):
                raise
            time.sleep(5 * (attempt + 1))


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
        + ("Some clips will be cartoon animation, so terms like 'cartoon cat' or 'animated forest' are good.\n"
           if profile.get("footage_style") in ("animation", "mixed") else "")
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
    return list(dict.fromkeys(terms + fallback + ["city", "nature", "people", "sky", "abstract"]))[:count]


def scene_terms(ai_client, scenes, item, profile, models):
    """For each scene, [specific term, generic backup term] describing what that scene's narration actually says.

    News channels may name public figures, countries and landmarks in the specific term (stock sites have little
    footage of named people, so the generic backup is tried next). Kids channels never name real people.
    """
    numbered = "\n".join(f"{i + 1}. {scene['text'][:400]}" for i, scene in enumerate(scenes))
    kids = profile.get("made_for_kids")
    people_rule = (
        "Never use names of real people." if kids else
        "If the scene is about a public figure (a head of state, minister or other government representative), a "
        "country or a famous place, the first term may name them, e.g. 'Narendra Modi speech' or 'Kremlin Moscow'."
    )
    prompt = (
        f"A narrated video about: {item['original_title']}\n"
        f"Channel niche: {profile.get('niche', '')}\n"
        + ("The audience is young children, so keep every term child-friendly.\n" if kids else "")
        + ("Some clips will be cartoon animation, so terms like 'cartoon cat' work well.\n"
           if profile.get("footage_style") in ("animation", "mixed") else "")
        + "For EACH numbered scene below, give two English stock-footage search terms (1 to 4 words each) for "
        "visuals that show what THAT scene is talking about: first the most specific match, then a more generic "
        "backup that stock sites are sure to have (people, places, objects, animals, maps, flags, actions). "
        f"{people_rule} Avoid graphic violence.\n"
        f"Reply with exactly {len(scenes)} lines in the form 'number. specific term | backup term' and nothing else."
        f"\n\n{numbered}"
    )
    terms = {}
    try:
        for line in generate_text(ai_client, prompt, models).splitlines():
            number, _, rest = line.partition(".")
            options = [t.strip().strip('"') for t in rest.split("|") if t.strip()]
            if number.strip().isdigit() and options:
                terms[int(number) - 1] = options[:2]
    except Exception as e:
        print(f"[Stock footage] Could not get scene terms from Gemini ({e}); using topic terms.")
    fallback = search_terms(ai_client, item, profile, models, 5) if len(terms) < len(scenes) else []
    return [terms.get(i) or [fallback[i % len(fallback)]] for i in range(len(scenes))]


def fetch_scene_clips(provider, api_key, terms, destination_dir, prefix, kids=False, style="any",
                      orientation="portrait"):
    """One clip per scene, in scene order, trying each scene's terms in turn (specific first, then backup).

    terms: [[term, ...] per scene]. Returns ([path or None per scene], credits, [term actually used per scene]).
    """
    os.makedirs(destination_dir, exist_ok=True)
    paths, credits, used_terms, used_ids, cache = [], [], [], set(), {}
    for index, options in enumerate(terms):
        wanted = clip_type(style, index)
        path, chosen = None, None
        try:
            for term in options:
                for video_type in ([wanted, None] if wanted else [None]):
                    key = (term, video_type)
                    if key not in cache:
                        cache[key] = with_retries(
                            lambda: CANDIDATES[provider](api_key, term, kids, video_type, orientation))
                    pool = [c for c in cache[key] if c[0] not in used_ids]
                    if pool:
                        clip_id, url, credit = pool[0]
                        used_ids.add(clip_id)
                        path = os.path.join(destination_dir, f"{prefix}_scene{index + 1:03d}.mp4")
                        with_retries(lambda: download_file(url, path))
                        credits.append(credit)
                        chosen = term
                        print(f"[Stock footage] Scene {index + 1}: '{term}' -> {PROVIDERS[provider]['name']} "
                              f"video {clip_id}")
                        break
                if path:
                    break
        except requests.RequestException as e:
            print(f"[Stock footage] Scene {index + 1} ({' / '.join(options)}) failed: {safe_error(e, api_key)}")
            path = None
        if not path:
            print(f"[Stock footage] Scene {index + 1}: no clip for {' / '.join(options)}; "
                  "a neighbouring clip will be reused.")
        paths.append(path)
        used_terms.append(chosen)
    return paths, credits, used_terms


def download_file(url, target):
    with requests.get(url, stream=True, timeout=180) as response:
        response.raise_for_status()
        with open(target, "wb") as f:
            for chunk in response.iter_content(chunk_size=1 << 20):
                f.write(chunk)


# ---------- Pexels ----------

def pick_file(video, orientation="portrait"):
    """Pexels: choose the MP4 rendition closest to the target height without going below 720p."""
    files = [f for f in video.get("video_files", [])
             if f.get("file_type") == "video/mp4" and f.get("height") and f.get("link")]
    usable = [f for f in files if min(f["height"], f.get("width") or f["height"]) >= 720] or files
    target = TARGET_HEIGHT[orientation]
    return min(usable, key=lambda f: abs(f["height"] - target)) if usable else None


def pexels_candidates(api_key, term, kids, video_type, orientation):
    # Pexels has no animation filter, so video_type is ignored here
    params = {"query": term, "orientation": orientation, "size": "medium", "per_page": 30}
    for url in PEXELS_URLS:
        response = requests.get(url, headers={"Authorization": api_key}, params=params, timeout=30)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        return [(v["id"], pick_file(v, orientation)["link"], v.get("user", {}).get("name", "Pexels"))
                for v in response.json().get("videos", []) if v.get("duration", 0) >= 3 and pick_file(v, orientation)]
    return []


# ---------- Pixabay ----------

def pick_rendition(renditions, orientation="portrait"):
    """Pixabay: pick the version that best fills the output frame after cropping, within the size cap."""
    usable = [r for r in renditions.values() if r.get("url") and r.get("height")]
    if not usable:
        return None
    portrait_clip = any(r["height"] > r["width"] for r in usable)
    if orientation == "landscape" or portrait_clip:
        # Already the right shape (or portrait for a Short): closest to the target height
        target = TARGET_HEIGHT[orientation]
        return min(usable, key=lambda r: abs(r["height"] - target))
    # Landscape clip for a vertical Short: the tallest version under the cap keeps the centre crop sharp
    small_enough = [r for r in usable if 0 < r.get("size", 0) <= MAX_DOWNLOAD_BYTES] or usable
    return max(small_enough, key=lambda r: r["height"])


def pixabay_candidates(api_key, term, kids, video_type, orientation):
    params = {"key": api_key, "q": term[:100], "per_page": 30, "order": "popular",
              "safesearch": "true" if kids else "false"}
    if video_type in ("animation", "film"):
        params["video_type"] = video_type
    response = requests.get(PIXABAY_URL, params=params, timeout=30)
    response.raise_for_status()
    hits = [h for h in response.json().get("hits", []) if h.get("duration", 0) >= 3]

    def is_portrait(hit):
        medium = hit["videos"].get("medium", {})
        return medium.get("height", 0) > medium.get("width", 1)

    # Prefer clips that already match the output shape; others are centre-cropped by the editor
    hits.sort(key=lambda h: 0 if is_portrait(h) == (orientation == "portrait") else 1)
    found = []
    for hit in hits:
        rendition = pick_rendition(hit["videos"], orientation)
        if rendition:
            found.append((hit["id"], rendition["url"], hit.get("user", "Pixabay")))
    return found


CANDIDATES = {"pexels": pexels_candidates, "pixabay": pixabay_candidates}


def clip_type(style, index):
    """footage_style -> Pixabay video_type for the index-th clip ("mixed" alternates cartoon and real)."""
    if style == "mixed":
        return "animation" if index % 2 == 0 else "film"
    return style if style in ("animation", "film") else None


def fetch_clips(provider, api_key, terms, destination_dir, prefix, kids=False, style="any",
                orientation="portrait", clips_per_term=1):
    """Download clips_per_term clips for each search term. Returns (file paths, contributor credits).

    Each term is searched once and several clips are taken from its results, to keep API requests low.
    """
    os.makedirs(destination_dir, exist_ok=True)
    paths, credits, used_ids, cache = [], [], set(), {}

    def candidates(term, video_type):
        if (term, video_type) not in cache:
            cache[(term, video_type)] = with_retries(
                lambda: CANDIDATES[provider](api_key, term, kids, video_type, orientation))
        return cache[(term, video_type)]

    def download(url, target):
        with requests.get(url, stream=True, timeout=180) as response:
            response.raise_for_status()
            with open(target, "wb") as f:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    f.write(chunk)

    index = 0
    for term in terms:
        for _ in range(clips_per_term):
            wanted = clip_type(style, index)
            try:
                pool = [c for c in candidates(term, wanted) if c[0] not in used_ids]
                if not pool and wanted:
                    # e.g. no cartoon clip for this term: take any kind of clip instead
                    pool = [c for c in candidates(term, None) if c[0] not in used_ids]
            except requests.RequestException as e:
                print(f"[Stock footage] {PROVIDERS[provider]['name']} search failed for '{term}': "
                      f"{safe_error(e, api_key)}")
                break
            if not pool:
                print(f"[Stock footage] No more clips for '{term}'.")
                break
            clip_id, url, credit = pool[0]
            used_ids.add(clip_id)
            index += 1
            target = os.path.join(destination_dir, f"{prefix}_clip{index}.mp4")
            try:
                with_retries(lambda: download(url, target))
            except requests.RequestException as e:
                print(f"[Stock footage] Download failed for {PROVIDERS[provider]['name']} video {clip_id}: "
                      f"{safe_error(e, api_key)}")
                continue
            paths.append(target)
            credits.append(credit)
            print(f"[Stock footage] '{term}' -> {PROVIDERS[provider]['name']} video {clip_id} by {credit}")
    return paths, credits
