import os
import json
import shutil
import subprocess
import tempfile
import textwrap
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
from moviepy.editor import (VideoFileClip, AudioFileClip, ImageClip, CompositeVideoClip, CompositeAudioClip,
                            concatenate_videoclips)

from agents import mascot
from agents.branding import channel_logo
from agents.stock_footage import REAL_SUFFIX
from brain import item_matches

SHORT_SIZE, LONG_SIZE = (1080, 1920), (1920, 1080)

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


def ffmpeg(*args):
    """Run FFmpeg (the system copy, or the one bundled with MoviePy) and raise if it fails."""
    exe = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run([exe, "-y", "-hide_banner", "-loglevel", "error", *map(str, args)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {result.stderr.strip()[-500:]}")


def watermark_image(text, fontsize, color, opacity=1.0):
    """The watermark as a transparent PIL image (white text with a dark outline)."""
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
    if opacity < 1:
        image.putalpha(image.getchannel("A").point(lambda a: int(a * opacity)))
    return image


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


def draw_button(draw, width, scale, center_y, text, fill, text_color, font):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    pad_x, pad_y = int(scale * 0.08), int(scale * 0.035)
    box_w, box_h = (right - left) + 2 * pad_x, (bottom - top) + 2 * pad_y
    x0, y0 = (width - box_w) // 2, center_y - box_h // 2
    draw.rounded_rectangle((x0, y0, x0 + box_w, y0 + box_h), radius=box_h // 2, fill=fill)
    draw.text((x0 + pad_x - left, y0 + pad_y - top), text, font=font, fill=text_color)


def render_end_card(background, logo_path, channel_name, like_text, subscribe_text, show_subscribe):
    """One end-card frame: blurred last frame, round logo, channel name, then the LIKE / SUBSCRIBE buttons."""
    w, h = background.size
    s = min(w, h)  # sizes follow the shorter side, so the card fits both vertical Shorts and landscape videos
    card = background.convert("RGB").filter(ImageFilter.GaussianBlur(radius=max(8, s // 40)))
    card = ImageEnhance.Brightness(card).enhance(0.4).convert("RGBA")
    draw = ImageDraw.Draw(card)

    diameter, center_y = int(s * 0.34), int(h * 0.30)
    if logo_path:
        logo = ImageOps.fit(Image.open(logo_path).convert("RGBA"), (diameter, diameter))
        mask = Image.new("L", (diameter, diameter), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, diameter, diameter), fill=255)
        ring = int(s * 0.012)
        draw.ellipse((w // 2 - diameter // 2 - ring, center_y - diameter // 2 - ring,
                      w // 2 + diameter // 2 + ring, center_y + diameter // 2 + ring), fill=(255, 255, 255))
        card.paste(logo, (w // 2 - diameter // 2, center_y - diameter // 2), mask)

    wide = w > h
    if logo_path:
        name_font = load_font(int(s * 0.065))
        y = center_y + diameter // 2 + int(s * 0.06)
        wrap_width = 30 if wide else 18
    else:
        # No logo: the channel name becomes the centrepiece, larger and where the logo would be
        name_font = load_font(int(s * 0.105))
        y = int(h * (0.22 if wide else 0.30))
        wrap_width = 24 if wide else 14
    for line in textwrap.wrap(channel_name, width=wrap_width):
        left, top, right, bottom = draw.textbbox((0, 0), line, font=name_font)
        draw.text(((w - (right - left)) // 2 - left, y - top), line, font=name_font, fill=(255, 255, 255))
        y += (bottom - top) + int(s * 0.02)

    button_font = load_font(int(s * 0.07))
    like_y, subscribe_y = (int(h * 0.58), int(h * 0.78)) if wide else (int(h * 0.62), int(h * 0.75))
    if show_subscribe:
        # LIKE stays on screen as "pressed", and SUBSCRIBE appears last
        draw_button(draw, w, s, like_y, like_text, (6, 95, 212), (255, 255, 255), button_font)
        draw_button(draw, w, s, subscribe_y, subscribe_text, (204, 0, 0), (255, 255, 255), button_font)
    else:
        draw_button(draw, w, s, like_y, like_text, (255, 255, 255), (20, 20, 20), button_font)
    return np.array(card.convert("RGB"))


class VideoEditorAgent:
    def __init__(self, config):
        self.config = config
        self.active_key = config["active_profile"]
        self.profile = config["profiles"][self.active_key]
        self.rules = config["editing_rules"]
        self.paths = config["storage_paths"]

    def end_card_frames(self, background):
        """The two end-card frames (LIKE, then LIKE + SUBSCRIBE) and their durations, or None when disabled."""
        settings = self.rules.get("end_card") or {}
        if not settings.get("enabled", True):
            return None
        seconds = float(settings.get("seconds", 3))
        branding_dir = self.paths.get("branding_dir", os.path.join(self.paths["base_dir"], "00_branding"))
        logo = channel_logo(self.profile, self.active_key, branding_dir) if settings.get("show_logo", False) else None
        frames = [
            render_end_card(background, logo, self.profile["channel_name"],
                            settings.get("like_text", "LIKE"), settings.get("subscribe_text", "SUBSCRIBE"), show)
            for show in (False, True)
        ]
        # LIKE alone first, then SUBSCRIBE joins it for the rest of the card
        return list(zip(frames, (seconds * 0.45, seconds * 0.55)))

    def end_card_clip(self, video):
        """The closing card as a MoviePy clip (used when re-using a trending video)."""
        card = self.end_card_frames(Image.fromarray(video.get_frame(max(0, video.duration - 0.1))))
        if card is None:
            return None
        return concatenate_videoclips([ImageClip(frame).set_duration(seconds) for frame, seconds in card])

    @staticmethod
    def custom_hero_clip(kind, width, height, work):
        """The owner's own intro / outro video (assets/wondersprout/intro.mp4, outro.mp4; intro_short.mp4 and
        outro_short.mp4 for vertical Shorts if given), fitted to this video's shape without stretching, or None."""
        names = [f"{kind}_short.mp4", f"{kind}.mp4"] if height > width else [f"{kind}.mp4"]
        source = next((os.path.join(mascot.ART_DIR, n) for n in names
                       if os.path.exists(os.path.join(mascot.ART_DIR, n))), None)
        if not source:
            return None
        target = os.path.join(work, f"custom_{kind}.mp4")
        exe = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
        has_audio = "Audio:" in subprocess.run([exe, "-i", source], capture_output=True, text=True).stderr
        # Whole clip on a blurred copy of itself when its shape differs from the video's (e.g. landscape in a Short)
        fit = (f"[0:v]split[a][b];[a]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
               f"boxblur=20:2[bg];[b]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
               f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=30,format=yuv420p,setsar=1[v]")
        encode = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-ar", "44100", "-ac", "2"]
        if has_audio:
            ffmpeg("-i", source, "-filter_complex", fit, "-map", "[v]", "-map", "0:a:0", *encode, target)
        else:
            # A clip without sound gets silence, so it still joins the rest of the video
            ffmpeg("-i", source, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-filter_complex", fit,
                   "-map", "[v]", "-map", "1:a", "-shortest", *encode, target)
        return target

    def mascot_settings(self, item):
        """{} for channels without a host; else which hero parts this video gets (intro, host, outro)."""
        if self.profile.get("mascot") != "wondersprout":
            return {}
        settings = dict(self.rules.get("mascot") or {})
        long_video = item.get("format") == "long"
        # Long videos open with her intro; Shorts by default don't (viewers swipe away from slow starts), and
        # she swoops into her corner over the opening instead
        settings["intro"] = settings.get("intro_long", True) if long_video else settings.get("intro_shorts", False)
        return settings

    def render_stock(self, item, output_video):
        """Build a stock-footage video entirely with FFmpeg: much faster than MoviePy for long videos.

        Each clip is cut to its scene's narration time, scaled and cropped to the frame, joined, then the
        watermark, narration and end card are added in a single final encode.
        """
        width, height = LONG_SIZE if item.get("format") == "long" else SHORT_SIZE
        voice = AudioFileClip(item["voiceover_path"])
        duration = voice.duration
        voice.close()
        clips = item["footage_paths"]
        scenes = item.get("scenes") or []
        if len(scenes) == len(clips):
            # Each clip plays exactly while its scene is narrated; the last one runs to the end of the narration
            lengths = [max(0.5, s["end"] - s["start"]) for s in scenes]
            lengths[-1] = max(0.5, duration - scenes[-1]["start"])
        else:
            lengths = [duration / len(clips)] * len(clips)
        fit = (f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
               f"setsar=1,fps=30,format=yuv420p")
        # Real footage (people at a table, a podium, a ship) is shown whole in a vertical Short, centred on a
        # blurred copy of itself, instead of cropping away everything but the middle
        whole = (f"split[a][b];[a]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                 f"boxblur=20:2[bg];[b]scale={width}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,"
                 f"setsar=1,fps=30,format=yuv420p")

        work = tempfile.mkdtemp(prefix="render_", dir=self.paths["processed_videos"])
        try:
            parts = []
            for index, clip in enumerate(clips):
                part = os.path.join(work, f"part{index:03d}.mp4")
                # -stream_loop repeats clips shorter than their share
                shape = whole if clip.endswith(REAL_SUFFIX) and height > width else fit
                ffmpeg("-stream_loop", "-1", "-i", clip, "-t", f"{lengths[index]:.3f}", "-an", "-vf", shape,
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", part)
                parts.append(part)
            with open(os.path.join(work, "parts.txt"), "w", encoding="utf-8") as f:
                f.writelines(f"file '{os.path.basename(p)}'\n" for p in parts)
            montage = os.path.join(work, "montage.mp4")
            ffmpeg("-f", "concat", "-safe", "0", "-i", os.path.join(work, "parts.txt"), "-c", "copy", montage)

            mark = os.path.join(work, "watermark.png")
            watermark_image(self.profile["watermark_text"], self.rules["watermark_fontsize"],
                            self.rules["watermark_color"], self.rules["watermark_opacity"]).save(mark)

            last_frame = os.path.join(work, "last.png")
            ffmpeg("-sseof", "-0.2", "-i", montage, "-frames:v", "1", last_frame)
            card = self.end_card_frames(Image.open(last_frame).convert("RGB"))

            inputs = ["-i", montage, "-i", item["voiceover_path"], "-i", mark]
            hero = self.mascot_settings(item)
            video = "[0:v]"
            if hero.get("host", True) and hero:
                # WonderSprout hosts from a corner, her face following the script and her mouth the narration
                sentences = item.get("sentence_timings") or [(s["start"], s["text"]) for s in scenes]
                host, hx, hy = mascot.render_host(item["voiceover_path"], sentences, duration, (width, height),
                                                  os.path.join(work, "host.mov"), entrance=not hero.get("intro"))
                inputs += ["-i", host]
                host_index = inputs.count("-i") - 1
                video = f"[0:v][{host_index}:v]overlay={hx}:{hy}:eof_action=pass[hosted];[hosted]"
            graph = (f"{video}[2:v]overlay=W-w-{WATERMARK_MARGIN}:H-h-{WATERMARK_MARGIN},format=yuv420p,setsar=1[main];"
                     "[1:a]aresample=44100,aformat=channel_layouts=stereo[voice];")
            segments = []

            def add_clip(path, name):
                """Add a ready-made clip with sound (hero intro or outro) as the next segment."""
                nonlocal graph
                inputs.extend(["-i", path])
                index = inputs.count("-i") - 1
                graph += (f"[{index}:v]scale={width}:{height},fps=30,format=yuv420p,setsar=1[{name}v];"
                          f"[{index}:a]aresample=44100,aformat=channel_layouts=stereo[{name}a];")
                segments.append((f"[{name}v]", f"[{name}a]"))

            # She speaks her intro and goodbye in the channel's own narrator voice
            voice, rate = self.profile["voice"], self.profile.get("voice_rate", "-5%")
            # A ready-made intro / outro video in assets/wondersprout is used when present; otherwise one is made
            if hero.get("intro"):
                add_clip(self.custom_hero_clip("intro", width, height, work) or
                         mascot.render_intro((width, height), os.path.join(work, "intro.mp4"), load_font, voice, rate,
                                             hero.get("intro_line", mascot.INTRO_LINE)), "intro")
            segments.append(("[main]", "[voice]"))
            if hero.get("outro", True) and hero:
                add_clip(self.custom_hero_clip("outro", width, height, work) or
                         mascot.render_outro((width, height), os.path.join(work, "outro.mp4"), load_font, voice, rate,
                                             hero.get("outro_line", mascot.OUTRO_LINE)), "outro")
            if card:
                for index, (frame, seconds) in enumerate(card):
                    path = os.path.join(work, f"card{index}.png")
                    Image.fromarray(frame).save(path)
                    inputs += ["-loop", "1", "-t", f"{seconds:.3f}", "-i", path,
                               "-f", "lavfi", "-t", f"{seconds:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
                    image_index = inputs.count("-i") - 2
                    graph += f"[{image_index}:v]fps=30,format=yuv420p,setsar=1[c{index}];"
                    segments.append((f"[c{index}]", f"[{image_index + 1}:a]"))
            graph += "".join(v + a for v, a in segments) + f"concat=n={len(segments)}:v=1:a=1[v][a]"
            ffmpeg(*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "160k",
                   "-movflags", "+faststart", output_video)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def run(self):
        manifest_path = self.paths["manifest_file"]
        with open(manifest_path, "r", encoding="utf-8") as f:
            trends = json.load(f)

        for item in trends:
            # Stock videos are ready once their scene clips are in; re-used trending videos once narrated
            ready = item["status"] == "ready_to_edit" or (item["status"] == "voiced" and not item.get("scenes"))
            if ready and item_matches(item, self.active_key, self.profile):
                vid_id = item["video_id"]
                raw_video = item["raw_video_path"]
                vo_audio = item["voiceover_path"]
                output_video = os.path.join(
                    self.paths["processed_videos"], f"final_{self.active_key}_{vid_id}.mp4"
                )

                print(f"\n[Agent 4: Editor] Editing & Watermarking {vid_id}...")
                if item.get("footage_paths"):
                    try:
                        self.render_stock(item, output_video)
                        item["edited_video_path"] = output_video
                        item["status"] = "edited"
                        print(f"[Agent 4: Editor] Saved final video to {output_video}")
                    except Exception as e:
                        print(f"[Agent 4: Editor] Error editing {vid_id}: {e}")
                    continue
                try:
                    # Re-used trending video (footage.source "youtube"): edited with MoviePy
                    voice_clip = AudioFileClip(vo_audio)
                    video_clip = VideoFileClip(raw_video)
                    sources = [video_clip]
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
                        # Long videos use a faster encoder setting so a 6-minute render stays quick
                        preset="veryfast" if item.get("format") == "long" else "medium",
                        threads=os.cpu_count() or 2,
                        logger=None
                    )

                    for source in sources:
                        source.close()
                    voice_clip.close()
                    final_clip.close()

                    item["edited_video_path"] = output_video
                    item["status"] = "edited"
                    print(f"[Agent 4: Editor] Saved final video to {output_video}")
                except Exception as e:
                    print(f"[Agent 4: Editor] Error editing {vid_id}: {e}")

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(trends, f, indent=4, ensure_ascii=False)
