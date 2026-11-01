"""Book storytelling channels (questions.yaml `story:`): a public-domain book told chapter by chapter, one illustrated
episode a day, each with a Short teaser.

For each episode:
  1. Book: found on Project Gutenberg (Gutendex catalog: author, subjects, summary; only books out of copyright),
     saved in Story_Books/<book>/ (book.json, the chapter texts, the episode plan, characters, progress).
     Chapters longer than story.split_words become two parts; a chapter too short for 5 minutes is never merged:
     its day gets two videos instead.
  2. Script (1 AI request): a faithful condensed retelling, one sentence per line, each line with its speaker and a
     picture description; plus hook, recap, cliffhanger, content warnings, teaser lines, title and description.
  3. Voices: the narrator and a voice per character, matched to gender and age (characters.json, kept for the
     whole book).
  4. Pictures: one per line, drawn by Cloudflare Workers AI (FLUX) in the channel's picture_style, characters
     described the same way in every prompt. Gemini checks them in batches against their lines (and for stray
     writing); failures are drawn again. The free daily picture allowance can run out mid-episode: the work is kept
     and the next run continues, which is why episodes are made a day ahead.
  5. Video: a book page per line (picture with a slow zoom, the sentence beside it), warning and title cards, the
     end card; the Short teaser reuses the episode's most gripping lines.
  6. Upload: scheduled for post_time (15:00), added to the book's playlist, Short linked to the episode.
"""
import base64
import copy
import html
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from google import genai
from PIL import Image, ImageChops, ImageDraw, ImageFont

from agents import expressive_voice
from agents.agent_4_editor import (LONG_SIZE, SHORT_SIZE, WATERMARK_MARGIN, VideoEditorAgent, ffmpeg, load_font,
                                   watermark_image)
from agents.agent_7_notifier import send_telegram
from agents.ai import generate_text, look_at_pictures
from brain import parse_time, view_rules

GUTENDEX = "https://gutendex.com/books/"
GUTENBERG_TEXT = "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt"
CLOUDFLARE_RUN = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"

# Defaults for BRAIN.md `story:`
DEFAULTS = {
    "picture_model": "@cf/black-forest-labs/flux-1-schnell",
    "picture_steps": 4,
    "retell_words": [1000, 1350],
    "split_words": 4000,
    "short_chapter_words": 725,
    "check_pictures": True,
    "pictures_per_check": 10,
    "picture_attempts": 3,
    "make_ahead_days": 1,
}
# The narration's pace at voice_speed -5%, measured on edge-tts English voices
WORDS_PER_MINUTE = 145
FPS = 25
# Book-page colours
PAPER = (244, 239, 227)
INK = (48, 42, 34)
FADED = (128, 117, 100)
PICTURE_STYLES = {
    "pencil": "detailed graphite pencil sketch, fine cross-hatching, classic Victorian book illustration, black and "
              "white, on plain white paper, no text, no letters, no writing, no signature",
    "color": "storybook illustration, fine ink lines and soft watercolor, muted classic palette, clean composition, "
             "no text, no letters, no writing, no signature",
}
WARNING_NAMES = {"violence": "violence", "sexual": "sexual themes", "alcohol_drugs": "alcohol or drug use",
                 "language": "strong language", "frightening": "frightening scenes"}
# The spoken viewer-discretion card is for content clearly shown; mild mentions are noted in the description only
CARD_LEVELS = ("moderate", "strong")

# Character voices (edge-tts), British first for classic English books. The narrator's voice is never reused.
MALE_VOICES = ["en-GB-RyanNeural", "en-GB-ThomasNeural", "en-IE-ConnorNeural", "en-US-ChristopherNeural",
               "en-AU-WilliamMultilingualNeural", "en-US-EricNeural", "en-US-SteffanNeural", "en-US-GuyNeural",
               "en-US-RogerNeural", "en-IN-PrabhatNeural"]
FEMALE_VOICES = ["en-GB-SoniaNeural", "en-GB-LibbyNeural", "en-IE-EmilyNeural", "en-AU-NatashaNeural",
                 "en-US-AriaNeural", "en-US-JennyNeural", "en-US-MichelleNeural"]
CHILD_VOICES = {"female": ("en-GB-MaisieNeural", 0), "male": ("en-US-AnaNeural", -15)}
# Pitch (Hz) and speed (%) by age, so an old butler and a young heir sound different even on similar voices
AGE_SHIFT = {"old": (-8, -8), "adult": (0, 0), "young": (4, 3), "child": (0, 0)}

SERIF = ["georgia.ttf", "Georgia.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "DejaVuSerif.ttf"]
SERIF_BOLD = ["georgiab.ttf", "Georgia Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
              "DejaVuSerif-Bold.ttf"]


class PictureLimit(Exception):
    """The free daily picture allowance is used up; the next run continues."""


class PromptRefused(Exception):
    """The picture service's safety filter refused a prompt (it has false alarms); a plainer wording is tried."""


# ---------- small helpers ----------

def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60] or "book"


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def parse_json_reply(text):
    """The JSON object in an AI reply (which may wrap it in ```json fences or a sentence)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the reply")
    return json.loads(text[start:end + 1])


def serif(size, bold=False):
    for candidate in SERIF_BOLD if bold else SERIF:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return load_font(size)


def wrap(draw, text, font, width):
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and draw.textlength(trial, font=font) > width:
            lines.append(current)
            current = word
        else:
            current = trial
    return lines + ([current] if current else [])


def fit_text(draw, text, width, height, size, bold=False, smallest=26):
    """(font, wrapped lines, line height) for the largest size from `size` down that fits the box."""
    while True:
        font = serif(size, bold)
        lines = wrap(draw, text, font, width)
        line_height = int(size * 1.35)
        if len(lines) * line_height <= height or size <= smallest:
            return font, lines, line_height
        size -= 2


def draw_block(draw, lines, font, line_height, x, center_y, width, fill, align="left"):
    y = center_y - len(lines) * line_height // 2
    for line in lines:
        offset = (width - draw.textlength(line, font=font)) // 2 if align == "center" else 0
        draw.text((x + offset, y), line, font=font, fill=fill)
        y += line_height
    return y


def frame_lengths(bounds):
    """Seconds of each clip between consecutive time bounds, rounded to whole frames so 100 clips don't drift."""
    frames = [round(t * FPS) for t in bounds]
    return [max(1, b - a) / FPS for a, b in zip(frames, frames[1:])]


def clock(seconds):
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


# ---------- the book ----------

def author_name(name):
    """Gutenberg's "Doyle, Arthur Conan" -> "Arthur Conan Doyle"."""
    last, _, first = name.partition(", ")
    return f"{first} {last}".strip() if first else name


def find_book(title):
    """The best English match on Project Gutenberg (Gutendex catalog), or None."""
    # Gutendex is a free volunteer service and sometimes slow; it's needed once per book
    for attempt in range(3):
        try:
            reply = requests.get(GUTENDEX, params={"search": title}, timeout=90)
            reply.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 2:
                raise
    results = [b for b in reply.json().get("results", []) if "en" in (b.get("languages") or [])]
    if not results:
        return None
    wanted = set(re.findall(r"[a-z]+", title.lower())) - {"the", "a", "an", "of", "and", "by"}
    # Every main word of the asked title must be in the book's title: a loose match would tell the wrong book
    # (a modern book that isn't on Gutenberg matches some other old book with one shared word)
    results = [b for b in results if wanted <= set(re.findall(r"[a-z]+", b["title"].lower()))]
    if not results:
        return None
    best = max(results, key=lambda b: b.get("download_count") or 0)
    authors = best.get("authors") or [{}]
    return {
        "gutenberg_id": best["id"],
        "title": best["title"].split("\r")[0].split("\n")[0].strip(),
        "author": author_name(authors[0].get("name") or "Unknown"),
        "author_died": authors[0].get("death_year"),
        # Gutenberg marks books still under copyright in the US; only books out of copyright are told
        "public_domain": best.get("copyright") is False,
        "subjects": (best.get("subjects") or [])[:8],
        "summary": (best.get("summaries") or [""])[0][:1200],
    }


