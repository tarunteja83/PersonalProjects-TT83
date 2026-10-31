"""Telegram inbox: the owner posts in the alerts channel, and the pipeline reads it and acts on it.

Runs from the "Telegram Inbox" GitHub workflow (hourly) or `python execution/execution.py --inbox`. The bot reads
the posts in TELEGRAM_CHANNEL_ID (only channel admins can post there) and handles:

1. A YouTube link. Gemini watches the video and decides what it is:
   - advice about getting views: its techniques become a "proposed" entry in execution/view_techniques.md and
     the bot replies with the proposed rules. "approve S5" (or "approve S5 1,3") puts them in ACTIVE RULES,
     "reject S5" drops them;
   - an example the owner likes: queued in execution/video_requests.json for the channel it fits. That channel's
     next run makes an original video like it (same topic and format, never its words, characters or footage),
     and its search phrases lead the Scout's searches for BOOST_DAYS days, so the following videos ride the trend.
2. A message asking for a video ("make a kids video about how octopuses hide"): queued the same way.
3. "book <title>" (e.g. "book Dracula"): queued for the book storytelling channel, told after the books in
   questions.yaml (only public-domain books found on Project Gutenberg).
4. "queue" lists the recent requests; "cancel Q3" removes one.

Each post is confirmed after it's handled, so it's handled once. Telegram keeps unread posts for 24 hours.
"""
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone

import requests

from agents import learner
from agents.agent_7_notifier import send_telegram
from agents.ai import generate_text, watch_video
from brain import view_rules

REQUESTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "execution",
                             "video_requests.json")
# A request's search phrases lead the Scout's searches for this many days
BOOST_DAYS = 7
# Requests not made within this many days are dropped (e.g. a long video for a channel that makes none)
KEEP_DAYS = 14
# YouTube Shorts can be up to 3 minutes long
SHORT_MAX_SECONDS = 180
YOUTUBE_LINK = re.compile(r"https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/\S+")
OTHER_VIDEO_LINK = re.compile(r"https?://(?:www\.)?(?:instagram\.com|tiktok\.com|facebook\.com|fb\.watch)/\S+")
REVIEW = re.compile(r"^/?(approve|reject)\s+S?(\d+)\s*([\d,\s]*)$", re.IGNORECASE)
CANCEL = re.compile(r"^/?cancel\s+Q?(\d+)$", re.IGNORECASE)
BOOK = re.compile(r"^/?book\b[:\s]+(.+)$", re.IGNORECASE)


# ---------- the request queue (execution/video_requests.json, committed by the workflow) ----------

def load_requests(path=REQUESTS_FILE):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    return {"next_id": int(data.get("next_id") or 1), "requests": list(data.get("requests") or [])}


def save_requests(data, path=REQUESTS_FILE, now=None):
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=KEEP_DAYS)).isoformat()
    # Books wait their turn however long the books before them take
    data["requests"] = [r for r in data["requests"] if r.get("added", "") >= cutoff or r.get("kind") == "book"]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def waiting_requests(channel_key, video_format, used_ids, path=REQUESTS_FILE):
    """The owner's requests this channel's Scout should make next (oldest first), skipping ones already made."""
    return [r for r in load_requests(path)["requests"]
            if r.get("channel") == channel_key and r.get("format") in (video_format, "any")
            and r.get("video_id") not in used_ids]


def boosted_phrases(channel_key, path=REQUESTS_FILE, now=None):
    """Search phrases from this channel's requests of the last BOOST_DAYS days, newest first."""
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=BOOST_DAYS)).isoformat()
    recent = [r for r in load_requests(path)["requests"] if r.get("channel") == channel_key
              and r.get("added", "") >= cutoff]
    return list(dict.fromkeys(p for r in reversed(recent) for p in r.get("search_phrases") or []))


