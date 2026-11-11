"""Long video -> scored highlight clips, using AutoClip (github.com/zhouxiaoka/autoclip, a clone in
~/video-pipeline/addons/autoclip with its own environment ~/video-pipeline/.autoclip-venv).

    studio_highlights.py VIDEO [--category knowledge] [--min-score 0.7] [--provider gemini|ollama|...] [--model M]

Subtitles come from this pack's faster-whisper worker, so AutoClip never installs its own Whisper. Picking the
moments needs a language model: a local Ollama when one is running, else Gemini with the user's GEMINI_API_KEY
(Google's free tier); the key is passed to AutoClip in its environment and never printed. Prints AutoClip's JSON:
clips with start/end, score, reason and file path, plus themed collections."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

PIPELINE = Path.home() / "video-pipeline"
AUTOCLIP = PIPELINE / "addons" / "autoclip"
AUTOCLIP_PY = PIPELINE / ".autoclip-venv" / "Scripts" / "python.exe"
WHISPER_PY = PIPELINE / ".venv" / "Scripts" / "python.exe"
WORKER = Path(__file__).resolve().parent / "transcribe_worker.py"
DATA = PIPELINE / "autoclip-data"
OLLAMA = "http://localhost:11434/api/tags"


def user_setting(name: str) -> str | None:
    """An environment variable, or the same name from the Windows user settings (set after this shell started)."""
    if os.environ.get(name):
        return os.environ[name]
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0]) or None
    except (ImportError, OSError):
        return None


def srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(segments: list[dict]) -> str:
    blocks = [f"{i}\n{srt_time(seg['start'])} --> {srt_time(seg['end'])}\n{seg['text']}\n"
              for i, seg in enumerate((s for s in segments if s["text"].strip()), 1)]
    return "\n".join(blocks)


def transcribe(video: Path, srt: Path) -> Path:
    if srt.exists():
        return srt
    out = subprocess.run([str(WHISPER_PY), str(WORKER), str(video)], capture_output=True, text=True, check=True)
    srt.write_text(to_srt(json.loads(out.stdout)), encoding="utf-8")
    return srt


def ollama_running() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA, timeout=2) as reply:
            return reply.status == 200
    except OSError:
        return False


def pick_provider(provider: str | None, model: str | None) -> tuple[list[str], dict]:
    """AutoClip arguments and extra environment for the model that picks the moments."""
    env: dict[str, str] = {}
    if provider is None:
        provider = "ollama" if ollama_running() else ("gemini" if user_setting("GEMINI_API_KEY") else None)
    if provider is None:
        sys.exit("No model to pick highlights with. Either start Ollama (local, free) or save a free Gemini key as "
                 "the Windows user setting GEMINI_API_KEY (https://aistudio.google.com/apikey).")
    if provider == "gemini":
        key = user_setting("GEMINI_API_KEY")
        if not key:
            sys.exit("GEMINI_API_KEY isn't set. The user saves it themselves.")
        env["AUTOCLIP_API_KEY"] = key
        model = model or "gemini-2.5-flash"
    elif provider not in ("ollama", "lmstudio"):
        key = user_setting("AUTOCLIP_API_KEY")
        if not key:
            sys.exit(f"{provider} needs its key saved as the Windows user setting AUTOCLIP_API_KEY.")
        env["AUTOCLIP_API_KEY"] = key
    args = ["--provider", provider] + (["--model", model] if model else [])
    return args, env


def run(video: Path, category: str, min_score: float, provider: str | None, model: str | None) -> dict:
    model_args, extra_env = pick_provider(provider, model)
    srt = transcribe(video, video.with_suffix(".srt"))
    env = {**os.environ, **extra_env, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "AUTOCLIP_DATA_DIR": str(DATA)}
    cmd = [str(AUTOCLIP_PY), "-m", "backend.cli", "run", str(video), "--srt", str(srt), "--category", category,
           "--min-score", str(min_score), "--copy", "--no-db", "--json", *model_args]
    proc = subprocess.run(cmd, cwd=AUTOCLIP, env=env, capture_output=True, text=True, encoding="utf-8")
    start = proc.stdout.find("{")
    if start < 0:
        sys.exit(f"AutoClip failed:\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout[start:])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("--category", default="knowledge",
                    choices=["default", "knowledge", "business", "opinion", "experience", "speech", "content_review",
                             "entertainment"])
    ap.add_argument("--min-score", type=float, default=0.7)
    ap.add_argument("--provider")
    ap.add_argument("--model")
    a = ap.parse_args()
    result = run(a.video.resolve(), a.category, a.min_score, a.provider, a.model)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
