"""--learn: analyse a YouTube video about getting views and add its techniques to execution/view_techniques.md.

Reads the video's title, description and statistics (YouTube Data API) and its transcript (subtitles via yt-dlp;
YouTube blocks this from cloud servers, so run it on a laptop), asks Gemini which techniques it teaches and how each
fits this pipeline, and appends an entry to SOURCES marked "proposed". Nothing is adopted automatically: rules are
only sent to Gemini once someone copies them into ACTIVE RULES.
"""
import glob
import os
import re
import tempfile
from datetime import datetime

from agents.ai import generate_text
from brain import VIEW_TECHNIQUES_FILE

MAX_TRANSCRIPT_CHARS = 60000
VERDICTS = ("ADOPT-RULE", "ADOPT-CODE", "ALREADY", "MANUAL", "SKIP", "REJECT")
VERDICT_TEXT = {
    "ADOPT-RULE": "Proposed: add the rule below to ACTIVE RULES",
    "ADOPT-CODE": "Proposed: needs a code change (ask Claude)",
    "ALREADY": "Already done by the pipeline",
    "MANUAL": "Owner does this by hand",
    "SKIP": "Doesn't apply to this pipeline",
    "REJECT": "Rejected: against YouTube's policies or misleading",
}


def video_id_from_link(link):
    match = re.search(r"(?:youtu\.be/|[?&]v=|/shorts/|/embed/|/live/)([\w-]{11})", link)
    if match:
        return match.group(1)
    if re.fullmatch(r"[\w-]{11}", link.strip()):
        return link.strip()
    raise ValueError(f"Not a YouTube video link: {link}")


def fetch_details(video_id):
    from googleapiclient.discovery import build
    youtube = build("youtube", "v3", developerKey=os.getenv("YOUTUBE_API_KEY"))
    items = youtube.videos().list(part="snippet,statistics,contentDetails", id=video_id).execute().get("items", [])
    if not items:
        raise ValueError(f"Video {video_id} not found (private, removed, or a wrong link).")
    return items[0]


def vtt_to_text(vtt):
    """Subtitle file -> plain text, dropping timings, tags and the repeated lines of auto-captions."""
    lines, previous = [], ""
    for line in vtt.splitlines():
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line or "-->" in line or line.startswith(("WEBVTT", "Kind:", "Language:")) or line == previous:
            continue
        lines.append(line)
        previous = line
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def fetch_transcript(video_id):
    """The English (or original-language) subtitles as text, or "" if none can be downloaded."""
    import yt_dlp
    with tempfile.TemporaryDirectory() as folder:
        options = {"skip_download": True, "writesubtitles": True, "writeautomaticsub": True,
                   "subtitleslangs": ["en.*", "en"], "subtitlesformat": "vtt", "quiet": True,
                   "no_warnings": True, "ignoreerrors": True, "outtmpl": os.path.join(folder, "%(id)s.%(ext)s")}
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        except Exception as e:
            print(f"[Learn] Could not download subtitles ({e}).")
        files = sorted(glob.glob(os.path.join(folder, "*.vtt")), key=lambda p: ("orig" not in p, p))
        for path in files:
            with open(path, "r", encoding="utf-8") as f:
                text = vtt_to_text(f.read())
            if text:
                return text
    return ""