def add_request(fields, path=REQUESTS_FILE):
    """Queue a request; fields: kind, channel, format, video_id (None for a topic), link, title, description,
    creator, notes, search_phrases. Returns its id, e.g. "Q3"."""
    data = load_requests(path)
    request_id = f"Q{data['next_id']}"
    data["next_id"] += 1
    request = dict(fields, id=request_id, added=datetime.now(timezone.utc).isoformat())
    # A topic has no source video; its id keeps it apart in the manifest
    request["video_id"] = request.get("video_id") or f"request-{request_id}"
    data["requests"].append(request)
    save_requests(data, path)
    return request_id


# ---------- Telegram ----------

def same_chat(chat, channel_id):
    channel_id = str(channel_id)
    return str(chat.get("id")) == channel_id or ("@" + (chat.get("username") or "")).lower() == channel_id.lower()


def get_posts(token, channel_id):
    """[(update id, text)] of the unread posts; posts from other chats come back with empty text."""
    reply = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                         params={"timeout": 0, "allowed_updates": json.dumps(["channel_post"])}, timeout=30)
    reply.raise_for_status()
    posts = []
    for update in reply.json().get("result", []):
        post = update.get("channel_post") or {}
        text = (post.get("text") or post.get("caption") or "").strip()
        posts.append((update["update_id"], text if same_chat(post.get("chat") or {}, channel_id) else ""))
    return posts


def confirm(token, last_update_id):
    """Tell Telegram every post up to last_update_id is handled, so it isn't delivered again."""
    requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                 params={"offset": last_update_id + 1, "timeout": 0, "limit": 1}, timeout=30)


# ---------- understanding a post ----------

def fields_of(text):
    """{"KIND": ..., "CHANNEL": ...} from "LABEL: value" lines of an AI reply."""
    fields = {}
    for line in text.splitlines():
        label, colon, value = line.strip().lstrip("*# ").partition(":")
        if colon and re.fullmatch(r"[A-Z]+", label.strip("* ")):
            fields.setdefault(label.strip("* "), value.strip().strip("*").strip())
    return fields


def channel_list(config):
    lines = []
    for key, p in config["profiles"].items():
        if p.get("story"):
            lines.append(f"- {key}: {p['channel_name']}; tells public-domain books chapter by chapter (the owner "
                         "queues a book with 'book <title>'); takes no topic requests")
            continue
        kids = " (made for kids, ages 4 to 9)" if p.get("made_for_kids") else ""
        long = "Shorts and long videos" if (p.get("long") or {}).get("count") else "Shorts only"
        lines.append(f"- {key}: {p['channel_name']}{kids}; niche: {p.get('niche')}; language: "
                     f"{p.get('language', 'English')}; makes {long}")
    return "\n".join(lines)


