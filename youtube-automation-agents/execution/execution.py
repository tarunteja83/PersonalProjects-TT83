"""The single entry point for the YouTube automation pipeline. Read execution/execution.md first.

Answers to every question live in execution/questions.yaml; this file reads them each time it runs.

    python execution/execution.py                        # run every channel with run: true in questions.yaml
    python execution/execution.py --channel kids_edu     # run one channel (or --channel all)
    python execution/execution.py --check                # validate both pages, show the plan, update GitHub schedule
    python execution/execution.py --list                 # show channels and their posting plans
    python execution/execution.py --authorize kids_edu   # sign in to YouTube for a channel (once)
    python execution/execution.py --ask                  # answer the questions interactively for a one-off run

One-off overrides for a run: --genre "..." --videos 6 --frequency "30 mins" --first-post "09:00" --dry-run
GitHub uses: --scheduled --cron "<cron>"  and  --due --cron "<cron>"
"""
import argparse
import copy
import html
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

EXECUTION_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(EXECUTION_DIR)
REPO_DIR = os.path.dirname(PROJECT_DIR)
WORKFLOW_FILE = os.path.join(REPO_DIR, ".github", "workflows", "daily_youtube_agents.yml")

sys.path.insert(0, PROJECT_DIR)
# The agents use paths relative to the project folder, wherever this file is started from
os.chdir(PROJECT_DIR)

from brain import (  # noqa: E402
    DEFAULT_VOICES, FREQUENCIES, LANGUAGES, MAX_VIDEOS_PER_DAY, SPEEDS, YOUTUBE_SCOPES,
    channel_matches, cron_lines, due_profiles, fetch_time_for_cron, load_brain, parse_time,
)

SCHEDULE_BEGIN = "# BEGIN CHANNEL SCHEDULE"
SCHEDULE_END = "# END CHANNEL SCHEDULE"
RUNNING_ON_GITHUB = os.getenv("GITHUB_ACTIONS") == "true"
DAILY_UPLOAD_LIMIT = 100
DAILY_SEARCH_LIMIT = 100
# Rough GitHub Actions build times, used only for the --check estimate (measured on October 2026 runs)
GITHUB_SETUP_MINUTES = 2
MINUTES_PER_SHORT = 3
MINUTES_PER_LONG = 12
GITHUB_FREE_MINUTES = 2000

VOICE_CHOICES = {
    "English": ["en-US-AndrewNeural", "en-US-ChristopherNeural", "en-US-GuyNeural", "en-US-AriaNeural",
                "en-US-EmmaNeural", "en-US-AnaNeural", "en-US-JennyNeural", "en-IN-PrabhatNeural", "en-IN-NeerjaNeural"],
    "Hindi": ["hi-IN-MadhurNeural", "hi-IN-SwaraNeural"],
    "Telugu": ["te-IN-MohanNeural", "te-IN-ShrutiNeural"],
}


# ---------- prompt helpers (used by --ask and sign-in) ----------

def ask(question, default=None, validate=None):
    while True:
        suffix = f" [{default}]" if default not in (None, "") else ""
        answer = input(f"{question}{suffix}: ").strip()
        if not answer and default is not None:
            answer = str(default)
        if not answer:
            print("   Please enter a value.")
            continue
        if validate:
            error = validate(answer)
            if error:
                print(f"   {error}")
                continue
        return answer


def ask_yes_no(question, default=True):
    hint = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{question} ({hint}): ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("   Please answer y or n.")


def ask_choice(question, options, default_value=None):
    print(question)
    default_index = 1
    for index, (value, label) in enumerate(options, 1):
        marker = ""
        if value == default_value:
            default_index, marker = index, "  <- current"
        print(f"   {index:>2}. {label}{marker}")

    def validate(answer):
        if not answer.isdigit() or not 1 <= int(answer) <= len(options):
            return f"Enter a number from 1 to {len(options)}."
        return None

    return options[int(ask("   Choose a number", default_index, validate)) - 1][0]


def ask_int(question, default, low, high):
    def validate(answer):
        if not answer.isdigit() or not low <= int(answer) <= high:
            return f"Enter a whole number from {low} to {high}."
        return None
    return int(ask(question, default, validate))


def validate_hhmm(answer):
    if not re.fullmatch(r"\d{1,2}:\d{2}", answer):
        return "Use 24-hour HH:MM, e.g. 07:00."
    try:
        parse_time(answer)
    except ValueError:
        return "That is not a real time of day."
    return None


def split_list(text):
    return [part.strip() for part in text.split(",") if part.strip()]


def heading(title):
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


# ---------- posting plan ----------

