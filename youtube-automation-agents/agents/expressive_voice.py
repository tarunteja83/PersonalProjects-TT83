"""Expressive narration with edge-tts: each sentence gets its own speed, pitch and volume, with natural pauses.

edge-tts reads a whole script in one even tone. Narrating sentence by sentence lets the hook start with energy,
questions rise, exclamations lift, plain sentences vary a little so the voice never drones, and the ending slows
down. The sentence audio is trimmed of silence and joined with pauses, which also gives exact sentence timings
for matching footage to the narration.

voice_expression in questions.yaml: "lively" (kids), "measured" (news), or "off" (the old one-pass narration).
"""
import asyncio
import re
import shutil
import subprocess

import edge_tts
import imageio_ffmpeg

SAMPLE_RATE = 24000

# Sound-effect cues the kids script writes in square brackets ([BOING], [TADA], ...) -> files in assets/audio/kids_sfx.
# The tags are never spoken; the editor plays the sound where the tag sits in the sentence.
KNOWN_SFX = {
    "boing": "boing.wav", "pop": "pop.wav", "whoosh": "whoosh.wav", "zoom": "whoosh.wav",
    "ding": "ding.wav", "chime": "ding.wav", "bell": "ding.wav",
    "tick": "tick.wav", "clock": "tick.wav", "timer": "tick.wav",
    "tada": "tada.wav", "ta-da": "tada.wav", "fanfare": "tada.wav",
    "splash": "splash.wav", "splat": "splash.wav",
    "cheer": "cheer.wav", "yay": "cheer.wav", "woohoo": "cheer.wav",
}
CUE_TAG = re.compile(r"\[([A-Za-z0-9_\-]+)\]")
# Without tags, a spoken sound word still gets its effect (whole words only: "holding" is not "ding")
SOUND_WORDS = re.compile(r"\b(boing|pop|whoosh|splash|splat|ding|ta-?da|yay|woohoo)\b|3[.,]*\s*2[.,]*\s*1\b", re.I)
# Repeats of one cue in a sentence ([TICK] [TICK] [TICK]) play this many seconds apart
REPEAT_GAP = 0.6


def strip_cues(text):
    """The text without its [SOUND] tags, so they are never read aloud or shown in a description."""
    return re.sub(r"\s+([!?.,])", r"\1", re.sub(r"\s{2,}", " ", CUE_TAG.sub("", text))).strip()


def extract_sfx_and_clean(sentence):
    """(spoken sentence, [(fraction of the sentence where the sound plays, sfx file)])."""
    clean = strip_cues(sentence)
    cues = [(m.start() / max(1, len(sentence)), KNOWN_SFX[m.group(1).lower()])
            for m in CUE_TAG.finditer(sentence) if m.group(1).lower() in KNOWN_SFX]
    if not CUE_TAG.search(sentence):
        word = SOUND_WORDS.search(clean)
        if word:
            # No group means the "3, 2, 1" countdown
            sound = KNOWN_SFX[word.group(1).lower().replace("-", "")] if word.group(1) else "tick.wav"
            cues = [(word.end() / max(1, len(clean)), sound)]
    return clean, cues


# Per sentence kind: (speed %, pitch Hz, volume %, pause after in seconds), added to the channel's voice_speed.
# "plain" sentences rotate through their variants so neighbouring sentences never sound identical.
STYLES = {
    "lively": {
        "hook": (8, 20, 10, 0.30), "exclaim": (6, 14, 8, 0.25), "question": (-2, 24, 4, 0.45),
        "plain": [(0, 6, 0, 0.20), (-4, -6, 0, 0.20), (3, 2, 3, 0.20)], "last": (-8, 8, 0, 0.35),
    },
    "measured": {
        "hook": (4, 6, 8, 0.35), "exclaim": (3, 4, 5, 0.30), "question": (-3, 10, 2, 0.45),
        "plain": [(0, 0, 0, 0.25), (-3, -4, 0, 0.25), (2, 2, 0, 0.25)], "last": (-6, -4, 0, 0.40),
    },
}
EXPRESSIONS = ["lively", "measured", "off"]
# Sentences this short are joined to the next one, so the voice doesn't stop and start on every fragment
MIN_WORDS = 3
PARALLEL = 4
# One sentence normally takes a few seconds; a stalled connection is given up on and retried
SPEAK_TIMEOUT_SECONDS = 60


def split_sentences(text):
    parts = [p.strip() for p in re.split(r"(?<=[.!?।…])[\"'”’)]*\s+", text.strip()) if p.strip()]
    sentences, carry = [], ""
    for part in parts:
        carry = f"{carry} {part}".strip()
        if len(carry.split()) >= MIN_WORDS:
            sentences.append(carry)
            carry = ""
    if carry:
        if sentences:
            sentences[-1] = f"{sentences[-1]} {carry}"
        else:
            sentences.append(carry)
    return sentences


def sentence_kind(index, count, text):
    if index == 0:
        return "hook"
    if index == count - 1:
        return "last"
    if text.rstrip("\"'”’) ").endswith("?"):
        return "question"
    if text.rstrip("\"'”’) ").endswith("!"):
        return "exclaim"
    return "plain"