def strip_gutenberg(text):
    start = re.search(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", text, re.I | re.S)
    end = re.search(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG", text, re.I)
    return text[start.end() if start else 0:end.start() if end else len(text)]


def paragraphs(text):
    paras = []
    for block in re.split(r"\n\s*\n", text):
        para = " ".join(block.split())
        if para and not re.fullmatch(r"\[(?:Illustration|Footnote)[^\]]*\]", para, re.I):
            paras.append(para)
    return paras


HEADING = re.compile(r"^[ \t]*chapter[ \t]+([ivxlcdm]+|\d+|[a-z\-]+)\b[.:]?[ \t]*(.*)$", re.I | re.M)


def split_chapters(text, section_words=3000):
    """[{"title", "paragraphs"}] of the book's chapters; a book without chapter headings is cut into sections."""
    body = strip_gutenberg(text.replace("\r\n", "\n").replace("\r", "\n"))
    marks = list(HEADING.finditer(body))
    chapters = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(body)
        paras = paragraphs(body[mark.end():end])
        title = mark.group(2).strip(" .\t")
        if not title and paras and len(paras[0].split()) <= 12:
            title = paras.pop(0).strip(" .")
        # Table-of-contents entries have (almost) no text under them
        if sum(len(p.split()) for p in paras) >= 150:
            chapters.append({"title": title, "paragraphs": paras})
    if chapters:
        return chapters
    sections, current = [], []
    for para in paragraphs(body):
        current.append(para)
        if sum(len(p.split()) for p in current) >= section_words:
            sections.append({"title": "", "paragraphs": current})
            current = []
    if current:
        sections.append({"title": "", "paragraphs": current})
    return sections


def halves(paras):
    """Split paragraphs into two parts of about equal length, at a paragraph boundary."""
    total, running = sum(len(p.split()) for p in paras), 0
    for index, para in enumerate(paras):
        running += len(para.split())
        if running >= total / 2:
            return [(0, index + 1), (index + 1, len(paras))]
    return [(0, len(paras))]


def plan_episodes(chapters, split_words, short_words):
    episodes = []
    for number, chapter in enumerate(chapters, 1):
        words = sum(len(p.split()) for p in chapter["paragraphs"])
        parts = halves(chapter["paragraphs"]) if words > split_words else [(0, len(chapter["paragraphs"]))]
        for part, (start, end) in enumerate(parts, 1):
            episodes.append({
                "key": f"c{number:02d}" + (f"p{part}" if len(parts) > 1 else ""),
                "chapter": number, "part": part, "parts": len(parts),
                "chapter_title": chapter["title"], "paragraphs": [start, end],
                "words": sum(len(p.split()) for p in chapter["paragraphs"][start:end]),
                # Too short for a 5-minute video: never merged with the next chapter, its day gets two videos
                "short": len(parts) == 1 and words < short_words,
            })
    return episodes


def episode_label(ep):
    return f"Chapter {ep['chapter']}" + (f", Part {ep['part']}" if ep["parts"] > 1 else "")


# ---------- the channel ----------

class StoryChannel:
    def __init__(self, config, key, instructions, overrides=None):
        self.config = copy.deepcopy(config)
        self.config["active_profile"] = key
        self.key = key
        self.instructions = instructions
        self.profile = self.config["profiles"][key]
        self.settings = self.profile["story"]
        self.tune = dict(DEFAULTS, **(config.get("story") or {}))
        paths = self.config["storage_paths"]
        self.root = os.path.normpath(paths.get("story_books") or os.path.join(paths["base_dir"], "Story_Books"))
        self.processed = paths["processed_videos"]
        self.tz = ZoneInfo(self.profile["schedule"]["timezone"])
        overrides = overrides or {}
        # A one-off "--post-now" run is a test: it uploads the next episode now without moving the schedule on
        self.test = bool(overrides.get("post_now"))
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.state_file = os.path.join(self.root, f"channel_{key}.json")
        self.state = read_json(self.state_file, {"last_date": None, "finished": []})
        self.book = None
        # Why this run stopped early but will be continued by a later one (pictures still to draw or check)
        self.waiting = ""

    # ----- books -----

    def book_titles(self):
        """The books in order: questions.yaml story.books, then books queued in Telegram ("book <title>")."""
        from agents.inbox import load_requests
        queued = [r.get("title") for r in load_requests()["requests"]
                  if r.get("kind") == "book" and r.get("channel") == self.key and r.get("title")]
        return list(dict.fromkeys(self.settings["books"] + queued))

    def current_book(self):
        if self.book is None:
            for title in self.book_titles():
                if slug(title) not in self.state["finished"]:
                    self.book = self.open_book(title)
                    break
        return self.book

    def open_book(self, title):
        folder = os.path.join(self.root, slug(title))
        details = read_json(os.path.join(folder, "book.json"), None)
        if details is None:
            details = self.fetch_book(title, folder)
        details["folder"] = folder
        details["progress"] = read_json(os.path.join(folder, "progress.json"), {"episodes": {}, "tests": []})
        details["characters"] = read_json(os.path.join(folder, "characters.json"), {"characters": {}, "places": {}})
        return details

    def fetch_book(self, title, folder):
        print(f"[Story] Looking up '{title}' on Project Gutenberg...")
        info = find_book(title)
        if not info:
            raise RuntimeError(f"'{title}' was not found on Project Gutenberg (it is probably still under "
                               "copyright). Check the title, or choose a public-domain book.")
        if not info["public_domain"]:
            raise RuntimeError(f"'{info['title']}' is still under copyright, so it can't be told on the channel.")
        reply = requests.get(GUTENBERG_TEXT.format(id=info["gutenberg_id"]), timeout=120)
        reply.raise_for_status()
        chapters = split_chapters(reply.content.decode("utf-8", errors="replace"))
        os.makedirs(os.path.join(folder, "text"), exist_ok=True)
        for number, chapter in enumerate(chapters, 1):
            with open(os.path.join(folder, "text", f"chapter_{number:02d}.txt"), "w", encoding="utf-8") as f:
                f.write("\n\n".join(chapter["paragraphs"]))
        info["source"] = f"https://www.gutenberg.org/ebooks/{info['gutenberg_id']}"
        info["chapters"] = [{"number": n, "title": c["title"], "words": sum(len(p.split()) for p in c["paragraphs"])}
                            for n, c in enumerate(chapters, 1)]
        info["episodes"] = plan_episodes(chapters, self.tune["split_words"], self.tune["short_chapter_words"])
        write_json(os.path.join(folder, "book.json"), info)
        weeks = len(info["episodes"]) / 7
        print(f"[Story] Saved {info['title']} ({len(chapters)} chapters, {len(info['episodes'])} episodes) to {folder}")
        send_telegram(f"📚 <b>{html.escape(self.profile['channel_name'])}</b>: new book ready, "
                      f"<b>{html.escape(info['title'])}</b> by {html.escape(info['author'])}"
                      + f".\n{len(chapters)} chapters → {len(info['episodes'])} episodes, about {weeks:.0f} weeks "
                        f"of daily videos. Public domain: {info['source']}")
        return info

    def save_book(self):
        book = self.book
        write_json(os.path.join(book["folder"], "progress.json"), book["progress"])
        write_json(os.path.join(book["folder"], "characters.json"), book["characters"])

    def chapter_text(self, ep):
        with open(os.path.join(self.book["folder"], "text", f"chapter_{ep['chapter']:02d}.txt"), encoding="utf-8") as f:
            paras = f.read().split("\n\n")
        start, end = ep["paragraphs"]
        return "\n\n".join(paras[start:end])

    # ----- what this run makes -----

    def post_at(self, day):
        return datetime.combine(day, parse_time(self.settings["post_time"]), tzinfo=self.tz)

    def plan(self, now=None):
        """[(episode, go-live date)] this run makes: every posting day up to make_ahead_days ahead not yet filled.

        A day normally gets one episode; a short chapter's day also gets the next episode.
        """
        book = self.current_book()
        if book is None:
            return []
        done = book["progress"]["episodes"]
        waiting = [ep for ep in book["episodes"] if (done.get(ep["key"]) or {}).get("status") != "uploaded"]
        if self.test:
            return [(waiting[0], None)] if waiting else []
        now = (now or datetime.now(self.tz)).astimezone(self.tz)
        first = now.date() if now + timedelta(minutes=90) < self.post_at(now.date()) else now.date() + timedelta(1)
        last = self.state.get("last_date")
        day = max(first, datetime.fromisoformat(last).date() + timedelta(1)) if last else first
        horizon = now.date() + timedelta(days=int(self.tune["make_ahead_days"]))
        targets = []
        while waiting and day <= horizon:
            group = [waiting.pop(0)]
            if group[0]["short"] and waiting:
                group.append(waiting.pop(0))
            targets += [(ep, day) for ep in group]
            day += timedelta(1)
        return targets

    def describe(self, targets):
        if not targets:
            book = self.current_book()
            return "no episode due (the next one is already scheduled)" if book else "no book left to tell"
        parts = []
        for ep, day in targets:
            when = "now (test)" if day is None else f"{self.post_at(day):%d %b %H:%M}"
            parts.append(f"{episode_label(ep)} + teaser ({when})" if self.settings["teaser"] else
                         f"{episode_label(ep)} ({when})")
        return f"{self.book['title']}: " + ", ".join(parts)

    # ----- 1. script -----

    def characters_text(self):
        sheet = self.book["characters"]
        people = [f"- {name}: {c['gender']}, {c['age']}; looks: {c['look']}" for name, c in sheet["characters"].items()]
        places = [f"- {name}: {p}" for name, p in sheet["places"].items()]
        return "\n".join(people + (["Places:"] + places if places else [])) or "(none yet)"

    def previous_summary(self, ep):
        index = [e["key"] for e in self.book["episodes"]].index(ep["key"])
        if index == 0:
            return None
        prev = self.book["progress"]["episodes"].get(self.book["episodes"][index - 1]["key"]) or {}
        return prev.get("summary")

    def script_prompt(self, ep, feedback=""):
        book, low_high = self.book, self.tune["retell_words"]
        source = self.chapter_text(ep)
        low, high = min(low_high[0], ep["words"]), min(low_high[1], max(ep["words"], low_high[0]))
        is_last = ep["key"] == book["episodes"][-1]["key"]
        recap = self.previous_summary(ep)
        part = f", part {ep['part']} of {ep['parts']}" if ep["parts"] > 1 else ""
        return (
            f'You are retelling chapter {ep["chapter"]} ("{ep["chapter_title"]}"){part} of "{book["title"]}" by '
            f"{book['author']}, a public-domain book, for the YouTube channel \"{self.profile['channel_name']}\": "
            "an illustrated audiobook for a general (adult and family) audience. Each sentence gets its own picture.\n\n"
            f"STORY SO FAR: {recap or 'This is the first episode.'}\n"
            f"CHARACTERS AND PLACES ALREADY DESCRIBED (use these exact names):\n{self.characters_text()}\n\n"
            f"TEXT OF THIS {'PART' if part else 'CHAPTER'}:\n<<<\n{source}\n>>>\n\n"
            f"Write a faithful retelling of {low} to {high} words in total (about {low / WORDS_PER_MINUTE:.0f} to "
            f"{high / WORDS_PER_MINUTE:.0f} minutes spoken). Rules:\n"
            "- Keep the plot, the order of events and the best original dialogue (quote it word for word where you "
            "can). Never invent events or change what happens. Condense descriptions, not plot.\n"
            "- Narration is third person in the narrator's voice; dialogue lines contain only the spoken words (no "
            "quote marks, no 'he said') and are spoken by that character.\n"
            "- Start with a 2 or 3 line HOOK that teases this episode's most gripping moment without spoiling how it "
            "ends" + (", then one RECAP line starting \"Previously,\"" if recap else "") + ", then the story.\n"
            "- End on the episode's cliffhanger" + ("" if is_last else ", then one NEXT line teasing the next "
                                                    "episode") + ".\n"
            "- Every line is ONE sentence of 5 to 30 words.\n"
            "- Explicit sexual content and graphic gore are told the way a careful narrator would, never explicitly.\n"
            "- picture: one illustration that shows exactly what the line says: which characters (by their exact "
            "name), doing what, where, the key objects, the framing (close-up, medium shot or wide shot). For a "
            "line with no action (a thought, a feeling), show the speaker's face and mood or the place it happens. "
            "Never ask for writing, letters, signs, labels or numbers in the picture, and never gore, nudity or "
            "sexual content.\n"
            "- Describe every new character and place in new_characters / new_places (appearance and clothes in "
            "the book's period, so every picture draws them the same way).\n"
            + (f"- {feedback}\n" if feedback else "")
            + "\nTitles and hooks follow these rules too:\n" + view_rules() + "\n\n"
            "Reply with ONLY this JSON:\n"
            "{\n"
            '  "lines": [{"kind": "hook|recap|story|next", "speaker": "narrator or the character\'s exact name", '
            '"text": "one sentence", "picture": "the illustration"}],\n'
            '  "new_characters": [{"name": "", "gender": "male|female", "age": "child|young|adult|old", '
            '"look": "face, hair, build, clothes"}],\n'
            '  "new_places": [{"name": "", "look": "what it looks like"}],\n'
            '  "warnings": {"violence": "none|mild|moderate|strong", "sexual": "none|mild|moderate|strong", '
            '"alcohol_drugs": "none|mild|moderate|strong", "language": "none|mild|moderate|strong", '
            '"frightening": "none|mild|moderate|strong"},\n'
            '  "teaser": {"from": first line number (0-based), "to": last line number, "hook": "on-screen hook, max 8 '
            'words"},\n'
            '  "short_title": "the teaser Short\'s title, max 60 characters, a curiosity hook naming the book",\n'
            '  "description": "2 or 3 sentences for the YouTube description, no spoilers of the ending",\n'
            '  "summary": "2 sentences: what happened in this episode, for the next episode\'s recap",\n'
            '  "keywords": ["5 search phrases people type to find this book or story"],\n'
            '  "hashtags": ["5 hashtags, e.g. #sherlockholmes"]\n'
            "}\n"
            "The teaser is 6 to 10 consecutive story lines (25 to 45 seconds) with the most gripping moment.\n"
            "Warnings rate only what this text actually shows: none = absent; mild = mentioned in passing (a glass "
            "of wine, an old crime referred to); moderate = shown in a scene (a fight, a drunk character, a "
            "frightening chase); strong = shown in vivid detail. Tobacco and pipes are not alcohol_drugs."
        )

    def write_script(self, ep, folder):
        path = os.path.join(folder, "script.json")
        script = read_json(path, None)
        if script:
            return script
        print(f"[Story] Writing the script for {episode_label(ep)}...")
        low, high = self.tune["retell_words"]
        low, high = min(low, ep["words"]), min(high, max(ep["words"], low))
        feedback, reply = "", None
        for attempt in range(2):
            try:
                reply = parse_json_reply(generate_text(self.client, self.script_prompt(ep, feedback),
                                                       self.config.get("ai_models")))
            except ValueError as e:
                feedback = f"Your last reply was not valid JSON ({e}). Reply with only the JSON object."
                continue
            lines = [line for line in reply.get("lines") or [] if str(line.get("text", "")).strip()]
            words = sum(len(str(line["text"]).split()) for line in lines)
            if lines and low * 0.85 <= words <= high * 1.15:
                break
            feedback = (f"Your last retelling had {words} words; it must have {low} to {high} words. "
                        "Adjust the amount of condensing, keeping every event.")
            print(f"[Story] {feedback}")
        if not reply or not reply.get("lines"):
            raise RuntimeError("the AI did not return a usable script")
        script = self.finish_script(ep, reply)
        write_json(path, script)
        return script

    def finish_script(self, ep, reply):
        sheet = self.book["characters"]
        for c in reply.get("new_characters") or []:
            name = str(c.get("name") or "").strip()
            if name and name not in sheet["characters"]:
                sheet["characters"][name] = {"gender": c.get("gender") or "male", "age": c.get("age") or "adult",
                                             "look": c.get("look") or ""}
        for p in reply.get("new_places") or []:
            if p.get("name") and p["name"] not in sheet["places"]:
                sheet["places"][p["name"]] = p.get("look") or ""
        self.assign_voices()

        story = []
        for n, line in enumerate(reply["lines"]):
            text = " ".join(str(line.get("text", "")).split())
            if text:
                story.append({"n": n, "kind": line.get("kind") or "story", "speaker": line.get("speaker") or "narrator",
                              "text": text, "picture": str(line.get("picture") or text)})
        warnings = {k: v for k, v in (reply.get("warnings") or {}).items()
                    if k in WARNING_NAMES and v in ("mild",) + CARD_LEVELS}
        cards = []
        carded = [k for k in WARNING_NAMES if warnings.get(k) in CARD_LEVELS]
        if carded:
            listed = [WARNING_NAMES[k] for k in carded]
            joined = listed[0] if len(listed) == 1 else ", ".join(listed[:-1]) + " and " + listed[-1]
            cards.append({"n": None, "kind": "card", "card": "warning", "speaker": "narrator", "picture": None,
                          "text": f"A note before we begin: this chapter contains {joined}. Viewer discretion is advised."})
        title = {"n": None, "kind": "card", "card": "title", "speaker": "narrator", "picture": None,
                 "text": f"{self.book['title']}. {episode_label(ep)}" + (f": {ep['chapter_title']}."
                                                                        if ep["chapter_title"] else ".")}
        # Warning first, then the hook and recap, then the title card, then the story
        opening = [line for line in story if line["kind"] in ("hook", "recap")]
        lines = cards + opening + [title] + [line for line in story if line["kind"] not in ("hook", "recap")]
        return {"episode": ep["key"], "lines": lines, "warnings": warnings,
                "teaser": reply.get("teaser") or {}, "short_title": reply.get("short_title") or "",
                "description": reply.get("description") or "", "summary": reply.get("summary") or "",
                "keywords": reply.get("keywords") or [], "hashtags": reply.get("hashtags") or [],
                "written_by": "ai"}

    def assign_voices(self):
        """Give every character without a voice the next free voice of their gender (kept for the whole book)."""
        people = self.book["characters"]["characters"]
        narrator = self.profile["voice"]
        used = {c.get("voice") for c in people.values()}
        for name, c in people.items():
            if c.get("voice"):
                continue
            gender = "female" if str(c.get("gender")).lower().startswith("f") else "male"
            age = c.get("age") if c.get("age") in AGE_SHIFT else "adult"
            pitch, rate = AGE_SHIFT[age]
            if age == "child":
                voice, child_pitch = CHILD_VOICES[gender]
                pitch += child_pitch
            else:
                pool = [v for v in (FEMALE_VOICES if gender == "female" else MALE_VOICES) if v != narrator]
                free = [v for v in pool if v not in used]
                voice = free[0] if free else pool[len(people) % len(pool)]
                if not free:
                    # Voices are reused only with a clearly different pitch
                    pitch += 10 if len(people) % 2 else -10
            c.update(voice=voice, pitch=pitch, rate=rate)
            used.add(voice)

    # ----- 2. voices -----

    def character(self, speaker):
        """The character sheet entry a script's speaker refers to ("Holmes" -> "Sherlock Holmes"), or None."""
        people = self.book["characters"]["characters"]
        if speaker in people:
            return people[speaker]
        lowered = {name.lower(): c for name, c in people.items()}
        if speaker.lower() in lowered:
            return lowered[speaker.lower()]
        matches = [c for name, c in people.items() if speaker.lower() in name.lower().split()]
        return matches[0] if len(matches) == 1 else None

    def voice_for(self, speaker):
        c = self.character(speaker) if speaker != "narrator" else None
        if self.settings["character_voices"] and c and c.get("voice"):
            return {"voice": c["voice"], "pitch": c.get("pitch", 0), "rate": c.get("rate", 0)}
        return {"voice": self.profile["voice"], "pitch": 0, "rate": 0}

    def narrate(self, script, folder):
        path = os.path.join(folder, "narration.mp3")
        if script.get("timing") and os.path.exists(path):
            return
        print(f"[Story] Narrating {len(script['lines'])} lines...")
        spec = [dict(self.voice_for(line["speaker"]), text=line["text"]) for line in script["lines"]]
        style = self.profile.get("voice_expression") if self.profile.get("voice_expression") in \
            expressive_voice.STYLES else "measured"
        seconds, starts = expressive_voice.narrate_lines(spec, self.profile.get("voice_rate", "-5%"), style, path)
        script["timing"] = {"seconds": seconds, "starts": starts}
        write_json(os.path.join(folder, "script.json"), script)
        print(f"[Story] Narration: {clock(seconds)} minutes")
        if not 5 * 60 <= seconds <= 10.5 * 60 and not self.episode(script["episode"])["short"]:
            print(f"[Story] Note: {clock(seconds)} is outside 5 to 10 minutes.")

    def episode(self, key):
        return next(ep for ep in self.book["episodes"] if ep["key"] == key)

    # ----- 3. pictures -----

    def picture_prompt(self, line, wording=0):
        """wording 0: the script's picture description with every character's look; 1: the description alone;
        2: the sentence itself (plainer wordings get past the picture service's over-eager safety filter)."""
        sheet = self.book["characters"]
        text = line["text"] if wording >= 2 else line["picture"]
        if wording:
            return f"{text}, {PICTURE_STYLES[self.settings['picture_style']]}"[:2000]
        described = set()
        for name, c in list(sheet["characters"].items()) + [(n, {"look": p}) for n, p in sheet["places"].items()]:
            pattern = re.compile(r"\b" + re.escape(name) + r"\b", re.I)
            if c.get("look") and name not in described and pattern.search(text):
                text = pattern.sub(lambda m: f"{m.group(0)} ({c['look']})", text, count=1)
                described.add(name)
        return f"{text}, {PICTURE_STYLES[self.settings['picture_style']]}"[:2000]

    def draw_picture(self, prompt):
        account, token = os.getenv("CLOUDFLARE_ACCOUNT_ID"), os.getenv("CLOUDFLARE_API_TOKEN")
        if not account or not token:
            raise RuntimeError("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are needed for story pictures")
        url = CLOUDFLARE_RUN.format(account=account, model=self.tune["picture_model"])
        for attempt in range(3):
            try:
                reply = requests.post(url, headers={"Authorization": f"Bearer {token}"},
                                      json={"prompt": prompt, "steps": int(self.tune["picture_steps"])}, timeout=120)
            except requests.RequestException as e:
                # The token is sent in a header, so the error text never contains it
                print(f"[Story] Picture request failed ({type(e).__name__}); retrying...")
                continue
            if reply.status_code == 200:
                return base64.b64decode(reply.json()["result"]["image"])
            text = reply.text[:300]
            if "NSFW" in text or '"code":8007' in text.replace(" ", ""):
                raise PromptRefused(text)
            if reply.status_code == 429 or re.search(r"allocation|neurons|limit|quota", text, re.I):
                raise PictureLimit(text)
            if reply.status_code < 500:
                raise RuntimeError(f"Cloudflare picture error {reply.status_code}: {text}")
        raise RuntimeError("Cloudflare pictures are unavailable right now")

    def check_pictures(self, script, folder, batch):
        """Gemini compares each picture with its line. Returns {index: reason} of failures, or None if it can't."""
        sheet = self.characters_text()
        pictures, numbered = [], []
        for index in batch:
            with Image.open(os.path.join(folder, "pictures", f"{index:03d}.jpg")) as img:
                small = img.convert("RGB").resize((448, 448))
                buffer = io.BytesIO()
                small.save(buffer, "JPEG", quality=80)
                pictures.append((f"Picture {index}:", buffer.getvalue()))
            numbered.append(f"Picture {index}: {script['lines'][index]['text']}")
        style = "pencil sketch" if self.settings["picture_style"] == "pencil" else "storybook illustration"
        prompt = (
            f"These pictures illustrate an audiobook of \"{self.book['title']}\", one picture per sentence.\n"
            f"Characters and places:\n{sheet}\n\nThe sentence each picture must show:\n" + "\n".join(numbered) +
            "\n\nA picture FAILS when: it does not clearly show the sentence's main subject and action (wrong person, "
            "wrong number of people, wrong gender, wrong place, a key object missing or wrong); it contains visible "
            "writing, letters, numbers, a signature or a logo; it shows gore, nudity or sexual content; or it is not "
            f"a {style}. Small artistic differences are fine.\n"
            'Reply with ONLY JSON: {"fail": [{"n": picture number, "why": "short reason"}]} (an empty list when all '
            "pass)."
        )
        reply = look_at_pictures(self.client, pictures, prompt, self.config.get("ai_models"))
        if reply is None:
            return None
        try:
            fails = parse_json_reply(reply).get("fail") or []
        except ValueError:
            return None
        return {int(f["n"]): str(f.get("why") or "") for f in fails if str(f.get("n", "")).isdigit()
                and int(f["n"]) in batch}

    def make_pictures(self, script, folder):
        """Draw and check a picture for every line. Returns "ready", "unchecked" (Gemini couldn't check), "waiting"
        (the free daily picture allowance ran out) or "failed" (a line kept failing its check)."""
        os.makedirs(os.path.join(folder, "pictures"), exist_ok=True)
        status_path = os.path.join(folder, "pictures.json")
        status = read_json(status_path, {})
        wanted = [i for i, line in enumerate(script["lines"]) if line.get("picture")]
        attempts = int(self.tune["picture_attempts"])
        for entry in status.values():
            if entry.get("ok") is False and entry["attempts"] > attempts:
                # Failed every check in an earlier run: this run gets fresh attempts
                entry["attempts"] = 0
        drawn = 0
        while True:
            try:
                for index in wanted:
                    path = os.path.join(folder, "pictures", f"{index:03d}.jpg")
                    entry = status.setdefault(str(index), {"attempts": 0, "ok": None})
                    if os.path.exists(path) or entry["attempts"] > attempts:
                        continue
                    wording = 2 if entry["attempts"] >= attempts else 0
                    data = None
                    for wording in range(wording, 3):
                        try:
                            data = self.draw_picture(self.picture_prompt(script["lines"][index], wording))
                            break
                        except PromptRefused:
                            print(f"[Story] Picture {index}: the safety filter refused the wording; trying a plainer one")
                    if data is None:
                        entry.update(attempts=attempts + 1, ok=False, why="refused by the picture safety filter")
                        continue
                    with Image.open(io.BytesIO(data)) as drawn_picture:
                        # The model sometimes adds colour to a pencil sketch: pencil pictures are kept black and white
                        mode = "L" if self.settings["picture_style"] == "pencil" else "RGB"
                        drawn_picture.convert(mode).save(path, "JPEG", quality=90)
                    entry.update(attempts=entry["attempts"] + 1, ok=None)
                    drawn += 1
            except PictureLimit as e:
                write_json(status_path, status)
                print(f"[Story] The free daily picture allowance is used up ({drawn} drawn this run); the next run "
                      f"continues: {e}")
                return "waiting"
            finally:
                write_json(status_path, status)
            if drawn:
                print(f"[Story] {drawn} picture(s) drawn")
            if not self.tune["check_pictures"]:
                return "ready"
            unchecked = [i for i in wanted if status[str(i)].get("ok") is None
                         and os.path.exists(os.path.join(folder, "pictures", f"{i:03d}.jpg"))]
            if not unchecked:
                break
            size = int(self.tune["pictures_per_check"])
            redraw = 0
            for start in range(0, len(unchecked), size):
                batch = unchecked[start:start + size]
                fails = self.check_pictures(script, folder, batch)
                if fails is None:
                    write_json(status_path, status)
                    return "unchecked"
                for index in batch:
                    entry = status[str(index)]
                    if index in fails:
                        entry.update(ok=False, why=fails[index])
                        print(f"[Story] Picture {index} redrawn: {fails[index]}")
                        os.remove(os.path.join(folder, "pictures", f"{index:03d}.jpg"))
                        redraw += 1
                    else:
                        entry["ok"] = True
            write_json(status_path, status)
            if not redraw:
                break
            drawn = 0
        failed = [i for i in wanted if not os.path.exists(os.path.join(folder, "pictures", f"{i:03d}.jpg"))]
        if failed:
            print(f"[Story] Line(s) {failed} failed every picture check.")
            return "failed"
        return "ready"

    # ----- 4. video -----

    def page(self, size):
        image = Image.new("RGB", size, PAPER)
        draw = ImageDraw.Draw(image)
        inset = 26 if size[0] > size[1] else 22
        draw.rectangle((inset, inset, size[0] - inset, size[1] - inset), outline=FADED, width=2)
        draw.rectangle((inset + 8, inset + 8, size[0] - inset - 8, size[1] - inset - 8), outline=FADED, width=1)
        return image, draw

    def inked(self, path, side):
        """The picture at side x side; pencil sketches are printed onto the paper (white becomes the page colour)."""
        with Image.open(path) as img:
            picture = img.convert("RGB").resize((side, side), Image.LANCZOS)
        if self.settings["picture_style"] == "pencil":
            picture = ImageChops.multiply(picture, Image.new("RGB", (side, side), PAPER))
        return picture

    def label(self, ep):
        return f"{self.book['title'].upper()}  ·  {episode_label(ep).upper()}"

    def card_frame(self, line, ep, size):
        image, draw = self.page(size)
        w, h = size
        if line.get("card") == "warning":
            listed = [WARNING_NAMES[k] for k, v in self.current_warnings.items() if v in CARD_LEVELS]
            heading_font = serif(int(min(w, h) * 0.075), True)
            draw_block(draw, ["Viewer discretion"], heading_font, int(min(w, h) * 0.1), 0, int(h * 0.36), w, INK,
                       "center")
            font, lines, lh = fit_text(draw, "This chapter contains " + ", ".join(listed) + ".", int(w * 0.7),
                                       int(h * 0.3), int(min(w, h) * 0.045))
            draw_block(draw, lines, font, lh, int(w * 0.15), int(h * 0.56), int(w * 0.7), FADED, "center")
        else:
            font, lines, lh = fit_text(draw, self.book["title"], int(w * 0.8), int(h * 0.3),
                                       int(min(w, h) * 0.1), True)
            bottom = draw_block(draw, lines, font, lh, int(w * 0.1), int(h * 0.36), int(w * 0.8), INK, "center")
            chapter = episode_label(ep) + (f": {ep['chapter_title']}" if ep["chapter_title"] else "")
            font, lines, lh = fit_text(draw, chapter, int(w * 0.8), int(h * 0.2), int(min(w, h) * 0.055))
            bottom = draw_block(draw, lines, font, lh, int(w * 0.1), bottom + int(h * 0.1), int(w * 0.8), FADED,
                                "center")
            by = f"by {self.book['author']}"
            draw_block(draw, [by], serif(int(min(w, h) * 0.035)), int(min(w, h) * 0.05), 0, bottom + int(h * 0.06),
                       w, FADED, "center")
        return image

    def long_layout(self, line, ep):
        """(background with the sentence beside the picture slot, picture box) for a 1920x1080 page."""
        image, draw = self.page(LONG_SIZE)
        box = (96, 72, 936)  # x, y, side
        draw.rectangle((box[0] - 8, box[1] - 8, box[0] + box[2] + 8, box[1] + box[2] + 8), outline=FADED, width=2)
        draw.text((1120, 96), self.label(ep), font=serif(24), fill=FADED)
        text = line["text"]
        top = 540
        if line["speaker"] != "narrator":
            text = f"“{text}”"
            name_font = serif(30, True)
            draw.text((1120, 230), line["speaker"].upper(), font=name_font, fill=FADED)
        font, lines, lh = fit_text(draw, text, 700, 640, 48)
        draw_block(draw, lines, font, lh, 1120, top, 700, INK)
        return image, box

    def short_layout(self, line, ep, hook):
        image, draw = self.page(SHORT_SIZE)
        box = (80, 470, 920)
        draw_block(draw, [self.book["title"].upper()], serif(30), 40, 0, 110, SHORT_SIZE[0], FADED, "center")
        font, lines, lh = fit_text(draw, hook, 940, 260, 72, True)
        draw_block(draw, lines, font, lh, 70, 300, 940, INK, "center")
        draw.rectangle((box[0] - 8, box[1] - 8, box[0] + box[2] + 8, box[1] + box[2] + 8), outline=FADED, width=2)
        if line is not None:
            text = line["text"] if line["speaker"] == "narrator" else f"“{line['text']}”"
            font, lines, lh = fit_text(draw, text, 940, 230, 50)
            draw_block(draw, lines, font, lh, 70, 1530, 940, INK, "center")
        return image, box

    def segment(self, work, index, background, picture, box, seconds):
        """One line's clip: the page, with the picture slowly zooming in its box (or a still card)."""
        bg_path = os.path.join(work, f"bg{index:03d}.png")
        background.save(bg_path)
        out = os.path.join(work, f"seg{index:03d}.mp4")
        encode = ["-t", f"{seconds:.3f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                  "-pix_fmt", "yuv420p", out]
        if picture is None:
            ffmpeg("-loop", "1", "-framerate", str(FPS), "-i", bg_path, "-vf", "setsar=1", *encode)
            return out
        x, y, side = box
        pic_path = os.path.join(work, f"pic{index:03d}.png")
        # Drawn at twice the size and zoomed down, so the slow zoom doesn't jitter
        picture.resize((side * 2, side * 2), Image.LANCZOS).save(pic_path)
        frames = max(1, int(seconds * FPS) + 1)
        zoom = (f"zoompan=z='1+0.07*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}"
                f":s={side}x{side}:fps={FPS}")
        ffmpeg("-loop", "1", "-framerate", str(FPS), "-i", bg_path, "-i", pic_path, "-filter_complex",
               f"[1:v]{zoom}[p];[0:v][p]overlay={x}:{y}:shortest=1,setsar=1", *encode)
        return out

    def finish_video(self, work, segments, audio, out, size, seconds):
        """Join the clips, add the soundtrack, watermark and the end card."""
        with open(os.path.join(work, "parts.txt"), "w", encoding="utf-8") as f:
            f.writelines(f"file '{os.path.basename(p)}'\n" for p in segments)
        montage = os.path.join(work, "montage.mp4")
        ffmpeg("-f", "concat", "-safe", "0", "-i", os.path.join(work, "parts.txt"), "-c", "copy", montage)
        mark = os.path.join(work, "watermark.png")
        watermark_image(self.profile["watermark_text"], 30 if size[0] > size[1] else 34, "#4a4234", 0.7).save(mark)
        last = os.path.join(work, "last.png")
        ffmpeg("-sseof", "-0.2", "-i", montage, "-frames:v", "1", last)
        card = VideoEditorAgent(self.config).end_card_frames(Image.open(last).convert("RGB"))
        inputs = ["-i", montage, "-i", audio, "-i", mark]
        graph = (f"[0:v][2:v]overlay=W-w-{WATERMARK_MARGIN}:H-h-{WATERMARK_MARGIN},format=yuv420p,setsar=1[main];"
                 # loudnorm at -19 LUFS gives the -20 dB mean of the other channels (BRAIN.md audio_mastering)
                 f"[1:a]loudnorm=I=-19:TP=-1.5:LRA=11,aresample=44100,"
                 f"aformat=channel_layouts=stereo,apad,atrim=0:{seconds:.3f}[voice];")
        segs = [("[main]", "[voice]")]
        for index, (frame, seconds) in enumerate(card or []):
            path = os.path.join(work, f"card{index}.png")
            Image.fromarray(frame).save(path)
            inputs += ["-loop", "1", "-t", f"{seconds:.3f}", "-i", path,
                       "-f", "lavfi", "-t", f"{seconds:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
            image_index = inputs.count("-i") - 2
            graph += f"[{image_index}:v]fps={FPS},format=yuv420p,setsar=1[c{index}];"
            segs.append((f"[c{index}]", f"[{image_index + 1}:a]"))
        graph += "".join(v + a for v, a in segs) + f"concat=n={len(segs)}:v=1:a=1[v][a]"
        ffmpeg(*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset",
               "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out)

    def render_episode(self, ep, script, folder):
        out = os.path.join(self.processed, f"final_{self.key}_{slug(self.book['title'])}_{ep['key']}.mp4")
        if os.path.exists(out):
            return out
        print(f"[Story] Rendering {episode_label(ep)}...")
        starts, seconds = script["timing"]["starts"], script["timing"]["seconds"]
        work = tempfile.mkdtemp(prefix="story_", dir=self.processed)
        try:
            segments = []
            lengths = frame_lengths([0.0] + starts[1:] + [seconds])
            for index, line in enumerate(script["lines"]):
                if line.get("card"):
                    background, picture, box = self.card_frame(line, ep, LONG_SIZE), None, None
                else:
                    background, box = self.long_layout(line, ep)
                    picture = self.inked(os.path.join(folder, "pictures", f"{index:03d}.jpg"), box[2])
                segments.append(self.segment(work, index, background, picture, box, lengths[index]))
            self.finish_video(work, segments, os.path.join(folder, "narration.mp3"), out, LONG_SIZE, sum(lengths))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        print(f"[Story] Saved {out}")
        return out

    def teaser_range(self, script):
        """(first, last) line indexes of the teaser, kept to 20 to 50 seconds of narration."""
        lines, starts, seconds = script["lines"], script["timing"]["starts"], script["timing"]["seconds"]
        by_n = {line["n"]: i for i, line in enumerate(lines) if line.get("n") is not None}
        teaser = script.get("teaser") or {}
        story = [i for i, line in enumerate(lines) if line["kind"] == "story" and not line.get("card")]
        first = by_n.get(teaser.get("from"), story[len(story) // 2] if story else 0)
        last = by_n.get(teaser.get("to"), first + 7)
        last = min(max(last, first), len(lines) - 1)

        def length(a, b):
            return (starts[b + 1] if b + 1 < len(starts) else seconds) - starts[a]

        while last + 1 < len(lines) and length(first, last) < 20 and not lines[last + 1].get("card"):
            last += 1
        while last > first and length(first, last) > 50:
            last -= 1
        return first, last

    def render_teaser(self, ep, script, folder):
        out = os.path.join(self.processed, f"final_{self.key}_{slug(self.book['title'])}_{ep['key']}_short.mp4")
        if os.path.exists(out):
            return out
        first, last = self.teaser_range(script)
        starts, seconds = script["timing"]["starts"], script["timing"]["seconds"]
        t0, t1 = starts[first], starts[last + 1] if last + 1 < len(starts) else seconds
        hook = (script.get("teaser") or {}).get("hook") or script.get("short_title") or self.book["title"]
        work = tempfile.mkdtemp(prefix="teaser_", dir=self.processed)
        try:
            closing = os.path.join(work, "closing.mp3")
            closing_seconds, _ = expressive_voice.narrate_lines(
                [dict(self.voice_for("narrator"), text=f"Watch the full chapter on {self.profile['channel_name']}.")],
                self.profile.get("voice_rate", "-5%"), "measured", closing)
            audio = os.path.join(work, "teaser.m4a")
            ffmpeg("-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", os.path.join(folder, "narration.mp3"),
                   "-i", closing, "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[a]", "-map", "[a]",
                   "-c:a", "aac", audio)
            segments = []
            lengths = frame_lengths([starts[i] for i in range(first, last + 1)] + [t1])
            for index, length in zip(range(first, last + 1), lengths):
                background, box = self.short_layout(script["lines"][index], ep, hook)
                picture = self.inked(os.path.join(folder, "pictures", f"{index:03d}.jpg"), box[2])
                segments.append(self.segment(work, index, background, picture, box, length))
            background, box = self.short_layout(None, ep, f"{episode_label(ep)}: full episode on the channel")
            picture = self.inked(os.path.join(folder, "pictures", f"{last:03d}.jpg"), box[2])
            closing_length = frame_lengths([0.0, closing_seconds])[0]
            segments.append(self.segment(work, 999, background, picture, box, closing_length))
            self.finish_video(work, segments, audio, out, SHORT_SIZE, sum(lengths) + closing_length)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        print(f"[Story] Saved {out}")
        return out

    def thumbnail(self, ep, script, folder):
        """1280x720: the teaser's opening picture beside the chapter in big letters."""
        path = os.path.join(folder, "thumbnail.jpg")
        first, _ = self.teaser_range(script)
        image, draw = self.page((1280, 720))
        image.paste(self.inked(os.path.join(folder, "pictures", f"{first:03d}.jpg"), 640), (40, 40))
        font, lines, lh = fit_text(draw, self.book["title"], 540, 260, 64, True)
        bottom = draw_block(draw, lines, font, lh, 710, 230, 540, INK)
        draw_block(draw, [f"CHAPTER {ep['chapter']}" + (f" · PART {ep['part']}" if ep["parts"] > 1 else "")],
                   serif(46, True), 60, 710, bottom + 70, 540, (150, 30, 30))
        draw_block(draw, ["Illustrated audiobook"], serif(34), 44, 710, bottom + 150, 540, FADED)
        image.save(path, "JPEG", quality=90)
        return path

    # ----- 5. upload -----

    def youtube(self):
        if not hasattr(self, "_youtube"):
            from agents.agent_5_publisher import PublisherAgent
            self._publisher = PublisherAgent(self.config, self.instructions)
            self._youtube = self._publisher.get_authenticated_service()
        return self._youtube

    def playlist(self):
        progress = self.book["progress"]
        if progress.get("playlist_id"):
            return progress["playlist_id"]
        body = {"snippet": {"title": f"{self.book['title']} | Illustrated Audiobook (Full Story)"[:150],
                            "description": f"{self.book['title']} by {self.book['author']}, told chapter by chapter "
                                           f"with pencil illustrations. Start with Chapter 1."},
                "status": {"privacyStatus": "public"}}
        progress["playlist_id"] = self.youtube().playlists().insert(part="snippet,status", body=body).execute()["id"]
        self.save_book()
        print(f"[Story] Created the playlist for {self.book['title']}")
        return progress["playlist_id"]

    def long_title(self, ep):
        chapter = episode_label(ep)
        title = f"{self.book['title']} | {chapter}: {ep['chapter_title']} | Illustrated Audiobook"
        if len(title) > 100 or not ep["chapter_title"]:
            title = f"{self.book['title']} | {chapter} | Illustrated Audiobook"
        return title[:100]

    def hashtags(self, script):
        tags = [t if t.startswith("#") else "#" + t for t in script.get("hashtags") or []]
        tags = [re.sub(r"[^\w#]", "", t) for t in tags] + ["#audiobook", "#classicliterature"]
        return list(dict.fromkeys(t for t in tags if len(t) > 2))[:8]

    def description(self, ep, script, previous_id=None):
        book = self.book
        parts = [script.get("description") or book.get("summary", "")[:400]]
        if script.get("warnings"):
            listed = ", ".join(WARNING_NAMES[k] for k in WARNING_NAMES if k in script["warnings"])
            parts.append(f"⚠️ Content note: this chapter contains {listed}.")
        links = []
        if book["progress"].get("playlist_id"):
            links.append(f"▶ The whole story, from Chapter 1: "
                         f"https://www.youtube.com/playlist?list={book['progress']['playlist_id']}")
        if previous_id:
            links.append(f"⏮ Previous episode: https://youtu.be/{previous_id}")
        if links:
            parts.append("\n".join(links))
        starts, lines = script["timing"]["starts"], script["lines"]
        marks = [(0.0, "Opening")]
        for index, line in enumerate(lines):
            if line.get("card") == "title":
                marks.append((starts[index], f"{episode_label(ep)}" + (f": {ep['chapter_title']}"
                                                                       if ep["chapter_title"] else "")))
            if line["kind"] == "next" and starts[index] - marks[-1][0] >= 10:
                marks.append((starts[index], "What happens next"))
                break
        if len(marks) >= 3:
            parts.append("\n".join(f"{clock(t)} {name}" for t, name in marks))
        parts.append(f"📖 {book['title']} by {book['author']} is in the public domain ({book['source']}). "
                     f"Retold and illustrated by {self.profile['channel_name']}: AI narration voices and AI-drawn "
                     "pencil illustrations.")
        parts.append(" ".join(self.hashtags(script)))
        return "\n\n".join(p for p in parts if p)[:4900]

    def search_tags(self, ep, script):
        book = self.book
        tags = [book["title"], f"{book['title']} audiobook", f"{book['title']} chapter {ep['chapter']}",
                book["author"], f"{book['author']} audiobook"] + list(script.get("keywords") or []) + \
            ["illustrated audiobook", "classic stories", "audiobook full", self.profile["channel_name"]]
        kept, length = [], 0
        for tag in dict.fromkeys(" ".join(re.sub(r"[<>,]", " ", t).split()) for t in tags if t):
            if length + len(tag) + 3 <= 450:
                kept.append(tag)
                length += len(tag) + 3
        return kept

    def upload_one(self, path, title, description, tags, publish_at, thumbnail=None):
        status = {"privacyStatus": self.profile["privacy_status"], "selfDeclaredMadeForKids": False,
                  "containsSyntheticMedia": self.config["upload_rules"]["synthetic_media_disclosure"]}
        # Only public episodes are scheduled (YouTube makes a scheduled video public); "unlisted" or "private" in
        # questions.yaml uploads them as that, for the owner to review first
        if publish_at and self.profile["privacy_status"] == "public":
            status.update(privacyStatus="private", publishAt=publish_at.strftime("%Y-%m-%dT%H:%M:%SZ"))
        body = {"snippet": {"title": title, "description": description, "categoryId": self.profile["category_id"],
                            "tags": tags, "defaultLanguage": "en", "defaultAudioLanguage": "en"},
                "status": status}
        print(f"[Story] Uploading '{title}'" + (f", live at {status['publishAt']}" if publish_at else ""))
        self.youtube()
        response = self._publisher.upload(self._youtube, path, body)
        if thumbnail:
            try:
                from googleapiclient.http import MediaFileUpload
                self._youtube.thumbnails().set(videoId=response["id"],
                                               media_body=MediaFileUpload(thumbnail, mimetype="image/jpeg")).execute()
            except Exception as e:
                print(f"[Story] Thumbnail not set (needs a phone-verified channel): {str(e)[:200]}")
        return response["id"]

    def upload(self, ep, script, folder, day, long_path, short_path):
        """Upload the episode and its teaser. Returns the manifest items for the Telegram message."""
        publish_at = None
        if day is not None:
            when = self.post_at(day).astimezone(timezone.utc)
            publish_at = when if when > datetime.now(timezone.utc) + timedelta(minutes=15) else None
        progress = self.book["progress"]
        keys = [e["key"] for e in self.book["episodes"]]
        index = keys.index(ep["key"])
        previous_id = (progress["episodes"].get(keys[index - 1]) or {}).get("youtube_id") if index else None
        if not self.test:
            self.playlist()
        title, hashtags = self.long_title(ep), self.hashtags(script)
        video_id = self.upload_one(long_path, title, self.description(ep, script, previous_id),
                                   self.search_tags(ep, script), publish_at, self.thumbnail(ep, script, folder))
        if not self.test:
            try:
                self._youtube.playlistItems().insert(part="snippet", body={"snippet": {
                    "playlistId": progress["playlist_id"],
                    "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
            except Exception as e:
                print(f"[Story] Could not add the episode to the playlist: {str(e)[:200]}")
        items = [self.manifest_item(ep, "long", video_id, title, publish_at, long_path, hashtags)]
        short_id = None
        if short_path:
            short_title = (script.get("short_title") or f"{self.book['title']}: {episode_label(ep)}")[:90]
            text = (f"▶ Full episode: https://youtu.be/{video_id}\n\n{script.get('description', '')}\n\n"
                    f"📖 {self.book['title']} by {self.book['author']}, public domain. Retold and illustrated by "
                    f"{self.profile['channel_name']}.\n\n" + " ".join(["#shorts"] + hashtags[:4]))
            short_id = self.upload_one(short_path, short_title, text, self.search_tags(ep, script), publish_at)
            items.append(self.manifest_item(ep, "short", short_id, short_title, publish_at, short_path, hashtags))
        record = {"youtube_id": video_id, "short_id": short_id, "title": title,
                  "publish_at": publish_at.isoformat() if publish_at else None,
                  "summary": script.get("summary"), "warnings": script.get("warnings")}
        if self.test:
            progress.setdefault("tests", []).append(dict(record, episode=ep["key"]))
        else:
            progress["episodes"][ep["key"]] = dict(record, status="uploaded", date=day.isoformat() if day else None)
            if day and (not self.state.get("last_date") or day.isoformat() > self.state["last_date"]):
                self.state["last_date"] = day.isoformat()
            write_json(self.state_file, self.state)
            self.archive(folder, ep)
        self.save_book()
        return items

    def manifest_item(self, ep, fmt, video_id, title, publish_at, path, hashtags):
        now = datetime.now(timezone.utc).isoformat()
        return {"video_id": f"{slug(self.book['title'])}-{ep['key']}" + ("-short" if fmt == "short" else "")
                            + ("-test" if self.test else ""),
                "profile": self.key, "format": fmt, "status": "published", "genre": self.book["title"],
                "original_title": title, "discovered_at": now, "uploaded_at": now,
                "uploaded_youtube_id": video_id, "uploaded_title": title,
                "publish_at": publish_at.strftime("%Y-%m-%dT%H:%M:%SZ") if publish_at else None,
                "edited_video_path": path, "upload_hashtags": hashtags}

    def archive(self, folder, ep):
        """Move a finished episode's working files to archive/, which runs don't download again (the pencil
        pictures and prompts are kept there for a future colour or animation pass)."""
        target = os.path.join(self.book["folder"], "archive", ep["key"])
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(folder, target)
        if os.getenv("GITHUB_ACTIONS") == "true" and shutil.which("rclone"):
            # GitHub runs: straight to Drive's archive, then the Drive working copy (from earlier runs) is removed,
            # so finished episodes aren't downloaded again every run
            relative = os.path.relpath(folder, os.path.normpath(self.config["storage_paths"]["base_dir"]))
            remote = "gdrive:YouTube_Automation/" + relative.replace(os.sep, "/")
            copied = subprocess.run(["rclone", "copy", target, remote.replace("/episodes/", "/archive/")],
                                    capture_output=True)
            if copied.returncode == 0:
                subprocess.run(["rclone", "purge", remote], capture_output=True)
                shutil.rmtree(target, ignore_errors=True)

    # ----- the run -----

    def make(self, ep, day, dry_run):
        folder = os.path.join(self.book["folder"], "episodes", ep["key"])
        os.makedirs(folder, exist_ok=True)
        script = self.write_script(ep, folder)
        self.save_book()
        self.current_warnings = script.get("warnings") or {}
        self.narrate(script, folder)
        pictures = self.make_pictures(script, folder)
        if pictures in ("waiting", "failed"):
            return pictures, []
        if pictures == "unchecked":
            # Checked next run, unless the episode goes live within 6 hours
            due = day is None or self.post_at(day) - datetime.now(self.tz) < timedelta(hours=6)
            if not due:
                return "unchecked", []
            print("[Story] Gemini couldn't check the pictures and the episode is due soon; using them unchecked.")
        long_path = self.render_episode(ep, script, folder)
        short_path = self.render_teaser(ep, script, folder) if self.settings["teaser"] else None
        if dry_run:
            return "made", []
        return pictures, self.upload(ep, script, folder, day, long_path, short_path)

    def run(self, targets, dry_run=False):
        """Make (and unless dry_run, upload) the planned episodes. Returns the number of videos uploaded."""
        uploaded, notes = [], []
        for ep, day in targets:
            print(f"\n[Story] {self.book['title']}: {episode_label(ep)}"
                  + (f" for {self.post_at(day):%d %b %H:%M}" if day else " (test)"))
            try:
                outcome, items = self.make(ep, day, dry_run)
            except PictureLimit:
                outcome, items = "waiting", []
            uploaded += items
            if outcome == "waiting":
                notes.append(f"🖼 {episode_label(ep)}: the free daily picture allowance ran out; the next run "
                             "finishes its pictures.")
                break
            if outcome == "failed":
                notes.append(f"⚠️ {episode_label(ep)}: some pictures kept failing their check; they are drawn again "
                             "in the next run.")
                break
            if outcome == "unchecked":
                notes.append(f"🔎 {episode_label(ep)}: the pictures are drawn; Gemini checks them in the next run.")
                break
        self.waiting = notes[-1] if notes else ""
        self.notify(uploaded, notes, dry_run)
        if not dry_run and self.book and all((self.book["progress"]["episodes"].get(e["key"]) or {}).get("status")
                                             == "uploaded" for e in self.book["episodes"]):
            self.state["finished"].append(slug(self.book["title"]))
            write_json(self.state_file, self.state)
            send_telegram(f"🏁 <b>{html.escape(self.profile['channel_name'])}</b>: every episode of "
                          f"<b>{html.escape(self.book['title'])}</b> is uploaded. The next book starts in the next run "
                          "(add one with \"book &lt;title&gt;\" in Telegram, or in questions.yaml).")
        return sum(1 for i in uploaded)

    def notify(self, uploaded, notes, dry_run):
        if dry_run:
            for note in notes:
                print(f"[Story] {note}")
            return
        if uploaded:
            self.add_to_manifest(uploaded)
        from agents.agent_7_notifier import TelegramNotifierAgent
        planned = len(uploaded) if not notes else len(uploaded) + 1
        if uploaded or notes:
            TelegramNotifierAgent(self.config).run(uploaded, planned)
        lines = []
        longs = [i for i in uploaded if i["format"] == "long"]
        shorts = [i for i in uploaded if i["format"] == "short"]
        if longs:
            done = sum(1 for e in self.book["progress"]["episodes"].values() if e.get("status") == "uploaded")
            lines.append(f"📖 <b>{html.escape(self.book['title'])}</b>: episode {done} of "
                         f"{len(self.book['episodes'])} scheduled" + (" (test upload)" if self.test else "") + ".")
        for short, long in zip(shorts, longs):
            lines.append(f"🙋 In YouTube Studio, open the Short https://youtube.com/shorts/{short['uploaded_youtube_id']}"
                         f" → Related video → pick \"{html.escape(long['uploaded_title'][:60])}\" (only Studio can "
                         "link it).")
        lines += notes
        if lines:
            send_telegram("\n".join(lines))

    def add_to_manifest(self, items):
        path = self.config["storage_paths"]["manifest_file"]
        trends = read_json(path, [])
        trends += items
        write_json(path, trends)
