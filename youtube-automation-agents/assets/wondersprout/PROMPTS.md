# WonderSprout assets: what to provide

Drop everything into Google Drive → `YouTube_Auto/wondersprout_assets/` and tell Claude. Claude imports it into
this folder (`assets/wondersprout/`) and pushes it to GitHub, so every run uses it. Until something is provided, the
pipeline makes its own version (a drawn cartoon WonderSprout and a generated intro and outro).

## 1. Intro and outro videos (optional, recommended)

| File | What it is |
|---|---|
| `intro.mp4` | Opens every long video. She flies in and says "Hey kids! This is Wonder Sprout, your fun and learning superhero!" |
| `outro.mp4` | Ends every video, just before the LIKE / SUBSCRIBE card. She says "Wonder Sprout signing off, kids! We'll meet next time!" and flies away |
| `intro_short.mp4`, `outro_short.mp4` | Optional vertical versions for Shorts. Without them, Shorts use the landscape files fitted on a blurred background |

- **Format:** MP4 (H.264 video, AAC sound), 1920 x 1080 landscape (16:9); vertical versions 1080 x 1920 (9:16).
- **Length:** 3 to 6 seconds each. Long intros make viewers leave before the story starts.
- **Sound:** include the voice and sound effects in the file. (A file without sound gets silence.)
- **Size:** under 20 MB each.
- **Ending:** the intro should end on a clean frame (the story cuts straight in); the outro's last frame is used as
  is, then the end card follows.
- Shorts: by default Shorts don't play the intro (viewers swipe away from slow starts; she swoops into her corner
  instead) but do play the outro. `intro_shorts: true` in BRAIN.md makes Shorts play it too.

## 2. Expression pictures for the corner host (9 pictures)

She sits in a corner of every video and changes expression with the script, so she needs one picture per
expression. All 9 must be **the same character, same outfit, same framing and size**, so she doesn't jump when
she switches.

| File name | Expression |
|---|---|
| `happy.png` | Smiling warmly, mouth closed, hands relaxed |
| `happy_open.png` | Same, mouth open as if laughing or talking |
| `talk.png` | Talking cheerfully to the viewer, mouth open mid-word, one hand gesturing |
| `think.png` | Thinking: finger on her chin, eyes looking up, small closed smile |
| `ask.png` | Asking a question: eyebrows raised, one palm held out and up, mouth slightly open |
| `wow.png` | Amazed: big eyes, mouth open in a round "Wow!", both hands up near her cheeks |
| `wave.png` | Waving hello with her right hand raised, closed-mouth smile |
| `wave_open.png` | Waving with her right hand raised, mouth open saying "Hi!" |
| `fly.png` | Flying like a superhero: body horizontal, one fist forward, legs together, hair streaming back |

- **Framing:** head to knees, facing the viewer, centred, a little space around her.
- **Background:** a transparent PNG is best. Otherwise a solid flat **magenta (#FF00FF)** background; avoid
  green or blue backgrounds (her outfit is green and blue).
- **Size:** at least 1024 pixels tall, PNG.

### Making them with a free image AI

Upload the WonderSprout reference image to the free Gemini app (gemini.google.com) or ChatGPT, and send this, plus
the expression from the table above:

> Use the woman in the green and light-blue superhero outfit from the attached picture. Keep her face, skin tone,
> dark wavy hair with green streaks in a high ponytail, the green and light-blue tunic with the yellow-green "WS"
> logo, the gold utility belt with the leaf buckle, green leggings and brown boots exactly the same. Show only her,
> from head to knees, facing the viewer, centred, with space around her. No children, no room, no text, no
> shadow. Solid flat pure magenta (#FF00FF) background, even studio lighting. Expression:

If the face or outfit drifts, ask again with "keep her face exactly like the reference".

## How Claude imports them

`python execution/execution.py --hero-art "<folder>"` cuts out the backgrounds (or keeps transparent ones), drops
an image AI's corner logo and any magenta fringe, crops and resizes the pictures into this folder and saves a
preview sheet; the MP4 files get a small "WS" badge over the image AI's logo in the bottom-right corner.
Missing pictures are fine: a missing expression borrows the closest one, and without a mouth-open picture she gives
a small "talking" pulse instead.
