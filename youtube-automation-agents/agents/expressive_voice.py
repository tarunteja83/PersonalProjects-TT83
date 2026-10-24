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


async def _speak(text, voice, rate, pitch, volume, attempts=3):
    for attempt in range(attempts):
        try:
            audio = bytearray()
            async for chunk in edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume).stream():
                if chunk["type"] == "audio":
                    audio += chunk["data"]
            if audio:
                return bytes(audio)
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
    """Write the expressive narration to out_mp3. Returns (seconds, [(sentence start seconds, sentence text)])."""
    sentences = split_sentences(script)
    plans = [prosody(style, i, len(sentences), s, base_rate) for i, s in enumerate(sentences)]
    audio = asyncio.run(_speak_all([(s, voice, r, p, v) for s, (r, p, v, _) in zip(sentences, plans)]))
    pcm, timings = bytearray(), []
    lead_in = int(0.15 * SAMPLE_RATE) * 2
    pcm += b"\0" * lead_in
    for sentence, mp3, (_, _, _, pause) in zip(sentences, audio, plans):
        timings.append((len(pcm) / 2 / SAMPLE_RATE, sentence))
        pcm += to_pcm(mp3)
        pcm += b"\0" * (int(pause * SAMPLE_RATE) * 2)
    subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "s16le", "-ar", str(SAMPLE_RATE),
         "-ac", "1", "-i", "pipe:0", "-c:a", "libmp3lame", "-b:a", "96k", out_mp3],
        input=bytes(pcm), check=True)
    return len(pcm) / 2 / SAMPLE_RATE, timings