def apply_overrides(profile, overrides):
    """Apply one-off command-line or --ask answers on top of questions.yaml (the file itself is not changed)."""
    if overrides.get("genre"):
        profile["niche"] = overrides["genre"]
        profile["search_queries"] = overrides.get("queries") or [f"{overrides['genre']} #shorts"]
    elif overrides.get("queries"):
        profile["search_queries"] = overrides["queries"]
    for key, field in (("videos", "max_videos_per_run"), ("language", "language"), ("voice", "voice"),
                       ("voice_rate", "voice_rate"), ("privacy", "privacy_status")):
        if overrides.get(key):
            profile[field] = overrides[key]
    if overrides.get("frequency"):
        profile["posting"]["frequency_minutes"] = FREQUENCIES[overrides["frequency"]]
    if overrides.get("first_post") or overrides.get("frequency"):
        # A one-off first time or gap replaces the fixed short_post_times
        profile["posting"]["post_times"] = []
    if overrides.get("first_post"):
        profile["posting"]["first_post_time"] = overrides["first_post"]
    if overrides.get("videos") and profile["long"]["count"]:
        # --videos N means at most N of each format
        profile["long"]["count"] = min(profile["long"]["count"], overrides["videos"])
    if overrides.get("post_now"):
        profile["posting"]["first_post_time"] = None
        profile["posting"]["post_times"] = []
        profile["long"]["post_times"] = []


