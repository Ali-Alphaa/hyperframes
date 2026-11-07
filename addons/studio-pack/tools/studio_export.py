#!/usr/bin/env python3
"""Studio Pack platform export: one finished render in, one file per platform out.

Each output gets the platform's frame size, H.264 High + AAC at 48 kHz, fast-start (plays while uploading),
and a two-pass loudness fix to the platform's target. A source whose shape differs from the platform's
(16:9 into 9:16) is fitted one of three ways:
  smart  follow the subject (faces, people, movement) like Resolve's Smart Reframe; see studio_reframe.py
  crop   fill the frame, cutting the sides (fixed centre crop)
  blur   whole picture centred over a blurred, zoomed copy of itself
  pad    whole picture centred over black bars

  python studio_export.py renders/video.mp4 --platform shorts tiktok --fit blur
  python studio_export.py renders/video.mp4 --all
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_audio import FFMPEG, loudnorm_filter, run  # noqa: E402

PRESETS = json.loads((Path(__file__).resolve().parent.parent / "presets" / "platforms.json").read_text("utf-8"))
PLATFORMS = {k: v for k, v in PRESETS.items() if not k.startswith("_")}


def probe(path: str | Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    num, den = (video["r_frame_rate"].split("/") if video else ("0", "1"))
    return {"width": video["width"] if video else 0, "height": video["height"] if video else 0,
            "fps": float(num) / float(den or 1), "duration": float(data["format"]["duration"]),
            "has_audio": any(s["codec_type"] == "audio" for s in data["streams"])}


def video_filter(src_w: int, src_h: int, w: int, h: int, fit: str) -> str:
    same_shape = abs(src_w / src_h - w / h) < 0.01
    if same_shape:
        return f"scale={w}:{h}:flags=lanczos,setsar=1"
    if fit == "crop":
        return f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},setsar=1"
    if fit == "pad":
        return f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    # blur: a soft, zoomed copy fills the frame behind the whole picture
    return (f"split=2[bg][fg];[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=40:2,"
            f"eq=brightness=-0.08[bgb];[fg]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos[fgs];"
            "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1")


def export(src: Path, platform: str, fit: str, out_dir: Path) -> Path:
    spec = PLATFORMS[platform]
    info = probe(src)
    if info["duration"] > spec["max_seconds"]:
        raise SystemExit(f"{spec['label']}: video is {info['duration']:.0f}s, the limit is {spec['max_seconds']}s. "
                         "Trim it first (or cut a short with the Shorts Generator).")
    if fit == "smart" and abs(info["width"] / info["height"] - spec["width"] / spec["height"]) >= 0.01:
        import tempfile
        from studio_reframe import reframe
        aspect = {(9, 16): "9:16", (4, 5): "4:5", (1, 1): "1:1"}.get(_ratio(spec["width"], spec["height"]))
        if aspect is None:
            raise SystemExit(f"smart reframe can't make {spec['width']}x{spec['height']}; use --fit blur")
        with tempfile.TemporaryDirectory() as tmp:
            framed = Path(tmp) / f"{src.stem}.mp4"
            reframe(str(src), str(framed), aspect, size=(spec["width"], spec["height"]))
            return _encode(framed, platform, spec, probe(framed), "blur", out_dir, src.stem)
    return _encode(src, platform, spec, info, fit, out_dir, src.stem)


def _ratio(w: int, h: int) -> tuple[int, int]:
    from math import gcd
    g = gcd(w, h)
    return w // g, h // g


def _encode(src: Path, platform: str, spec: dict, info: dict, fit: str, out_dir: Path, stem: str) -> Path:
    fps = min(info["fps"] or 30, spec["max_fps"])
    out = out_dir / f"{stem}.{platform}.mp4"
    vf = video_filter(info["width"], info["height"], spec["width"], spec["height"], fit)
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(src)]
    cmd += ["-filter_complex" if ";" in vf or "split" in vf else "-vf", vf]
    cmd += ["-r", f"{fps:g}", "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-preset", "slow",
            "-b:v", spec["video_bitrate"], "-maxrate", spec["video_bitrate"], "-bufsize", "20M"]
    if info["has_audio"]:
        cmd += ["-af", loudnorm_filter(src, spec["lufs"], spec["true_peak"]) + ",aresample=48000",
                "-c:a", "aac", "-b:a", spec["audio_bitrate"], "-ar", "48000"]
    cmd += ["-movflags", "+faststart", str(out)]
    run(cmd)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--platform", nargs="+", choices=list(PLATFORMS))
    ap.add_argument("--all", action="store_true", help="every platform")
    ap.add_argument("--fit", choices=["smart", "crop", "blur", "pad"], default="blur",
                    help="how to fit a different shape (default blur)")
    ap.add_argument("--out-dir", help="default: next to the input")
    a = ap.parse_args(argv)
    platforms = list(PLATFORMS) if a.all else (a.platform or [])
    if not platforms:
        ap.error("pick --platform ... or --all")
    src = Path(a.input).resolve()
    out_dir = Path(a.out_dir).resolve() if a.out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in platforms:
        out = export(src, p, a.fit, out_dir)
        i = probe(out)
        print(f"{PLATFORMS[p]['label']}: {out.name}  {i['width']}x{i['height']} @ {i['fps']:g} fps, {i['duration']:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
