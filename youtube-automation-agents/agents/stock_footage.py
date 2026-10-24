"""Free stock footage from Pixabay or Pexels, used instead of re-using trending YouTube videos.

YouTube blocks video downloads from cloud servers such as GitHub's, while these APIs work everywhere and their clips
are free to use. Both ask that you show where the footage came from, so each description credits the site.

footage.provider in BRAIN.md: "pixabay", "pexels", or "auto" (whichever has a key: PIXABAY_API_KEY, then PEXELS_API_KEY).
"""
import html
import os
import re
import shutil
import subprocess
import time

import imageio_ffmpeg
import requests

from agents.ai import generate_text

PROVIDERS = {
    "pixabay": {"name": "Pixabay", "site": "https://pixabay.com", "env": "PIXABAY_API_KEY"},
    "pexels": {"name": "Pexels", "site": "https://www.pexels.com", "env": "PEXELS_API_KEY"},
}
PEXELS_URLS = ["https://api.pexels.com/videos/search", "https://api.pexels.com/v1/videos/search"]
# Real footage of the actual people, places and events (Frontline): Wikimedia Commons hosts official government and
# news-agency video under free licences. Its API asks every client to identify itself.
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
COMMONS_HEADERS = {"User-Agent": "FrontlineLedgerTT83/1.0 (https://github.com/tarunteja83/PersonalProjects-TT83)"}
COMMONS_SITE = "Wikimedia Commons"
# File-name ending of real-footage clips; the editor shows them whole (not cropped) in vertical Shorts
REAL_SUFFIX = "_real.mp4"
# Words in a footage term that describe the shot rather than who or what is in it
SHOT_WORDS = {"the", "and", "with", "speech", "address", "remarks", "footage", "video", "clip", "scene", "shot",
              "close", "closeup", "view", "aerial", "live", "news", "of", "in", "at", "on", "for"}
PIXABAY_URL = "https://pixabay.com/api/videos/"
PIXABAY_IMAGES_URL = "https://pixabay.com/api/"
# Words that describe the look of a clip, not what is in it; they don't make a clip match a scene
STYLE_WORDS = {"cartoon", "cartoons", "animated", "animation", "funny", "cute", "happy", "silly", "colorful",
               "colourful", "kids", "kid", "children", "magical", "fun", "playful", "little", "illustration"}
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024
# The ideal clip height for each output shape: Shorts are 1080x1920, long videos 1920x1080
TARGET_HEIGHT = {"portrait": 1920, "landscape": 1080}


def safe_error(error, api_key):
    # Pixabay puts the key in the request URL, so error messages must never be printed with it
    return str(error).replace(api_key, "***") if api_key else str(error)


def with_retries(action, attempts=4):
    """Run action(), retrying dropped connections and rate limits with a growing pause."""
    for attempt in range(attempts):
        try:
            return action()
        except requests.RequestException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if attempt == attempts - 1 or (status is not None and status < 500 and status != 429):
                raise
            # Rate limits reset per minute, so wait long enough for the window to clear
            time.sleep(30 * (attempt + 1) if status == 429 else 5 * (attempt + 1))


_last_request = [0.0]


def pace(min_gap=0.7):
    """Keep API searches under about 85 a minute (Pixabay allows 100)."""
    wait = _last_request[0] + min_gap - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = time.monotonic()


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
    return with_generic_terms(terms, profile)[:count]


def with_generic_terms(terms, profile):
    fallback = [w for w in profile.get("niche", "").replace(",", " ").split() if len(w) > 3]
    return list(dict.fromkeys(terms + fallback + ["city", "nature", "people", "sky", "abstract"]))


def topic_fallback(item, profile):
    """Footage terms Gemini wrote alongside the script, topped up with generic ones."""
    return with_generic_terms(list(item.get("topic_terms") or []), profile)[:5]


TERM_PAIR_RULE = (
    "two English stock-footage search terms (1 to 4 words each) for visuals that show what THAT part is talking "
    "about: first the most specific match, then a more generic backup that stock sites are sure to have (people, "
    "places, objects, animals, maps, flags, actions). "
)