def next_occurrences(times, tz, now, count):
    """The next `count` go-live times (UTC) from a list of daily "HH:MM", at least 30 minutes from now."""
    earliest = now.astimezone(tz) + timedelta(minutes=30)
    upcoming = []
    for days in range(0, count // max(len(times), 1) + 2):
        for t in times:
            local = datetime.combine(earliest.date() + timedelta(days=days), parse_time(t), tzinfo=tz)
            if local >= earliest:
                upcoming.append(local)
    return [t.astimezone(timezone.utc) for t in sorted(upcoming)[:count]]


def publish_plan(profile, now=None):
    """Turn first_post_time and post_frequency into the plan Agent 5 uses to schedule go-live times."""
    tz = ZoneInfo(profile["schedule"]["timezone"])
    if profile["posting"].get("post_times"):
        upcoming = next_occurrences(profile["posting"]["post_times"], tz, now or datetime.now(tz),
                                    profile["max_videos_per_run"])
        return {"times_utc": [t.isoformat() for t in upcoming]}
    first_post = profile["posting"]["first_post_time"]
    if not first_post:
        return None
    today = (now or datetime.now(tz)).astimezone(tz).date()
    first_local = datetime.combine(today, parse_time(first_post), tzinfo=tz)

    fetch_times = profile["schedule"]["post_times"]
    if fetch_times and parse_time(first_post) <= parse_time(fetch_times[0]):
        # e.g. fetch at 23:00 and first post at 07:00 means 07:00 the next morning
        first_local += timedelta(days=1)

    return {
        "first_utc": first_local.astimezone(timezone.utc).isoformat(),
        "interval_minutes": profile["posting"]["frequency_minutes"],
    }


def long_publish_plan(profile, now=None):
    """One fixed go-live time per long video (long_post_times); times already past move to the next day."""
    long = profile["long"]
    if not long["count"] or not long["post_times"]:
        return None
    tz = ZoneInfo(profile["schedule"]["timezone"])
    now_local = (now or datetime.now(tz)).astimezone(tz)
    times = []
    for post_time in long["post_times"][:long["count"]]:
        local = datetime.combine(now_local.date(), parse_time(post_time), tzinfo=tz)
        if local <= now_local:
            local += timedelta(days=1)
        times.append(local.astimezone(timezone.utc))
    return {"times_utc": [t.isoformat() for t in sorted(times)]}


def day_slots(profile, day):
    """Go-live times (local) of one posting day that starts on `day`: (Shorts, long videos).

    A time at or before the day's first fetch time belongs to the next morning (e.g. fetch 23:00, post 07:00).
    """
    tz = ZoneInfo(profile["schedule"]["timezone"])
    fetches = sorted(parse_time(t) for t in profile["schedule"]["post_times"])

    def at(value):
        local = datetime.combine(day, parse_time(value), tzinfo=tz)
        return local + timedelta(days=1) if fetches and parse_time(value) <= fetches[0] else local

    shorts = []
    if profile["posting"].get("post_times"):
        shorts = [at(t) for t in profile["posting"]["post_times"]]
    elif profile["posting"]["first_post_time"]:
        first, gap = at(profile["posting"]["first_post_time"]), timedelta(minutes=profile["posting"]["frequency_minutes"])
        shorts = [first + gap * k for k in range(profile["max_videos_per_run"])]
    longs = [at(t) for t in profile["long"]["post_times"][:profile["long"]["count"]]]
    return shorts, longs


def run_window(profile, now=None, fetch=None):
    """(start, end) local times of this run: from its fetch time up to the channel's next fetch time.

    fetch is the fetch time behind a scheduled run, which GitHub may start hours early or late
    (github_schedule_lead_minutes), so its nearest occurrence is used; manual runs use the window that contains
    the current time.
    """
    tz = ZoneInfo(profile["schedule"]["timezone"])
    now_local = (now or datetime.now(tz)).astimezone(tz)
    times = sorted(parse_time(t) for t in profile["schedule"]["post_times"])
    if not times:
        return None

    def latest(t):
        local = datetime.combine(now_local.date(), t, tzinfo=tz)
        return local - timedelta(days=1) if local > now_local else local

    if fetch in times:
        start = min((datetime.combine(now_local.date() + timedelta(days=d), fetch, tzinfo=tz) for d in (-1, 0, 1)),
                    key=lambda local: abs(local - now_local))
    else:
        start = max(latest(t) for t in times)

    def following(t):
        local = datetime.combine(start.date(), t, tzinfo=tz)
        return local + timedelta(days=1) if local <= start else local

    return start, min(following(t) for t in times)


def run_slots(profile, now=None, fetch=None):
    """This run's go-live times in UTC, (Shorts, long videos): the day's slots that fall inside its window."""
    window = run_window(profile, now, fetch)
    if not window:
        return None
    start, end = window
    shorts, longs = set(), set()
    for offset in (-1, 0, 1):
        day_shorts, day_longs = day_slots(profile, start.date() + timedelta(days=offset))
        shorts.update(t for t in day_shorts if start <= t < end)
        longs.update(t for t in day_longs if start <= t < end)
    return ([t.astimezone(timezone.utc) for t in sorted(shorts)],
            [t.astimezone(timezone.utc) for t in sorted(longs)])


def plan_run(profile, fetch=None, shared=True, now=None):
    """Set this run's video counts and go-live plans on the profile.

    shared: the day's videos are shared between the channel's runs (one per fetch time), each run making the
    videos whose go-live times fall before the next run. Off for one-off runs that set their own counts or times.
    """
    profile["publish_plan"] = publish_plan(profile, now)
    profile["long_publish_plan"] = long_publish_plan(profile, now)
    slots = run_slots(profile, now, fetch) if shared else None
    if not slots:
        return
    shorts, longs = slots
    if profile["posting"].get("post_times") or profile["posting"]["first_post_time"]:
        profile["max_videos_per_run"] = len(shorts)
        profile["publish_plan"] = {"times_utc": [t.isoformat() for t in shorts]}
    if profile["long"]["post_times"]:
        profile["long"]["count"] = len(longs)
        profile["long_publish_plan"] = {"times_utc": [t.isoformat() for t in longs]} if longs else None


def plan_times(plan, tz):
    if not plan or "times_utc" not in plan:
        return []
    return [datetime.fromisoformat(t).astimezone(ZoneInfo(tz)).strftime("%H:%M") for t in plan["times_utc"]]


def describe_run(profile, formats=("long", "short")):
    """e.g. '1 long (12:00) + 2 Shorts (08:00, 11:00)' for the plan set by plan_run."""
    tz = profile["schedule"]["timezone"]
    parts = []
    if "long" in formats and profile["long"]["count"]:
        times = plan_times(profile.get("long_publish_plan"), tz)
        parts.append(f"{profile['long']['count']} long" + (f" ({', '.join(times)})" if times else ""))
    if "short" in formats and profile["max_videos_per_run"]:
        times = plan_times(profile.get("publish_plan"), tz)
        count = profile["max_videos_per_run"]
        parts.append(f"{count} Short{'s' if count > 1 else ''}"
                     + (f" ({', '.join(times)})" if times else " (live on upload)"))
    return " + ".join(parts) or "no videos"


def describe_runs(profile):
    """Each daily run and the videos it makes, e.g. '06:00 -> 1 long (12:00) + 2 Shorts (08:00, 11:00)'."""
    tz = ZoneInfo(profile["schedule"]["timezone"])
    lines = []
    for fetch in sorted(parse_time(t) for t in profile["schedule"]["post_times"]):
        preview = copy.deepcopy(profile)
        at_fetch = datetime.combine(datetime.now(tz).date(), fetch, tzinfo=tz)
        plan_run(preview, fetch=fetch, now=at_fetch)
        lines.append(f"{fetch:%H:%M} -> {describe_run(preview)}")
    return lines


def run_label(fetch):
    if fetch is None:
        return "Manual run"
    return "Morning run" if fetch.hour < 12 else "Afternoon run" if fetch.hour < 17 else "Evening run"


def describe_long(profile):
    long = profile["long"]
    if not long["count"]:
        return "no long videos"
    minutes = str(long["minutes"]).replace("-", " to ")
    return (f"{long['count']} long landscape video(s) of {minutes} min, live at "
            f"{', '.join(f'{parse_time(t):%H:%M}' for t in long['post_times'][:long['count']])}")


def describe_plan(profile):
    posting = profile["posting"]
    count = profile["max_videos_per_run"]
    fetch = ", ".join(f"{parse_time(t):%H:%M}" for t in profile["schedule"]["post_times"]) or "manual runs only"
    if posting.get("post_times"):
        return f"fetch {fetch} | {count} video(s) live at " + ", ".join(
            f"{parse_time(t):%H:%M}" for t in posting["post_times"])
    if not posting["first_post_time"]:
        return f"fetch {fetch} | {count} video(s), live immediately after upload"
    freq = next((name for name, minutes in FREQUENCIES.items() if minutes == posting["frequency_minutes"]),
                f"{posting['frequency_minutes']} mins")
    first = datetime.combine(datetime(2000, 1, 1), parse_time(posting["first_post_time"]))
    last = first + timedelta(minutes=posting["frequency_minutes"] * (count - 1))
    return (f"fetch {fetch} | {count} video(s): first live {first:%H:%M}, every {freq}, "
            f"last {last:%H:%M}{' (next day)' if last.date() > first.date() else ''}")


# ---------- running ----------

def token_path(config, key):
    return os.path.normpath(os.path.join(PROJECT_DIR, config["storage_paths"]["tokens_dir"], f"token_{key}.json"))


def preflight(config, keys, dry_runs):
    """Return the channels that are ready to run, explaining why any are skipped."""
    problems = []
    if not os.path.exists(os.path.join(PROJECT_DIR, ".env")) and not RUNNING_ON_GITHUB:
        problems.append("No .env file. Copy .env.example to .env and fill in your API keys.")
    for name in ("YOUTUBE_API_KEY", "GEMINI_API_KEY"):
        if not os.getenv(name):
            problems.append(f"{name} is not set (add it to .env, or as a GitHub secret).")
    footage = config.get("footage") or {}
    if footage.get("source", "stock") == "stock":
        from agents.stock_footage import choose_provider
        if not choose_provider(footage.get("provider", "auto"))[0]:
            problems.append("No stock footage key is set. Add PIXABAY_API_KEY (free at https://pixabay.com/api/docs/) "
                            "to .env, or as a GitHub secret.")
    if problems:
        print("\nCannot start:\n  - " + "\n  - ".join(problems))
        return []

    ready = []
    for key in keys:
        if dry_runs[key] or os.path.exists(token_path(config, key)):
            ready.append(key)
        elif RUNNING_ON_GITHUB or not sys.stdin.isatty():
            print(f"\n[Skip] {key}: no YouTube login file. Run 'python execution/execution.py --authorize {key}' "
                  "on your laptop and upload the token file to Google Drive: YouTube_Automation/00_tokens/")
        else:
            print(f"\n{key} has no YouTube login yet ({token_path(config, key)}).")
            if ask_yes_no("Sign in to YouTube for this channel now?", True) and authorize(config, key):
                ready.append(key)
    return ready


def notify_start(config, keys, fetches, formats):
    """Telegram message at the start of a run: which videos each channel is preparing and when they go live."""
    from agents.agent_7_notifier import send_telegram
    first = config["profiles"][keys[0]]
    tz = ZoneInfo(first["schedule"]["timezone"])
    now = datetime.now(tz)
    fetch = fetches.get(keys[0])
    started = f"started {now:%H:%M} {now:%Z}"
    if fetch:
        planned_at = min((datetime.combine(now.date() + timedelta(days=d), fetch, tzinfo=tz) for d in (-1, 0, 1)),
                         key=lambda local: abs(local - now))
        late = int((now - planned_at).total_seconds() // 60)
        off = f"{abs(late) // 60}h {abs(late) % 60:02d}m {'late' if late > 0 else 'early'}"
        started = f"planned {fetch:%H:%M}, {started}" + (f" ({off})" if abs(late) >= 15 else "")
    lines = [f"🎬 <b>{run_label(fetch)} started</b> ({started})", "Video preparation in progress:"]
    for key in keys:
        p = config["profiles"][key]
        lines.append(f"• <b>{html.escape(p['channel_name'])}</b>: {describe_run(p, formats)}")
    lines.append("A message per channel follows once its videos are uploaded and scheduled.")
    send_telegram("\n".join(lines))


def run_channels(config, instructions, keys, overrides=None, force_dry_run=False, formats=("long", "short"),
                 cron=None):
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_DIR, ".env"))

    config = copy.deepcopy(config)
    for key in keys:
        apply_overrides(config["profiles"][key], overrides or {})
    dry_runs = {key: force_dry_run or config["profiles"][key]["test_run"] for key in keys}

    ready = preflight(config, keys, dry_runs)
    if not ready:
        return 1

    from orchestrator import OrchestratorAgent
    orchestrator = OrchestratorAgent(config, instructions)
    failed, results = [], {}
    # One-off counts or times replace the shared daily plan for this run
    shared = not any((overrides or {}).get(k) for k in ("videos", "first_post", "frequency", "post_now"))
    fetches, planned = {}, {}
    for key in ready:
        profile = config["profiles"][key]
        fetches[key] = fetch_time_for_cron(profile, cron) if cron else None
        plan_run(profile, fetch=fetches[key], shared=shared)
        planned[key] = (profile["max_videos_per_run"] if "short" in formats else 0) + \
                       (profile["long"]["count"] if "long" in formats else 0)
    if not all(dry_runs[key] for key in ready):
        notify_start(config, ready, fetches, formats)

    for key in ready:
        profile = config["profiles"][key]
        print(f"\n>>> {key}: {describe_run(profile, formats)}")
        if not planned[key]:
            print(f"[Orchestrator] {key}: no go-live times fall in this run; nothing to make.")
            continue
        try:
            results[key] = orchestrator.execute_pipeline(key, dry_run=dry_runs[key], formats=formats,
                                                         planned=planned[key])
            if not dry_runs[key] and results[key] == 0:
                failed.append(key)
        except Exception as e:
            print(f"\n[Error] {key} failed: {e}")
            failed.append(key)
            if not dry_runs[key]:
                from agents.agent_7_notifier import send_telegram
                send_telegram(f"❌ <b>{html.escape(profile['channel_name'])}</b>: the run stopped with an error "
                              f"before uploading.\n<code>{html.escape(str(e)[:300])}</code>")

    heading("RESULT")
    for key in keys:
        if key not in ready:
            print(f" {key}: skipped (no YouTube login)")
        elif dry_runs[key]:
            print(f" {key}: test run, videos made but not uploaded")
        else:
            print(f" {key}: {results.get(key, 0)} of {planned[key]} video(s) uploaded")
    if failed or len(ready) < len(keys):
        print("\nSome channels uploaded nothing. Scroll up for the [Agent ...] lines explaining why.")
        return 1
    return 0