def prosody(style, index, count, text, base_rate):
    """(rate, pitch, volume, pause) strings and seconds for one sentence."""
    preset = STYLES[style][sentence_kind(index, count, text)]
    if isinstance(preset, list):
        preset = preset[index % len(preset)]
    rate, pitch, volume, pause = preset
    base = int(str(base_rate).replace("%", "") or 0)
    return f"{base + rate:+d}%", f"{pitch:+d}Hz", f"{volume:+d}%", pause


async def _stream(text, voice, rate, pitch, volume):
    audio = bytearray()
    async for chunk in edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume).stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    return bytes(audio)


async def _speak(text, voice, rate, pitch, volume, attempts=3):
    for attempt in range(attempts):
        try:
            # A stalled connection to the voice service would otherwise wait forever
            audio = await asyncio.wait_for(_stream(text, voice, rate, pitch, volume), SPEAK_TIMEOUT_SECONDS)
            if audio:
                return audio
        except Exception:
            if attempt == attempts - 1:
                raise
        await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError(f"No audio for: {text[:60]}")


async def _speak_all(jobs):
    gate = asyncio.Semaphore(PARALLEL)

    async def one(job):
        async with gate:
            return await _speak(*job)
    return await asyncio.gather(*(one(job) for job in jobs))


def ffmpeg_exe():
    return shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()


def to_pcm(mp3):
    """Decode to 16-bit mono PCM, trimming silence at both ends of the sentence."""
    trim = "silenceremove=start_periods=1:start_threshold=-45dB"
    result = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-af", f"{trim},areverse,{trim},areverse", "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1"],
        input=mp3, capture_output=True, check=True)
    return result.stdout


def narrate(script, voice, base_rate, style, out_mp3):
    """Write the expressive narration to out_mp3.

    Returns (seconds, [(sentence start seconds, sentence text)], [(seconds, sfx file)]).
    """
    spoken, cue_plans = [], []
    for sentence in split_sentences(script):
        clean, cues = extract_sfx_and_clean(sentence)
        # A sentence that is only sound tags ("[TADA] [CHEER]!") isn't spoken: its sounds play in the pause
        if re.search(r"\w", clean):
            spoken.append(clean)
            cue_plans.append(cues)
        elif cue_plans:
            cue_plans[-1] += [(1.0, sound) for _, sound in cues]
    plans = [prosody(style, i, len(spoken), s, base_rate) for i, s in enumerate(spoken)]
    audio = asyncio.run(_speak_all([(s, voice, r, p, v) for s, (r, p, v, _) in zip(spoken, plans)]))
    pcm, timings, sfx_events = bytearray(), [], []
    lead_in = int(0.15 * SAMPLE_RATE) * 2
    pcm += b"\0" * lead_in
    for sentence, cues, mp3, (_, _, _, pause) in zip(spoken, cue_plans, audio, plans):
        start = len(pcm) / 2 / SAMPLE_RATE
        timings.append((start, sentence))
        speech = to_pcm(mp3)
        length = len(speech) / 2 / SAMPLE_RATE
        repeats = {}
        for fraction, sound in cues:
            at = start + fraction * length + REPEAT_GAP * repeats.get(sound, 0)
            repeats[sound] = repeats.get(sound, 0) + 1
            sfx_events.append((round(at, 2), sound))
        pcm += speech
        pcm += b"\0" * (int(pause * SAMPLE_RATE) * 2)
    subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "s16le", "-ar", str(SAMPLE_RATE),
         "-ac", "1", "-i", "pipe:0", "-c:a", "libmp3lame", "-b:a", "96k", out_mp3],
        input=bytes(pcm), check=True)
    return len(pcm) / 2 / SAMPLE_RATE, timings, sfx_events


def shift(value, offset, unit):
    """'+4Hz' shifted by offset -> '+10Hz' (unit 'Hz' or '%')."""
    return f"{int(str(value).replace(unit, '') or 0) + int(offset):+d}{unit}"


def narrate_lines(lines, base_rate, style, out_mp3):
    """Narrate a story line by line, each in its speaker's voice, never merging lines (each has its own picture).

    lines: [{"text", "voice", "pitch" (Hz offset), "rate" (% offset)}]. Returns (seconds, [line start seconds]).
    """
    plans = []
    for index, line in enumerate(lines):
        rate, pitch, volume, pause = prosody(style, index, len(lines), line["text"], base_rate)
        plans.append((line["text"], line["voice"], shift(rate, line.get("rate", 0), "%"),
                      shift(pitch, line.get("pitch", 0), "Hz"), volume, pause))
    audio = asyncio.run(_speak_all([plan[:5] for plan in plans]))
    pcm, starts = bytearray(b"\0" * (int(0.15 * SAMPLE_RATE) * 2)), []
    for mp3, plan in zip(audio, plans):
        starts.append(len(pcm) / 2 / SAMPLE_RATE)
        pcm += to_pcm(mp3)
        pcm += b"\0" * (int(plan[5] * SAMPLE_RATE) * 2)
    subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "s16le", "-ar", str(SAMPLE_RATE),
         "-ac", "1", "-i", "pipe:0", "-c:a", "libmp3lame", "-b:a", "128k", out_mp3],
        input=bytes(pcm), check=True)
    return len(pcm) / 2 / SAMPLE_RATE, starts
