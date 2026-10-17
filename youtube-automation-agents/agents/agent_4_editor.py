import os
import json
import textwrap
import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
from moviepy.editor import (VideoFileClip, AudioFileClip, ImageClip, CompositeVideoClip, CompositeAudioClip,
                            concatenate_videoclips)

from agents.branding import channel_logo

# MoviePy 1.0.3's resize uses Image.ANTIALIAS, which Pillow 10 removed (LANCZOS is the same filter)
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.LANCZOS

# Bold fonts tried in order: Windows, macOS, then the DejaVu font present on GitHub's Ubuntu runners
FONT_CANDIDATES = [
    "arialbd.ttf",
    "Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
]
WATERMARK_MARGIN = 24


def load_font(size):
    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def render_watermark(text, fontsize, color):
    # Drawn with Pillow instead of MoviePy's TextClip, so ImageMagick is not needed
    font = load_font(fontsize)
    stroke = max(1, fontsize // 12)
    left, top, right, bottom = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox(
        (0, 0), text, font=font, stroke_width=stroke
    )
    image = Image.new("RGBA", (right - left + 4, bottom - top + 4), (0, 0, 0, 0))
    ImageDraw.Draw(image).text(
        (2 - left, 2 - top), text, font=font, fill=ImageColor.getrgb(color),
        stroke_width=stroke, stroke_fill=(0, 0, 0)
    )
    return ImageClip(np.array(image))


def draw_button(draw, width, center_y, text, fill, text_color, font):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    pad_x, pad_y = int(width * 0.08), int(width * 0.035)
    box_w, box_h = (right - left) + 2 * pad_x, (bottom - top) + 2 * pad_y
    x0, y0 = (width - box_w) // 2, center_y - box_h // 2
    draw.rounded_rectangle((x0, y0, x0 + box_w, y0 + box_h), radius=box_h // 2, fill=fill)
    draw.text((x0 + pad_x - left, y0 + pad_y - top), text, font=font, fill=text_color)


def render_end_card(background, logo_path, channel_name, like_text, subscribe_text, show_subscribe):
    """One end-card frame: blurred last frame, round logo, channel name, then the LIKE / SUBSCRIBE buttons."""
    w, h = background.size
    card = background.convert("RGB").filter(ImageFilter.GaussianBlur(radius=max(8, w // 40)))
    card = ImageEnhance.Brightness(card).enhance(0.4).convert("RGBA")
    draw = ImageDraw.Draw(card)

    diameter, center_y = int(w * 0.34), int(h * 0.30)
    if logo_path:
        logo = ImageOps.fit(Image.open(logo_path).convert("RGBA"), (diameter, diameter))
        mask = Image.new("L", (diameter, diameter), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, diameter, diameter), fill=255)
        ring = int(w * 0.012)
        draw.ellipse((w // 2 - diameter // 2 - ring, center_y - diameter // 2 - ring,
                      w // 2 + diameter // 2 + ring, center_y + diameter // 2 + ring), fill=(255, 255, 255))
        card.paste(logo, (w // 2 - diameter // 2, center_y - diameter // 2), mask)

    if logo_path:
        name_font = load_font(int(w * 0.065))
        y = center_y + diameter // 2 + int(w * 0.06)
        wrap_width = 18
    else:
        # No logo: the channel name becomes the centrepiece, larger and where the logo would be
        name_font = load_font(int(w * 0.105))
        y = int(h * 0.30)
        wrap_width = 14
    for line in textwrap.wrap(channel_name, width=wrap_width):
        left, top, right, bottom = draw.textbbox((0, 0), line, font=name_font)
        draw.text(((w - (right - left)) // 2 - left, y - top), line, font=name_font, fill=(255, 255, 255))
        y += (bottom - top) + int(w * 0.02)

    button_font = load_font(int(w * 0.07))
    if show_subscribe:
        # LIKE stays on screen as "pressed", and SUBSCRIBE appears last
        draw_button(draw, w, int(h * 0.62), like_text, (6, 95, 212), (255, 255, 255), button_font)
        draw_button(draw, w, int(h * 0.75), subscribe_text, (204, 0, 0), (255, 255, 255), button_font)
    else:
        draw_button(draw, w, int(h * 0.62), like_text, (255, 255, 255), (20, 20, 20), button_font)
    return np.array(card.convert("RGB"))


class VideoEditorAgent:
    def __init__(self, config):
        self.config = config
        self.active_key = config["active_profile"]
        self.profile = config["profiles"][self.active_key]
        self.rules = config["editing_rules"]
        self.paths = config["storage_paths"]

    def end_card_clip(self, video):
        """The closing "LIKE, then SUBSCRIBE" card, or None when end_card.enabled is false in BRAIN.md."""
        settings = self.rules.get("end_card") or {}
        if not settings.get("enabled", True):
            return None
        seconds = float(settings.get("seconds", 3))
        background = Image.fromarray(video.get_frame(max(0, video.duration - 0.1)))
        branding_dir = self.paths.get("branding_dir", os.path.join(self.paths["base_dir"], "00_branding"))
        logo = channel_logo(self.profile, self.active_key, branding_dir) if settings.get("show_logo", False) else None
        frames = [
            render_end_card(background, logo, self.profile["channel_name"],
                            settings.get("like_text", "LIKE"), settings.get("subscribe_text", "SUBSCRIBE"), show)
            for show in (False, True)
        ]
        # LIKE alone first, then SUBSCRIBE joins it for the rest of the card
        return concatenate_videoclips([
            ImageClip(frames[0]).set_duration(seconds * 0.45),
            ImageClip(frames[1]).set_duration(seconds * 0.55),
        ])

    def run(self):
        manifest_path = self.paths["manifest_file"]
        with open(manifest_path, "r", encoding="utf-8") as f:
            trends = json.load(f)

        for item in trends:
            if item["status"] == "voiced" and item["profile"] == self.active_key:
                vid_id = item["video_id"]
                raw_video = item["raw_video_path"]
                vo_audio = item["voiceover_path"]
                output_video = os.path.join(
                    self.paths["processed_videos"], f"final_{self.active_key}_{vid_id}.mp4"
                )

                print(f"\n[Agent 4: Editor] Editing & Watermarking {vid_id}...")
                try:
                    video_clip = VideoFileClip(raw_video)
                    voice_clip = AudioFileClip(vo_audio)

                    # Loop or trim the picture only; looping a clip's audio in MoviePy 1.0.3 reads past its end
                    original_audio = video_clip.audio
                    picture = video_clip.without_audio()
                    if picture.duration < voice_clip.duration:
                        picture = picture.loop(duration=voice_clip.duration)
                    else:
                        picture = picture.subclip(0, voice_clip.duration)

                    # Zoom in, then crop back to the original frame size around the centre
                    width, height = picture.size
                    transformed_video = picture.resize(self.rules["zoom_factor"]).crop(
                        x_center=width * self.rules["zoom_factor"] / 2,
                        y_center=height * self.rules["zoom_factor"] / 2,
                        width=width - width % 2,
                        height=height - height % 2,
                    )

                    # original_audio_volume 0 means the source's sound is dropped and only the AI voice is heard
                    if original_audio is not None and self.rules.get("original_audio_volume", 0) > 0:
                        bg_end = max(0, min(original_audio.duration - 0.1, voice_clip.duration))
                        bg_audio = original_audio.subclip(0, bg_end).volumex(self.rules["original_audio_volume"])
                        mixed_audio = CompositeAudioClip([bg_audio, voice_clip])
                    else:
                        mixed_audio = voice_clip

                    transformed_video = transformed_video.set_audio(mixed_audio)

                    watermark = render_watermark(
                        self.profile["watermark_text"],
                        self.rules["watermark_fontsize"],
                        self.rules["watermark_color"],
                    )
                    watermark = (
                        watermark
                        .set_opacity(self.rules["watermark_opacity"])
                        .set_position((
                            transformed_video.w - watermark.w - WATERMARK_MARGIN,
                            transformed_video.h - watermark.h - WATERMARK_MARGIN,
                        ))
                        .set_duration(transformed_video.duration)
                    )

                    final_clip = CompositeVideoClip([transformed_video, watermark])
                    end_card = self.end_card_clip(transformed_video)
                    if end_card is not None:
                        final_clip = concatenate_videoclips([final_clip, end_card])
                    final_clip.write_videofile(
                        output_video,
                        codec="libx264",
                        audio_codec="aac",
                        fps=30,
                        logger=None
                    )

                    video_clip.close()
                    voice_clip.close()
                    final_clip.close()

                    item["edited_video_path"] = output_video
                    item["status"] = "edited"
                    print(f"[Agent 4: Editor] Saved final video to {output_video}")
                except Exception as e:
                    print(f"[Agent 4: Editor] Error editing {vid_id}: {e}")

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(trends, f, indent=4, ensure_ascii=False)
