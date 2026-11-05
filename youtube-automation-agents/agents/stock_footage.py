"""Free stock footage from Pixabay or Pexels, used instead of re-using trending YouTube videos.

YouTube blocks video downloads from cloud servers such as GitHub's, while these APIs work everywhere and their clips
are free to use. Both ask that you show where the footage came from, so each description credits the site.

footage.provider in BRAIN.md: "pixabay", "pexels", or "auto" (whichever has a key: PIXABAY_API_KEY, then PEXELS_API_KEY).
"""
import html
import io
import os
import re
import shutil
import subprocess
import time

import imageio_ffmpeg
import requests
from PIL import Image

from agents.ai import generate_text, look_at_pictures

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
# Footage check (owner, 5 Nov 2026: clips didn't match the words): each scene's candidates are shown to Gemini as
# thumbnails next to the scene's narration, and it picks the one that shows what is said
CANDIDATES_PER_SCENE = 5
SCENES_PER_CHECK = 8
OPTION_LETTERS = "abcde"
THUMB_WIDTH = 320
# Real (Commons) material dated more than this many years ago is archive footage, kept out of current stories
OLD_YEARS = 15


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


def commons_about(title, meta):
    """What a Commons file shows, for the footage check: its name, date and description."""
    date = re.sub(r"<[^>]+>", "", meta.get("DateTimeOriginal", {}).get("value", ""))[:10]
    text = re.sub(r"<[^>]+>", " ", meta.get("ImageDescription", {}).get("value", ""))
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return title[5:] + (f" (dated {date})" if date else "") + (f": {text[:200]}" if text else "")


def commons_credit(meta, page, licence):
    artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", ""))
    artist = re.sub(r"\s+", " ", html.unescape(artist)).strip() or "unknown"
    return f"{artist[:60]}, {licence}, commons.wikimedia.org/?curid={page.get('pageid')}"


def commons_candidates(term, orientation="portrait", min_seconds=4):
    """Freely licensed real footage whose title names the term: [{key, kind, site, url, credit, duration, thumb,
    about}]."""
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
        "viprop": "url|size|extmetadata|derivatives", "viurlwidth": THUMB_WIDTH, "format": "json"}).json()
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
        found.append({"key": title, "kind": "real footage", "site": COMMONS_SITE, "url": best["src"],
                      "credit": commons_credit(meta, page, licence), "duration": float(details["duration"]),
                      "thumb": details.get("thumburl"), "about": commons_about(title, meta)})
    return found


def commons_photo_candidates(term, orientation="portrait", min_width=800):
    """Freely licensed real photos whose title names the term (shown as a slow zoom): [{key, kind, site, url,
    credit, thumb, about}]."""
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
        url = details.get("thumburl") or details["url"]
        found.append({"key": title, "kind": "real photo", "site": COMMONS_SITE, "url": url,
                      "credit": commons_credit(meta, page, licence),
                      "thumb": url.replace("/1920px-", f"/{THUMB_WIDTH}px-"), "about": commons_about(title, meta)})
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


def has_picture(path):
    """True if the video file has at least one decodable frame."""
    exe = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-i", path, "-map", "0:v:0", "-frames:v", "1",
                             "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True, timeout=60)
    return result.returncode == 0 and len(result.stdout) > 0


def download_segment(url, target, start, seconds):
    """Fetch only `seconds` of video from `start`: FFmpeg seeks over HTTP, so long videos aren't downloaded whole."""
    exe = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", "-user_agent", COMMONS_HEADERS["User-Agent"],
                    "-ss", f"{start:.2f}", "-i", url, "-t", f"{seconds:.2f}", "-an", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "23", target], check=True, timeout=300, capture_output=True)
    # FFmpeg can finish "successfully" with no picture (e.g. a seek past the real end of the video); the editor then
    # failed on that scene and the whole video was lost (31 Oct and 1 Nov runs). Stock footage is used instead.
    if not has_picture(target):
        os.remove(target)
        raise ValueError("the downloaded clip has no picture")


def segment_start(duration, seconds):
    """Skip the first quarter of longer videos (titles, intros) but stay inside the video."""
    return max(0.0, min(duration * 0.25, duration - seconds - 0.5)) if duration > seconds + 1 else 0.0


