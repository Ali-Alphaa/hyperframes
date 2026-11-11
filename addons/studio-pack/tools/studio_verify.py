"""Checks a finished render, after the fact (ideas from github.com/hassancs91/claude-youtube-editor, MIT).

    studio_verify.py words RENDER --expect script.txt   re-transcribe the render, diff against the intended words
    studio_verify.py av RENDER                          audio and video lengths agree
    studio_verify.py flicker RENDER [--crops DIR]       1-2 frame blinks (vendored scan_flicker.py)
    studio_verify.py all RENDER [--expect script.txt]   all of the above

All advisory: they say WHERE to look or listen. Exit code 1 when something is flagged."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
WHISPER_PY = Path.home() / "video-pipeline" / ".venv" / "Scripts" / "python.exe"
GAP = 0.6           # a pause this long inside speech is worth a listen
LOW_CONFIDENCE = 0.5
AV_TOLERANCE = 0.1  # seconds


def norm(text: str) -> list[str]:
    """Comparable spoken words: lower case, digits kept, punctuation dropped, '.env' spoken as 'dot env'."""
    t = text.lower()
    t = re.sub(r"/(?=[a-z0-9])", " slash ", t)
    t = re.sub(r"\.(?=[a-z])", " dot ", t)
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return t.split()


def clock(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:05.2f}"


def diff_words(expected: list[str], heard: list[dict]) -> list[dict]:
    """Inserted, missing and replaced words between the script and what the render says, with render times."""
    flat, owner = [], []
    for i, w in enumerate(heard):
        for piece in norm(w["word"]):
            flat.append(piece)
            owner.append(i)
    issues = []
    for op, a0, a1, b0, b1 in SequenceMatcher(None, expected, flat, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        at = heard[owner[b0]]["start"] if b0 < len(owner) else (heard[-1]["end"] if heard else 0.0)
        issues.append({"kind": {"insert": "extra", "delete": "missing", "replace": "changed"}[op], "at": at,
                       "expected": " ".join(expected[a0:a1]), "heard": " ".join(flat[b0:b1])})
    return issues


def gaps(heard: list[dict], gap: float = GAP) -> list[dict]:
    return [{"at": a["end"], "pause": round(b["start"] - a["end"], 2), "between": f"{a['word']} ... {b['word']}"}
            for a, b in zip(heard, heard[1:]) if b["start"] - a["end"] >= gap]


def transcribe_words(render: Path) -> list[dict]:
    out = subprocess.run([str(WHISPER_PY), str(TOOLS / "transcribe_worker.py"), str(render), "--words"],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def check_words(render: Path, expect: Path) -> dict:
    heard = transcribe_words(render)
    issues = diff_words(norm(expect.read_text(encoding="utf-8")), heard)
    unclear = [{"at": w["start"], "word": w["word"], "p": w["p"]} for w in heard if w["p"] < LOW_CONFIDENCE]
    return {"words_heard": len(heard), "issues": issues, "pauses": gaps(heard), "unclear": unclear,
            "flagged": bool(issues)}


def stream_seconds(render: Path, kind: str) -> float | None:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", f"{kind}:0", "-show_entries",
                          "stream=duration:format=duration", "-of", "json", str(render)],
                         capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    streams = data.get("streams") or []
    if not streams:
        return None
    d = streams[0].get("duration") or data.get("format", {}).get("duration")
    return float(d) if d else None


def check_av(render: Path) -> dict:
    video, audio = stream_seconds(render, "v"), stream_seconds(render, "a")
    if video is None or audio is None:
        return {"video": video, "audio": audio, "flagged": video is None, "note": "no audio stream" if audio is None else ""}
    return {"video": round(video, 3), "audio": round(audio, 3), "difference": round(audio - video, 3),
            "flagged": abs(audio - video) > AV_TOLERANCE}


def check_flicker(render: Path, crops: Path | None) -> dict:
    report = render.with_suffix(".flicker.json")
    cmd = [sys.executable, str(TOOLS / "scan_flicker.py"), str(render), "--json", str(report)]
    if crops:
        cmd += ["--crops", str(crops)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    events = json.loads(report.read_text()) if report.exists() else []
    return {"events": events, "flagged": proc.returncode != 0, "report": str(report)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("check", choices=["words", "av", "flicker", "all"])
    ap.add_argument("render", type=Path)
    ap.add_argument("--expect", type=Path, help="text file with the words the render should say")
    ap.add_argument("--crops", type=Path, help="flicker: save a 4-frame strip per event here")
    a = ap.parse_args()
    if a.check == "words" and not a.expect:
        ap.error("words needs --expect")
    result = {}
    if a.check in ("words", "all") and a.expect:
        result["words"] = check_words(a.render, a.expect)
    if a.check in ("av", "all"):
        result["av"] = check_av(a.render)
    if a.check in ("flicker", "all"):
        result["flicker"] = check_flicker(a.render, a.crops)
    print(json.dumps(result, indent=2))
    for name, r in result.items():
        if name == "words":
            for i in r["issues"]:
                print(f"  {clock(i['at'])} {i['kind']}: expected '{i['expected']}', heard '{i['heard']}'", file=sys.stderr)
    sys.exit(1 if any(r.get("flagged") for r in result.values()) else 0)


if __name__ == "__main__":
    main()