def seconds_of(duration):
    """ISO 8601 duration (PT1M5S) -> seconds."""
    parts = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    if not parts:
        return 0
    days, hours, minutes, seconds = (int(x or 0) for x in parts.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def link_prompt(config, video, note, watching=True):
    snippet = video["snippet"]
    seen = ("You are watching the video." if watching else
            f"You can't watch the video; judge from its title and description: {snippet['title']} / "
            f"{snippet.get('description', '')[:1500]}.")
    return (
        "The owner of these YouTube channels shared the video below in their Telegram channel:\n"
        f"{channel_list(config)}\n"
        f"Owner's note with the link: {note or '(none)'}\n\n"
        f"{seen} First decide what it is, and put KIND on the first line of your reply:\n"
        "KIND: ADVICE when the video teaches how to get more views or grow a channel. Then follow the ADVICE "
        "instructions below.\n"
        "KIND: EXAMPLE when it's a content video the owner wants more videos like. Then reply with exactly these "
        "lines:\n"
        "CHANNEL: the channel key it fits best (follow the owner's note if it names a channel), or NONE if it fits "
        "no channel's niche or isn't suitable (a made-for-kids channel only takes content safe for ages 4 to 9)\n"
        "TOPIC: the topic in under 10 words\n"
        "WHY: 2 or 3 sentences on what makes it work (hook, structure, pacing, tone, visuals) that an original video "
        "can copy without copying its words, characters, music or footage\n"
        "SEARCH: 3 YouTube search phrases (2 to 5 words, in the channel's language, comma-separated) that find more "
        "videos on this trend\n\n"
        "ADVICE instructions:\n" + learner.build_prompt(video, "", view_rules(), watching=watching)
        + f"\n\n(The video: {snippet['title']} by {snippet['channelTitle']}.)"
    )


def text_prompt(config, text):
    return (
        "The owner of these YouTube channels posted this message in their Telegram channel:\n"
        f"{channel_list(config)}\n\n"
        f'Message: "{text}"\n\n'
        "If it asks for a video (a topic to make a video about), reply with exactly these lines:\n"
        "KIND: REQUEST\n"
        "CHANNEL: the channel key it is for (named in the message, or clearly that channel's niche), or NONE if "
        "it's unclear or the topic isn't safe for a made-for-kids channel it names\n"
        "FORMAT: short or long only if the message says so, otherwise any\n"
        "TOPIC: the topic in under 12 words, in the channel's language\n"
        "WHY: what the owner wants from the video (angle, style, length) in 1 or 2 sentences, or -\n"
        "SEARCH: 3 YouTube search phrases (2 to 5 words, in the channel's language, comma-separated) that find "
        "current videos on this topic\n"
        "Otherwise reply with only: KIND: OTHER"
    )


def phrases_of(value):
    return [p.strip().strip('"') for p in (value or "").split(",") if p.strip()][:3]


class Inbox:
    def __init__(self, config, ai_client):
        self.config = config
        self.client = ai_client
        self.models = config.get("ai_models")

    def queue(self, kind, fields, channel, video_format, **source):
        """Queue a request and the Telegram reply that confirms it."""
        profile = self.config["profiles"][channel]
        if video_format == "long" and not (profile.get("long") or {}).get("count"):
            video_format = "short"
        phrases = phrases_of(fields.get("SEARCH"))
        request_id = add_request(dict(source, kind=kind, channel=channel, format=video_format,
                                      title=source.get("title") or fields.get("TOPIC", ""),
                                      topic=fields.get("TOPIC", ""),
                                      notes="" if fields.get("WHY", "-") == "-" else fields.get("WHY", ""),
                                      search_phrases=phrases))
        which = {"short": "Short", "long": "long video"}.get(video_format, "video")
        return (f"🎬 <b>Queued {request_id}</b> for <b>{html.escape(profile['channel_name'])}</b>: its next "
                f"{which} will be an original video on <b>{html.escape(fields.get('TOPIC', ''))}</b>"
                + (" made like the video you shared" if kind == "example" else "") + ".\n"
                + (f"{'Why it works' if kind == 'example' else 'Your wishes'}: {html.escape(fields['WHY'])}\n"
                   if fields.get("WHY", "-") != "-" else "")
                + (f"Searching this trend first for {BOOST_DAYS} days: {html.escape(', '.join(phrases))}\n"
                   if phrases else "")
                + f'Reply "cancel {request_id}" to drop it.')

    def handle_link(self, link, note):
        video_id = learner.video_id_from_link(link)
        known = learner.known_source(video_id)
        if known:
            return f"ℹ️ That video was already analysed as S{known} in view_techniques.md."
        if any(r.get("video_id") == video_id for r in load_requests()["requests"]):
            return "ℹ️ That video is already queued."
        video = learner.fetch_details(video_id)
        reply = watch_video(self.client, f"https://youtu.be/{video_id}", link_prompt(self.config, video, note),
                            self.models)
        basis = "watching the video"
        if reply is None:
            basis = "the title and description only (the video couldn't be watched)"
            reply = generate_text(self.client, link_prompt(self.config, video, note, watching=False), self.models)
        fields = fields_of(reply)
        title = video["snippet"]["title"]
        if fields.get("KIND", "").upper() == "EXAMPLE":
            channel = fields.get("CHANNEL", "NONE").strip().lower()
            if channel not in self.config["profiles"]:
                return (f"🤔 <b>{html.escape(title)}</b> doesn't fit any of the channels (or isn't suitable for "
                        "kids), so nothing was queued. Post it again with the channel name (kids or frontline) "
                        "to choose one.")
            video_format = ("short" if seconds_of(video.get("contentDetails", {}).get("duration")) <= SHORT_MAX_SECONDS
                            else "long")
            snippet = video["snippet"]
            return self.queue("example", fields, channel, video_format, video_id=video_id, link=link,
                              description=snippet.get("description", ""), creator=snippet["channelTitle"],
                              title=title)
        techniques = learner.parse_techniques(reply)
        if not techniques:
            return f"⚠️ Couldn't analyse <b>{html.escape(title)}</b> right now. Post the link again later."
        number = learner.save_entry(link, video, techniques, basis)
        return self.advice_reply(number, title, techniques)

    @staticmethod
    def advice_reply(number, title, techniques):
        lines = [f"🧠 <b>Learned from:</b> {html.escape(title)} (S{number})"]
        rules = [(i, t) for i, t in enumerate(techniques, 1) if t["verdict"] == "ADOPT-RULE"]
        if rules:
            lines.append("<b>Proposed rules for every future video:</b>")
            lines += [f"{i}. {html.escape(t['rule'])}" for i, t in rules]
            lines.append(f'Reply "approve S{number}", "approve S{number} {rules[0][0]}" (only some) or '
                         f'"reject S{number}".')
        else:
            lines.append("Nothing new to adopt as a writing rule.")
        groups = {"ALREADY": "♻️ Already done", "ADOPT-CODE": "🛠 Needs a code change (ask Claude)",
                  "MANUAL": "🙋 For you to do", "REJECT": "⛔ Rejected (against YouTube's rules)"}
        for verdict, label in groups.items():
            names = [t["name"] for t in techniques if t["verdict"] == verdict]
            if names:
                lines.append(f"{label}: {html.escape('; '.join(names))}")
        return "\n".join(lines)

    def handle_text(self, text):
        review = REVIEW.match(text)
        if review:
            approve, number = review.group(1).lower() == "approve", int(review.group(2))
            picks = {int(n) for n in re.findall(r"\d+", review.group(3))} or None
            rules = learner.review(number, picks, approve)
            if rules:
                return (f"✅ Added to the rules every video follows, from the next run:\n"
                        + "\n".join(f"• {html.escape(r)}" for r in rules))
            return f"🗑 S{number} reviewed: no rules added."
        cancel = CANCEL.match(text)
        if cancel:
            data = load_requests()
            before = len(data["requests"])
            data["requests"] = [r for r in data["requests"] if r.get("id") != f"Q{cancel.group(1)}"]
            save_requests(data)
            return (f"🗑 Q{cancel.group(1)} cancelled." if len(data["requests"]) < before
                    else f"There is no request Q{cancel.group(1)}.")
        book = BOOK.match(text.strip())
        if book:
            return self.queue_book(book.group(1).strip().strip('"'))
        if text.strip().lower().lstrip("/") in ("queue", "requests"):
            recent = load_requests()["requests"]
            if not recent:
                return f"No requests in the last {KEEP_DAYS} days."
            return (f"📋 Requests of the last {KEEP_DAYS} days (each is made in its channel's next run; the "
                    "channel's upload message marks it):\n" + "\n".join(
                        f"{r['id']}: {html.escape(r.get('topic') or r.get('title', ''))} → {r['channel']}, "
                        f"{r['format']}" for r in recent))
        fields = fields_of(generate_text(self.client, text_prompt(self.config, text), self.models))
        if fields.get("KIND", "").upper() != "REQUEST":
            return None
        channel = fields.get("CHANNEL", "NONE").strip().lower()
        if channel not in self.config["profiles"]:
            return ("🤔 I couldn't tell which channel that video is for (or the topic isn't suitable for kids). "
                    "Say it again with the channel name, e.g. \"make a kids video about ...\".")
        if self.config["profiles"][channel].get("story"):
            return ("📚 The book channel tells whole books. Queue one with \"book &lt;title&gt;\", "
                    "e.g. \"book Dracula\".")
        video_format = fields.get("FORMAT", "any").strip().lower()
        return self.queue("topic", fields, channel, video_format if video_format in ("short", "long") else "any",
                          description=text, creator="owner request")

    def queue_book(self, title):
        """Queue a public-domain book for the story channel (found on Project Gutenberg)."""
        from agents.storyteller import find_book
        channel = next((k for k, p in self.config["profiles"].items() if p.get("story")), None)
        if not channel:
            return "There is no book channel (a channel with a story: section in questions.yaml)."
        found = find_book(title)
        if not found:
            return (f"🤔 <b>{html.escape(title)}</b> isn't on Project Gutenberg, so it is probably still under "
                    "copyright. Check the spelling, or pick a public-domain classic.")
        if not found["public_domain"]:
            return f"⛔ <b>{html.escape(found['title'])}</b> is still under copyright, so it can't be told."
        profile = self.config["profiles"][channel]
        if found["title"].lower() in (b.lower() for b in profile["story"]["books"]) or any(
                r.get("kind") == "book" and r.get("title", "").lower() == found["title"].lower()
                for r in load_requests()["requests"]):
            return f"ℹ️ <b>{html.escape(found['title'])}</b> is already on the list."
        request_id = add_request({"kind": "book", "channel": channel, "format": "long", "title": found["title"],
                                  "topic": found["title"], "creator": found["author"], "notes": "",
                                  "description": found["summary"][:300], "search_phrases": [],
                                  "video_id": f"book-{found['gutenberg_id']}"})
        return (f"📚 <b>Queued {request_id}</b> for <b>{html.escape(profile['channel_name'])}</b>: "
                f"<b>{html.escape(found['title'])}</b> by {html.escape(found['author'])} (public domain, Project "
                f"Gutenberg #{found['gutenberg_id']}). It starts after the books before it are finished.\n"
                f'Reply "cancel {request_id}" to drop it.')

    def handle(self, text):
        """The reply to one post (Telegram HTML), or None to stay quiet."""
        link = YOUTUBE_LINK.search(text)
        if link:
            return self.handle_link(link.group(0), YOUTUBE_LINK.sub("", text).strip())
        if OTHER_VIDEO_LINK.search(text):
            return ("📎 Only YouTube links can be analysed for now: Instagram, TikTok and Facebook need a login. "
                    "Find the same video on YouTube, or describe it as a request (\"make a kids video about ...\").")
        return self.handle_text(text)


def run(config, ai_client):
    """Handle every unread post in the Telegram channel. Returns how many were handled."""
    token, channel_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHANNEL_ID")
    if not token or not channel_id:
        print("[Inbox] TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID is not set.")
        return 0
    try:
        posts = get_posts(token, channel_id)
    except Exception as e:
        # The exception text can contain the request URL, which includes the bot token
        print(f"[Inbox] Could not read Telegram: {str(e).replace(token, '***')}")
        return 0
    inbox, handled = Inbox(config, ai_client), 0
    for update_id, text in posts:
        if text:
            print(f"[Inbox] Post: {text[:120]}")
            try:
                reply = inbox.handle(text)
            except Exception as e:
                print(f"[Inbox] Failed: {e}")
                reply = f"⚠️ Couldn't handle that post ({html.escape(str(e)[:150])}). Try again later."
            if reply:
                send_telegram(reply)
            handled += 1
        # Confirmed one by one, so a crash never makes Telegram deliver an already-handled post again
        confirm(token, update_id)
    print(f"[Inbox] {handled} post(s) handled.")
    return handled
