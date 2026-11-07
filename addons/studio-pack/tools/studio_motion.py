#!/usr/bin/env python3
"""Studio Pack motion blocks: install them into a HyperFrames project, and measure audio for the reactive ones.

  envelope  measure an audio file's loudness (overall and per frequency band) at the video's frame rate
  list      show the blocks
  add       copy a block into a project, with its data built in, and print the mount to paste into index.html

The blocks live in addons/studio-pack/blocks/ in HyperFrames' own registry-item format. They aren't in Studio's
Catalog tab (that would need changes to HyperFrames' code); Claude installs them with `add`.

Reactive blocks don't listen to sound while playing: renders must be identical every time, so the audio is
measured once, here, and the numbers are written into the block. Moving the audio means re-running `add`.

  python studio_motion.py add audio-bars --project . --audio assets/music/bed.wav --as music-bars
  python studio_motion.py add picture-in-picture --project . --src assets/screen.mp4 --as demo-pip
  python studio_motion.py add lottie-player --project . --lottie assets/icon.json --as icon --loop
  python studio_motion.py add studio-loops --project .
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

BLOCKS = Path(__file__).resolve().parent.parent / "blocks"


# --------------------------------------------------------------------- envelope
def envelope(audio: str, fps: float = 30.0, bands: int = 24, attack: float = 0.5, release: float = 0.15) -> dict:
    """Per-frame levels, 0-1. `level` is overall loudness; `bands` are log-spaced frequency bands (bass first).
    Rises follow the sound quickly (attack) and falls ease off (release), like a VU meter, so bars don't flicker."""
    import librosa
    import numpy as np
    y, sr = librosa.load(audio, sr=22050, mono=True)
    hop = int(round(sr / fps))
    rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop, center=True)[0]
    mel = librosa.feature.melspectrogram(y=y, sr=sr, n_fft=2048, hop_length=hop, n_mels=bands, fmin=40, fmax=8000)
    mel_db = librosa.power_to_db(mel, ref=np.max)

    def norm(x, lo_pct=5, hi_pct=99):
        lo, hi = np.percentile(x, lo_pct), np.percentile(x, hi_pct)
        return np.clip((x - lo) / max(hi - lo, 1e-9), 0, 1)

    def smooth(x):
        out, v = np.empty_like(x), 0.0
        for i, s in enumerate(x):
            v += (s - v) * (attack if s > v else release)
            out[i] = v
        return out

    level = smooth(norm(20 * np.log10(rms + 1e-9), 10, 99.5))
    band_levels = np.array([smooth(norm(row)) for row in mel_db])  # bands x frames
    frames = min(len(level), band_levels.shape[1])
    return {"version": 1, "audio": Path(audio).name, "fps": fps, "duration": round(frames / fps, 3),
            "level": [round(float(v), 2) for v in level[:frames]],
            "bands": [[round(float(v), 2) for v in band_levels[:, i]] for i in range(frames)]}


# ------------------------------------------------------------------------ blocks
def blocks() -> dict[str, dict]:
    out = {}
    for item in sorted(BLOCKS.glob("*/registry-item.json")):
        meta = json.loads(item.read_text(encoding="utf-8"))
        meta["_dir"] = item.parent
        out[meta["name"]] = meta
    return out


def _probe_duration(path: Path) -> float | None:
    from studio_export import probe
    try:
        return probe(path)["duration"]
    except Exception:  # noqa: BLE001 - not a media file
        return None