def name_only(term):
    """'Narendra Modi summit' -> 'Narendra Modi': the first two words of a longer term, tried when the full term finds
    nothing. Two-word terms aren't shortened ('Western intelligence' must not become 'Western')."""
    words = key_words(term)
    return " ".join(words[:2]) if len(words) > 2 else ""


# ---------- choosing the footage that shows what is said ----------

def archive_material(candidate, term):
    """True for archive material (a 1914 trench photo, a decades-old negative) when the term isn't about the past."""
    if re.search(r"\b1[0-9]{3}\b|\b20[0-9]{2}\b|world war|\bww ?(?:1|2|i|ii)\b", term, re.IGNORECASE):
        return False
    years = [int(y) for y in re.findall(r"\b(1[89][0-9]{2}|20[0-9]{2})\b", candidate["about"])]
    return "negative" in candidate["about"].lower() or bool(years and max(years) < time.localtime().tm_year - OLD_YEARS)


def scene_candidates(provider, api_key, options, style, index, kids, orientation, cache):
    """Up to CANDIDATES_PER_SCENE ways to show one scene, the most specific first: for each of its terms (specific,
    then backup) real footage and photos (style "real"), stock clips, and cartoon pictures (cartoon styles)."""
    def cached(key, search):
        if key not in cache:
            try:
                cache[key] = with_retries(search) or []
            except (requests.RequestException, ValueError, KeyError) as e:
                print(f"[Stock footage] Search for '{key[1]}' failed: {safe_error(e, api_key)}")
                cache[key] = []
        return cache[key]

    wanted = clip_type(style, index)
    per_term = []
    for n, term in enumerate(options):
        found = []
        if style == "real" and n == 0:
            short = name_only(term)
            videos = cached(("commons", term), lambda: commons_candidates(term, orientation)
                            or (commons_candidates(short, orientation) if short else []))
            found += videos[:3]
            if len(videos) < 2:
                found += cached(("photo", term), lambda: commons_photo_candidates(term, orientation)
                                or (commons_photo_candidates(short, orientation) if short else []))[:3]
        for video_type in ([wanted, None] if wanted else [None]):
            stock = cached((provider, term, video_type),
                           lambda: CANDIDATES[provider](api_key, term, kids, video_type, orientation))
            found += stock[:3]
            if stock:
                break
        if provider == "pixabay" and style in ("animation", "mixed"):
            found += cached(("illustration", term), lambda: pixabay_illustrations(api_key, term, kids))[:2]
        per_term.append([dict(c, term=term) for c in found
                         if not (c["site"] == COMMONS_SITE and archive_material(c, term))])
    # Most from the specific term, some from the backup, so the check can fall back to a generic but true picture
    ordered = [c for share, group in zip((3, 2, 1), per_term) for c in group[:share]]
    ordered += [c for group in per_term for c in group]
    chosen, seen = [], set()
    for c in ordered:
        if c["key"] not in seen:
            seen.add(c["key"])
            chosen.append(c)
    return chosen[:CANDIDATES_PER_SCENE]


def thumbnail(url):
    """A small JPEG of a candidate's picture for the footage check, or None."""
    if not url:
        return None
    try:
        response = requests.get(url, headers=COMMONS_HEADERS, timeout=20)
        response.raise_for_status()
        picture = Image.open(io.BytesIO(response.content)).convert("RGB")
        picture.thumbnail((THUMB_WIDTH, THUMB_WIDTH))
        out = io.BytesIO()
        picture.save(out, "JPEG", quality=80)
        return out.getvalue()
    except Exception:
        return None


def check_prompt(context, kids, listing, seeing):
    return (
        f"You choose the footage for a narrated YouTube video ({context}). For each SCENE you get the words spoken "
        "during it and up to 5 options, each with a description" + (" and a picture" if seeing else "") + ". "
        + ("" if seeing else "You can't see the pictures: judge from the descriptions. ")
        + "Pick the option that shows what is being said at that moment: the same person, place, animal, object or "
        "action. When the words are about a person, any picture of that person fits, wherever it was taken"
        + ("" if kids else "; for events, places and armies it must be the right country and time (a current story "
           "never gets pictures of an old war, another country's army, or archive photos and negatives from decades "
           "ago, unless the words are about them)")
        + ". A general picture that clearly fits the words (a map of the region, a flag, the right kind of animal "
        "doing the right thing, the right kind of landscape) is fine. When several fit, prefer moving footage to a "
        "photo. Reject options that show something else, are mostly text, a logo or a blank frame, or would confuse "
        "or mislead a viewer" + (", and anything not suitable for young children" if kids else "") + ".\n"
        'Reply with one line per scene and nothing else: "Scene N: b" (or "Scene N: b, a" with a second choice) or '
        '"Scene N: none" when no option shows it.\n\n' + listing
    )