# ---------- schedule, check, login and list ----------

def read_workflow_schedule():
    if not os.path.exists(WORKFLOW_FILE):
        return None, None
    with open(WORKFLOW_FILE, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")
    begin = next((i for i, line in enumerate(lines) if SCHEDULE_BEGIN in line), None)
    end = next((i for i, line in enumerate(lines) if SCHEDULE_END in line), None)
    if begin is None or end is None or end < begin:
        return lines, None
    return lines, (begin, end)


def schedule_in_sync(config):
    lines, markers = read_workflow_schedule()
    if not markers:
        return True
    current = [re.sub(r".*cron:\s*'([^']*)'.*", r"\1", line) for line in lines[markers[0] + 1:markers[1]]]
    return current == cron_lines(config)


def sync_schedule(config):
    crons = cron_lines(config)
    lines, markers = read_workflow_schedule()
    if lines is None or markers is None:
        print("Could not find the schedule section in the GitHub workflow. Add these lines under 'schedule:':")
        print("\n".join(f"    - cron: '{c}'" for c in crons))
        return False
    if not crons:
        print("No channel has a fetch_time, so the GitHub schedule was left unchanged.")
        return False

    begin, end = markers
    indent = lines[begin][:len(lines[begin]) - len(lines[begin].lstrip())]
    lines[begin + 1:end] = [f"{indent}- cron: '{c}'" for c in crons]
    with open(WORKFLOW_FILE, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines))

    print("GitHub schedule updated (times shown in UTC):")
    for c in crons:
        print(f"   {c}")
    print("Push to GitHub so the new schedule takes effect.")
    return True


