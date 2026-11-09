---
name: studio-pack
description: Fork add-on with DaVinci Resolve-style finishing tools for HyperFrames projects. Use for voice cleanup (noise, hum, echo), dialogue leveling, beat detection when `hyperframes beats` finds none, snapping cuts to beats, auto-ducking music under voice clips, and exporting one render to YouTube, Shorts, TikTok, Reels and X specs. Use before publishing any video.
---

# Studio Pack (batches 1-5: audio, export, reframe, footage fixes, colour, motion blocks, voice)

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

## Footage fixes (camera footage and screen recordings, before they go into a project)

```bash
$PY $PACK/studio_footage.py stabilise shaky.mp4 steady.mp4            # --strength light|medium|strong
$PY $PACK/studio_footage.py denoise grainy.mp4 clean.mp4              # --strength light|medium|strong
$PY $PACK/studio_footage.py slowmo clip.mp4 slow.mp4 --factor 4       # 4x slower, sound dropped
$PY $PACK/studio_footage.py slowmo clip.mp4 smooth.mp4 --factor 2 --mode smooth   # 2x frame rate, sound kept
$PY $PACK/studio_footage.py upscale small.mp4 big.mp4 --scale 2       # --model photo (camera) | fast (screens, graphics)
```

- Stabilise zooms in to hide moving edges (about 25% on strong shake): tell the user, and frame titles with that
  margin. It doesn't fix motion blur.
- Denoise averages matching pixels across neighbouring frames: medium is right for most footage; check a frame
  with fast motion for trails.
- Slow motion uses RIFE on the GPU (FFmpeg minterpolate if RIFE is missing). It invents frames: check fast,
  overlapping motion (hands, spinning objects) for warping.
- Upscale "photo" (Real-ESRGAN x4plus) is sharpest but slow (~1 s per 480x270 frame) and can make skin look
  plastic; "fast" suits screen recordings and graphics. It can't recover detail that isn't there. Topaz Video AI
  (paid) runs with `--engine topaz` only if the user has installed it.
- GPU tools exit through TerminateProcess on Windows (their Vulkan teardown crashes); outputs are complete.

## Colour for footage files

```bash
$PY $PACK/studio_color.py autocorrect clip.mp4 fixed.mp4 --save-lut fixed.cube
$PY $PACK/studio_color.py match b-cam.mp4 a-cam.mp4 b-matched.mp4 --save-lut b-to-a.cube
$PY $PACK/studio_color.py lut clip.mp4 look.mp4 --cube film.cube --amount 0.6
```

- autocorrect neutralises a colour cast but keeps 30% of the scene's own colour, and nudges exposure/contrast
  within limits. match moves a clip onto a reference clip's colour. Both print Lab numbers before and after:
  report them. Run per shot: a clip with very different shots gets one average correction.
- The saved `.cube` works in Resolve and other editors too.

## Motion blocks (installed into a project, then mounted in index.html)

```bash
$PY $PACK/studio_motion.py list
$PY $PACK/studio_motion.py add audio-bars --project . --audio assets/music/bed.wav --as music-bars
$PY $PACK/studio_motion.py add audio-pulse --project . --audio assets/vo/line.wav --as voice-pulse --offset 0
$PY $PACK/studio_motion.py add picture-in-picture --project . --src assets/clips/cam.mp4 --as cam
$PY $PACK/studio_motion.py add lottie-player --project . --lottie assets/icon.json --as icon --loop --duration 6
$PY $PACK/studio_motion.py add studio-loops --project .
```

