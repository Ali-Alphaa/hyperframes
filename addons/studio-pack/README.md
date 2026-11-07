# Studio Pack

A fork add-on that brings DaVinci Resolve 21-style finishing tools to HyperFrames, without touching HyperFrames'
own code. Everything lives in this folder, so merging HeyGen's updates never conflicts with it. The plan behind it
is the "HyperFrames Studio Pack plan" doc.

| Batch             | Status  | What                                                                                                  |
| ----------------- | ------- | ----------------------------------------------------------------------------------------------------- |
| 1 Audio + presets | done    | `clean`, `level`, `beats`, `snap`, `duck`, platform export                                            |
| 2 Footage editing | done    | smart reframe; DeepFilterNet voice cleanup (text cuts and silence removal already exist in media-use) |
| 3 Fixes + color   | planned | stabilise, slow-motion, denoise, auto-correct, shot matching                                          |
| 4 Motion blocks   | planned | audio-reactive, Lottie, picture-in-picture, loop easing                                               |
| 5 Voice cloning   | planned | local clone of your own voice                                                                         |
| 6 Footage search  | planned | find clips by what is said or shown                                                                   |
| 7 Publishing      | planned | Postiz drafts, Buffer backup, approval per post                                                       |

## Setup

```bash
uv venv --python 3.12 ~/video-pipeline/.ai-venv
uv pip install --python ~/video-pipeline/.ai-venv/Scripts/python.exe numpy soundfile librosa pyloudnorm pytest
cp -r addons/studio-pack/skill ~/.claude/skills/studio-pack     # so Claude uses it
```

FFmpeg must be on PATH. For smart reframe: `uv pip install --python ~/video-pipeline/.ai-venv/Scripts/python.exe "opencv-python-headless<5"`.

DeepFilterNet (better voice cleanup) runs from a clone of https://github.com/Rikorose/DeepFilterNet in its own
environment, because it needs NumPy 1 and a ready-made `deepfilterlib`, which exists only up to Python 3.11:

```bash
git clone --depth 1 https://github.com/Rikorose/DeepFilterNet.git ~/video-pipeline/addons/DeepFilterNet
cd ~/video-pipeline/addons/DeepFilterNet/models && unzip DeepFilterNet3.zip && cd ~/video-pipeline
uv venv --python 3.11 .df-venv
uv pip install --python .df-venv/Scripts/python.exe torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .df-venv/Scripts/python.exe "numpy<2" deepfilterlib==0.5.6 loguru appdirs requests "packaging<25" sympy "soundfile<0.13"
uv pip install --python .df-venv/Scripts/python.exe --no-deps -e addons/DeepFilterNet/DeepFilterNet
```

Building the repo's Rust `deep-filter` program also works, but needs Visual Studio's C++ build tools.

## Tests

```bash
~/video-pipeline/.ai-venv/Scripts/python.exe -m pytest addons/studio-pack/tests -q
```

Usage is in `skill/SKILL.md`.