def list_channels(config):
    heading("CHANNELS AND POSTING PLANS")
    for key, p in config["profiles"].items():
        if not p["has_answers"]:
            state = "NOT ANSWERED in questions.yaml (will not run)"
        else:
            state = "will run" if p["enabled"] else "run: false (paused)"
        login = "yes" if os.path.exists(token_path(config, key)) else "NO"
        days = p["schedule"]["days"] if isinstance(p["schedule"]["days"], str) else ",".join(p["schedule"]["days"])
        print(f" {key}  -  {p['channel_name']}")
        print(f"    {state} | YouTube login: {login} | {'TEST RUN' if p['test_run'] else 'uploads'}")
        if p["has_answers"]:
            print(f"    Shorts: {describe_plan(p)} ({days}, {p['schedule']['timezone']})")
            print(f"    Long:   {describe_long(p)}")
            if len(p["schedule"]["post_times"]) > 1:
                for line in describe_runs(p):
                    print(f"    Run {line}")
            print(f"    genre: {p['niche']} | {p['language']} | voice: {p['voice']} {p['voice_rate']}")


def usage_report(config):
    """Estimate the YouTube API calls the answers use per day, against YouTube's daily limits."""
    # Channels due at the same time share one GitHub run, so setup time is counted once per scheduled time.
    # videos_per_day and long_videos_per_day are day totals, shared between a channel's runs
    uploads = searches = 0
    github_minutes = len(cron_lines(config)) * GITHUB_SETUP_MINUTES
    for p in config["profiles"].values():
        if not p["enabled"]:
            continue
        runs_per_day = len(p["schedule"]["post_times"]) or 1
        long_count = p["long"]["count"]
        github_minutes += p["max_videos_per_run"] * MINUTES_PER_SHORT + long_count * MINUTES_PER_LONG
        if p["test_run"]:
            continue
        uploads += p["max_videos_per_run"] + long_count
        searches += len(p["search_queries"]) * runs_per_day * (2 if long_count else 1)
    print(f"\nDaily YouTube usage (all channels): {uploads} of {DAILY_UPLOAD_LIMIT} uploads, "
          f"up to {searches} of {DAILY_SEARCH_LIMIT} searches.")
    if uploads > DAILY_UPLOAD_LIMIT or searches > DAILY_SEARCH_LIMIT:
        print("WARNING: over YouTube's daily limit. Lower videos_per_day or search_phrases, "
              "or request more quota in Google Cloud Console.")
    monthly = github_minutes * 30
    if config.get("github_repo_public"):
        print(f"Estimated GitHub Actions time: about {github_minutes} min a day (public repository: unlimited).")
        return
    print(f"Estimated GitHub Actions time: about {github_minutes} min a day, {monthly} min a month "
          f"(private repos get {GITHUB_FREE_MINUTES} free minutes a month; public repos are unlimited).")
    if monthly > GITHUB_FREE_MINUTES:
        print("WARNING: likely over GitHub's free minutes for a private repo. Make the repo public, lower the "
              "video counts, or GitHub will stop the runs (or bill you, if a payment method is set) for the rest "
              "of the month.")