- `add` writes `compositions/<id>.html` and prints the mount `<div>` to paste inside the root. Set its
  `data-start` (and `data-duration`), its `data-track-index`, and options with `data-variable-values='{...}'`
  (each block's variables are in its `blocks/<name>/registry-item.json`). Use `--as` to add a block twice.
- Reactive blocks (`audio-bars`, `audio-pulse`) carry the audio's measured levels inside them, so renders match
  the preview. They are tied to the audio's timing: mount them at the same `data-start` as the audio element,
  or pass `--offset` = seconds into the audio where the block starts. Re-run `add` if the audio changes.
- `picture-in-picture` is muted (webcam/screen recordings usually have separate sound); remove `muted` and add
  `data-has-audio="true"` in the installed file to keep its sound.
- `lottie-player` builds the Lottie JSON in (no fetch at render). Modes: hold, loop, ping-pong (variable `mode`).
- Loop and ping-pong motion on anything (Resolve 21's loop/ping-pong keyframe modes): load
  `compositions/lib/studio-loops.js` in the root `<head>`, then
  `StudioLoops.add(tl, "#el", fromVars, toVars, { start, end, cycle, mode: "loop"|"pingpong"|"relative", ease })`.
  Never use `repeat: -1` in a HyperFrames timeline: it makes the timeline endless; this adds finite repeats.
- These blocks don't appear in Studio's Catalog tab (that needs changes to HyperFrames' code); Claude installs them.
- After mounting: `npx hyperframes check`, then snapshot the frames where the blocks are on screen.

## Voice: the user's own cloned voice

```bash
$PY $PACK/studio_voice.py record                                    # interactive: level test, then record (user runs it)
$PY $PACK/studio_voice.py record --list                             # microphones (the Rode shows as "Jack Mic")
$PY $PACK/studio_voice.py check recording.m4a                       # level, clipping, noise, length
$PY $PACK/studio_voice.py enrol ali recording.m4a --consent "This is my own voice"
$PY $PACK/studio_voice.py say ali "A pullback is not a reversal." line.wav
$PY $PACK/studio_voice.py script ali script.txt --out-dir assets/vo/ali   # one file per paragraph + manifest.json
$PY $PACK/studio_voice.py script kokoro script.txt --out-dir assets/vo/stock --kokoro-voice bm_george
```

- Only the user's own voice, or a voice whose owner agreed in writing. `enrol` requires `--consent`, stored with
  the voice; never enrol a voice from a video, a public figure or anyone else on the user's say-so alone.
- Kokoro stays the default for videos unless the user asks for their own voice.
- The user records with the "Record voice sample" shortcut on their Desktop (it opens the script and runs
  `record`, which saves to Desktop/video-pipeline/recordings/). Their Rode plugs into the jack and appears as
  "Jack Mic (Realtek(R) Audio)".
- Speed: about 10 s a line plus ~20 s loading, on mains power. On battery the GPU is capped near 300 MHz and each
  second of speech takes ~14 s: check `nvidia-smi` clocks and ask the user to plug in before a long script.
- The user's voice is `ali`, on F5-TTS at 151 words a minute (chosen by ear). F5-TTS continues from a short clip
  of their own speech and keeps their accent; Chatterbox drifted toward an American accent, so don't switch back.
  F5-TTS runs in `~/video-pipeline/.f5-venv`; Chatterbox stays available with `--engine chatterbox`.
- `set NAME --f5-from recording.wav` adds F5-TTS to a voice: it transcribes the recording and picks 6-11 s of
  whole sentences as the reference. `set NAME --pace 151` sets words per minute; every script is stretched as a
  whole to that pace with pitch kept (rubberband). `--pace 0` on say/script leaves the pace as generated.
- To enrol a new voice with F5-TTS in one go: `enrol NAME rec.wav --consent "..." --no-clean --f5` (a good mic in a
  quiet room clones better without cleanup).
- Lines come out trimmed, 48 kHz, -16 LUFS: ready for `studio_audio.py duck` and the platform export.
  `manifest.json` lists each line's duration for laying out `data-start` / `data-duration`.
- Check a cloned line before using it: transcribe it (faster-whisper) and compare with the text; regenerate any
  line with skipped or garbled words (change `--cfg` slightly to get a different take).
- ElevenLabs (paid): `--engine elevenlabs` only when the user has set `ELEVENLABS_API_KEY` and
  `ELEVENLABS_VOICE_ID` (a voice they made in their ElevenLabs account). This tool never uploads a voice sample.

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
