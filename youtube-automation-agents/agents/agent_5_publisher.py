import os
import re
import json
from datetime import datetime, timedelta, timezone
from google import genai
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
import time

from agents.ai import generate_text
from agents.agent_1_scout import GENERIC_HASHTAGS
from agents.stock_footage import COMMONS_SITE
from brain import YOUTUBE_SCOPES as SCOPES, channel_matches, item_matches

UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
UPLOAD_RETRIES = 5
PASSED_SLOT_GAP = timedelta(minutes=30)
MAX_HASHTAGS = 12
MAX_DESCRIPTION = 4900
# Words too general to make a search phrase relevant to a video on their own (without a final "s", as compared:
# "kids" -> "kid", "this" -> "thi", "news" -> "new")
KEYWORD_FILLER = {"kid", "children", "short", "video", "with", "from", "thi", "that", "what", "your", "new",
                  "today", "latest", "best", "funny", "story", "fact", "update"}

class PublisherAgent:
    def __init__(self, config, instructions):
        self.config = config
        self.instructions = instructions
        self.active_key = config["active_profile"]
        self.profile = config["profiles"][self.active_key]
        self.upload_rules = config["upload_rules"]
        self.paths = config["storage_paths"]
        self.ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    def get_authenticated_service(self):
        tokens_dir = self.paths.get("tokens_dir", ".")
        token_file = os.path.join(tokens_dir, f"token_{self.active_key}.json")
        creds = None

        if os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if os.getenv("GITHUB_ACTIONS") == "true":
                    raise RuntimeError(
                        f"YouTube login for '{self.active_key}' is missing or expired. Run "
                        f"'python execution/execution.py --authorize {self.active_key}' on your laptop "
                        "and upload the token file to Google Drive: YouTube_Automation/00_tokens/"
                    )
                print(f"\n[!] Please select the '{self.profile['channel_name']}' Brand Channel in the browser popup!")
                flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
                creds = flow.run_local_server(port=0)
            os.makedirs(tokens_dir, exist_ok=True)
            with open(token_file, "w") as token:
                token.write(creds.to_json())

        youtube = build("youtube", "v3", credentials=creds)
        self.verify_channel(youtube)
        return youtube

    def verify_channel(self, youtube):
        # Stop before uploading if this login belongs to a different channel than channels.yaml says
        items = youtube.channels().list(part="snippet", mine=True).execute().get("items", [])
        if not items:
            raise RuntimeError(f"The YouTube login for '{self.active_key}' has no channel.")
        channel_id, snippet = items[0]["id"], items[0]["snippet"]
        if not channel_matches(self.profile["channel_url"], channel_id, snippet.get("customUrl")):
            raise RuntimeError(
                f"The YouTube login for '{self.active_key}' is for '{snippet['title']}' ({channel_id}), "
                f"not {self.profile['channel_url']}. Run --authorize {self.active_key} again and pick the right channel."
            )
        print(f"[Agent 5: Publisher] Confirmed channel: {snippet['title']}")

    @staticmethod
    def fallback_seo_package(item, max_title):
        """Used when Gemini is unavailable: the source video's own title, and the opening of our narration."""
        title = re.sub(r"#\w+", "", item.get("original_title") or "").replace('"', "")
        title = " ".join(title.split()).strip(" |-:") or "New video"
        if len(title) > max_title:
            title = title[:max_title - 3].rsplit(" ", 1)[0] + "..."
        script = " ".join((item.get("generated_script") or "").split())
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script) if s.strip()]
        return title, " ".join(sentences[:2])[:400]

    def generate_seo_package(self, item):
        max_title = 75 if item.get("format") == "long" else 65
        if item.get("seo_title"):
            # Written together with the script, so no extra Gemini request is needed
            new_title = item["seo_title"].replace('"', '').strip()[:max_title]
            return new_title, self.finish_description(item, item.get("seo_description") or "",
                                                      item.get("seo_hashtags") or "")

        prompt = (
            f"Follow these system instructions: {self.instructions}\n"
            f"Profile: {self.active_key}\n"
            f"Original Title: {item['original_title']}\n"
            f"Our Voiceover Script: {item['generated_script']}\n"
            f"Language for title, description and hashtags: {self.profile.get('language', 'English')}\n\n"
            + ("Generate a click-worthy YouTube video title (maximum 70 characters, no quotes) on Line 1.\n"
               "On the following lines, write a 3-sentence SEO description followed by 5 relevant hashtags "
               "(do not use #shorts)."
               if item.get("format") == "long" else
               "Generate a viral YouTube Short Title (maximum 60 characters, no quotes) on Line 1.\n"
               "On the following lines, write a 2-sentence SEO description followed by 5 relevant hashtags.")
        )
        try:
            resp = generate_text(self.ai_client, prompt, self.config.get("ai_models")).split("\n")
            new_title = resp[0].replace('"', '').strip()[:max_title]
            seo_body = "\n".join(resp[1:]).strip()
        except Exception as e:
            # Gemini quota ran out or it is down: the video is already rendered, so publish it under the
            # source video's title instead of losing the slot
            print(f"[Agent 5: Publisher] The AI could not write the title for {item['video_id']}, "
                  f"using the source video's title: {str(e)[:160]}")
            new_title, seo_body = self.fallback_seo_package(item, max_title)

        # Gemini's hashtags may sit inside the text; gather them so they are merged with the trending ones
        gemini_tags = " ".join(re.findall(r"#\w+", seo_body))
        seo_body = "\n".join(line for line in seo_body.splitlines() if not re.fullmatch(r"(\s*#\w+)+\s*", line))
        return new_title, self.finish_description(item, seo_body.strip(), gemini_tags)

    def hashtags(self, item, gemini_tags):
        """Hashtags for reach: what is trending in the niche right now, the video's own topic, and the source's.

        YouTube shows the first three above the title, so they are the two strongest trending niche tags and the
        strongest topic tag; then #shorts (Shorts), the other topic tags, the rest of the trending ones and the
        source video's. Generic tags (#viral, #fyp) and other channels' names are left out; at most MAX_HASHTAGS.
        """
        source_text = f"{item.get('original_title') or ''} {item.get('original_description') or ''}"
        creator = re.sub(r"\W", "", (item.get("original_creator") or "").lower())
        source = [t for t in re.findall(r"#\w+", source_text)
                  if not (creator and (creator in t.lower() or t.lower().lstrip("#") in creator))
                  and t.lower() not in GENERIC_HASHTAGS]
        niche = list(item.get("trending_hashtags") or [])
        topic = re.findall(r"#\w+", gemini_tags)
        tags = (niche[:2] + topic[:1] + (["#shorts"] if item.get("format") != "long" else [])
                + topic[1:] + niche[2:] + source)
        if len(tags) <= 1:
            # Nothing from Gemini or the source: fall back to the channel's niche words
            tags += ["#" + w.lower() for w in re.findall(r"[A-Za-z]{4,}", self.profile.get("niche", ""))][:4]
        unique = {}
        for tag in tags:
            if item.get("format") == "long" and tag.lower() == "#shorts":
                continue
            unique.setdefault(tag.lower(), tag)
        return list(unique.values())[:MAX_HASHTAGS]

    @staticmethod
    def relevant_keywords(item):
        """The Scout's search phrases (autocomplete, rising videos' tags) that are about this video.

        A phrase is kept only when every word of 4+ letters in it (besides general ones like "kids") is in the
        video's title, keywords, hashtags or script, so the tags never promise something the video isn't about
        and never carry other channels' names.
        """
        def words(text):
            return {w.rstrip("s") for w in re.findall(r"\w{4,}", text.lower())} - KEYWORD_FILLER

        own = words(" ".join([item.get("seo_title") or "", item.get("seo_keyword") or "",
                              " ".join(item.get("seo_keywords") or []), item.get("seo_hashtags") or "",
                              item.get("generated_script") or ""]))
        return [k for k in item.get("search_keywords") or [] if words(k) and words(k) <= own]

    def search_tags(self, item):
        """YouTube's hidden search tags, strongest first: the video's search keyword and its variations, the real
        search phrases that fit it, the hashtags as words, the footage topic terms and the channel name."""
        keyword = [item["seo_keyword"]] if item.get("seo_keyword") else []
        tags = keyword + list(item.get("seo_keywords") or []) + self.relevant_keywords(item) + \
            [t.lstrip("#") for t in item.get("upload_hashtags") or []] + list(item.get("topic_terms") or []) + \
            [self.profile["channel_name"].replace(" ", "")]
        kept, seen, length = [], set(), 0
        # YouTube rejects tags with < or > and splits them at commas
        for tag in (" ".join(re.sub(r"[<>,]", " ", t).split()) for t in tags):
            if not tag or tag.lower() in seen:
                continue
            # YouTube rejects the upload if the tags add up to more than 500 characters; shorter ones may still fit
            if length + len(tag) + 3 > 450:
                continue
            seen.add(tag.lower())
            kept.append(tag)
            length += len(tag) + 3
        return kept

    def finish_description(self, item, text, gemini_tags):
        tags = self.hashtags(item, gemini_tags)
        item["upload_hashtags"] = tags
        credit = self.credit_block(item)
        # YouTube rejects descriptions over 5,000 characters: shorten the AI text, never the hashtags or credits
        room = MAX_DESCRIPTION - len(credit) - len(" ".join(tags)) - 4
        if len(text) > room:
            text = text[:max(room - 3, 0)].rstrip() + "..."
        return "\n\n".join(p for p in (text, " ".join(tags)) if p) + credit

    @staticmethod
    def credit_block(item):
        """Credits only for real footage and photos of the actual events (Wikimedia Commons, e.g. Frontline).

        Stock sites (Pixabay, Pexels) and music get no credits section (owner's decision, 2026-10-31).
        """
        commons = (item.get("footage_sources") or {}).get(COMMONS_SITE) if item.get("footage_paths") else None
        if not commons:
            return ""
        # CC BY asks for the author, the licence and a link; public domain clips are credited too
        lines = ["", "", "---", "Real footage and photos via Wikimedia Commons (free licences):"]
        return "\n".join(lines + [f"- {credit}" for credit in commons])

    @staticmethod
    def upload(youtube, path, body):
        """Upload in 8 MB chunks, resuming after dropped connections, so long videos survive a shaky network."""
        media = MediaFileUpload(path, chunksize=UPLOAD_CHUNK_BYTES, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response, failures = None, 0
        while response is None:
            try:
                status, response = request.next_chunk()
                failures = 0
                if status:
                    print(f"[Agent 5: Publisher] Uploaded {int(status.progress() * 100)}%")
            except (HttpError, OSError) as e:
                retryable = not isinstance(e, HttpError) or e.resp.status in (500, 502, 503, 504)
                failures += 1
                if not retryable or failures > UPLOAD_RETRIES:
                    raise
                print(f"[Agent 5: Publisher] Upload interrupted ({e}); resuming in {10 * failures}s...")
                time.sleep(10 * failures)
        return response

    def scheduled_publish_time(self, index, now=None):
        """Go-live time for the index-th video of this run, or None to publish immediately.

        execution.py sets publish_plan to either
          {"first_utc": ISO time, "interval_minutes": gap}: slots repeat every interval from first_utc, and if
          preparing the videos took past some slots, the next future slot is used (Shorts); or
          {"times_utc": [ISO times]}: this run's go-live slots, one per video. A slot that passed while the
          videos were being made (GitHub often starts scheduled runs hours late) goes live soon instead, keeping
          at least PASSED_SLOT_GAP between videos.
        """
        plan = self.profile.get("publish_plan")
        if not plan:
            return None
        if "times_utc" in plan:
            earliest = (now or datetime.now(timezone.utc)) + timedelta(minutes=10)
            slots, previous = [], None
            for t in sorted(datetime.fromisoformat(t) for t in plan["times_utc"]):
                if t < earliest:
                    t = earliest if previous is None else max(earliest, previous + PASSED_SLOT_GAP)
                elif previous is not None and t < previous + PASSED_SLOT_GAP:
                    t = previous + PASSED_SLOT_GAP
                slots.append(t)
                previous = t
            return slots[index] if index < len(slots) else None
        first = datetime.fromisoformat(plan["first_utc"])
        interval = timedelta(minutes=plan.get("interval_minutes") or 0)
        earliest = (now or datetime.now(timezone.utc)) + timedelta(minutes=10)

        if first < earliest:
            if not interval:
                return None
            missed_slots = -((first - earliest) // interval)
            first += interval * missed_slots
        return first + interval * index

    def run(self):
        manifest_path = self.paths["manifest_file"]
        with open(manifest_path, "r", encoding="utf-8") as f:
            trends = json.load(f)

        items_to_publish = [
            i for i in trends if i["status"] == "edited" and item_matches(i, self.active_key, self.profile)
        ]
        # A finished video whose file was deleted (e.g. an old day folder) can't be uploaded any more
        for item in [i for i in items_to_publish if not os.path.exists(i.get("edited_video_path") or "")]:
            print(f"[Agent 5: Publisher] Skipping {item['video_id']}: its edited video file no longer exists.")
            item["status"] = "expired"
            items_to_publish.remove(item)
        plan = self.profile.get("publish_plan") or {}
        if "times_utc" in plan and len(items_to_publish) > len(plan["times_utc"]):
            # This run has a fixed number of go-live slots; the oldest finished videos fill them first and
            # the rest wait for the next run
            waiting = len(items_to_publish) - len(plan["times_utc"])
            items_to_publish = items_to_publish[:len(plan["times_utc"])]
            print(f"[Agent 5: Publisher] {waiting} more finished video(s) will be uploaded in a later run.")
        if not items_to_publish:
            print("[Agent 5: Publisher] No edited videos pending upload.")
            return

        youtube = self.get_authenticated_service()

        for index, item in enumerate(items_to_publish):
            vid_id = item["video_id"]
            try:
                title, description = self.generate_seo_package(item)
            except Exception as e:
                # The video stays "edited" and is uploaded on the next run
                print(f"[Agent 5: Publisher] Could not write the title for {vid_id}, will retry next run: {e}")
                continue

            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "categoryId": self.profile["category_id"],
                    "tags": self.search_tags(item)
                },
                "status": {
                    "privacyStatus": self.profile.get("privacy_status", self.upload_rules["privacy_status"]),
                    "selfDeclaredMadeForKids": self.profile["made_for_kids"],
                    "containsSyntheticMedia": self.upload_rules["synthetic_media_disclosure"]
                }
            }

            publish_at = self.scheduled_publish_time(index)
            if publish_at:
                # YouTube only accepts publishAt on private videos; it flips them public at that time
                body["status"]["privacyStatus"] = "private"
                body["status"]["publishAt"] = publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                print(f"[Agent 5: Publisher] Scheduled to go public at {body['status']['publishAt']}")

            print(f"\n[Agent 5: Publisher] Uploading '{title}' to {self.profile['channel_name']}...")
            try:
                response = self.upload(youtube, item["edited_video_path"], body)
                item["uploaded_youtube_id"] = response["id"]
                item["uploaded_title"] = title
                item["uploaded_tags"] = body["snippet"]["tags"]
                item["publish_at"] = body["status"].get("publishAt")
                item["uploaded_at"] = datetime.now(timezone.utc).isoformat()
                item["status"] = "published"
                print(f"[Agent 5: Publisher] SUCCESS! Uploaded Video ID: {response['id']}")
            except Exception as e:
                print(f"[Agent 5: Publisher] Upload failed for {vid_id}: {e}")

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(trends, f, indent=4, ensure_ascii=False)
