#!/usr/bin/env python3
"""Studio Pack colour for footage files (Resolve's Color page, the parts an explainer needs).

  autocorrect  neutral white balance and sensible exposure/contrast, from the clip's own statistics
  match        make a clip's colour match a reference clip (two cameras, two days, two locations)
  lut          apply a .cube LUT (a "look"), optionally at partial strength

Every correction is written as a standard 33-point .cube LUT (--save-lut keeps it), so it can be reused on other
clips, in Resolve, or frozen into a HyperFrames grade. Corrections are measured in Lab colour space: L is
lightness, a is green-red, b is blue-yellow; a neutral picture has a and b near 0 on average.

  python studio_color.py autocorrect clip.mp4 clip.fixed.mp4
  python studio_color.py match b-cam.mp4 a-cam.mp4 b-cam.matched.mp4 --save-lut b-to-a.cube
  python studio_color.py lut clip.mp4 clip.look.mp4 --cube film.cube --amount 0.6

For a <video> inside a HyperFrames composition, prefer `npx hyperframes media-treatment --analyze` (it grades the
element without re-encoding). Use these for footage files before they go into a project.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_audio import FFMPEG  # noqa: E402
from studio_export import probe  # noqa: E402

LUT_SIZE = 33


# ------------------------------------------------------------------- measuring
def sample_frames(path: str, count: int = 24) -> np.ndarray:
    """RGB float frames (0-1) spread evenly through a clip, downscaled for speed. Images work too."""
    if Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        img = cv2.imread(path)
        return np.array([cv2.cvtColor(img, cv2.COLOR_BGR2RGB)]).astype(np.float32) / 255
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    frames = []
    for i in np.linspace(0, total - 1, min(count, total)).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, frame = cap.read()
        if ok:
            frame = cv2.resize(frame, (320, int(320 * frame.shape[0] / frame.shape[1])), interpolation=cv2.INTER_AREA)
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    cap.release()
    if not frames:
        raise SystemExit(f"couldn't read frames from {path}")
    return np.array(frames).astype(np.float32) / 255


def to_lab(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb.reshape(-1, 1, 3).astype(np.float32), cv2.COLOR_RGB2LAB).reshape(-1, 3)


def stats(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lab = to_lab(rgb)
    # ignore clipped blacks and whites: they carry no colour information
    keep = (lab[:, 0] > 3) & (lab[:, 0] < 97)
    lab = lab[keep] if keep.sum() > 100 else lab
    return lab.mean(axis=0), lab.std(axis=0) + 1e-3


# -------------------------------------------------------------- building LUTs
def lab_transfer_lut(src_mean, src_std, dst_mean, dst_std, amount: float = 1.0,
                     max_gain: float = 1.6) -> np.ndarray:
    """A LUT that moves each colour's Lab value from the source distribution toward the target one (Reinhard
    transfer), limited so no channel's spread changes by more than `max_gain` either way."""
    grid = np.linspace(0, 1, LUT_SIZE, dtype=np.float32)
    b, g, r = np.meshgrid(grid, grid, grid, indexing="ij")  # .cube order: red varies fastest
    rgb = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    lab = to_lab(rgb)
    gain = np.clip(dst_std / src_std, 1 / max_gain, max_gain)
    moved = (lab - src_mean) * gain + dst_mean
    lab = lab + (moved - lab) * amount
    out = cv2.cvtColor(lab.reshape(-1, 1, 3).astype(np.float32), cv2.COLOR_LAB2RGB).reshape(-1, 3)
    return np.clip(out, 0, 1)


def autocorrect_lut(rgb: np.ndarray, amount: float = 1.0) -> np.ndarray:
    """Mostly neutral greys, exposure nudged toward mid-grey and contrast toward a natural spread. Keeps 30% of the
    clip's own colour, because a forest or a sunset really is green or warm on average; forcing a and b fully to 0
    (grey-world) made an outdoor test clip look cold. Exposure and contrast moves are bounded so a deliberately
    dark or soft shot isn't flattened."""
    mean, std = stats(rgb)
    target_mean = np.array([np.clip(mean[0], 42, 62), 0.3 * mean[1], 0.3 * mean[2]], np.float32)
    target_std = np.array([np.clip(std[0], 18, 26), std[1], std[2]], np.float32)
    return lab_transfer_lut(mean, std, target_mean, target_std, amount, max_gain=1.35)


