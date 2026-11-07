#!/usr/bin/env python3
"""Studio Pack audio tools for HyperFrames projects (fork add-on, outside HyperFrames' own code).

  clean   Remove background noise and room hum from a voice recording.
  level   Even out loud and quiet speech, then set the loudness.
  beats   Find the beats in a music file and write them where Studio shows them.
  snap    Move chosen clips' start times onto the nearest beat.
  duck    Dip the music under every voice clip, as a volume lane Studio can edit.

Run with the shared environment: ~/video-pipeline/.ai-venv/Scripts/python.exe studio_audio.py <command> -h
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PACK_DIR = Path(__file__).resolve().parent.parent
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(cmd[:4])} ...\n{proc.stderr[-1500:]}")
    return proc


# --------------------------------------------------------------------- loudness
def measure_loudness(path: str | Path, extra_filter: str = "") -> dict:
    """Integrated loudness (LUFS), true peak and range, from FFmpeg's loudnorm analysis pass."""
    chain = f"{extra_filter}," if extra_filter else ""
    proc = run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-af",
                f"{chain}loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
    blob = proc.stderr[proc.stderr.rindex("{"): proc.stderr.rindex("}") + 1]
    return json.loads(blob)


def loudnorm_filter(path: str | Path, target: float, true_peak: float = -1.5, pre: str = "") -> str:
    """Second-pass loudnorm using the first pass's measurement, so the target is hit exactly."""
    m = measure_loudness(path, pre)
    return (f"loudnorm=I={target}:TP={true_peak}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}"
            f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}"
            ":linear=true")


# ------------------------------------------------------------------------ clean
PIPELINE = Path(os.environ.get("VIDEO_PIPELINE", Path.home() / "video-pipeline"))


def find_deepfilter() -> list[str] | None:
    """The command that runs DeepFilterNet, or None. Prefers the Python package installed from the repo clone
    (~/video-pipeline/.df-venv + addons/DeepFilterNet), with the repo's own DeepFilterNet3 model so nothing is
    downloaded; falls back to a deep-filter program on PATH or in addons/bin."""
    py = PIPELINE / ".df-venv" / "Scripts" / "python.exe"
    model = PIPELINE / "addons" / "DeepFilterNet" / "models" / "DeepFilterNet3"
    if py.exists() and (model / "config.ini").exists():
        return [str(py), "-m", "df.enhance", "-m", str(model)]
    for exe in (shutil.which("deep-filter"), PIPELINE / "addons" / "bin" / "deep-filter.exe"):
        if exe and Path(exe).exists():
            return [str(exe)]
    return None


FFMPEG_CLEAN = {
    # highpass removes rumble, the notches remove mains hum (50 Hz UK + harmonics), afftdn lowers the
    # broadband noise floor while tracking it, and a soft gate quiets the pauses between phrases.
    # Measured on a voice with added pink noise, hum and echo: pauses -44 dB -> about -70 dB (medium),
    # speech level unchanged. afftdn alone plateaus near -50 dB however hard it is pushed.
    "light": "highpass=f=70,bandreject=f=50:width_type=q:w=8,afftdn=nr=15:nf=-45:tn=1,"
             "agate=threshold=0.015:range=0.3:attack=5:release=250",
    "medium": "highpass=f=80,bandreject=f=50:width_type=q:w=8,bandreject=f=100:width_type=q:w=8,"
              "afftdn=nr=25:nf=-40:tn=1,agate=threshold=0.02:range=0.1:attack=5:release=200",
    "strong": "highpass=f=90,bandreject=f=50:width_type=q:w=8,bandreject=f=100:width_type=q:w=8,"
              "bandreject=f=150:width_type=q:w=8,afftdn=nr=30:nf=-40:tn=1,afftdn=nr=20:nf=-50:tn=1,"
              "agate=threshold=0.025:range=0.05:attack=5:release=250",
}


