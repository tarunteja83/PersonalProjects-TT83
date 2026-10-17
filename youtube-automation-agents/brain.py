"""Loads BRAIN.md (global rules), execution/channels.yaml (who each channel is) and
execution/questions.yaml (what and when each channel posts), and works out GitHub schedules."""
import os
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
BRAIN_FILE = os.path.join(PROJECT_DIR, "BRAIN.md")
CHANNELS_FILE = os.path.join(PROJECT_DIR, "execution", "channels.yaml")
QUESTIONS_FILE = os.path.join(PROJECT_DIR, "execution", "questions.yaml")

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DEFAULT_TIMEZONE = "Asia/Kolkata"

FREQUENCIES = {"15 mins": 15, "30 mins": 30, "1 hour": 60, "3 hours": 180}
LANGUAGES = {"English": "en-", "Hindi": "hi-", "Telugu": "te-"}
DEFAULT_VOICES = {
    # (language, made_for_kids) -> voice
    ("English", False): "en-US-AndrewNeural",
    ("English", True): "en-US-AnaNeural",
    ("Hindi", False): "hi-IN-MadhurNeural",
    ("Hindi", True): "hi-IN-SwaraNeural",
    ("Telugu", False): "te-IN-MohanNeural",
    ("Telugu", True): "te-IN-ShrutiNeural",
}
SPEEDS = ["-15%", "-5%", "+0%", "+10%"]
VIDEO_LENGTHS = [15, 30, 45, 60]
VISIBILITIES = ["public", "unlisted", "private"]
FOOTAGE_STYLES = ["any", "animation", "film", "mixed"]
LONG_MINUTES = [5, 6, 7, "5-7"]
MAX_LONG_VIDEOS_PER_DAY = 4
MAX_VIDEOS_PER_DAY = 50
# Upload videos, and read which channel a login belongs to (checked before every upload)
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


# ---------- loading ----------

def load_brain(brain_file=BRAIN_FILE, channels_file=CHANNELS_FILE, questions_file=QUESTIONS_FILE):
    with open(brain_file, "r", encoding="utf-8") as f:
        raw_md = f.read()

    fence = "`" * 3
    yaml_match = re.search(fence + r"yaml\n(.*?)\n" + fence, raw_md, re.DOTALL)
    if not yaml_match:
        raise ValueError("Invalid BRAIN.md: Missing yaml configuration block.")

    config = yaml.safe_load(yaml_match.group(1))
    instructions = raw_md.split("## AGENT INSTRUCTIONS")[-1].strip()
    config["profiles"] = load_profiles(channels_file, questions_file)
    apply_media_folders(config)
    return config, instructions


def apply_media_folders(config):
    """Local runs keep video files in <root>/<YYYY-MM-DD>/download and /upload, so whole days are easy to delete.

    GitHub runs store nothing between runs, so they keep the storage_paths folders from BRAIN.md.
    """
    media = config.get("local_media") or {}
    if os.getenv("GITHUB_ACTIONS") == "true" or not media.get("enabled", True):
        config["storage_mode"] = "cloud"
        return
    root = os.path.normpath(os.path.expanduser(media.get("root", "~/YouTube_Auto")))
    if not os.path.isdir(os.path.splitdrive(root)[0] + os.sep):
        # e.g. Google Drive for Desktop isn't running, so its drive letter is missing
        fallback = os.path.normpath(os.path.expanduser("~/YouTube_Auto"))
        print(f"[Storage] {root} isn't available; using {fallback} for video files this run.")
        root = fallback
    day = os.path.join(root, datetime.now().strftime("%Y-%m-%d"))
    paths = config["storage_paths"]
    paths["raw_downloads"] = os.path.join(day, "download")
    paths["audio_renders"] = os.path.join(day, "upload")
    paths["processed_videos"] = os.path.join(day, "upload")
    config["storage_mode"] = "local"
    config["media_root"] = root


