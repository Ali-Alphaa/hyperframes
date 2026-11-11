# Studio Pack

A fork add-on that brings DaVinci Resolve 21-style finishing tools to HyperFrames, without touching HyperFrames'
own code. Everything lives in this folder, so merging HeyGen's updates never conflicts with it. The plan behind it
is the "HyperFrames Studio Pack plan" doc.

| Batch             | Status | What                                                                                                  |
| ----------------- | ------ | ----------------------------------------------------------------------------------------------------- |
| 1 Audio + presets | done   | `clean`, `level`, `beats`, `snap`, `duck`, platform export                                            |
| 2 Footage editing | done   | smart reframe; DeepFilterNet voice cleanup (text cuts and silence removal already exist in media-use) |
| 3 Fixes + color   | done   | stabilise, slow-motion, denoise, auto-correct, shot matching                                          |
| 4 Motion blocks   | done   | audio-reactive, Lottie, picture-in-picture, loop easing                                               |
| 5 Voice cloning   | done   | local clone of your own voice                                                                         |
| 6 Footage search  | done   | index a folder (faster-whisper + CLIP), search spoken or visual, contact sheet, export                |
| 7 Publishing      | done   | Postiz drafts, approve/cancel with a log, Buffer route for TikTok and Instagram                       |
| 8 Repo add-ons    | done   | AutoClip highlights bridge; render checks (word diff, A/V length, flicker) from claude-youtube-editor |

## Setup

```bash
uv venv --python 3.12 ~/video-pipeline/.ai-venv
uv pip install --python ~/video-pipeline/.ai-venv/Scripts/python.exe numpy soundfile librosa pyloudnorm pytest
cp -r addons/studio-pack/skill ~/.claude/skills/studio-pack     # so Claude uses it
```

FFmpeg must be on PATH. For smart reframe:

```bash
uv pip install --python ~/video-pipeline/.ai-venv/Scripts/python.exe "opencv-python-headless<5"
# optional but much better face finding (sunglasses, helmets, turned heads): OpenCV's YuNet model, 230 KB
curl -L -o ~/video-pipeline/addons/models/face_detection_yunet_2023mar.onnx   https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
```

Tested on public-domain and CC-BY footage (credits in `footage/samples/CREDITS.md`): on an outdoor army interview,
YuNet found faces in 138 of 201 looks, against 46 with the built-in detector.

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

Batch 3's GPU tools (they run through Vulkan, so they don't need PyTorch):

```bash
uv pip install --python ~/video-pipeline/.ai-venv/Scripts/python.exe realesrgan-ncnn-py rife-ncnn-vulkan-python-tntwise "opencv-python>=4.8,<5"
# RIFE ships without models: fetch rife-v4.6 (flownet.param, flownet.bin) from
# https://github.com/TNTwise/rife-ncnn-vulkan/tree/master/models/rife-v4.6 into
# ~/video-pipeline/.ai-venv/Lib/site-packages/rife_ncnn_vulkan_python/models/rife-v4.6/
```

Batch 5's voice cloning runs in its own environment (Chatterbox pins PyTorch 2.6 and NumPy 1); the model
(about 3 GB) downloads from Hugging Face on first use:

```bash
UV_PYTHON_INSTALL_DIR=~/video-pipeline/.python uv venv --python 3.11 ~/video-pipeline/.voice-venv
uv pip install --python ~/video-pipeline/.voice-venv/Scripts/python.exe torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
uv pip install --python ~/video-pipeline/.voice-venv/Scripts/python.exe chatterbox-tts resemblyzer soundfile
```

F5-TTS (the engine that keeps the user's accent) has its own environment too; its model (about 1.3 GB) downloads
from Hugging Face on first use, and enrolment transcribes with faster-whisper in `~/video-pipeline/.venv`:

```bash
UV_PYTHON_INSTALL_DIR=~/video-pipeline/.python uv venv --python 3.11 ~/video-pipeline/.f5-venv
uv pip install --python ~/video-pipeline/.f5-venv/Scripts/python.exe torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
uv pip install --python ~/video-pipeline/.f5-venv/Scripts/python.exe f5-tts soundfile
```

Keep OpenCV below 5 (Real-ESRGAN's package pulls in the newest, and OpenCV 5 dropped the face detector reframe
falls back on).

AutoClip (batch 8) runs from a clone in its own environment; its prompts pick moments with a language model
(local Ollama, or Gemini with the user's `GEMINI_API_KEY`):

```bash
git clone --depth 1 https://github.com/zhouxiaoka/autoclip.git ~/video-pipeline/addons/autoclip
UV_PYTHON_INSTALL_DIR=~/video-pipeline/.python uv venv --python 3.11 ~/video-pipeline/.autoclip-venv
uv pip install --python ~/video-pipeline/.autoclip-venv/Scripts/python.exe -r ~/video-pipeline/addons/autoclip/requirements.txt
```

`tools/scan_flicker.py` is vendored unchanged from https://github.com/hassancs91/claude-youtube-editor (MIT).

## Tests

```bash
~/video-pipeline/.ai-venv/Scripts/python.exe -m pytest addons/studio-pack/tests -q
```

Usage is in `skill/SKILL.md`.

## Publishing setup

The `postiz` command-line tool (`npm install -g postiz`) and two user settings the user saves themselves:
`POSTIZ_API_KEY` (Postiz: Settings, Public API) and `POSTIZ_API_URL` (a self-hosted Postiz serves its API under
`/api`, e.g. `https://postiz.example.com/api`). `studio_publish.py` reads them from the environment or the Windows
user settings and never prints the key.