def clean(inp: str, out: str, engine: str = "auto", strength: str = "medium") -> str:
    """Returns the engine used. Output is always 48 kHz WAV."""
    if engine == "auto":
        engine = "deepfilter" if find_deepfilter() else "ffmpeg"
    if engine == "elevenlabs":
        return _clean_elevenlabs(inp, out)
    if engine == "deepfilter":
        command = find_deepfilter()
        if not command:
            raise SystemExit("DeepFilterNet isn't set up (see the Studio Pack README); use --engine ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            wav48 = Path(tmp) / "in.wav"  # DeepFilterNet only takes 48 kHz WAV
            run([FFMPEG, "-y", "-v", "error", "-i", inp, "-ac", "1", "-ar", "48000", str(wav48)])
            run(command + ["-o", str(Path(tmp) / "out"), str(wav48)])
            result = next((Path(tmp) / "out").glob("*.wav"))  # the package appends the model name
            run([FFMPEG, "-y", "-v", "error", "-i", str(result), "-af", "highpass=f=70", "-ar", "48000", out])
        return "deepfilter"
    run([FFMPEG, "-y", "-v", "error", "-i", inp, "-af", FFMPEG_CLEAN[strength], "-ar", "48000", out])
    return "ffmpeg"


def _clean_elevenlabs(inp: str, out: str) -> str:
    """Paid option: ElevenLabs Voice Isolator. Needs ELEVENLABS_API_KEY, which you set yourself."""
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise SystemExit("ELEVENLABS_API_KEY isn't set; the paid option is off. Use --engine ffmpeg (free).")
    import urllib.request
    import uuid
    boundary = uuid.uuid4().hex
    data = Path(inp).read_bytes()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"{Path(inp).name}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request("https://api.elevenlabs.io/v1/audio-isolation", data=body, method="POST",
                                 headers={"xi-api-key": key, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=300) as resp, tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(resp.read())
    run([FFMPEG, "-y", "-v", "error", "-i", f.name, "-ar", "48000", out])
    os.unlink(f.name)
    return "elevenlabs"


# ------------------------------------------------------------------------ level
def level(inp: str, out: str, target: float = -16.0) -> None:
    """speechnorm rides the gain phrase by phrase (quiet words up, loud ones down); loudnorm then sets the
    overall level. -16 LUFS suits a voice that music will sit under; the platform export sets the final mix."""
    pre = "speechnorm=e=12:c=4:r=0.001:f=0.001:l=1"
    run([FFMPEG, "-y", "-v", "error", "-i", inp, "-af", f"{pre},{loudnorm_filter(inp, target, pre=pre)}",
         "-ar", "48000", out])


# ------------------------------------------------------------------------ beats
def detect_beats(audio: str) -> tuple[list[float], list[float], float | None]:
    import librosa
    import numpy as np
    y, sr = librosa.load(audio, sr=22050, mono=True)
    onset = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, frames = librosa.beat.beat_track(onset_envelope=onset, sr=sr, units="frames")
    times = librosa.frames_to_time(frames, sr=sr).tolist()
    if len(frames) == 0:
        return [], [], None
    strength = onset[frames]
    strength = (strength / strength.max()).tolist() if strength.max() > 0 else [0.5] * len(frames)
    bpm = float(np.atleast_1d(tempo)[0]) if len(times) > 1 else None
    return times, strength, bpm


def beat_file(project: Path, audio_rel: str) -> Path:
    return project / "beats" / f"{audio_rel}.json"


def write_beats(project: Path, audio_rel: str, times: list[float], strengths: list[float]) -> Path:
    """Same format as HyperFrames' own `hyperframes beats` (core/src/beats/beatFile.ts), so Studio shows them."""
    beats = [{"time": round(t, 3), "strength": round(min(1.0, max(0.0, s)), 3)} for t, s in zip(times, strengths)]
    path = beat_file(project, audio_rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "audio": audio_rel, "beats": beats}, indent=2) + "\n", encoding="utf-8")
    return path


def read_beats(path: Path) -> list[float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise SystemExit(f"{path}: unknown beat file version")
    return [b["time"] for b in data.get("beats", [])]


# ------------------------------------------------------------- composition I/O
TAG_RE = re.compile(r"<(audio|video|div|section|img)\b[^>]*>", re.I)


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf'\s{re.escape(name)}\s*=\s*(["\'])(.*?)\1', tag, re.S)
    return html.unescape(m.group(2)) if m else None