def add(name: str, project: Path, as_id: str | None = None, audio: str | None = None, src: str | None = None,
        lottie: str | None = None, duration: float | None = None, offset: float = 0.0, loop: bool = False,
        fps: float = 30.0) -> str:
    """Install a block; returns the mount snippet."""
    meta = blocks().get(name)
    if not meta:
        raise SystemExit(f"no block {name!r}; try: {', '.join(blocks())}")
    comp_id = as_id or name
    if not re.fullmatch(r"[a-z][a-z0-9-]*", comp_id):
        raise SystemExit("--as must be lowercase letters, digits and dashes")
    if meta["type"] == "hyperframes:component":
        for f in meta["files"]:
            dest = project / f["target"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(meta["_dir"] / f["path"], dest)
        return meta["usage"]

    html = (meta["_dir"] / meta["files"][0]["path"]).read_text(encoding="utf-8")
    values: dict[str, str] = {"__ID__": comp_id}
    needs = meta.get("needs", [])
    if "audio" in needs:
        if not audio:
            raise SystemExit(f"{name} needs --audio (the music or voice it reacts to)")
        env = envelope(str(project / audio), fps=fps)
        values["__DATA__"] = json.dumps(env, separators=(",", ":"))
        duration = duration or round(env["duration"] - offset, 3)
        values["__OFFSET__"] = f"{offset:g}"
    if "src" in needs:
        if not src:
            raise SystemExit(f"{name} needs --src (the video to show)")
        if not (project / src).exists():
            raise SystemExit(f"{project / src} not found")
        duration = duration or _probe_duration(project / src)
        values["__SRC__"] = "../" + src.replace("\\", "/").lstrip("./")  # compositions/ is one level down
    if "lottie" in needs:
        data_path = Path(lottie) if lottie else meta["_dir"] / "assets" / "sample-check.json"
        data_path = data_path if data_path.is_absolute() or not lottie else project / data_path
        anim = json.loads(data_path.read_text(encoding="utf-8"))
        values["__DATA__"] = json.dumps(anim, separators=(",", ":"))
        values["__LOOP__"] = "true" if loop else "false"
        duration = duration or (round((anim["op"] - anim["ip"]) / anim["fr"], 3) if not loop else meta["duration"])
    duration = float(duration or meta["duration"])
    values["__DURATION__"] = f"{duration:g}"
    for key, val in values.items():
        html = html.replace(key, val)
    left = re.search(r"__(ID|DATA|OFFSET|SRC|LOOP|DURATION)__", html)
    if left:
        raise SystemExit(f"{name}: unfilled placeholder {left.group(0)}")
    target = project / "compositions" / f"{comp_id}.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    w, h = meta["dimensions"]["width"], meta["dimensions"]["height"]
    return (f'<div id="{comp_id}" class="clip" data-composition-id="{comp_id}" '
            f'data-composition-src="compositions/{comp_id}.html" data-start="0" data-duration="{duration:g}" '
            f'data-track-index="5" data-width="{w}" data-height="{h}"></div>')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("envelope", help="measure audio loudness per frame")
    e.add_argument("audio"), e.add_argument("output")
    e.add_argument("--fps", type=float, default=30.0)
    e.add_argument("--bands", type=int, default=24)
    sub.add_parser("list", help="show the blocks")
    a = sub.add_parser("add", help="install a block into a project")
    a.add_argument("name")
    a.add_argument("--project", default=".")
    a.add_argument("--as", dest="as_id", help="id for this copy (lets you add a block twice)")
    a.add_argument("--audio", help="project-relative audio the block reacts to")
    a.add_argument("--src", help="project-relative video for picture-in-picture")
    a.add_argument("--lottie", help="project-relative Lottie .json (default: a sample)")
    a.add_argument("--loop", action="store_true", help="loop the Lottie for the block's whole duration")
    a.add_argument("--duration", type=float)
    a.add_argument("--offset", type=float, default=0.0, help="seconds into the audio where the block starts")
    a.add_argument("--fps", type=float, default=30.0)
    args = ap.parse_args(argv)
    if args.cmd == "envelope":
        env = envelope(args.audio, args.fps, args.bands)
        Path(args.output).write_text(json.dumps(env, separators=(",", ":")), encoding="utf-8")
        print(f"{len(env['level'])} frames, {len(env['bands'][0])} bands -> {args.output}")
    elif args.cmd == "list":
        for name, meta in blocks().items():
            print(f"{name:20s} {meta['description']}")
    else:
        snippet = add(args.name, Path(args.project).resolve(), args.as_id, args.audio, args.src, args.lottie,
                      args.duration, args.offset, args.loop, args.fps)
        print(snippet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
