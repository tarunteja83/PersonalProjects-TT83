import os
import json
from datetime import datetime, timedelta, timezone
from google import genai
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from agents.ai import generate_text
from brain import YOUTUBE_SCOPES as SCOPES, channel_matches

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

    def generate_seo_package(self, item):
        clean_creator_name = item["original_creator"].replace("@", "").strip()

        prompt = (
            f"Follow these system instructions: {self.instructions}\n"
            f"Profile: {self.active_key}\n"
            f"Original Title: {item['original_title']}\n"
            f"Our Voiceover Script: {item['generated_script']}\n"
            f"Language for title, description and hashtags: {self.profile.get('language', 'English')}\n\n"
            "Generate a viral YouTube Short Title (maximum 60 characters, no quotes) on Line 1.\n"
            "On the following lines, write a 2-sentence SEO description followed by 5 relevant hashtags."
        )
        resp = generate_text(self.ai_client, prompt, self.config.get("ai_models")).split("\n")

        new_title = resp[0].replace('"', '').strip()[:65]
        seo_body = "\n".join(resp[1:]).strip()

        if item.get("footage_paths"):
            # Pixabay and Pexels ask that you show where the footage came from
            site = {"pexels": ("Pexels", "https://www.pexels.com")}.get(item.get("footage_provider"),
                                                                         ("Pixabay", "https://pixabay.com"))
            credit_block = (
                f"\n\n---\n"
                f"Stock footage: {site[0]} ({site[1]}), videos by {', '.join(item.get('footage_credits') or [site[0]])}\n"
                f"Commentary produced by {self.profile['channel_name']}."
            )
        else:
            credit_block = (
                f"\n\n---\n"
                f"Visual Reference & Credit: {clean_creator_name}\n"
                f"Commentary and analysis produced by {self.profile['channel_name']} under Fair Use guidelines."
            )

        return new_title, f"{seo_body}{credit_block}"

    def scheduled_publish_time(self, index, now=None):
        """Go-live time for the index-th video of this run, or None to publish immediately.

        execution.py sets publish_plan = {"first_utc": ISO time, "interval_minutes": gap}. Slots repeat every
        interval from first_utc; if preparing the videos took past some slots, the next future slot is used.
        """
        plan = self.profile.get("publish_plan")
        if not plan:
            return None
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
            i for i in trends if i["status"] == "edited" and i["profile"] == self.active_key
        ]
        # A finished video whose file was deleted (e.g. an old day folder) can't be uploaded any more
        for item in [i for i in items_to_publish if not os.path.exists(i.get("edited_video_path") or "")]:
            print(f"[Agent 5: Publisher] Skipping {item['video_id']}: its edited video file no longer exists.")
            item["status"] = "expired"
            items_to_publish.remove(item)
        if not items_to_publish:
            print("[Agent 5: Publisher] No edited videos pending upload.")
            return

        youtube = self.get_authenticated_service()

        for index, item in enumerate(items_to_publish):
            vid_id = item["video_id"]
            title, description = self.generate_seo_package(item)

            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "categoryId": self.profile["category_id"],
                    "tags": ["shorts", self.active_key, self.profile["channel_name"].replace(" ", "")]
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
                media = MediaFileUpload(item["edited_video_path"], chunksize=-1, resumable=True)
                request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
                response = request.execute()

                item["uploaded_youtube_id"] = response["id"]
                item["status"] = "published"
                print(f"[Agent 5: Publisher] SUCCESS! Uploaded Video ID: {response['id']}")
            except Exception as e:
                print(f"[Agent 5: Publisher] Upload failed for {vid_id}: {e}")

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(trends, f, indent=4, ensure_ascii=False)
