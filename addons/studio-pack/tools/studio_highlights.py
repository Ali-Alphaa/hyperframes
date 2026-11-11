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
import shutil
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
        model = model or "gemini-3.5-flash"  # 2.5-flash is closed to new keys
    elif provider not in ("ollama", "lmstudio"):
        key = user_setting("AUTOCLIP_API_KEY")
        if not key:
            sys.exit(f"{provider} needs its key saved as the Windows user setting AUTOCLIP_API_KEY.")
        env["AUTOCLIP_API_KEY"] = key
    args = ["--provider", provider] + (["--model", model] if model else [])
    return args, env


def forget_keys(settings: Path) -> None:
    """AutoClip writes the key into cli-settings.json in plain text to start its model client; blank it afterwards."""
    if not settings.exists():
        return
    data = json.loads(settings.read_text(encoding="utf-8"))
    data.update({k: "" for k in data if k.endswith("_api_key")})
    settings.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run(video: Path, category: str, min_score: float, provider: str | None, model: str | None) -> dict:
    model_args, extra_env = pick_provider(provider, model)
    srt = transcribe(video, video.with_suffix(".srt"))
    env = {**os.environ, **extra_env, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "AUTOCLIP_DATA_DIR": str(DATA)}
    cmd = [str(AUTOCLIP_PY), "-m", "backend.cli", "run", str(video), "--srt", str(srt), "--category", category,
           "--min-score", str(min_score), "--copy", "--no-db", "--json", *model_args]
    try:
        proc = subprocess.run(cmd, cwd=AUTOCLIP, env=env, capture_output=True, text=True, encoding="utf-8")
    finally:
        forget_keys(DATA / "cli-settings.json")
    start = proc.stdout.find("{")
    if start < 0:
        sys.exit(f"AutoClip failed:\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout[start:])


def srt_seconds(stamp: str) -> float:
    h, m, rest = stamp.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def spoken_between(srt: Path, start: float, end: float) -> str:
    """The subtitle text inside [start, end], for writing titles in the video's own language."""
    lines = []
    for block in srt.read_text(encoding="utf-8").strip().split("\n\n"):
        parts = block.splitlines()
        if len(parts) >= 3 and " --> " in parts[1]:
            a, b = (srt_seconds(t) for t in parts[1].split(" --> "))
            if a >= start - 0.05 and b <= end + 0.05:
                lines.append(" ".join(parts[2:]))
    return " ".join(lines)


def collect(result: dict, video: Path) -> Path:
    """Copies the clips next to the video as <video>-highlights/NN-scoreSS-MmSSs.mp4, best first, with highlights.json
    (times, score, AutoClip's own title and reason, which may be Chinese, and the English transcript of each clip)."""
    out = video.parent / f"{video.stem}-highlights"
    out.mkdir(exist_ok=True)
    srt = video.with_suffix(".srt")
    rows = []
    clips = sorted(result.get("clips", []), key=lambda c: -c.get("score", 0))
    for n, clip in enumerate(clips, 1):
        start, end = srt_seconds(clip["start_time"]), srt_seconds(clip["end_time"])
        name = f"{n:02d}-score{clip.get('score_100', 0)}-{int(start // 60)}m{int(start % 60):02d}s.mp4"
        if clip.get("file") and Path(clip["file"]).exists():
            shutil.copy2(clip["file"], out / name)
        rows.append({"file": name, "start": start, "end": end, "seconds": round(end - start, 1),
                     "score": clip.get("score"), "spoken": spoken_between(srt, start, end) if srt.exists() else "",
                     "autoclip_title": clip.get("title"), "autoclip_reason": clip.get("reason"),
                     "id": clip.get("id")})
    groups = [{"title": c.get("title"), "summary": c.get("summary"), "clip_ids": c.get("clip_ids")}
              for c in result.get("collections", [])]
    (out / "highlights.json").write_text(json.dumps({"video": str(video), "clips": rows, "collections": groups},
                                                    ensure_ascii=False, indent=2), encoding="utf-8")
    return out


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
    sys.stdout.reconfigure(encoding="utf-8")  # AutoClip's titles can be Chinese; the Windows console code page can't print them
    result = run(a.video.resolve(), a.category, a.min_score, a.provider, a.model)
    if result.get("ok"):
        result["highlights_dir"] = str(collect(result, a.video.resolve()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