def check_footage(ai_client, models, texts, pools, context, kids):
    """For each scene, the indexes of its candidates that show what is said, best first ([] = none does), or None
    where the check couldn't run (the candidates are then tried in search order).

    Gemini sees each candidate's thumbnail (SCENES_PER_CHECK scenes per request); when it can't, the backup AI
    judges from the titles, dates and descriptions alone.
    """
    picks = [None] * len(pools)
    scenes = [i for i, pool in enumerate(pools) if pool]
    for start in range(0, len(scenes), SCENES_PER_CHECK):
        group = scenes[start:start + SCENES_PER_CHECK]
        pictures, listing = [], []
        for i in group:
            listing.append(f'SCENE {i + 1}, narration: "{" ".join(texts[i].split())[:350]}"')
            for letter, c in zip(OPTION_LETTERS, pools[i]):
                label = f"Scene {i + 1} option {letter} ({c['kind']}): {c['about'][:220]}"
                listing.append("  " + label)
                data = thumbnail(c.get("thumb"))
                if data:
                    pictures.append((label, data))
        reply = None
        if pictures:
            reply = look_at_pictures(ai_client, pictures, check_prompt(context, kids, "\n".join(listing), True),
                                     models)
        if reply is None:
            try:
                reply = generate_text(ai_client, check_prompt(context, kids, "\n".join(listing), False), models)
            except Exception as e:
                print(f"[Stock footage] Footage check unavailable for scenes {group[0] + 1} to {group[-1] + 1} "
                      f"({str(e)[:120]}); using search order.")
                continue
        for number, choice in re.findall(r"scene\s*(\d+)\s*[:\-]\s*(none|[a-e](?:\s*,\s*[a-e])*)", reply, re.I):
            i = int(number) - 1
            if i in group:
                letters = [] if choice.lower() == "none" else re.findall(r"[a-e]", choice.lower())
                picks[i] = [OPTION_LETTERS.index(x) for x in letters if OPTION_LETTERS.index(x) < len(pools[i])]
    checked = [p for p in picks if p is not None]
    print(f"[Stock footage] Footage check: {sum(1 for p in checked if p)} of {len(scenes)} scenes have a matching "
          f"option, {sum(1 for p in checked if not p)} have none, {len(scenes) - len(checked)} unchecked.")
    return picks


def take(candidate, path, seconds, orientation, uses):
    """Download one candidate as the scene's clip."""
    if candidate["kind"] == "real footage":
        duration = candidate["duration"]
        # A real video used again starts from a later moment
        start = segment_start(duration, seconds) + uses.get(candidate["key"], 0) * (seconds + 2)
        if start + seconds > duration:
            start = (start % max(duration - seconds, 1.0)) if duration > seconds else 0.0
        uses[candidate["key"]] = uses.get(candidate["key"], 0) + 1
        download_segment(candidate["url"], path, start, seconds)
    elif candidate["kind"] in ("real photo", "cartoon picture"):
        photo_clip(candidate["url"], path, seconds, orientation)
    else:
        with_retries(lambda: download_file(candidate["url"], path))