def read_section(path, section):
    with open(path, "r", encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get(section) or {}


def load_profiles(channels_file=CHANNELS_FILE, questions_file=QUESTIONS_FILE):
    channels = read_section(channels_file, "channels")
    answers = read_section(questions_file, "channels")

    problems = validate_channels(channels) + validate_answers(answers, channels)
    if problems:
        raise ValueError("Please fix these problems:\n  - " + "\n  - ".join(problems))

    return {key: build_profile(channels[key], answers.get(key)) for key in channels}


def build_profile(channel, answers):
    """Combine a channel's identity with its answers into the settings the agents use."""
    kids = bool(channel["made_for_kids"])
    a = answers or {}
    language = a.get("language") or "English"
    genre = a.get("genre") or channel["channel_name"]

    return {
        # From the channel page
        "channel_name": channel["channel_name"],
        "channel_url": channel["channel_url"],
        "made_for_kids": kids,
        "watermark_text": channel.get("watermark_text") or "@" + re.sub(r"[^A-Za-z0-9]", "", channel["channel_name"]),
        "category_id": str(channel.get("category_id") or ("27" if kids else "24")),
        "logo_file": channel.get("logo_file"),
        # From the questions file
        "has_answers": answers is not None,
        "enabled": answers is not None and a.get("run", True) is not False,
        "niche": genre,
        "search_queries": a.get("search_phrases") or [f"{genre} #shorts"],
        "language": language,
        "voice": a.get("voice") or DEFAULT_VOICES[(language, kids)],
        "voice_rate": a.get("voice_speed") or ("-15%" if kids else "-5%"),
        "tone": a.get("tone") or ("Enthusiastic, warm, curious storyteller for young children." if kids
                                  else "Clear, engaging and informative."),
        "target_duration_seconds": int(a.get("video_length_seconds") or 30),
        "avoid_terms": a.get("avoid_words") or [],
        "lookback_days": int(a.get("lookback_days") or 5),
        "max_videos_per_run": int(a.get("videos_per_day") or 1),
        "privacy_status": a.get("visibility") or "public",
        "test_run": bool(a.get("test_run", False)),
        "exclude_sources": a.get("exclude_sources") or [],
        "footage_style": a.get("footage_style") or "any",
        "schedule": {
            "timezone": a.get("timezone") or DEFAULT_TIMEZONE,
            "post_times": [a["fetch_time"]] if a.get("fetch_time") else [],
            "days": a.get("days") or "daily",
        },
        "posting": {
            "first_post_time": a.get("first_post_time"),
            "frequency_minutes": FREQUENCIES.get(a.get("post_frequency") or "1 hour", 60),
        },
        # Long (landscape, 5 to 7 minute) videos, prepared in the same run as the Shorts
        "long": {
            "count": int(a.get("long_videos_per_day") or 0),
            "minutes": a.get("long_video_minutes") or 6,
            "post_times": [str(t) for t in (a.get("long_post_times") or [])],
        },
    }


# ---------- validation ----------

def validate_channels(channels):
    if not channels:
        return ["channels.yaml: no channels listed under 'channels:'."]
    problems = []
    for key, details in channels.items():
        where = f"channels.yaml '{key}'"
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", str(key)):
            problems.append(f"{where}: key must be lowercase letters, numbers and underscores, starting with a letter")
        if not isinstance(details, dict):
            problems.append(f"{where}: details must be indented under the key")
            continue
        for field in ("channel_name", "channel_url", "made_for_kids"):
            if details.get(field) in (None, ""):
                problems.append(f"{where}: missing required field '{field}'")
        if details.get("channel_url") and not parse_channel_url(details["channel_url"]):
            problems.append(f"{where}: channel_url must look like https://www.youtube.com/@handle "
                            "or https://www.youtube.com/channel/UC...")
    return problems


def item_matches(item, channel_key, profile):
    """True if a manifest item belongs to this channel and to the format (short or long) being processed."""
    return item.get("profile") == channel_key and item.get("format", "short") == profile.get("format", "short")


def parse_channel_url(url):
    """Return ("handle", "@name") or ("id", "UC...") from a YouTube channel link, or None if unrecognised."""
    match = re.search(r"youtube\.com/(@[\w.\-]+)", str(url))
    if match:
        return "handle", match.group(1).lower()
    match = re.search(r"youtube\.com/channel/(UC[\w\-]{22})", str(url))
    if match:
        return "id", match.group(1)
    return None


def channel_matches(url, channel_id, custom_url):
    """True if the signed-in channel (its ID and @handle from the YouTube API) is the one the link points to."""
    kind, value = parse_channel_url(url)
    if kind == "id":
        return channel_id == value
    return (custom_url or "").lower() == value


def validate_answers(answers, channels):
    problems = []
    for key, a in answers.items():
        where = f"questions.yaml '{key}'"
        if key not in channels:
            problems.append(f"{where}: no channel with this key in channels.yaml")
            continue
        if not isinstance(a, dict):
            problems.append(f"{where}: answers must be indented under the channel key")
            continue

        language = a.get("language", "English")
        if language not in LANGUAGES:
            problems.append(f"{where}: language must be one of {', '.join(LANGUAGES)}")
        voice = a.get("voice")
        if voice:
            if not re.fullmatch(r"[a-z]{2,3}-[A-Z]{2,4}-\w+Neural", str(voice)):
                problems.append(f"{where}: voice '{voice}' should look like en-US-AndrewNeural")
            elif language in LANGUAGES and not str(voice).startswith(LANGUAGES[language]):
                problems.append(f"{where}: voice '{voice}' does not speak {language}; pick a {LANGUAGES[language]}... voice")
        checks = [
            ("voice_speed", SPEEDS), ("video_length_seconds", VIDEO_LENGTHS),
            ("post_frequency", list(FREQUENCIES)), ("visibility", VISIBILITIES), ("footage_style", FOOTAGE_STYLES),
        ]
        for field, allowed in checks:
            if a.get(field) is not None and a[field] not in allowed:
                problems.append(f"{where}: {field} must be one of {', '.join(str(x) for x in allowed)}")
        for field in ("search_phrases", "avoid_words", "exclude_sources"):
            value = a.get(field)
            if value is not None and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
                problems.append(f'{where}: {field} must be a list in quotes, e.g. ["one", "two"] or []')
        videos = a.get("videos_per_day")
        if videos is not None and not (isinstance(videos, int) and 1 <= videos <= MAX_VIDEOS_PER_DAY):
            problems.append(f"{where}: videos_per_day must be a whole number from 1 to {MAX_VIDEOS_PER_DAY}")
        long_count = a.get("long_videos_per_day")
        if long_count is not None and not (isinstance(long_count, int) and 0 <= long_count <= MAX_LONG_VIDEOS_PER_DAY):
            problems.append(f"{where}: long_videos_per_day must be a whole number from 0 to {MAX_LONG_VIDEOS_PER_DAY}")
        if a.get("long_video_minutes") is not None and a["long_video_minutes"] not in LONG_MINUTES:
            problems.append(f'{where}: long_video_minutes must be one of 5, 6, 7 or "5-7"')
        long_times = a.get("long_post_times") or []
        if not isinstance(long_times, list):
            problems.append(f'{where}: long_post_times must be a list, e.g. ["13:00", "19:00"]')
            long_times = []
        for t in long_times:
            try:
                parse_time(t)
            except ValueError:
                problems.append(f"{where}: long post time '{t}' must be HH:MM in 24-hour format, in quotes")
        if isinstance(long_count, int) and long_count > len(long_times):
            problems.append(f"{where}: give one long_post_times entry per long video "
                            f"({long_count} needed, {len(long_times)} given)")
        lookback = a.get("lookback_days")
        if lookback is not None and not (isinstance(lookback, int) and 1 <= lookback <= 30):
            problems.append(f"{where}: lookback_days must be a whole number from 1 to 30")
        for field in ("fetch_time", "first_post_time"):
            if a.get(field) is not None:
                try:
                    parse_time(a[field])
                except ValueError:
                    problems.append(f"{where}: {field} '{a[field]}' must be HH:MM in 24-hour format, in quotes")
        try:
            ZoneInfo(a.get("timezone") or DEFAULT_TIMEZONE)
        except (ZoneInfoNotFoundError, ValueError):
            problems.append(f"{where}: unknown timezone '{a.get('timezone')}'")
        try:
            parse_days(a.get("days") or "daily")
        except ValueError:
            problems.append(f"{where}: days must be daily, weekdays, weekends or a list like [mon, wed, fri]")
    return problems


# ---------- time helpers ----------

def parse_time(value):
    # YAML reads an unquoted 09:30 as the base-60 integer 570, so accept both forms
    if isinstance(value, int):
        return time(value // 60, value % 60)
    hours, minutes = str(value).strip().split(":")
    return time(int(hours), int(minutes))


def parse_days(value):
    if value in (None, "daily", "everyday", "all"):
        return list(range(7))
    if value == "weekdays":
        return list(range(5))
    if value == "weekends":
        return [5, 6]
    if isinstance(value, str):
        value = value.split(",")
    return sorted({DAY_NAMES.index(str(day).strip().lower()[:3]) for day in value})


def utc_slots(profile, ref_date=None):
    """Map each fetch time to UTC as {(minute, hour): {cron weekdays}} (cron weekdays: 0 = Sunday)."""
    schedule = profile.get("schedule") or {}
    tz = ZoneInfo(schedule.get("timezone", DEFAULT_TIMEZONE))
    ref = ref_date or datetime.now(tz).date()
    slots = {}

    for post_time in schedule.get("post_times") or []:
        local_time = parse_time(post_time)
        for weekday in parse_days(schedule.get("days", "daily")):
            local_date = ref + timedelta(days=(weekday - ref.weekday()) % 7)
            utc_dt = datetime.combine(local_date, local_time, tzinfo=tz).astimezone(timezone.utc)
            slots.setdefault((utc_dt.minute, utc_dt.hour), set()).add((utc_dt.weekday() + 1) % 7)

    return slots


def format_cron_days(days):
    return "*" if len(days) == 7 else ",".join(str(day) for day in sorted(days))


def cron_lines(config):
    merged = {}
    for profile in config["profiles"].values():
        if not profile.get("enabled", True):
            continue
        for slot, days in utc_slots(profile).items():
            merged.setdefault(slot, set()).update(days)

    ordered = sorted(merged.items(), key=lambda item: (item[0][1], item[0][0]))
    return [f"{minute} {hour} * * {format_cron_days(days)}" for (minute, hour), days in ordered]


def due_profiles(config, cron, now=None):
    now = now or datetime.now(timezone.utc)
    minute, hour = (int(part) for part in cron.split()[:2])

    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if scheduled > now:
        # GitHub started the run late, after midnight UTC
        scheduled -= timedelta(days=1)
    cron_day = (scheduled.weekday() + 1) % 7

    return [
        key for key, profile in config["profiles"].items()
        if profile.get("enabled", True) and cron_day in utc_slots(profile).get((minute, hour), set())
    ]
