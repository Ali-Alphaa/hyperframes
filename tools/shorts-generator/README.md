# Shorts Generator

Cuts a long video into ranked vertical clips. Copied from
[Anil-matcha/AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator)
at commit `13451d4` under its MIT licence (`LICENSE`). The original instructions are in `README.upstream.md`.

Changes in this copy:

- Local mode is the default, so nothing goes to the paid MuAPI service unless you pass `--mode api`.
- Claude picks the highlights, with no API key. See the two routes below.
- Whisper audio is decoded with ffmpeg, because current PyAV breaks faster-whisper's own decoder.
- Whisper finds the GPU through CTranslate2 and the pip-installed CUDA libraries on Windows.
- Clips are H.264, not MPEG-4, so they upload and play everywhere.
- OpenCV is pinned below 5, which removed the face detector the crop uses.
- Each source video gets its own folder in `output/`, so a new run doesn't overwrite earlier clips.

## Setup

```bash
pip install -r requirements-local.txt
```

On this computer, use `~/video-pipeline/.venv`, which already has everything.

## Route 1: Claude in a session (works now)

```bash
python main.py video.mp4 --transcript-only
python main.py video.mp4 --highlights-file highlights.json --num-clips 3
```

The first command writes `output/<video>/transcript.json`. Claude reads it and writes `highlights.json`:

```json
{
  "highlights": [
    {
      "title": "...",
      "start_time": 12.0,
      "end_time": 58.5,
      "score": 90,
      "hook_sentence": "...",
      "virality_reason": "..."
    }
  ]
}
```

The second command cuts the clips.

## Route 2: one command

```bash
python main.py video.mp4 --num-clips 3
```

This calls the `claude` command with all its tools switched off, because transcripts are untrusted text. It needs
Claude Code logged in: run `claude`, then `/login`. `LLM_PROVIDER=openai` or `gemini` also still work, with a key.

## Known limits

- The crop follows the largest face. A video with no face gets a fixed centre crop, which cuts off wide charts and
  slides, so it suits talking heads and not screen-wide graphics.
- Whisper's segment times are approximate, so check where each clip starts and ends.
- Only download videos you have the right to use.
- `.env` and `output/` stay out of git. The original project's open security issues about its paid API mode and
  ffmpeg argument handling were not all addressed here.
