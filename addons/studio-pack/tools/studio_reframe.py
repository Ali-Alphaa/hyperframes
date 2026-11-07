#!/usr/bin/env python3
"""Studio Pack smart reframe: turn landscape footage into vertical (or square) by following the subject,
the way DaVinci Resolve's Smart Reframe does. Local, no downloads: OpenCV's bundled detectors only.

How it frames each shot:
  1. Looks at ~5 frames a second for faces (front and side), then whole people, then movement.
  2. Splits the video into shots at hard cuts, so the framing never drifts across a cut.
  3. Moves like a camera operator: holds still while the subject stays near the middle of the frame, then
     glides to follow once they leave it. No jitter.
  4. FFmpeg does the crop, so the picture is cut from the original frames and the sound is kept.

  python studio_reframe.py in.mp4 out.mp4                       # 9:16, 1080x1920
  python studio_reframe.py in.mp4 out.mp4 --aspect 1:1 --preview check.mp4 --plan plan.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_audio import FFMPEG, run  # noqa: E402

OUTPUT_SIZES = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080)}
DETECT_WIDTH = 640  # detection runs on a downscaled copy; positions are scaled back


@dataclass
class Sample:
    t: float
    cx: float | None  # subject centre, source pixels
    cy: float | None
    kind: str  # face | person | motion | none
    cut: bool  # a new shot starts here


# ------------------------------------------------------------------ detection
class Detector:
    def __init__(self) -> None:
        base = cv2.data.haarcascades
        self.front = cv2.CascadeClassifier(base + "haarcascade_frontalface_default.xml")
        self.profile = cv2.CascadeClassifier(base + "haarcascade_profileface.xml")
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def faces(self, gray: np.ndarray) -> list[tuple[int, int, int, int]]:
        h = gray.shape[0]
        min_size = (max(20, h // 14),) * 2
        found = list(self.front.detectMultiScale(gray, 1.1, 6, minSize=min_size))
        found += list(self.profile.detectMultiScale(gray, 1.1, 6, minSize=min_size))
        flipped = cv2.flip(gray, 1)
        for x, y, w, hh in self.profile.detectMultiScale(flipped, 1.1, 6, minSize=min_size):
            found.append((gray.shape[1] - x - w, y, w, hh))
        return found

    def people(self, gray: np.ndarray) -> list[tuple[int, int, int, int]]:
        rects, weights = self.hog.detectMultiScale(gray, winStride=(8, 8), padding=(8, 8), scale=1.05)
        return [tuple(r) for r, wt in zip(rects, np.ravel(weights)) if wt > 0.5]


def subject(det: Detector, gray: np.ndarray, prev: np.ndarray | None) -> tuple[float | None, float | None, str]:
    """Subject centre in detection pixels. Several faces: weighted by size, so the main speaker wins."""
    faces = det.faces(gray)
    if faces:
        areas = np.array([w * h for _, _, w, h in faces], dtype=float)
        weights = areas ** 2  # favour the biggest face strongly
        cx = float(np.average([x + w / 2 for x, _, w, _ in faces], weights=weights))
        cy = float(np.average([y + h / 2 for _, y, _, h in faces], weights=weights))
        return cx, cy, "face"
    people = det.people(gray)
    if people:
        x, y, w, h = max(people, key=lambda r: r[2] * r[3])
        return x + w / 2, y + h / 4, "person"  # aim at the head end of a body
    if prev is not None:
        diff = cv2.absdiff(cv2.GaussianBlur(gray, (9, 9), 0), cv2.GaussianBlur(prev, (9, 9), 0))
        mask = (diff > 18).astype(np.uint8)
        if mask.mean() > 0.003:
            m = cv2.moments(mask, binaryImage=True)
            return m["m10"] / m["m00"], m["m01"] / m["m00"], "motion"
    return None, None, "none"


def analyse(path: str, sample_fps: float = 5.0, cut_threshold: float = 0.55) -> tuple[list[Sample], dict]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit(f"can't open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    src_w, src_h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, round(fps / sample_fps))
    scale = DETECT_WIDTH / src_w if src_w > DETECT_WIDTH else 1.0
    det, samples = Detector(), []
    prev_gray = prev_hist = None
    index = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if index % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else frame
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            hist = cv2.calcHist([cv2.cvtColor(small, cv2.COLOR_BGR2HSV)], [0, 1], None, [32, 32], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            cut = prev_hist is None or cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL) < cut_threshold
            cx, cy, kind = subject(det, gray, None if cut else prev_gray)
            samples.append(Sample(index / fps, cx / scale if cx is not None else None,
                                  cy / scale if cy is not None else None, kind, cut))
            prev_gray, prev_hist = gray, hist
        index += 1
    cap.release()
    return samples, {"fps": fps, "width": src_w, "height": src_h, "frames": index}


# ----------------------------------------------------------------- the camera
def shots(samples: list[Sample]) -> list[list[int]]:
    groups: list[list[int]] = []
    for i, s in enumerate(samples):
        if s.cut or not groups:
            groups.append([])
        groups[-1].append(i)
    return groups


def camera_path(targets: list[float | None], lo: float, hi: float, dead_zone: float, smooth: float = 1.5) -> list[float]:
    """One shot's crop centres. Gaps are filled from the nearest detection; a shot with none stays centred.

    The whole shot is known before rendering, so the camera needn't lag: it holds while the target stays inside
    the dead zone, and once the target leaves, it follows exactly at the dead zone's edge. A forward-and-backward
    (zero-lag) Gaussian then rounds off the starts and stops. A held stretch stays perfectly still."""
    known = [i for i, t in enumerate(targets) if t is not None]
    if not known:
        return [(lo + hi) / 2] * len(targets)
    filled = [targets[min(known, key=lambda k: abs(k - i))] for i in range(len(targets))]
    med = [float(np.median(filled[max(0, i - 2): i + 3])) for i in range(len(filled))]  # drops one-off misfires
    pos, raw = float(np.clip(med[0], lo, hi)), []
    for t in med:
        gap = t - pos
        if abs(gap) > dead_zone:
            pos = t - np.sign(gap) * dead_zone
        pos = float(np.clip(pos, lo, hi))
        raw.append(pos)
    if len(raw) < 3 or smooth <= 0:
        return raw
    radius = int(3 * smooth)
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / smooth) ** 2)
    kernel /= kernel.sum()
    # extend the motion past the shot's ends (not freeze it), so a pan isn't dragged back at the last frames
    padded = np.pad(np.array(raw), radius, mode="reflect", reflect_type="odd")
    return [float(np.clip(v, lo, hi)) for v in np.convolve(padded, kernel, mode="valid")]


def plan(samples: list[Sample], info: dict, aspect: str, dead_zone_frac: float = 0.12) -> dict:
    aw, ah = (int(v) for v in aspect.split(":"))
    src_w, src_h = info["width"], info["height"]
    crop_h = src_h
    crop_w = int(round(src_h * aw / ah))
    if crop_w > src_w:
        crop_w, crop_h = src_w, int(round(src_w * ah / aw))
    crop_w -= crop_w % 2
    crop_h -= crop_h % 2
    xs, ys = [0.0] * len(samples), [0.0] * len(samples)
    for group in shots(samples):
        cx = camera_path([samples[i].cx for i in group], crop_w / 2, src_w - crop_w / 2, dead_zone_frac * crop_w)
        cy = camera_path([samples[i].cy for i in group], crop_h / 2, src_h - crop_h / 2, dead_zone_frac * crop_h)
        for k, i in enumerate(group):
            xs[i], ys[i] = cx[k] - crop_w / 2, cy[k] - crop_h / 2
    return {"crop": [crop_w, crop_h], "source": [src_w, src_h], "fps": info["fps"],
            "samples": [{"t": round(s.t, 3), "x": round(x), "y": round(y), "kind": s.kind, "cut": s.cut}
                        for s, x, y in zip(samples, xs, ys)]}


def frame_commands(p: dict, frames: int, filter_name: str = "crop") -> str:
    """sendcmd script: the crop position at every frame, interpolated between samples and held across cuts."""
    fps = p["fps"]
    ts = np.array([s["t"] for s in p["samples"]])
    lines, last = [], None
    for f in range(frames):
        t = f / fps
        # inside a shot interpolate; never interpolate across a cut (jump exactly at it)
        nxt = int(np.searchsorted(ts, t, side="right"))
        a = max(0, nxt - 1)
        b = min(len(ts) - 1, nxt)
        sa, sb = p["samples"][a], p["samples"][b]
        if b == a or sb["cut"]:
            x, y = sa["x"], sa["y"]
        else:
            k = (t - sa["t"]) / max(1e-6, sb["t"] - sa["t"])
            x, y = sa["x"] + (sb["x"] - sa["x"]) * k, sa["y"] + (sb["y"] - sa["y"]) * k
        xy = (int(round(x)) & ~1, int(round(y)) & ~1)
        if xy != last:
            lines.append(f"{t:.4f} {filter_name} x {xy[0]}, {filter_name} y {xy[1]};")
            last = xy
    return "\n".join(lines) + "\n"


def render(src: str, out: str, p: dict, frames: int, size: tuple[int, int], preview: str | None = None) -> None:
    cw, ch = p["crop"]
    first = p["samples"][0] if p["samples"] else {"x": 0, "y": 0}
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "cmds.txt").write_text(frame_commands(p, frames), encoding="utf-8")
        vf = (f"sendcmd=f=cmds.txt,crop={cw}:{ch}:{first['x']}:{first['y']},"
              f"scale={size[0]}:{size[1]}:flags=lanczos,setsar=1")
        cmd = [FFMPEG, "-y", "-v", "error", "-i", str(Path(src).resolve()), "-vf", vf, "-c:v", "libx264", "-crf", "17",
               "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart",
               str(Path(out).resolve())]
        proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise SystemExit(f"ffmpeg failed: {proc.stderr[-1500:]}")
        if preview:
            (Path(tmp) / "box.txt").write_text(frame_commands(p, frames, "drawbox"), encoding="utf-8")
            vf = (f"sendcmd=f=box.txt,drawbox=x={first['x']}:y={first['y']}:w={cw}:h={ch}:color=yellow@0.9:t=6,"
                  "scale=1280:-2")
            proc = subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(Path(src).resolve()), "-vf", vf, "-an",
                                   "-c:v", "libx264", "-crf", "23", str(Path(preview).resolve())],
                                  cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if proc.returncode != 0:
                raise SystemExit(f"preview failed: {proc.stderr[-800:]}")


def reframe(src: str, out: str, aspect: str = "9:16", size: tuple[int, int] | None = None,
            preview: str | None = None, plan_path: str | None = None, dead_zone: float = 0.12) -> dict:
    samples, info = analyse(src)
    p = plan(samples, info, aspect, dead_zone)
    if plan_path:
        Path(plan_path).write_text(json.dumps(p, indent=1), encoding="utf-8")
    render(src, out, p, info["frames"], size or OUTPUT_SIZES[aspect], preview)
    kinds = [s.kind for s in samples]
    p["summary"] = {k: kinds.count(k) for k in ("face", "person", "motion", "none")}
    p["summary"]["shots"] = len(shots(samples))
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input"), ap.add_argument("output")
    ap.add_argument("--aspect", choices=list(OUTPUT_SIZES), default="9:16")
    ap.add_argument("--dead-zone", type=float, default=0.12, help="fraction of the crop the subject may move "
                                                                   "before the camera follows (default 0.12)")
    ap.add_argument("--preview", help="also write the source with the crop box drawn on it")
    ap.add_argument("--plan", help="write the framing decisions as JSON")
    a = ap.parse_args(argv)
    p = reframe(a.input, a.output, a.aspect, preview=a.preview, plan_path=a.plan, dead_zone=a.dead_zone)
    s = p["summary"]
    print(f"{a.output}: {s['shots']} shots; framed on faces {s['face']}, people {s['person']}, movement "
          f"{s['motion']}, nothing found {s['none']} (of {sum(s[k] for k in ('face', 'person', 'motion', 'none'))} "
          "looks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