def plan_warnings(config):
    warnings = []
    for key, p in config["profiles"].items():
        if not p["enabled"]:
            continue
        fetch, first = p["schedule"]["post_times"], p["posting"]["first_post_time"]
        if fetch and first:
            gap = (datetime.combine(datetime(2000, 1, 1), parse_time(first))
                   - datetime.combine(datetime(2000, 1, 1), parse_time(fetch[0]))).total_seconds() / 60 % 1440
            if gap < 30:
                warnings.append(f"{key}: first_post_time is only {int(gap)} min after fetch_time; allow 30+ min "
                                "to prepare videos (later slots are used automatically if it runs late).")
        span = p["posting"]["frequency_minutes"] * (p["max_videos_per_run"] - 1)
        if first and not p["posting"].get("post_times") and span >= 1440:
            warnings.append(f"{key}: {p['max_videos_per_run']} videos every {p['posting']['frequency_minutes']} min "
                            "runs past 24 hours and overlaps the next day's videos.")
        # Each run needs time to prepare its videos before the first one goes live
        tz = ZoneInfo(p["schedule"]["timezone"])
        for run_time in sorted(parse_time(t) for t in fetch):
            preview = copy.deepcopy(p)
            at_fetch = datetime.combine(datetime.now(tz).date(), run_time, tzinfo=tz)
            plan_run(preview, fetch=run_time, now=at_fetch)
            times = [datetime.fromisoformat(t) for plan in (preview.get("publish_plan"), preview.get("long_publish_plan"))
                     for t in (plan or {}).get("times_utc", [])]
            if times and (min(times) - at_fetch).total_seconds() < 45 * 60:
                warnings.append(f"{key}: the {run_time:%H:%M} run's first video goes live under 45 min after the run "
                                "starts; it will go live a little later than planned.")
    return warnings


def check(config):
    list_channels(config)
    print("\nBoth pages are valid.")
    for warning in plan_warnings(config):
        print(f"Note: {warning}")
    usage_report(config)
    if schedule_in_sync(config):
        print("GitHub schedule already matches questions.yaml.")
    else:
        sync_schedule(config)