def fetch_scene_clips(provider, api_key, terms, destination_dir, prefix, kids=False, style="any",
                      orientation="portrait", lengths=None, ai_client=None, models=None, texts=None, context=""):
    """One clip per scene, in scene order, that shows what the scene's narration says.

    terms: [[specific term, backup term, ...] per scene]; texts: each scene's spoken words; lengths: seconds each
    scene lasts. Each scene's candidates (scene_candidates) are checked against its words (check_footage) and the
    best one that downloads is used; a scene where none fits reuses a neighbouring clip. Returns ([path or None per
    scene], [(site, credit)], [term used per scene]).
    """
    os.makedirs(destination_dir, exist_ok=True)
    cache = {}
    pools = [scene_candidates(provider, api_key, options, style, index, kids, orientation, cache)
             for index, options in enumerate(terms)]
    picks = (check_footage(ai_client, models, texts, pools, context, kids) if ai_client and texts
             else [None] * len(pools))
    paths, credits, used_terms, used_ids, uses = [], [], [], set(), {}
    for index, pool in enumerate(pools):
        order = pool if picks[index] is None else [pool[i] for i in picks[index]]
        scene_seconds = lengths[index] if lengths and index < len(lengths) else 8
        path, chosen = None, None
        for c in order:
            # Real videos may be reused from a later moment; any other clip is shown once
            if c["key"] in used_ids and c["kind"] != "real footage":
                continue
            real = c["site"] == COMMONS_SITE
            target = os.path.join(destination_dir, f"{prefix}_scene{index + 1:03d}{REAL_SUFFIX if real else '.mp4'}")
            seconds = min(max(scene_seconds + 1, 4 if real else 3), 30)
            try:
                take(c, target, seconds, orientation, uses)
            except (requests.RequestException, subprocess.SubprocessError, OSError, ValueError, KeyError) as e:
                print(f"[Stock footage] Scene {index + 1}: {c['kind']} failed ({type(e).__name__}); trying the next.")
                continue
            used_ids.add(c["key"])
            credits.append((c["site"], c["credit"]))
            path, chosen = target, c["term"]
            print(f"[Stock footage] Scene {index + 1}: '{c['term']}' -> {c['kind']}: {c['about'][:80]}")
            break
        if not path:
            why = "no option shows what is said" if picks[index] == [] else f"no clip for {' / '.join(terms[index])}"
            print(f"[Stock footage] Scene {index + 1}: {why}; a neighbouring clip will be reused.")
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
        # Pexels gives no tags; its page address names what the clip shows
        return [{"key": v["id"], "kind": "stock video", "site": "Pexels", "url": pick_file(v, orientation)["link"],
                 "credit": v.get("user", {}).get("name", "Pexels"), "thumb": v.get("image"),
                 "about": v.get("url", "").rstrip("/").split("/")[-1].replace("-", " ")}
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
    """Pixabay cartoon illustrations whose tags match the term's subject: [{key, kind, site, url, credit, thumb,
    about}]."""
    pace()
    params = {"key": api_key, "q": term[:100], "image_type": "illustration", "per_page": 30, "order": "popular",
              "safesearch": "true" if kids else "false"}
    response = requests.get(PIXABAY_IMAGES_URL, params=params, timeout=30)
    response.raise_for_status()
    return [{"key": f"img{h['id']}", "kind": "cartoon picture", "site": "Pixabay",
             "url": h.get("largeImageURL") or h["webformatURL"], "credit": h.get("user", "Pixabay"),
             "thumb": h.get("webformatURL") or h.get("previewURL"), "about": f"tags: {h.get('tags', '')}"}
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
    # Pixabay ranks popular clips even when they don't match; keep only clips whose tags share a word with the term,
    # and not the ones Pixabay itself marks as low quality
    hits = [h for h in response.json().get("hits", []) if h.get("duration", 0) >= 3 and not h.get("isLowQuality")
            and tags_match(h.get("tags", ""), term)]

    def is_portrait(hit):
        medium = hit["videos"].get("medium", {})
        return medium.get("height", 0) > medium.get("width", 1)

    # Prefer clips that already match the output shape; others are centre-cropped by the editor
    hits.sort(key=lambda h: 0 if is_portrait(h) == (orientation == "portrait") else 1)
    found = []
    for hit in hits:
        rendition = pick_rendition(hit["videos"], orientation)
        if rendition:
            about = "; ".join(x for x in (hit.get("name") or "", f"tags: {hit.get('tags', '')}",
                                          (hit.get("description") or "")[:150]) if x)
            found.append({"key": hit["id"], "kind": "stock video", "site": "Pixabay", "url": rendition["url"],
                          "credit": hit.get("user", "Pixabay"), "about": about,
                          "thumb": (hit["videos"].get("medium") or {}).get("thumbnail") or rendition.get("thumbnail")})
    return found


CANDIDATES = {"pexels": pexels_candidates, "pixabay": pixabay_candidates}


def clip_type(style, index):
    """footage_style -> Pixabay video_type for the index-th clip ("mixed" alternates cartoon and real)."""
    if style == "mixed":
        return "animation" if index % 2 == 0 else "film"
    if style == "real":
        return "film"
    return style if style in ("animation", "film") else None