def _set_attr(tag: str, name: str, value: str) -> str:
    quoted = value.replace("&", "&amp;").replace("'", "&#39;")
    if _attr(tag, name) is not None:
        return re.sub(rf'(\s{re.escape(name)}\s*=\s*)(["\']).*?\2', lambda m: f"{m.group(1)}'{quoted}'", tag, count=1,
                      flags=re.S)
    return re.sub(r"\s*/?>$", lambda m: f" {name}='{quoted}'{m.group(0)}", tag, count=1)


def clips(html_text: str, id_pattern: str) -> list[dict]:
    """Elements whose id matches the pattern (a regex, or a '#id' / '#prefix*' selector)."""
    if id_pattern.startswith("#"):
        id_pattern = "^" + re.escape(id_pattern[1:]).replace(r"\*", ".*") + "$"
    rx = re.compile(id_pattern)
    out = []
    for m in TAG_RE.finditer(html_text):
        tag = m.group(0)
        el_id = _attr(tag, "id")
        start = _attr(tag, "data-start")
        if el_id and rx.search(el_id) and start is not None:
            dur = _attr(tag, "data-duration")
            out.append({"id": el_id, "start": float(start), "duration": float(dur) if dur else None,
                        "span": m.span(), "tag": tag})
    return out


# ------------------------------------------------------------------------- snap
def snap_times(times: list[float], beats: list[float], max_shift: float) -> list[tuple[float, float]]:
    """(original, snapped) per time. A time with no beat within max_shift stays where it is."""
    result = []
    for t in times:
        nearest = min(beats, key=lambda b: abs(b - t)) if beats else t
        result.append((t, round(nearest, 3) if abs(nearest - t) <= max_shift else t))
    return result


# ------------------------------------------------------------------------- duck
def speech_spans(voice: list[dict], merge_gap: float = 0.6) -> list[tuple[float, float]]:
    spans = sorted((c["start"], c["start"] + (c["duration"] or 0)) for c in voice if c["duration"])
    merged: list[list[float]] = []
    for s, e in spans:
        if merged and s - merged[-1][1] <= merge_gap:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def duck_lane(spans, music_start: float, music_end: float, base: float = 1.0, depth: float = 0.3,
              attack: float = 0.15, release: float = 0.4) -> dict:
    """Volume lane in clip-local time. Starts with an explicit full-level point at t=0 so the bed doesn't begin
    already ducked (the mistake HyperFrames' audio docs warn about)."""
    low = round(base * depth, 3)
    pts = [{"t": 0.0, "v": base}]
    for s, e in spans:
        s_local, e_local = s - music_start, e - music_start
        if e_local <= 0 or s_local >= music_end - music_start:
            continue
        down_from = max(0.0, s_local - attack)
        if down_from > pts[-1]["t"]:
            pts.append({"t": round(down_from, 3), "v": base})
        pts.append({"t": round(max(0.0, s_local), 3), "v": low})
        pts.append({"t": round(e_local, 3), "v": low})
        pts.append({"t": round(e_local + release, 3), "v": base})
    # Overlapping ramps (a release running into the next attack) keep the lowest value, in time order.
    cleaned: list[dict] = []
    for p in pts:
        if cleaned and p["t"] <= cleaned[-1]["t"]:
            cleaned[-1]["v"] = min(cleaned[-1]["v"], p["v"])
        else:
            cleaned.append(p)
    return {"version": 1, "lanes": [{"target": "volume", "points": cleaned[:512]}]}


def _composition(project: Path, comp: str | None) -> Path:
    path = project / (comp or "index.html")
    if not path.exists():
        raise SystemExit(f"{path} not found")
    return path


def _write_back(path: Path, text: str, new: str) -> None:
    backup = path.with_suffix(path.suffix + ".studio-pack.bak")
    backup.write_text(text, encoding="utf-8")
    path.write_text(new, encoding="utf-8")
    print(f"wrote {path} (backup: {backup.name})")