def authorize(config, key):
    client_secret = os.path.join(PROJECT_DIR, "client_secret.json")
    if key not in config["profiles"]:
        print(f"'{key}' is not on the channel page.")
        return False
    if not os.path.exists(client_secret):
        print(f"client_secret.json not found at {client_secret}. See execution.md, 'One-Time Setup'.")
        return False
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    profile = config["profiles"][key]
    target = token_path(config, key)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    print(f"\nA browser window will open. Sign in with the Google account that owns '{profile['channel_name']}'")
    print(f"and choose that channel ({profile['channel_url']}).")
    creds = InstalledAppFlow.from_client_secrets_file(client_secret, YOUTUBE_SCOPES).run_local_server(port=0)

    items = build("youtube", "v3", credentials=creds).channels().list(part="snippet", mine=True).execute().get("items", [])
    if not items:
        print("That Google account has no YouTube channel. Nothing was saved; try again.")
        return False
    channel_id, snippet = items[0]["id"], items[0]["snippet"]
    if not channel_matches(profile["channel_url"], channel_id, snippet.get("customUrl")):
        print(f"You signed in to '{snippet['title']}' ({snippet.get('customUrl') or channel_id}), "
              f"but '{key}' is {profile['channel_url']}. Nothing was saved; try again and pick the right channel.")
        return False
    print(f"Confirmed channel: {snippet['title']}")

    with open(target, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    print(f"Saved YouTube login to: {target}")
    print("For GitHub runs, upload this file to Google Drive: YouTube_Automation/00_tokens/")
    return True


# ---------- --ask: answer the questions interactively for one run ----------

def ask_mode(config, instructions):
    answered = {k: p for k, p in config["profiles"].items() if p["has_answers"]}
    if not answered:
        print("No channel is answered in questions.yaml yet.")
        return 1

    heading("ONE-OFF RUN  (questions.yaml is not changed)")
    print("Each answer starts at the value in questions.yaml. Press Enter to keep it. Ctrl+C cancels.\n")
    key = ask_choice("Q1. Which channel should the videos be posted to?",
                     [(k, f"{p['channel_name']} ({k})") for k, p in answered.items()])
    p = answered[key]
    o = {}

    genre = ask("\nQ3. Genre to source trending videos for", p["niche"])
    if genre != p["niche"]:
        o["genre"] = genre
    default_queries = f"{genre} #shorts" if "genre" in o else ", ".join(p["search_queries"])
    queries = split_list(ask("Q4. YouTube search phrases, comma-separated", default_queries))
    if queries != p["search_queries"]:
        o["queries"] = queries

    o["language"] = ask_choice("\nQ5. Narration language:", [(lang, lang) for lang in LANGUAGES], p["language"])
    voices = VOICE_CHOICES[o["language"]]
    current_voice = p["voice"] if p["voice"] in voices else DEFAULT_VOICES[(o["language"], p["made_for_kids"])]
    o["voice"] = ask_choice("\nQ6. Narrator voice:", [(v, v) for v in voices], current_voice)
    o["voice_rate"] = ask_choice("\nQ7. Speaking speed:", [
        ("-15%", "-15%  slow and very clear"), ("-5%", "-5%   clear (recommended)"),
        ("+0%", "+0%   natural"), ("+10%", "+10%  brisk")], p["voice_rate"] if p["voice_rate"] in SPEEDS else "-5%")

    o["videos"] = ask_int("\nQ15. How many videos?", p["max_videos_per_run"], 1, MAX_VIDEOS_PER_DAY)
    when = ask_choice("\nWhen should they go live?", [
        ("schedule", "Spaced out: first video at a set time, then one every 15 mins / 30 mins / 1 hour / 3 hours"),
        ("now", "All immediately after upload")], "schedule" if p["posting"]["first_post_time"] else "now")
    if when == "schedule":
        o["first_post"] = ask(f"Q13. First video goes live at (HH:MM, {p['schedule']['timezone']})",
                              p["posting"]["first_post_time"] or "", validate_hhmm)
        current_freq = next((n for n, m in FREQUENCIES.items() if m == p["posting"]["frequency_minutes"]), "1 hour")
        o["frequency"] = ask_choice("\nQ14. Gap between videos:", [(f, f) for f in FREQUENCIES], current_freq)
    else:
        o["post_now"] = True
    o["privacy"] = ask_choice("\nQ18. Visibility:", [(v, v) for v in ("public", "unlisted", "private")],
                              p["privacy_status"])
    dry_run = ask_yes_no("\nQ19. Test run? (make the videos but do NOT upload)", p["test_run"])

    heading("SUMMARY")
    preview = copy.deepcopy(p)
    apply_overrides(preview, o)
    print(f" Channel:  {key} ({p['channel_name']})")
    print(f" Genre:    {preview['niche']}")
    print(f" Search:   {', '.join(preview['search_queries'])}")
    print(f" Voice:    {preview['voice']} {preview['voice_rate']} ({preview['language']})")
    print(f" Plan:     {describe_plan(preview).split('| ', 1)[-1]}")
    print(f" Mode:     {'TEST RUN (no upload)' if dry_run else 'Upload to YouTube, ' + preview['privacy_status']}")
    if not ask_yes_no("\nStart now?", True):
        print("Cancelled.")
        return 0
    return run_channels(config, instructions, [key], o, force_dry_run=dry_run, formats=("short",))


# ---------- command line ----------

def main():
    parser = argparse.ArgumentParser(
        description="Run the YouTube automation using the answers in execution/questions.yaml.",
    )
    parser.add_argument("--channel", help="Run one channel key, or 'all' (default: every channel with run: true)")
    parser.add_argument("--genre", help="One-off genre to source trending videos for")
    parser.add_argument("--videos", help="One-off number of videos")
    parser.add_argument("--frequency", help="One-off gap between videos: '15 mins', '30 mins', '1 hour' or '3 hours'")
    parser.add_argument("--first-post", help="One-off time the first video goes live (HH:MM)")
    parser.add_argument("--post-now", action="store_true", help="One-off: make every video live immediately")
    parser.add_argument("--visibility", choices=["public", "unlisted", "private"], help="One-off visibility")
    parser.add_argument("--dry-run", action="store_true", help="Make the videos but do not upload")
    parser.add_argument("--format", choices=["both", "short", "long"], default="both",
                        help="Which videos to make: Shorts, long videos, or both (default)")
    parser.add_argument("--ask", action="store_true", help="Answer the questions interactively for a one-off run")
    parser.add_argument("--scheduled", action="store_true", help="Run channels due for --cron (used by GitHub)")
    parser.add_argument("--due", action="store_true", help="Only print channels due for --cron (used by GitHub)")
    parser.add_argument("--cron", help="The cron expression that triggered a scheduled GitHub run")
    parser.add_argument("--authorize", metavar="CHANNEL", help="Sign in to YouTube for a channel")
    parser.add_argument("--check", action="store_true", help="Validate pages, show plan and usage, update schedule")
    parser.add_argument("--list", action="store_true", help="Show channels and their posting plans")
    args = parser.parse_args()

    try:
        config, instructions = load_brain()
    except (ValueError, FileNotFoundError) as e:
        print(e)
        return 2

    if args.due or args.scheduled:
        if not args.cron:
            print("--cron is required with --due and --scheduled.")
            return 2
        keys = due_profiles(config, args.cron)
        if args.due:
            print(" ".join(keys))
            return 0
        if not keys:
            print("No channels are due for this schedule.")
            return 0
        return run_channels(config, instructions, keys, force_dry_run=args.dry_run, cron=args.cron)

    if args.check:
        check(config)
        return 0
    if args.list:
        list_channels(config)
        return 0
    if args.authorize:
        return 0 if authorize(config, args.authorize) else 1
    if args.ask:
        return ask_mode(config, instructions)

    # GitHub passes empty strings for form fields left blank, so treat "" as not given
    overrides = {
        "genre": args.genre or None,
        "videos": int(args.videos) if args.videos else None,
        "frequency": args.frequency or None,
        "first_post": args.first_post or None,
        "post_now": args.post_now,
        "privacy": args.visibility,
    }
    if overrides["frequency"] and overrides["frequency"] not in FREQUENCIES:
        print(f"--frequency must be one of: {', '.join(FREQUENCIES)}")
        return 2
    if overrides["first_post"] and validate_hhmm(overrides["first_post"]):
        print(f"--first-post: {validate_hhmm(overrides['first_post'])}")
        return 2
    if overrides["videos"] is not None and not 1 <= overrides["videos"] <= MAX_VIDEOS_PER_DAY:
        print(f"--videos must be from 1 to {MAX_VIDEOS_PER_DAY}")
        return 2

    if args.channel and args.channel != "all":
        if args.channel not in config["profiles"]:
            print(f"'{args.channel}' is not on the channel page. Channels: {', '.join(config['profiles'])}")
            return 2
        if not config["profiles"][args.channel]["has_answers"]:
            print(f"'{args.channel}' has no answers in questions.yaml yet.")
            return 2
        keys = [args.channel]
    else:
        keys = [k for k, p in config["profiles"].items() if p["enabled"]]
        if not keys:
            print("No channel has run: true in questions.yaml.")
            return 1
    # Long videos first, so they are ready well before their go-live times
    formats = ("long", "short") if args.format == "both" else (args.format,)
    return run_channels(config, instructions, keys, overrides, force_dry_run=args.dry_run, formats=formats)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        sys.exit(1)