def build_prompt(video, transcript, pipeline_rules):
    snippet, stats = video["snippet"], video.get("statistics", {})
    return (
        "You review advice about growing YouTube views for a fully automated, faceless YouTube pipeline. It makes "
        "Shorts and 4 to 7 minute videos from trending topics: Gemini writes an original script, title, description "
        "and hashtags; an AI voice narrates; free stock clips are edited to match; videos upload on a fixed daily "
        "schedule to a news/geopolitics channel and a kids entertainment channel. It can change: how topics are "
        "chosen (YouTube search, trend speed, language), what the script/title/hashtag writer is told, posting "
        "times, and it reads public view counts. It cannot film people or reply to comments.\n\n"
        f"Rules the writer already follows:\n{pipeline_rules or '(none)'}\n\n"
        f"Video: {snippet['title']} by {snippet['channelTitle']} ({stats.get('viewCount', '?')} views, "
        f"published {snippet['publishedAt'][:10]})\n"
        f"Description: {snippet.get('description', '')[:1500]}\n"
        + (f"Transcript: {transcript[:MAX_TRANSCRIPT_CHARS]}\n\n" if transcript else
           "Transcript: not available; judge from the title and description only.\n\n")
        + "List each distinct technique the video teaches for getting more views (ignore its adverts and sponsors). "
        "For each, choose one verdict: ADOPT-RULE (a new instruction for the script/title/hashtag writer), "
        "ADOPT-CODE (the pipeline's code would need to change), ALREADY (the pipeline or its rules already do it), "
        "MANUAL (the channel owner must do it by hand), SKIP (doesn't apply), REJECT (fakes engagement, misleads "
        "viewers, re-uploads other people's content, or otherwise breaks YouTube's policies). Be strict: only ADOPT "
        "what the video gives evidence for.\n"
        "Reply with one line per technique, nothing else, in exactly this form:\n"
        "TECHNIQUE: <short name> | VERDICT: <one verdict> | WHY: <one sentence of evidence from the video> | "
        "RULE: <for ADOPT-RULE, the instruction in one sentence; otherwise ->"
    )


def parse_techniques(text):
    techniques = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*0123456789. ")
        if not line.upper().startswith("TECHNIQUE:"):
            continue
        fields = {}
        for part in line.split("|"):
            label, _, value = part.partition(":")
            fields[label.strip().upper()] = value.strip()
        verdict = fields.get("VERDICT", "").upper()
        if fields.get("TECHNIQUE") and verdict in VERDICTS:
            rule = fields.get("RULE", "")
            techniques.append({"name": fields["TECHNIQUE"], "verdict": verdict, "why": fields.get("WHY", ""),
                               "rule": rule if verdict == "ADOPT-RULE" and rule not in ("", "-") else ""})
    return techniques


def source_entry(number, link, video, techniques, had_transcript, today=None):
    snippet, stats = video["snippet"], video.get("statistics", {})
    lines = [
        f'### S{number}. "{snippet["title"]}" by {snippet["channelTitle"]}',
        f"- Link: {link}",
        f"- When analysed: {today or datetime.now().date().isoformat()}, by --learn with Gemini, from "
        + ("the transcript" if had_transcript else "the title and description only (no transcript available)")
        + f" ({int(stats.get('viewCount', 0)):,} views; published {snippet['publishedAt'][:10]})",
        "- Status: proposed (review before adopting; nothing here is sent to Gemini yet)",
        "",
        "**What it teaches:**",
    ]
    for i, t in enumerate(techniques, 1):
        lines.append(f"{i}. **{t['name']}.** {t['why']}")
        lines.append(f"   → {VERDICT_TEXT[t['verdict']]}." + (f' Rule: "{t["rule"]}"' if t["rule"] else ""))
    return "\n".join(lines) + "\n"


def next_source_number(text):
    numbers = [int(n) for n in re.findall(r"^### S(\d+)\.", text, re.MULTILINE)]
    return max(numbers, default=0) + 1


def learn(link, ai_client, models=None, path=VIEW_TECHNIQUES_FILE):
    """Analyse one video and append its entry to view_techniques.md. Returns the techniques found."""
    from brain import view_rules
    video_id = video_id_from_link(link)
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if f"/{video_id}" in text or f"v={video_id}" in text:
        print(f"[Learn] {link} is already in {os.path.basename(path)}.")
        return []
    video = fetch_details(video_id)
    print(f"[Learn] {video['snippet']['title']} by {video['snippet']['channelTitle']}")
    transcript = fetch_transcript(video_id)
    print(f"[Learn] Transcript: {len(transcript.split())} words" if transcript else
          "[Learn] No transcript; analysing the title and description only.")
    techniques = parse_techniques(generate_text(ai_client, build_prompt(video, transcript, view_rules(path)), models))
    if not techniques:
        print("[Learn] Gemini returned no techniques in the expected form; nothing was saved. Try again later.")
        return []
    entry = source_entry(next_source_number(text), link, video, techniques, bool(transcript))
    with open(path, "a", encoding="utf-8") as f:
        f.write(("\n" if not text.endswith("\n\n") else "") + entry)
    print(f"[Learn] Added to {path}:\n\n{entry}")
    return techniques