def footage_rules(profile):
    """Rules every footage term follows, shared by the script request and the per-scene request.

    News channels may name public figures, countries and landmarks in the specific term (stock sites have little
    footage of named people, so the generic backup is tried next). Kids channels never name real people.
    """
    kids = profile.get("made_for_kids")
    return (
        ("Never use names of real people. The audience is young children, so keep every term child-friendly. "
         if kids else
         "When the part is about a specific person (a head of state, minister or other government representative), "
         "country, military unit, ship, weapon, place or event, the first term names it in 1 to 3 words so real "
         "footage of it can be found, e.g. 'Zelensky', 'Narendra Modi', 'USS Gerald Ford', 'NATO summit', 'Kremlin'. ")
        + ("Some clips will be cartoon animation, so terms like 'cartoon cat' work well. "
           if profile.get("footage_style") in ("animation", "mixed") else "")
        + "Avoid graphic violence."
    )


def parse_scene_hints(text):
    """'a | b ; c | d ; e | f' -> [[a, b], [c, d], [e, f]]."""
    hints = []
    for part in text.split(";"):
        options = [t.strip().strip('"') for t in part.split("|") if t.strip().strip('"')]
        if options:
            hints.append(options[:2])
    return hints


def hinted_terms(hints, scenes, item, profile):
    """Spread the script's hints over the scenes in order, each with one topic term as a last resort."""
    fallback = topic_fallback(item, profile)
    return [hints[i * len(hints) // len(scenes)] + [fallback[i % len(fallback)]] for i in range(len(scenes))]


def scene_terms(ai_client, scenes, item, profile, models):
    """For each scene, [specific term, generic backup term] describing what that scene's narration actually says."""
    if item.get("scene_hints") and item.get("format") != "long":
        # Shorts: the script request already chose terms for the opening, middle and end of the narration
        return hinted_terms(item["scene_hints"], scenes, item, profile)
    numbered = "\n".join(f"{i + 1}. {scene['text'][:400]}" for i, scene in enumerate(scenes))
    prompt = (
        f"A narrated video about: {item['original_title']}\n"
        f"Channel niche: {profile.get('niche', '')}\n"
        + "For EACH numbered scene below, give " + TERM_PAIR_RULE + footage_rules(profile)
        + f"\nReply with exactly {len(scenes)} lines in the form 'number. specific term | backup term' and nothing "
        f"else.\n\n{numbered}"
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
    fallback = []
    if len(terms) < len(scenes):
        # The script request already returned footage terms for the whole topic; only ask again if it didn't
        fallback = topic_fallback(item, profile) if item.get("topic_terms") else \
            search_terms(ai_client, item, profile, models, 5)
    return [terms.get(i) or [fallback[i % len(fallback)]] for i in range(len(scenes))]


# ---------- Wikimedia Commons (real footage) ----------

def free_licence(name):
    """Public domain, CC0 and CC BY can go in a monetised video with a credit; share-alike, non-commercial and
    no-derivatives licences can't, so they are skipped."""
    name = (name or "").upper().replace("-", " ")
    if "PUBLIC DOMAIN" in name or name.startswith(("PD", "CC0", "CC ZERO")):
        return True
    return name.startswith("CC BY") and not any(f" {x} " in f" {name} " for x in ("SA", "NC", "ND"))


def key_words(term):
    return [w for w in re.findall(r"[a-z0-9]+", term.lower()) if len(w) >= 3 and w not in SHOT_WORDS]


def relevant(title, term):
    """Every name and subject word of the term must be in the clip's title (a Zelensky scene needs Zelensky)."""
    words = key_words(term)
    title = title.lower()
    return bool(words) and all(w in title for w in words)


def commons_candidates(term, orientation="portrait", min_seconds=4):
    """[(file title, video url, credit, duration seconds)] of freely licensed real footage matching the term."""
    pace()
    search = requests.get(COMMONS_API, headers=COMMONS_HEADERS, timeout=30, params={
        "action": "query", "list": "search", "srsearch": f"{term} filetype:video", "srnamespace": 6,
        "srlimit": 20, "format": "json"}).json()
    titles = [h["title"] for h in search.get("query", {}).get("search", []) if relevant(h["title"], term)]
    if not titles:
        return []
    pace()
    info = requests.get(COMMONS_API, headers=COMMONS_HEADERS, timeout=30, params={
        "action": "query", "titles": "|".join(titles[:20]), "prop": "videoinfo",
        "viprop": "url|size|extmetadata|derivatives", "format": "json"}).json()
    pages = {p["title"]: p for p in info.get("query", {}).get("pages", {}).values()}
    target = TARGET_HEIGHT[orientation] if orientation == "landscape" else 720
    found = []
    for title in titles:
        page = pages.get(title) or {}
        details = (page.get("videoinfo") or [{}])[0]
        meta = details.get("extmetadata", {})
        licence = meta.get("LicenseShortName", {}).get("value", "")
        if not free_licence(licence) or (details.get("duration") or 0) < min_seconds:
            continue
        versions = [d for d in details.get("derivatives", [])
                    if d.get("type", "").startswith("video/webm") and (d.get("height") or 0) >= 360]
        if not versions:
            continue
        best = min(versions, key=lambda d: abs(d["height"] - target))
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", ""))
        artist = re.sub(r"\s+", " ", html.unescape(artist)).strip() or "unknown"
        credit = f"{artist[:60]}, {licence}, commons.wikimedia.org/?curid={page.get('pageid')}"
        found.append((title, best["src"], credit, float(details["duration"])))
    return found


def commons_photo_candidates(term, orientation="portrait", min_width=800):
    """[(file title, image url, credit)] of freely licensed real photos matching the term (when there is no video)."""
    pace()
    search = requests.get(COMMONS_API, headers=COMMONS_HEADERS, timeout=30, params={
        "action": "query", "list": "search", "srsearch": f"{term} filetype:bitmap", "srnamespace": 6,
        "srlimit": 20, "format": "json"}).json()
    titles = [h["title"] for h in search.get("query", {}).get("search", []) if relevant(h["title"], term)]
    if not titles:
        return []
    pace()
    info = requests.get(COMMONS_API, headers=COMMONS_HEADERS, timeout=30, params={
        "action": "query", "titles": "|".join(titles[:20]), "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1920, "format": "json"}).json()
    pages = {p["title"]: p for p in info.get("query", {}).get("pages", {}).values()}
    found = []
    for title in titles:
        page = pages.get(title) or {}
        details = (page.get("imageinfo") or [{}])[0]
        meta = details.get("extmetadata", {})
        licence = meta.get("LicenseShortName", {}).get("value", "")
        if (not free_licence(licence) or details.get("mime") not in ("image/jpeg", "image/png")
                or (details.get("width") or 0) < min_width):
            continue
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", ""))
        artist = re.sub(r"\s+", " ", html.unescape(artist)).strip() or "unknown"
        credit = f"{artist[:60]}, {licence}, commons.wikimedia.org/?curid={page.get('pageid')}"
        found.append((title, details.get("thumburl") or details["url"], credit))
    return found


def photo_clip(url, target, seconds, orientation="landscape"):
    """Turn a photo into a moving shot lasting `seconds`: the whole photo (faces are never cropped) on a blurred copy
    of itself, in the video's shape, slowly zooming in (the Ken Burns effect)."""
    image_path = target + ".img"
    for wait in (3, 6, 10, None):
        response = requests.get(url, headers=COMMONS_HEADERS, timeout=60)
        # Image servers (Pixabay's especially) refuse quick bursts for a few seconds: wait and try again
        if response.status_code != 429 or wait is None:
            break
        time.sleep(wait)
    response.raise_for_status()
    with open(image_path, "wb") as f:
        f.write(response.content)
    frames = max(int(seconds * 30), 30)
    exe = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
    try:
        w, h = (1080, 1920) if orientation == "portrait" else (1920, 1080)
        layout = (f"split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=25:2[bg];"
                  f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,")
        subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", "-i", image_path, "-vf",
                        layout + f"zoompan=z='min(zoom+0.0006,1.12)':d={frames}:x='iw/2-(iw/zoom/2)':"
                        f"y='ih/2-(ih/zoom/2)':s={w}x{h}:fps=30", "-frames:v", str(frames), "-c:v", "libx264",
                        "-preset", "veryfast",
                        "-crf", "23", "-pix_fmt", "yuv420p", target], check=True, timeout=300, capture_output=True)
    finally:
        os.remove(image_path)


def download_segment(url, target, start, seconds):
    """Fetch only `seconds` of video from `start`: FFmpeg seeks over HTTP, so long videos aren't downloaded whole."""
    exe = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", "-user_agent", COMMONS_HEADERS["User-Agent"],
                    "-ss", f"{start:.2f}", "-i", url, "-t", f"{seconds:.2f}", "-an", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "23", target], check=True, timeout=300, capture_output=True)


def segment_start(duration, seconds):
    """Skip the first quarter of longer videos (titles, intros) but stay inside the video."""
    return max(0.0, min(duration * 0.25, duration - seconds - 0.5)) if duration > seconds + 1 else 0.0


def name_only(term):
    """'Narendra Modi summit' -> 'Narendra Modi': the first two words of a longer term, tried when the full term finds
    nothing. Two-word terms aren't shortened ('Western intelligence' must not become 'Western')."""
    words = key_words(term)
    return " ".join(words[:2]) if len(words) > 2 else ""


def real_photo(term, path, seconds, orientation, used_ids, cache):
    """When there's no real video, a real photo of the person, place or event as a slow zoom; (title, credit) or None."""
    key = ("photo", term)
    if key not in cache:
        cache[key] = commons_photo_candidates(term, orientation)
        if not cache[key] and name_only(term):
            cache[key] = commons_photo_candidates(name_only(term), orientation)
    for title, url, credit in cache[key]:
        if title in used_ids:
            continue
        used_ids.add(title)
        photo_clip(url, path, seconds, orientation)
        return title, credit
    return None


def real_clip(term, path, seconds, orientation, used_ids, cache, uses):
    """Download real footage for the term into path; returns (file title, credit), or None if nothing matches.

    An unused video is preferred; if every match was already used, the best one is reused from a later moment,
    which keeps the number of credits (and the description) short.
    """
    if term not in cache:
        cache[term] = commons_candidates(term, orientation)
        if not cache[term] and name_only(term):
            cache[term] = commons_candidates(name_only(term), orientation)
    if not cache[term]:
        return None
    fresh = [c for c in cache[term] if c[0] not in used_ids]
    title, url, credit, duration = (fresh or cache[term])[0]
    used_ids.add(title)
    start = segment_start(duration, seconds) + uses.get(title, 0) * (seconds + 2)
    if start + seconds > duration:
        start = (start % max(duration - seconds, 1.0)) if duration > seconds else 0.0
    uses[title] = uses.get(title, 0) + 1
    download_segment(url, path, start, seconds)
    return title, credit


def fetch_scene_clips(provider, api_key, terms, destination_dir, prefix, kids=False, style="any",
                      orientation="portrait", lengths=None):
    """One clip per scene, in scene order, trying each scene's terms in turn (specific first, then backup).

    style "real": each scene's specific term is first looked up as real footage of the actual people, places and
    events (Wikimedia Commons); stock footage is used when there is none. terms: [[term, ...] per scene];
    lengths: seconds each scene lasts. Returns ([path or None per scene], [(site, credit)], [term used per scene]).
    """
    os.makedirs(destination_dir, exist_ok=True)
    paths, credits, used_terms, used_ids, cache, real_cache, uses = [], [], [], set(), {}, {}, {}
    for index, options in enumerate(terms):
        wanted = clip_type(style, index)
        path, chosen = None, None
        if style == "real" and options:
            target = os.path.join(destination_dir, f"{prefix}_scene{index + 1:03d}{REAL_SUFFIX}")
            scene_seconds = lengths[index] if lengths and index < len(lengths) else 8
            try:
                seconds = min(max(scene_seconds + 1, 4), 30)
                got = real_clip(options[0], target, seconds, orientation, used_ids, real_cache, uses)
                kind = "real footage"
                if not got:
                    # No video of it: a real photo of the person, place or event, slowly zooming
                    got = real_photo(options[0], target, seconds, orientation, used_ids, real_cache)
                    kind = "real photo"
                if got:
                    path, chosen = target, options[0]
                    credits.append((COMMONS_SITE, got[1]))
                    print(f"[Stock footage] Scene {index + 1}: '{options[0]}' -> {kind}: {got[0][5:75]}")
            except (requests.RequestException, subprocess.SubprocessError, OSError, ValueError, KeyError) as e:
                print(f"[Stock footage] Scene {index + 1}: real footage for '{options[0]}' failed "
                      f"({type(e).__name__}); using stock footage.")
                path = None
        try:
            for term in ([] if path else options):
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
                        credits.append((PROVIDERS[provider]["name"], credit))
                        chosen = term
                        print(f"[Stock footage] Scene {index + 1}: '{term}' -> {PROVIDERS[provider]['name']} "
                              f"video {clip_id}")
                        break
                if not path and provider == "pixabay" and style in ("animation", "mixed"):
                    # No video really shows it: a matching cartoon drawing, gently zooming, before trying the
                    # next (more generic) term
                    key = ("illustration", term)
                    if key not in cache:
                        cache[key] = with_retries(lambda: pixabay_illustrations(api_key, term, kids))
                    seconds = lengths[index] + 1 if lengths and index < len(lengths) else 8
                    target = os.path.join(destination_dir, f"{prefix}_scene{index + 1:03d}.mp4")
                    # Pixabay's image server sometimes refuses one picture for a while: try a few matching ones
                    for image_id, url, credit in [c for c in cache[key] if c[0] not in used_ids][:4]:
                        used_ids.add(image_id)
                        try:
                            photo_clip(url, target, min(max(seconds, 3), 30), orientation)
                        except (requests.RequestException, subprocess.SubprocessError):
                            continue
                        path, chosen = target, term
                        credits.append(("Pixabay", credit))
                        print(f"[Stock footage] Scene {index + 1}: '{term}' -> Pixabay cartoon illustration "
                              f"{image_id[3:]}")
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
        pace()
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


def tags_match(tags, term):
    """True if a clip's tags share a SUBJECT word with the search term (allowing plurals and word endings).

    Style words ("cartoon", "funny", "cute"...) don't count: a clip tagged only "cartoon" doesn't show a
    "cartoon turtle". They are used only when the term has nothing else.
    """
    words = key_words(term)
    if not words:
        return True
    subjects = [w for w in words if w not in STYLE_WORDS] or words
    tag_words = re.findall(r"[a-z0-9]+", tags.lower())
    return any(t[:5] == w[:5] if min(len(t), len(w)) >= 5 else t == w for w in subjects for t in tag_words)


def pixabay_illustrations(api_key, term, kids):
    """[(id, image url, artist)] of Pixabay cartoon illustrations whose tags match the term's subject."""
    pace()
    params = {"key": api_key, "q": term[:100], "image_type": "illustration", "per_page": 30, "order": "popular",
              "safesearch": "true" if kids else "false"}
    response = requests.get(PIXABAY_IMAGES_URL, params=params, timeout=30)
    response.raise_for_status()
    return [(f"img{h['id']}", h.get("largeImageURL") or h["webformatURL"], h.get("user", "Pixabay"))
            for h in response.json().get("hits", [])
            if tags_match(h.get("tags", ""), term) and (h.get("imageWidth") or 0) >= 1000]


def pixabay_candidates(api_key, term, kids, video_type, orientation):
    params = {"key": api_key, "q": term[:100], "per_page": 30, "order": "popular",
              "safesearch": "true" if kids else "false"}
    if video_type in ("animation", "film"):
        params["video_type"] = video_type
    pace()
    response = requests.get(PIXABAY_URL, params=params, timeout=30)
    response.raise_for_status()
    # Pixabay ranks popular clips even when they don't match; keep only clips whose tags share a word with the term
    hits = [h for h in response.json().get("hits", []) if h.get("duration", 0) >= 3 and tags_match(h.get("tags", ""), term)]

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
    if style == "real":
        return "film"
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
