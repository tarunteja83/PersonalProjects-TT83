"""WonderSprout, the kids channel's superhero host: drawn in code, so every frame is consistent and needs no licence.

She appears in three ways (agents/agent_4_editor.py):
- intro: swoops in and waves with her name at the start of every video;
- host: in a corner of the video, her expression following the script sentence by sentence (talking, thinking,
  asking, amazed, happy, waving), her mouth moving with the narration;
- outro: waves goodbye and flies off just before the LIKE and SUBSCRIBE card.

To use professionally drawn art instead, see ART_DIR: a PNG per expression there replaces the drawn version.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter

# Base drawing size of the half-body character; drawn SUPER times larger and scaled down for smooth edges
BASE_W, BASE_H = 600, 720
SUPER = 3

SKIN = "#C68A5E"
SKIN_SHADE = "#A86F48"
HAIR = "#3A2215"
SUIT = "#2FB45A"
SUIT_DARK = "#23914A"
CAPE = "#139C93"
CAPE_DARK = "#0E7A73"
GOLD = "#FFCB2F"
LEAF = "#7BD65A"
LINE = "#2A1A10"
WHITE = "#FFFFFF"
IRIS = "#5B3A1E"
BLUSH = (255, 120, 120, 90)
MOUTH = "#7A1F2B"
TONGUE = "#E8697A"

EXPRESSIONS = ["happy", "talk", "think", "ask", "wow", "wave"]
# Character art, if added (transparent PNGs, see `--hero-art` in execution.py and assets/wondersprout/PROMPTS.md):
# <expression>.png, optionally <expression>_open.png with the mouth open, and fly.png for flying. It is kept in the
# project so GitHub runs have it too. Once any art is there, it replaces the drawn character everywhere.
ART_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "wondersprout")
ART_NAMES = EXPRESSIONS + ["fly"]
# A missing pose borrows the closest one that exists, so the art style never mixes with the drawing
ART_FALLBACK = {"talk": ["happy"], "think": ["happy"], "ask": ["talk", "happy"], "wow": ["happy"],
                "wave": ["happy"], "fly": ["wave", "happy"], "happy": ["talk", "wave"]}


class Pen:
    """Draws in base coordinates on the supersampled canvas, with a cartoon outline."""

    def __init__(self, image):
        self.draw = ImageDraw.Draw(image, "RGBA")

    @staticmethod
    def box(x0, y0, x1, y1):
        return [x0 * SUPER, y0 * SUPER, x1 * SUPER, y1 * SUPER]

    def ellipse(self, x0, y0, x1, y1, fill, outline=LINE, width=4):
        self.draw.ellipse(self.box(x0, y0, x1, y1), fill=fill, outline=outline, width=width * SUPER if outline else 0)

    def rounded(self, x0, y0, x1, y1, radius, fill, outline=LINE, width=4):
        self.draw.rounded_rectangle(self.box(x0, y0, x1, y1), radius * SUPER, fill=fill, outline=outline,
                                    width=width * SUPER if outline else 0)

    def polygon(self, points, fill, outline=LINE, width=4):
        pts = [(x * SUPER, y * SUPER) for x, y in points]
        self.draw.polygon(pts, fill=fill)
        if outline:
            self.draw.line(pts + [pts[0]], fill=outline, width=width * SUPER, joint="curve")

    def arc(self, x0, y0, x1, y1, start, end, width=4, fill=LINE):
        self.draw.arc(self.box(x0, y0, x1, y1), start, end, fill=fill, width=width * SUPER)

    def line(self, points, width=4, fill=LINE):
        self.draw.line([(x * SUPER, y * SUPER) for x, y in points], fill=fill, width=width * SUPER, joint="curve")


def leaf(pen, cx, cy, length, angle, fill=LEAF):
    """A pointed leaf from (cx, cy) in direction angle (degrees)."""
    a = math.radians(angle)
    tip = (cx + length * math.cos(a), cy - length * math.sin(a))
    side = math.radians(angle + 90)
    mid = (cx + length * 0.5 * math.cos(a), cy - length * 0.5 * math.sin(a))
    w = length * 0.32
    p1 = (mid[0] + w * math.cos(side), mid[1] - w * math.sin(side))
    p2 = (mid[0] - w * math.cos(side), mid[1] + w * math.sin(side))
    pen.polygon([(cx, cy), p1, tip, p2], fill)
    pen.line([(cx, cy), tip], width=2, fill="#4E9E3A")


def glove(pen, x, y, r=30):
    pen.ellipse(x - r, y - r, x + r, y + r, GOLD)


def limb(pen, points, width=46, fill=SUIT):
    """A bendy cartoon arm through the points: an outline stroke, then the colour, with round ends."""
    for colour, w in ((LINE, width + 8), (fill, width)):
        pen.line(points, width=w, fill=colour)
        for x, y in (points[0], points[-1]):
            r = w / 2
            pen.ellipse(x - r, y - r, x + r, y + r, colour, outline=None)


def open_hand(pen, x, y, up=True):
    """A gloved open hand: palm plus four fingers pointing up (wave) or a cupped palm facing up (ask)."""
    if up:
        glove(pen, x, y, 36)
        for dx, h in ((-24, 46), (-8, 54), (8, 54), (24, 46)):
            pen.rounded(x + dx - 9, y - h - 10, x + dx + 9, y - 14, 9, GOLD)
        pen.rounded(x + 26, y - 6, x + 58, y + 12, 9, GOLD)                            # thumb
    else:
        pen.draw.chord(pen.box(x - 62, y - 46, x + 62, y + 44), 0, 180, fill=GOLD, outline=LINE, width=4 * SUPER)
        pen.rounded(x - 70, y - 16, x - 30, y + 6, 10, GOLD)                           # thumb


def draw_arms(pen, expression):
    """Arms in front of the body for the expressions that use them."""
    if expression == "think":
        limb(pen, [(440, 560), (450, 660), (360, 520)])                                 # elbow down, hand to the chin
        glove(pen, 352, 452, 34)
    elif expression == "ask":
        limb(pen, [(165, 560), (140, 660), (95, 585)])                                   # palm up, inviting an answer
        open_hand(pen, 92, 560, up=False)
    elif expression == "wave":
        limb(pen, [(440, 540), (530, 560), (540, 420)])                                 # elbow out, hand up
        open_hand(pen, 540, 390, up=True)


def draw_face(pen, expression, mouth_open):
    cx = 300
    # Eyes
    if expression == "happy" and not mouth_open:
        for ex in (255, 345):
            pen.arc(ex - 30, 285, ex + 30, 335, 200, 340, width=6)                    # smiling closed eyes
    else:
        big = expression == "wow"
        for ex in (255, 345):
            ry = 34 if big else 29
            pen.ellipse(ex - 26, 300 - ry, ex + 26, 300 + ry, WHITE, width=3)
            look_x, look_y = (8, -12) if expression == "think" else (0, 2)
            ir = 17 if big else 16
            pen.ellipse(ex - ir + look_x, 302 - ir + look_y, ex + ir + look_x, 302 + ir + look_y, IRIS, outline=None)
            pen.ellipse(ex - 8 + look_x, 302 - 8 + look_y, ex + 8 + look_x, 302 + 8 + look_y, "#1A0F08", outline=None)
            pen.ellipse(ex - 3 + look_x, 293 + look_y, ex + 5 + look_x, 301 + look_y, WHITE, outline=None)
            side = -1 if ex < cx else 1                                               # one lash at the outer corner
            pen.line([(ex + side * 24, 300 - ry + 10), (ex + side * 36, 300 - ry + 2)], width=4)
    # Eyebrows
    lift = {"wow": -14, "ask": -10, "think": -4}.get(expression, 0)
    pen.arc(222, 236 + lift, 288, 270 + lift, 205, 335, width=5)
    if expression == "think":
        pen.line([(318, 250), (372, 256)], width=5)                                   # one brow flat: pondering
    else:
        pen.arc(312, 236 + lift, 378, 270 + lift, 205, 335, width=5)
    # Nose and cheeks
    pen.arc(288, 318, 312, 345, 20, 160, width=3, fill=SKIN_SHADE)
    pen.draw.ellipse(pen.box(205, 335, 245, 360), fill=BLUSH)
    pen.draw.ellipse(pen.box(355, 335, 395, 360), fill=BLUSH)
    # Mouth
    if mouth_open or expression == "wow":
        if expression in ("wow", "ask"):
            pen.ellipse(cx - 20, 362, cx + 20, 400, MOUTH, width=3)                   # round "Oh!"
        else:
            pen.draw.chord(pen.box(cx - 40, 345, cx + 40, 410), 0, 180, fill=MOUTH, outline=LINE, width=3 * SUPER)
            pen.draw.chord(pen.box(cx - 22, 380, cx + 22, 406), 180, 360, fill=TONGUE)
    elif expression == "think":
        pen.line([(cx - 14, 378), (cx + 16, 372)], width=5)                           # small thoughtful mouth
    else:
        pen.arc(cx - 40, 335, cx + 40, 392, 20, 160, width=6)                         # smile


def draw_hero(expression="happy", mouth_open=False):
    """The half-body character as an RGBA image of BASE_W x BASE_H."""
    image = Image.new("RGBA", (BASE_W * SUPER, BASE_H * SUPER), (0, 0, 0, 0))
    pen = Pen(image)
    # Cape behind the shoulders
    pen.polygon([(150, 470), (450, 470), (540, 720), (60, 720)], CAPE)
    pen.polygon([(160, 480), (205, 480), (150, 720), (90, 720)], CAPE_DARK, outline=None)
    # Long hair falling behind the shoulders
    pen.rounded(140, 140, 460, 600, 140, HAIR)
    # Neck and body
    pen.rounded(270, 400, 330, 480, 20, SKIN)
    pen.rounded(150, 460, 450, 760, 90, SUIT)
    pen.polygon([(150, 470), (205, 450), (230, 500)], CAPE)                            # cape collar points
    pen.polygon([(450, 470), (395, 450), (370, 500)], CAPE)
    pen.polygon([(240, 455), (360, 455), (300, 505)], SKIN)                            # V neckline
    pen.ellipse(250, 520, 350, 620, GOLD)                                              # emblem
    leaf(pen, 300, 590, 50, 70)
    leaf(pen, 300, 590, 42, 125)
    pen.rounded(150, 690, 450, 720, 8, GOLD, outline=None)                             # belt
    # Head, hair, tiara and sprout
    pen.ellipse(165, 140, 435, 430, SKIN)
    pen.draw.chord(pen.box(158, 122, 442, 360), 180, 360, fill=HAIR, outline=LINE, width=4 * SUPER)
    pen.polygon([(165, 260), (200, 190), (250, 250)], HAIR, outline=None)              # side-swept fringe
    pen.polygon([(250, 190), (330, 175), (300, 245)], HAIR, outline=None)
    pen.polygon([(435, 260), (400, 190), (360, 240)], HAIR, outline=None)
    pen.rounded(195, 178, 405, 202, 10, GOLD)                                          # tiara
    pen.ellipse(284, 168, 316, 200, LEAF, width=3)
    pen.line([(300, 124), (300, 84)], width=6, fill="#4E9E3A")                         # sprout on her head
    leaf(pen, 300, 92, 58, 35)
    leaf(pen, 300, 92, 52, 150)
    draw_face(pen, expression, mouth_open)
    draw_arms(pen, expression)
    return image.resize((BASE_W, BASE_H), Image.LANCZOS)


def has_art(name=None):
    """True if character art exists: for one pose, or (no name) for any pose."""
    if name:
        return os.path.exists(os.path.join(ART_DIR, f"{name}.png"))
    return any(has_art(n) for n in ART_NAMES)


def art_image(expression, mouth_open):
    """The art for a pose: its mouth-open version if asked for and present, else the pose, else the closest pose."""
    # Its own picture, then the closest poses, then any pose of her at all
    for name in dict.fromkeys([expression] + ART_FALLBACK.get(expression, []) + ART_NAMES):
        if mouth_open and has_art(f"{name}_open"):
            return Image.open(os.path.join(ART_DIR, f"{name}_open.png")).convert("RGBA")
        if has_art(name):
            image = Image.open(os.path.join(ART_DIR, f"{name}.png")).convert("RGBA")
            if mouth_open:
                # No mouth-open picture: a tiny squash shows she is talking
                image = image.resize((image.width, round(image.height * 0.985)), Image.LANCZOS)
            return image
    return None


def hero_image(expression="happy", mouth_open=False, height=None):
    """The character for an expression: the art in ART_DIR if there is any, else the drawing; scaled to height."""
    image = art_image(expression, mouth_open) if has_art() else None
    if image is None:
        image = draw_hero(expression if expression in EXPRESSIONS else "happy", mouth_open)
    if height and image.height != height:
        image = image.resize((round(image.width * height / image.height), height), Image.LANCZOS)
    return image


def clean_cutout(image):
    """Keep only the character in a transparent picture.

    - Marks that aren't part of her (an image AI's logo in a corner, specks) are dropped: only her shape, and
      pieces right next to it such as loose strands of hair, are kept.
    - Leftover backdrop-coloured pixels along her outline (a magenta fringe round the hair) are removed: the
      backdrop colour is read from the transparent pixels, which still hold it.
    - The edge is softened slightly, so she sits naturally over video.
    """
    import numpy as np
    alpha = np.asarray(image.getchannel("A"))
    solid = alpha > 20
    if not solid.any():
        return image
    # Her shape: flood-fill from the visible pixel nearest the centre of all visible pixels
    ys, xs = np.nonzero(solid)
    nearest = np.argmin((ys - ys.mean()) ** 2 + (xs - xs.mean()) ** 2)
    # .copy(): an image made straight from a NumPy array can be read-only, and the fill would silently do nothing
    marked = Image.fromarray(np.where(solid, 255, 0).astype(np.uint8), "L").copy()
    ImageDraw.floodfill(marked, (int(xs[nearest]), int(ys[nearest])), 128)
    body = np.asarray(marked) == 128
    # Keep anything within about 3% of the picture's width of her (hair strands); drop what's further away
    small = Image.fromarray(np.where(body, 255, 0).astype(np.uint8), "L").resize(
        (max(1, image.width // 4), max(1, image.height // 4)), Image.BOX)
    reach = max(3, (image.width // 4) // 33) | 1
    near = np.asarray(small.filter(ImageFilter.MaxFilter(reach)).resize(image.size, Image.NEAREST)) > 0
    keep = body | (solid & near)
    # The backdrop colour, from the pixels that are transparent
    rgb = np.asarray(image.convert("RGB")).astype(np.int32)
    hidden = alpha < 10
    if hidden.any():
        backdrop = np.median(rgb[hidden], axis=0)
        inner = np.asarray(Image.fromarray(np.where(keep, 255, 0).astype(np.uint8), "L")
                           .filter(ImageFilter.MinFilter(7))) > 0
        edge = keep & ~inner
        tinted = np.sqrt(((rgb - backdrop) ** 2).sum(axis=2)) < 140
        keep &= ~(edge & tinted)
        # Hair strands further in can still carry a tint of the backdrop: take that colour out of the outer band
        # (about 12 pixels) instead of deleting them. Only pixels whose red AND blue both exceed green change, so
        # skin and her green and blue costume keep their colours.
        small_keep = Image.fromarray(np.where(keep, 255, 0).astype(np.uint8), "L").resize(
            (max(1, image.width // 4), max(1, image.height // 4)), Image.BOX)
        deep = np.asarray(small_keep.filter(ImageFilter.MinFilter(7)).resize(image.size, Image.NEAREST)) > 250
        band = keep & ~deep
        spill = np.minimum(rgb[..., 0], rgb[..., 2]) - rgb[..., 1]
        fix = band & (spill > 0) if backdrop[0] > backdrop[1] and backdrop[2] > backdrop[1] else np.zeros_like(band)
        rgb = rgb.copy()
        rgb[..., 0] = np.where(fix, rgb[..., 0] - spill, rgb[..., 0])
        rgb[..., 2] = np.where(fix, rgb[..., 2] - spill, rgb[..., 2])
        # What's left of a red-purple cast on those strands goes back to her dark brown hair colour
        cast = band & (rgb[..., 0] - rgb[..., 1] > 25) & (rgb[..., 2] - rgb[..., 1] > -5) if fix.any() else fix
        rgb[..., 0] = np.where(cast, np.minimum(rgb[..., 0], rgb[..., 1] + 12), rgb[..., 0])
        rgb[..., 2] = np.where(cast, np.minimum(rgb[..., 2], rgb[..., 1] + 6), rgb[..., 2])
    soft = Image.fromarray(np.where(keep, 255, 0).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(1.2))
    new_alpha = np.minimum(np.asarray(soft), alpha).astype(np.uint8)
    result = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    result.putalpha(Image.fromarray(new_alpha, "L"))
    return result


def import_art(source_dir, out_dir=ART_DIR, height=1200):
    """Turn pictures of her into transparent pose images: cut out the background, clean the edges, crop, resize.

    Pictures in source_dir are named after poses (happy.png, talk.png, wave_open.jpg, fly.png...). A transparent
    PNG keeps its own transparency; otherwise the plain backdrop colour is taken from the picture's corners (magenta
    is easiest: it's in none of her colours). Returns the poses imported.
    """
    import numpy as np
    os.makedirs(out_dir, exist_ok=True)
    done = []
    for file in sorted(os.listdir(source_dir)):
        name, ext = os.path.splitext(file)
        if ext.lower() not in (".png", ".jpg", ".jpeg", ".webp") or name.split("_open")[0] not in ART_NAMES:
            continue
        picture = Image.open(os.path.join(source_dir, file))
        if picture.mode in ("RGBA", "LA", "PA") and picture.convert("RGBA").getchannel("A").getextrema()[0] < 128:
            image = picture.convert("RGBA")                   # already a transparent cut-out
        else:
            rgb = np.asarray(picture.convert("RGB")).astype(np.float32)
            h, w, _ = rgb.shape
            k = max(4, min(h, w) // 40)
            corners = np.concatenate([rgb[:k, :k].reshape(-1, 3), rgb[:k, -k:].reshape(-1, 3),
                                      rgb[-k:, :k].reshape(-1, 3), rgb[-k:, -k:].reshape(-1, 3)])
            background = np.median(corners, axis=0)
            distance = np.sqrt(((rgb - background) ** 2).sum(axis=2))
            alpha = np.clip((distance - 40) / 50, 0, 1)        # soft edge between 40 and 90 colour steps away
            # Remove the background colour's tint from the soft edge pixels
            spill = (1 - alpha)[..., None]
            clean = np.clip((rgb - background * spill) / np.maximum(alpha[..., None], 0.05), 0, 255)
            rgba = np.dstack([np.where(alpha[..., None] > 0, clean, rgb), alpha * 255]).astype(np.uint8)
            image = Image.fromarray(rgba, "RGBA")
        image = clean_cutout(image)
        box = image.getchannel("A").point(lambda a: 255 if a > 20 else 0).getbbox()
        if box:
            image = image.crop(box)
        image = image.resize((round(image.width * height / image.height), height), Image.LANCZOS)
        image.save(os.path.join(out_dir, f"{name}.png"))
        done.append(name)
    return done


def ws_badge(width, font_loader):
    """A small green "WS" badge with a gold border (her emblem), `width` pixels wide."""
    scale = 4
    w, h = width * scale, round(width * 0.52) * scale
    badge = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=SUIT, outline=GOLD, width=max(2, h // 12))
    font = font_loader(round(h * 0.62))
    box = draw.textbbox((0, 0), "WS", font=font, stroke_width=max(1, h // 24))
    draw.text(((w - box[2] - box[0]) / 2, (h - box[3] - box[1]) / 2), "WS", font=font, fill=GOLD,
              stroke_width=max(1, h // 24), stroke_fill="#1C6B2E")
    return badge.resize((width, round(width * 0.52)), Image.LANCZOS)


def cover_corner_logo(source, target, font_loader, work_dir):
    """Copy a video with a "WS" badge over its bottom-right corner, where image AIs put their logo (e.g. "Grok").

    The badge is about 12% of the video's width, just inside the corner; sound is copied unchanged.
    """
    import re
    import subprocess
    info = subprocess.run([ffmpeg_exe(), "-i", source], capture_output=True, text=True).stderr
    width, height = (int(n) for n in re.search(r"Video: .*?, (\d{2,5})x(\d{2,5})", info).groups())
    badge_w = max(40, round(width * 0.12))
    badge = os.path.join(work_dir, "ws_badge.png")
    ws_badge(badge_w, font_loader).save(badge)
    margin_x, margin_y = max(2, round(width * 0.005)), max(3, round(height * 0.005))
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", source, "-i", badge,
                    "-filter_complex", f"[0:v][1:v]overlay=W-w-{margin_x}:H-h-{margin_y},format=yuv420p[v]",
                    "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "copy", target], check=True, capture_output=True)
    os.remove(badge)
    return target


def expression_for(sentence, index=0):
    """Which face suits a sentence of the script."""
    text = sentence.lower()
    if index == 0 and any(w in text for w in ("hi ", "hello", "hey ")):
        return "wave"
    if any(w in text for w in ("whoa", "wow", "amazing", "incredible", "can you believe", "surprise", "oh no")):
        return "wow"
    if any(w in text for w in ("guess", "think", "wonder", "hmm", "what if", "imagine")):
        return "think"
    if text.rstrip("\"'”’) ").endswith("?"):
        return "ask"
    if any(w in text for w in ("bye", "see you", "next time")):
        return "wave"
    if text.rstrip("\"'”’) ").endswith("!"):
        return "happy"
    return "talk"


def sheet(path):
    """Every expression side by side (mouth closed, then open) for a quick visual check."""
    images = [hero_image(e, m, BASE_H) for e in EXPRESSIONS for m in (False, True)]
    w, h = BASE_W // 2, BASE_H // 2
    out = Image.new("RGB", (w * 6, h * 2), "#BFE8FF")
    for i, image in enumerate(images):
        small = image.resize((round(image.width * h / image.height), h), Image.LANCZOS)
        col, row = (i // 2), (i % 2)
        out.paste(small, (col * w, row * h), small)
    out.save(path)


# ---------- animation ----------

HOST_FPS = 15          # the host overlay changes pose and mouth at this rate; plenty for a cartoon
CLIP_FPS = 30
# Her lines, spoken in the channel's narrator voice (editing_rules.mascot in BRAIN.md can change them)
INTRO_LINE = "Hey kids! This is Wonder Sprout, your fun and learning superhero!"
OUTRO_LINE = "Wonder Sprout signing off, kids! We'll meet next time!"
ENTRANCE_SECONDS = 0.8  # Shorts: she swoops into her corner over the opening instead of a separate intro


def ffmpeg_exe():
    import shutil
    import imageio_ffmpeg
    return shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()


def mouth_track(voice_path, fps=HOST_FPS):
    """True for each frame where the narration is loud enough for her mouth to be open."""
    import subprocess
    import numpy as np
    pcm = subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", voice_path, "-f", "s16le",
                          "-ac", "1", "-ar", "16000", "pipe:1"], capture_output=True, check=True).stdout
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    step = 16000 // fps
    frames = len(samples) // step
    if not frames:
        return []
    rms = np.sqrt((samples[:frames * step].reshape(frames, step) ** 2).mean(axis=1))
    loud = np.percentile(rms, 90) or 1.0
    return list(rms > 0.3 * loud)


def expression_track(sentences, frames, fps=HOST_FPS):
    """The expression for each frame, from the sentence being spoken (sentences: [(start seconds, text)])."""
    track, current, index = [], "happy", -1
    for frame in range(frames):
        t = frame / fps
        while index + 1 < len(sentences) and sentences[index + 1][0] <= t:
            index += 1
            current = expression_for(sentences[index][1], index)
        track.append(current)
    return track


def sprite_set(height):
    """Every expression with its mouth closed and open, scaled to height."""
    return {(e, m): hero_image(e, m, height) for e in EXPRESSIONS for m in (False, True)}


def ease_out(t):
    return 1 - (1 - min(max(t, 0.0), 1.0)) ** 3


def render_host(voice_path, sentences, duration, frame_size, out_path, entrance=False):
    """A transparent video of her hosting in the bottom-left corner, as long as the narration.

    Returns (out_path, x, y): where to overlay it on the main video.
    """
    import subprocess
    width, height = frame_size
    portrait = height > width
    # Full-body art needs more height than the half-body drawing for her face to read
    full_body = has_art()
    hero_h = round(height * ((0.3 if portrait else 0.55) if full_body else (0.24 if portrait else 0.36)))
    sprites = sprite_set(hero_h)
    sw, sh = sprites[("happy", False)].size
    bob = max(4, hero_h // 60)
    box_w, box_h = sw, sh + 2 * bob
    x = round(width * 0.02)
    y = height - box_h - round(height * (0.11 if portrait else 0.03))   # above YouTube's Shorts caption area
    frames = int(duration * HOST_FPS) + 1
    mouths = mouth_track(voice_path)
    faces = expression_track(sentences, frames)
    cache = {}
    encoder = subprocess.Popen(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
         "-s", f"{box_w}x{box_h}", "-r", str(HOST_FPS), "-i", "pipe:0", "-c:v", "qtrle", "-pix_fmt", "argb", out_path],
        stdin=subprocess.PIPE)
    try:
        for frame in range(frames):
            t = frame / HOST_FPS
            face = faces[frame]
            mouth = bool(mouths[frame]) if frame < len(mouths) else False
            if face == "think":
                mouth = False                      # thinking is shown with a closed mouth even mid-sentence
            dy = round(bob + bob * math.sin(2 * math.pi * t / 1.6))
            dx = 0
            if entrance and t < ENTRANCE_SECONDS:
                # Swoop in from above the right side of the frame into her corner
                p = ease_out(t / ENTRANCE_SECONDS)
                dx, dy = round((1 - p) * width * 0.7), round(dy - (1 - p) * height * 0.6)
            key = (face, mouth, dx, dy)
            if key not in cache:
                canvas = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
                sprite = sprites[(face, mouth)]
                canvas.paste(sprite, (dx, dy), sprite)          # paste clips whatever falls outside the box
                cache[key] = canvas.tobytes()
                if len(cache) > 400:
                    cache.clear()
            encoder.stdin.write(cache[key])
    finally:
        encoder.stdin.close()
        encoder.wait()
    if encoder.returncode:
        raise RuntimeError("Could not render the host overlay")
    return out_path, x, y


def speed_lines(draw, cx, cy, size, alpha):
    for i in range(7):
        off = (i - 3) * size * 0.09
        length = size * (0.5 + 0.12 * (i % 3))
        draw.line([(cx + size * 0.2, cy + off), (cx + size * 0.2 + length, cy + off - length * 0.35)],
                  fill=(255, 255, 255, alpha), width=max(3, size // 70))


def star(draw, cx, cy, r, colour):
    points = []
    for k in range(10):
        a = math.pi / 2 + k * math.pi / 5
        rr = r if k % 2 == 0 else r * 0.45
        points.append((cx + rr * math.cos(a), cy - rr * math.sin(a)))
    draw.polygon(points, fill=colour)


def title_card(text, font, colour="#FFCB2F"):
    """Big outlined title text as an RGBA image."""
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    box = probe.textbbox((0, 0), text, font=font, stroke_width=max(4, font.size // 10))
    image = Image.new("RGBA", (box[2] - box[0] + 20, box[3] - box[1] + 20), (0, 0, 0, 0))
    ImageDraw.Draw(image).text((10 - box[0], 10 - box[1]), text, font=font, fill=colour,
                               stroke_width=max(4, font.size // 10), stroke_fill="#1C6B2E")
    return image


def sky(width, height):
    """A bright sky gradient, the backdrop she flies through."""
    top, bottom = (78, 170, 245), (190, 232, 255)
    column = Image.new("RGB", (1, height))
    for y in range(height):
        k = y / max(height - 1, 1)
        column.putpixel((0, y), tuple(round(a + (b - a) * k) for a, b in zip(top, bottom)))
    return column.resize((width, height))


def cloud(size):
    """A soft white cloud as an RGBA image `size` pixels wide."""
    w, h = size, size // 2
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for cx, cy, r in ((0.3, 0.62, 0.22), (0.5, 0.45, 0.28), (0.7, 0.6, 0.22), (0.5, 0.7, 0.3)):
        draw.ellipse([w * (cx - r), h * (cy - r * 1.6), w * (cx + r), h * (cy + r * 1.6)], fill=(255, 255, 255, 235))
    return image.filter(ImageFilter.GaussianBlur(size // 40))


class FlyingSky:
    """Clouds drifting past at different speeds (near ones bigger and faster), so she seems to fly."""

    def __init__(self, width, height, seed=7):
        import random
        rng = random.Random(seed)
        self.width, self.height = width, height
        self.base = sky(width, height)
        self.clouds = []
        for layer, (scale, speed) in enumerate(((0.18, 0.08), (0.3, 0.16), (0.45, 0.3))):
            for _ in range(3):
                image = cloud(round(width * scale))
                self.clouds.append((image, rng.random(), rng.uniform(0.05, 0.85), speed * (0.8 + 0.4 * rng.random())))

    def frame(self, t):
        image = self.base.copy().convert("RGBA")
        span = self.width * 1.6
        for picture, start, y, speed in self.clouds:
            x = self.width - ((start * span + t * speed * self.width) % span)
            image.paste(picture, (round(x), round(y * self.height)), picture)
        return image


def speak_line(line, voice, rate, out_path):
    """Her line in the channel's voice, lively. Returns the audio's length in seconds."""
    from agents import expressive_voice
    seconds, _ = expressive_voice.narrate(line, voice, rate, "lively", out_path)
    return seconds


def effect_sources(seconds, whooshes=(), chimes=()):
    """FFmpeg audio sources: a soft wind bed for flight, whooshes and sparkle chimes at the given times."""
    sources = [f"anoisesrc=d={seconds}:c=brown:r=44100:a=0.5,lowpass=f=500,highpass=f=80,volume=0.25,"
               f"afade=t=in:d=0.4,afade=t=out:st={max(seconds - 0.5, 0)}:d=0.5"]
    for start in whooshes:
        sources.append(f"anoisesrc=d=0.9:c=pink:r=44100:a=0.45,highpass=f=300,lowpass=f=4500,afade=t=in:d=0.3,"
                       f"afade=t=out:st=0.35:d=0.55,adelay={int(start * 1000)}")
    for start in chimes:
        sources.append("aevalsrc=exprs='0.25*sin(2*PI*1568*t)*exp(-5*t)+0.2*sin(2*PI*2093*t)*exp(-6*t)"
                       f"+0.15*sin(2*PI*2637*t)*exp(-7*t)':d=1.2:s=44100,adelay={int(start * 1000)}")
    return sources


def render_clip(frames_fn, size, seconds, out_path, voice_path=None, voice_at=0.0, whooshes=(), chimes=()):
    """Encode a clip from frames_fn(t) -> RGB image, with her line (voice_path, starting at voice_at) mixed over
    the flight sound effects."""
    import subprocess
    width, height = size
    total = int(seconds * CLIP_FPS)
    command = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{width}x{height}", "-r", str(CLIP_FPS), "-i", "pipe:0"]
    labels, graph = [], ""
    if voice_path:
        command += ["-i", voice_path]
        delay = int(voice_at * 1000)
        graph += f"[1:a]aresample=44100,adelay={delay}|{delay},volume=1.6[voice];"
        labels.append("[voice]")
    effects = effect_sources(seconds, whooshes, chimes)
    inputs_before = command.count("-i")
    for source in effects:
        command += ["-f", "lavfi", "-i", source]
    for k in range(len(effects)):
        graph += f"[{inputs_before + k}:a]aformat=channel_layouts=stereo,aresample=44100[e{k}];"
        labels.append(f"[e{k}]")
    graph += "".join(labels) + f"amix=inputs={len(labels)}:duration=longest:normalize=0,apad,atrim=0:{seconds}[a]"
    command += ["-filter_complex", graph, "-map", "0:v", "-map", "[a]", "-t", f"{seconds:.3f}", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                "-ar", "44100", "-ac", "2", out_path]
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        for frame in range(total):
            encoder.stdin.write(frames_fn(frame / CLIP_FPS).tobytes())
    finally:
        encoder.stdin.close()
        encoder.wait()
    if encoder.returncode:
        raise RuntimeError("Could not render the hero clip")
    return out_path


def flying(sprite, t, tilt=6):
    """Her pose in flight: a gentle sway (the drawn character tilts; photo art just bobs)."""
    if os.path.isdir(ART_DIR) and has_art():
        return sprite
    angle = tilt * math.sin(2 * math.pi * t / 1.4)
    return sprite.rotate(angle, resample=Image.BICUBIC, expand=True)


def talking_frames(voice_path, seconds):
    """Mouth open/closed per CLIP_FPS frame for a line of speech."""
    track = mouth_track(voice_path, fps=CLIP_FPS)
    return track + [False] * max(0, int(seconds * CLIP_FPS) - len(track))


def render_intro(size, out_path, font_loader, voice, rate, line, name="WonderSprout!"):
    """She swoops in through the sky, hovers, waves and says her line; her name pops up with a sparkle."""
    width, height = size
    work = out_path + ".voice.mp3"
    spoken = speak_line(line, voice, rate, work)
    swoop, speak_at = 0.9, 0.7
    seconds = round(speak_at + spoken + 0.5, 2)
    backdrop = FlyingSky(width, height)
    hero_h = round(height * (0.55 if height > width else 0.68))
    sprites = sprite_set(hero_h)
    fly = hero_image("fly", False, hero_h) if has_art("fly") else None
    sw, sh = sprites[("wave", False)].size
    home_x, home_y = (width - sw) // 2, height - sh - round(height * (0.12 if height > width else 0.02))
    title = title_card(name, font_loader(round(min(width, height) * 0.11)))
    mouths = talking_frames(work, seconds)
    title_at = speak_at + 0.6

    def frame_at(t):
        image = backdrop.frame(t)
        draw = ImageDraw.Draw(image, "RGBA")
        bob = round(sh * 0.025 * math.sin(2 * math.pi * t / 1.5))
        if t < swoop:                                          # swoop in from the top right, trailing speed lines
            p = ease_out(t / swoop)
            x = round(home_x + (1 - p) * width * 0.9)
            y = round(home_y - (1 - p) * height * 0.6)
            speed_lines(draw, x + sw * 0.55, y + sh * 0.35, sw, int(220 * (1 - p)))
            sprite = flying(fly or sprites[("happy", False)], t, tilt=14)
        else:
            spoken_frame = int((t - speak_at) * CLIP_FPS)
            open_mouth = 0 <= spoken_frame < len(mouths) and mouths[spoken_frame]
            pose = "wave" if int(t * 3) % 2 == 0 else "happy"
            sprite = flying(sprites[(pose, bool(open_mouth))], t)
            x, y = home_x, home_y + bob
        image.paste(sprite, (x, y), sprite)
        if t > title_at:                                        # her name pops in with a little overshoot
            p = min((t - title_at) / 0.35, 1.0)
            scale = 0.6 + 0.5 * ease_out(p) - 0.1 * max(0.0, p - 0.7) / 0.3
            tw, th = max(1, round(title.width * scale)), max(1, round(title.height * scale))
            sized = title.resize((tw, th), Image.LANCZOS)
            image.alpha_composite(sized, ((width - tw) // 2, round(height * 0.06)))
            for k, (sx, sy) in enumerate(((0.15, 0.1), (0.85, 0.14), (0.1, 0.28), (0.9, 0.3))):
                twinkle = 0.6 + 0.4 * math.sin(t * 8 + k)
                star(draw, width * sx, height * sy, min(width, height) * 0.025 * twinkle, (255, 236, 120, 230))
        return image.convert("RGB")

    try:
        return render_clip(frame_at, size, seconds, out_path, work, speak_at, whooshes=(0.0,), chimes=(title_at,))
    finally:
        if os.path.exists(work):
            os.remove(work)


def render_outro(size, out_path, font_loader, voice, rate, line):
    """She hovers in the sky, waves and says goodbye, then flies off into the distance with a sparkle trail."""
    width, height = size
    work = out_path + ".voice.mp3"
    spoken = speak_line(line, voice, rate, work)
    leave_at = round(0.3 + spoken + 0.2, 2)
    seconds = round(leave_at + 1.3, 2)
    backdrop = FlyingSky(width, height, seed=11)
    hero_h = round(height * (0.55 if height > width else 0.68))
    sprites = sprite_set(hero_h)
    fly = hero_image("fly", False, hero_h) if has_art("fly") else None
    sw, sh = sprites[("wave", False)].size
    home_x, home_y = (width - sw) // 2, height - sh - round(height * (0.12 if height > width else 0.02))
    mouths = talking_frames(work, seconds)

    def frame_at(t):
        image = backdrop.frame(t)
        draw = ImageDraw.Draw(image, "RGBA")
        if t < leave_at:
            frame = int((t - 0.3) * CLIP_FPS)
            open_mouth = 0 <= frame < len(mouths) and mouths[frame]
            pose = "wave" if int(t * 3) % 2 == 0 else "happy"
            sprite = flying(sprites[(pose, bool(open_mouth))], t)
            bob = round(sh * 0.025 * math.sin(2 * math.pi * t / 1.5))
            image.paste(sprite, (home_x, home_y + bob), sprite)
        else:                                                  # zoom off up and away, shrinking into the sky
            p = ease_in((t - leave_at) / (seconds - leave_at))
            scale = max(0.05, 1 - 0.95 * p)
            sprite = flying(fly or sprites[("happy", False)], t, tilt=14)
            small = sprite.resize((max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale))))
            cx = home_x + sw / 2 + p * width * 0.45
            cy = home_y + sh / 2 - p * height * 0.75
            for k in range(6):                                 # sparkle trail behind her
                lag = k * 0.06
                q = ease_in(max(0.0, (t - leave_at - lag)) / (seconds - leave_at))
                star(draw, home_x + sw / 2 + q * width * 0.45, home_y + sh / 2 - q * height * 0.75,
                     min(width, height) * 0.02 * (1 - k / 7), (255, 236, 120, 220 - k * 30))
            image.paste(small, (round(cx - small.width / 2), round(cy - small.height / 2)), small)
        return image.convert("RGB")

    try:
        return render_clip(frame_at, size, seconds, out_path, work, 0.3, whooshes=(leave_at - 0.1,),
                           chimes=(leave_at + 0.2,))
    finally:
        if os.path.exists(work):
            os.remove(work)


def ease_in(t):
    return min(max(t, 0.0), 1.0) ** 2


if __name__ == "__main__":
    sheet("wondersprout_sheet.png")