# -------------------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("clean", help="remove noise and hum from a voice recording")
    c.add_argument("input"), c.add_argument("output")
    c.add_argument("--engine", choices=["auto", "ffmpeg", "deepfilter", "elevenlabs"], default="auto")
    c.add_argument("--strength", choices=list(FFMPEG_CLEAN), default="medium", help="for the ffmpeg engine")

    lv = sub.add_parser("level", help="even out speech, then set loudness")
    lv.add_argument("input"), lv.add_argument("output")
    lv.add_argument("--target", type=float, default=-16.0, help="LUFS (default -16)")

    b = sub.add_parser("beats", help="detect beats; write beats/<audio>.json in a project")
    b.add_argument("audio", help="audio path relative to --project")
    b.add_argument("--project", default=".")

    s = sub.add_parser("snap", help="move clip starts onto the nearest beat")
    s.add_argument("--project", default=".")
    s.add_argument("--composition")
    s.add_argument("--beats", required=True, help="beat file, e.g. beats/assets/music/bed.wav.json")
    s.add_argument("--clips", required=True, help="id pattern: '#scene-*' or a regex")
    s.add_argument("--max-shift", type=float, default=0.25)
    s.add_argument("--apply", action="store_true", help="write the new starts (a backup is kept)")

    d = sub.add_parser("duck", help="dip the music under the voice clips")
    d.add_argument("--project", default=".")
    d.add_argument("--composition")
    d.add_argument("--music", default="#bed")
    d.add_argument("--voice", default="#vo-*")
    d.add_argument("--depth", type=float, default=0.3, help="music level under speech (0-1, default 0.3)")
    d.add_argument("--attack", type=float, default=0.15)
    d.add_argument("--release", type=float, default=0.4)
    d.add_argument("--apply", action="store_true", help="write data-automation on the music element")

    a = ap.parse_args(argv)
    if a.cmd == "clean":
        used = clean(a.input, a.output, a.engine, a.strength)
        print(f"cleaned with {used}: {a.output}")
    elif a.cmd == "level":
        level(a.input, a.output, a.target)
        print(f"levelled to {a.target} LUFS: {a.output}")
    elif a.cmd == "beats":
        project = Path(a.project).resolve()
        times, strengths, bpm = detect_beats(str(project / a.audio))
        if not times:
            print("no beats found; nothing written")
            return 1
        path = write_beats(project, a.audio.replace("\\", "/"), times, strengths)
        print(f"{len(times)} beats, ~{bpm:.0f} bpm -> {path.relative_to(project)}")
    elif a.cmd == "snap":
        project = Path(a.project).resolve()
        comp = _composition(project, a.composition)
        text = comp.read_text(encoding="utf-8")
        beats = read_beats(project / a.beats)
        found = clips(text, a.clips)
        if not found:
            raise SystemExit(f"no clips match {a.clips}")
        new = text
        for clip, (old, snapped) in sorted(zip(found, snap_times([c["start"] for c in found], beats, a.max_shift)),
                                           key=lambda x: -x[0]["span"][0]):
            print(f"{clip['id']}: {old:.3f}s -> {snapped:.3f}s" + ("" if snapped != old else "  (no beat within reach)"))
            if snapped != old:
                s0, s1 = clip["span"]
                new = new[:s0] + _set_attr(clip["tag"], "data-start", f"{snapped:.3f}") + new[s1:]
        if a.apply and new != text:
            _write_back(comp, text, new)
    elif a.cmd == "duck":
        project = Path(a.project).resolve()
        comp = _composition(project, a.composition)
        text = comp.read_text(encoding="utf-8")
        music = clips(text, a.music)
        if len(music) != 1:
            raise SystemExit(f"expected one music element for {a.music}, found {len(music)}")
        m = music[0]
        spans = speech_spans(clips(text, a.voice))
        lane = duck_lane(spans, m["start"], m["start"] + (m["duration"] or 1e9), depth=a.depth,
                         attack=a.attack, release=a.release)
        print(f"{len(spans)} speech spans; {len(lane['lanes'][0]['points'])} points on {m['id']}")
        print("data-automation='" + json.dumps(lane, separators=(",", ":")) + "'")
        if a.apply:
            s0, s1 = m["span"]
            new = text[:s0] + _set_attr(m["tag"], "data-automation", json.dumps(lane, separators=(",", ":"))) + text[s1:]
            _write_back(comp, text, new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
