---
name: studio-pack
description: Fork add-on with DaVinci Resolve-style finishing tools for HyperFrames projects. Use for voice cleanup (noise, hum, echo), dialogue leveling, beat detection when `hyperframes beats` finds none, snapping cuts to beats, auto-ducking music under voice clips, and exporting one render to YouTube, Shorts, TikTok, Reels and X specs. Use before publishing any video.
---

# Studio Pack (batches 1-2: audio, platform export, smart reframe)

Tools live in the fork at `addons/studio-pack/tools/`. Run them with the shared environment:

```bash
PY=~/video-pipeline/.ai-venv/Scripts/python.exe
PACK=~/video-pipeline/hyperframes/addons/studio-pack/tools
```

## Use HyperFrames' own tool first when it exists

| Job                                       | First choice                      | Studio Pack adds                                                             |
| ----------------------------------------- | --------------------------------- | ---------------------------------------------------------------------------- |
| Beats                                     | `npx hyperframes beats`           | `studio_audio.py beats` when that finds 0 beats (ambient or soft music)      |
| Ducking from a transcript                 | media-use `audio-duck.mjs`        | `studio_audio.py duck`: from the voice `<audio>` clips, no transcript needed |
| Matching two clips' loudness              | `npx hyperframes normalize-audio` | —                                                                            |
| Text-based cuts, silence and "um" removal | media-use `transcript-cut.mjs`    | —                                                                            |
| Noise and hum removal                     | nothing in HyperFrames            | `studio_audio.py clean`                                                      |
| Evening out loud and quiet phrases        | nothing in HyperFrames            | `studio_audio.py level`                                                      |
| Platform versions                         | nothing in HyperFrames            | `studio_export.py`                                                           |

## Voice: clean, then level (in that order)

```bash
$PY $PACK/studio_audio.py clean raw.wav voice.clean.wav            # --strength light|medium|strong
$PY $PACK/studio_audio.py level voice.clean.wav voice.wav          # -16 LUFS; --target to change
```

- Engines: `--engine auto` uses DeepFilterNet (Python package from the repo clone at
  `~/video-pipeline/addons/DeepFilterNet`, run in `~/video-pipeline/.df-venv`, with the repo's own DeepFilterNet3
  model, so nothing downloads), else FFmpeg. `--engine elevenlabs` is the paid option: it only runs when the user
  has set `ELEVENLABS_API_KEY` themselves. Never ask for, read, print or set a key.
- Clean recorded voice only. Kokoro or other generated voices are already clean.
- Listen-check: compare a few seconds before and after with `npx hyperframes audio` (loudness and silence)
  and tell the user the pause level before and after.

## Music: beats, snap, duck (edit a copy or keep the backup)

```bash
$PY $PACK/studio_audio.py beats assets/music/bed.wav --project .          # writes beats/<audio>.json for Studio
$PY $PACK/studio_audio.py snap --project . --beats beats/assets/music/bed.wav.json --clips "#scene-*"   # preview
$PY $PACK/studio_audio.py snap ... --apply                                 # write; keeps index.html.studio-pack.bak
$PY $PACK/studio_audio.py duck --project . --music "#bed" --voice "#vo-*" --depth 0.3 --apply
```

- `snap` only moves a clip when a beat is within `--max-shift` (0.25 s). Run without `--apply` first and show the
  user the moves. Moving a scene moves everything timed inside it.
- `duck` writes a `data-automation` volume lane (clip-local time, starts at full level). Studio shows and edits it.
  If the bed was already ducked when it was mixed, don't duck it again.
- After any edit: `npx hyperframes check`, then snapshot the frames you moved.

## Smart reframe (landscape footage to vertical or square)

```bash
$PY $PACK/studio_reframe.py talk.mp4 talk.vertical.mp4 --preview talk.box.mp4 --plan talk.plan.json
$PY $PACK/studio_reframe.py talk.mp4 talk.square.mp4 --aspect 1:1          # 9:16 (default), 4:5, 1:1
```

- Follows one subject: a face (OpenCV's YuNet model at `~/video-pipeline/addons/models/face_detection_yunet_2023mar.onnx`,
  which finds faces with sunglasses and helmets; OpenCV's built-in Haar detector if that file is missing), then a
  person, then movement (never a camera pan). With two people it keeps the one it was following, never the gap
  between them. Restarts at every hard cut; holds still while the subject stays within `--dead-zone` (12% of the
  crop), then follows without lag.
- Always make `--preview` (the source with the crop box drawn) and look at frames from it: check the box stays on
  the speaker, especially in two-person shots: it can't know who is talking, so it keeps the biggest face.
- Use it on real footage (talking heads, screen-recorded presenters). For HyperFrames compositions, re-lay out the
  composition at 1080x1920 instead: cropping designed graphics cuts text off.

## Export for platforms (after the final render)

```bash
$PY $PACK/studio_export.py renders/video.mp4 --all                   # youtube, shorts, tiktok, reels, x
$PY $PACK/studio_export.py renders/video.mp4 --platform shorts --fit crop
```

- Specs are in `addons/studio-pack/presets/platforms.json`: size, max length, -14 LUFS, H.264 High + AAC 48 kHz,
  fast start. It refuses a video longer than a platform allows instead of trimming it.
- `--fit`: `blur` (default; whole picture over a blurred copy, best for designed graphics), `smart` (smart
  reframe, best for footage of people), `crop` (fixed centre crop), `pad` (black bars).
- Look at one frame of each vertical output before calling it done.

## Done checks for anything built with this pack

Output measured (loudness, levels, size), a frame looked at, `npx hyperframes check` passing for project edits.
