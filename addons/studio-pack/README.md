# Studio Pack

A fork add-on that brings DaVinci Resolve 21-style finishing tools to HyperFrames, without touching HyperFrames'
own code. Everything lives in this folder, so merging HeyGen's updates never conflicts with it. The plan behind it
is the "HyperFrames Studio Pack plan" doc.

| Batch             | Status  | What                                                                     |
| ----------------- | ------- | ------------------------------------------------------------------------ |
| 1 Audio + presets | done    | `clean`, `level`, `beats`, `snap`, `duck`, platform export               |
| 2 Footage editing | planned | smart reframe (text cuts and silence removal already exist in media-use) |
| 3 Fixes + color   | planned | stabilise, slow-motion, denoise, auto-correct, shot matching             |
| 4 Motion blocks   | planned | audio-reactive, Lottie, picture-in-picture, loop easing                  |
| 5 Voice cloning   | planned | local clone of your own voice                                            |
| 6 Footage search  | planned | find clips by what is said or shown                                      |
| 7 Publishing      | planned | Postiz drafts, Buffer backup, approval per post                          |

## Setup

```bash
uv venv --python 3.12 ~/video-pipeline/.ai-venv
uv pip install --python ~/video-pipeline/.ai-venv/Scripts/python.exe numpy soundfile librosa pyloudnorm pytest
cp -r addons/studio-pack/skill ~/.claude/skills/studio-pack     # so Claude uses it
```

FFmpeg must be on PATH. Optional: DeepFilterNet's `deep-filter.exe` (from its GitHub releases) in
`~/video-pipeline/addons/bin` makes `clean` use it instead of FFmpeg's filters.

## Tests

```bash
~/video-pipeline/.ai-venv/Scripts/python.exe -m pytest addons/studio-pack/tests -q
```

Usage is in `skill/SKILL.md`.