def write_cube(lut: np.ndarray, path: str | Path, title: str) -> None:
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {LUT_SIZE}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in lut]
    Path(path).write_text("\n".join(lines) + "\n", encoding="ascii")


def read_cube(path: str | Path) -> np.ndarray:
    rows = [ln.split() for ln in Path(path).read_text().splitlines()]
    return np.array([[float(v) for v in r] for r in rows if len(r) == 3 and r[0][0] in "-.0123456789"], np.float32)


def apply_lut_to_rgb(rgb: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Nearest-point preview of a LUT on float RGB (for measuring; FFmpeg does the real, interpolated apply)."""
    idx = np.clip(np.rint(rgb * (LUT_SIZE - 1)), 0, LUT_SIZE - 1).astype(int)
    flat = idx[..., 0] + idx[..., 1] * LUT_SIZE + idx[..., 2] * LUT_SIZE * LUT_SIZE
    return lut[flat]


# -------------------------------------------------------------------- applying
def apply_cube(src: str, out: str, cube: str | Path, amount: float = 1.0) -> None:
    """lut3d with tetrahedral interpolation; amount < 1 blends with the original."""
    info = None if Path(src).suffix.lower() in {".png", ".jpg", ".jpeg"} else probe(src)
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "look.cube"  # lut3d's file option can't take a Windows drive path
        local.write_bytes(Path(cube).read_bytes())
        if amount >= 0.999:
            vf = "lut3d=file=look.cube:interp=tetrahedral"
        else:
            vf = (f"split[o][g];[g]lut3d=file=look.cube:interp=tetrahedral[l];"
                  f"[o][l]blend=all_mode=normal:all_opacity={amount:.3f}")
        cmd = [FFMPEG, "-y", "-v", "error", "-i", str(Path(src).resolve()), "-vf", vf]
        if info:
            cmd += ["-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
            cmd += ["-c:a", "aac", "-b:a", "256k"] if info["has_audio"] else []
        cmd.append(str(Path(out).resolve()))
        proc = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise SystemExit(f"ffmpeg failed: {proc.stderr[-1200:]}")


def report(label: str, rgb: np.ndarray) -> str:
    mean, std = stats(rgb)
    return f"{label}: L {mean[0]:.1f}±{std[0]:.1f}  a {mean[1]:+.1f}  b {mean[2]:+.1f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("autocorrect", help="neutral white balance, sensible exposure and contrast")
    c.add_argument("input"), c.add_argument("output")
    m = sub.add_parser("match", help="match a clip's colour to a reference")
    m.add_argument("input"), m.add_argument("reference"), m.add_argument("output")
    lt = sub.add_parser("lut", help="apply a .cube look")
    lt.add_argument("input"), lt.add_argument("output")
    lt.add_argument("--cube", required=True)
    for p in (c, m, lt):
        p.add_argument("--amount", type=float, default=1.0, help="0-1 strength (default 1)")
    for p in (c, m):
        p.add_argument("--save-lut", help="also keep the correction as a .cube file")
    a = ap.parse_args(argv)
    if a.cmd == "lut":
        apply_cube(a.input, a.output, a.cube, a.amount)
        print(f"applied {Path(a.cube).name} at {a.amount:.0%}: {a.output}")
        return 0
    frames = sample_frames(a.input)
    print(report("before", frames))
    if a.cmd == "autocorrect":
        lut = autocorrect_lut(frames, a.amount)
    else:
        ref = sample_frames(a.reference)
        print(report("reference", ref))
        lut = lab_transfer_lut(*stats(frames), *stats(ref), a.amount)
    cube = Path(a.save_lut) if a.save_lut else Path(tempfile.gettempdir()) / f"studio-pack-{a.cmd}.cube"
    write_cube(lut, cube, f"Studio Pack {a.cmd}")
    print(report("after (estimate)", apply_lut_to_rgb(frames, lut)))
    apply_cube(a.input, a.output, cube, 1.0)
    print(f"{a.cmd}: {a.output}" + (f"  (LUT: {cube})" if a.save_lut else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
