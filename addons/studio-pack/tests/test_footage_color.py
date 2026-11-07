"""Studio Pack batch 3 tests: stabilise, denoise, slow motion, upscale, colour. Generated footage only.

The GPU tools (RIFE, Real-ESRGAN) run in a child process, as the CLI does, because their Vulkan libraries can crash
while Python shuts down; tests needing them are skipped when they aren't installed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import studio_color as sc  # noqa: E402
import studio_export as se  # noqa: E402
import studio_footage as sf  # noqa: E402

W, H, FPS = 640, 360, 30


def scene(i: int) -> np.ndarray:
    """A textured scene with a moving bar, so motion, noise and colour are all measurable."""
    y, x = np.mgrid[0:H, 0:W]
    img = np.zeros((H, W, 3), np.uint8)
    img[..., 0] = (80 + 60 * np.sin(x / 23.0) * np.cos(y / 17.0)).astype(np.uint8)
    img[..., 1] = (110 + 50 * np.cos(x / 31.0)).astype(np.uint8)
    img[..., 2] = (90 + 40 * np.sin(y / 19.0)).astype(np.uint8)
    cv2.rectangle(img, (40 + 6 * i, 120), (120 + 6 * i, 240), (230, 230, 230), -1)
    return img


def write(path: Path, frames, fps: float = FPS) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (frames[0].shape[1], frames[0].shape[0]))
    for f in frames:
        writer.write(f)
    writer.release()
    return path


def frames_of(path: Path) -> list[np.ndarray]:
    cap, out = cv2.VideoCapture(str(path)), []
    while True:
        ok, f = cap.read()
        if not ok:
            return out
        out.append(f)


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    return cv2.PSNR(a, b)


def shake(frames_out: list[np.ndarray]) -> float:
    shifts, prev = [], None
    for f in frames_out:
        g = np.float32(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY))
        if prev is not None:
            (dx, dy), _ = cv2.phaseCorrelate(prev, g)
            shifts.append(np.hypot(dx, dy))
        prev = g
    return float(np.mean(shifts))


def test_stabilise_removes_shake(tmp_path):
    rng = np.random.default_rng(2)
    still = scene(0)
    big = cv2.resize(still, (W + 80, H + 80))
    offsets = np.cumsum(rng.normal(0, 6, (60, 2)), axis=0).clip(-35, 35) + 40
    shaky = [big[int(oy): int(oy) + H, int(ox): int(ox) + W] for ox, oy in offsets]
    src = write(tmp_path / "shaky.mp4", shaky)
    sf.stabilise(str(src), str(tmp_path / "steady.mp4"))
    before, after = shake(frames_of(src)), shake(frames_of(tmp_path / "steady.mp4"))
    assert after < before * 0.5, f"shake {before:.1f} px -> {after:.1f} px"


@pytest.mark.parametrize("strength", ["light", "medium", "strong"])
def test_denoise_gets_closer_to_clean(tmp_path, strength):
    rng = np.random.default_rng(4)
    clean = [scene(0) for _ in range(20)]  # still scene: temporal denoise should do very well
    noisy = [np.clip(f.astype(int) + rng.normal(0, 18, f.shape), 0, 255).astype(np.uint8) for f in clean]
    src = write(tmp_path / "noisy.mp4", noisy)
    sf.denoise(str(src), str(tmp_path / "clean.mp4"), strength)
    out = frames_of(tmp_path / "clean.mp4")
    before = np.mean([psnr(n, c) for n, c in zip(noisy[5:15], clean[5:15])])
    after = np.mean([psnr(o, c) for o, c in zip(out[5:15], clean[5:15])])
    assert after > before + 2, f"{strength}: {before:.1f} dB -> {after:.1f} dB"


def test_slowmo_ffmpeg_doubles_length(tmp_path):
    src = write(tmp_path / "move.mp4", [scene(i) for i in range(30)])
    sf.slowmo(str(src), str(tmp_path / "slow.mp4"), factor=2, mode="slow", engine="ffmpeg")
    info = se.probe(tmp_path / "slow.mp4")
    assert info["duration"] == pytest.approx(2.0, abs=0.15) and info["fps"] == pytest.approx(30)


@pytest.mark.skipif(not sf._rife_available(), reason="RIFE isn't installed")
def test_rife_invents_better_frames_than_repeating(tmp_path):
    full = [scene(i) for i in range(24)]
    src = write(tmp_path / "half.mp4", full[::2], fps=15)
    out = tmp_path / "smooth.mp4"
    subprocess.run([sys.executable, str(TOOLS / "studio_footage.py"), "slowmo", str(src), str(out), "--factor", "2",
                    "--mode", "smooth", "--engine", "rife"], check=True, capture_output=True)
    made = frames_of(out)
    assert se.probe(out)["fps"] == pytest.approx(30)
    invented = [psnr(made[k], full[k]) for k in range(1, 21, 2)]
    repeated = [psnr(full[k - 1], full[k]) for k in range(1, 21, 2)]
    assert np.mean(invented) > np.mean(repeated) + 3


@pytest.mark.skipif(not sf._realesrgan_available(), reason="Real-ESRGAN isn't installed")
def test_upscale_image_doubles_size(tmp_path):
    small = tmp_path / "small.png"
    cv2.imwrite(str(small), cv2.resize(scene(3), (320, 180)))
    out = tmp_path / "big.png"
    subprocess.run([sys.executable, str(TOOLS / "studio_footage.py"), "upscale", str(small), str(out), "--model", "fast"],
                   check=True, capture_output=True)
    assert cv2.imread(str(out)).shape[:2] == (360, 640)


def test_topaz_is_off_unless_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(sf, "TOPAZ_FFMPEG", tmp_path / "missing.exe")
    with pytest.raises(SystemExit, match="paid option"):
        sf.upscale(str(tmp_path / "x.png"), str(tmp_path / "y.png"), engine="topaz")


def cast(img: np.ndarray, warm: float) -> np.ndarray:
    f = img.astype(float)
    f[..., 2] *= 1 + warm   # BGR: red up
    f[..., 0] *= 1 - warm   # blue down
    return np.clip(f, 0, 255).astype(np.uint8)


def test_autocorrect_removes_a_colour_cast(tmp_path):
    src = write(tmp_path / "warm.mp4", [cast(scene(i), 0.25) for i in range(10)])
    before = sc.stats(sc.sample_frames(str(src)))[0]
    assert sc.main(["autocorrect", str(src), str(tmp_path / "fixed.mp4")]) == 0
    after = sc.stats(sc.sample_frames(str(tmp_path / "fixed.mp4")))[0]
    assert abs(after[1]) < abs(before[1]) * 0.5 and abs(after[2]) < abs(before[2]) * 0.5


def test_match_moves_a_clip_onto_the_reference(tmp_path):
    ref = write(tmp_path / "ref.mp4", [scene(i) for i in range(10)])
    off = write(tmp_path / "off.mp4", [cv2.convertScaleAbs(cast(scene(i), 0.2), alpha=0.8) for i in range(10)])
    lut = tmp_path / "m.cube"
    assert sc.main(["match", str(off), str(ref), str(tmp_path / "m.mp4"), "--save-lut", str(lut)]) == 0
    r, o, m = (sc.stats(sc.sample_frames(str(p)))[0] for p in (ref, off, tmp_path / "m.mp4"))
    assert np.linalg.norm(m - r) < np.linalg.norm(o - r) * 0.35
    assert sc.read_cube(lut).shape == (sc.LUT_SIZE ** 3, 3)


def test_identity_cube_changes_nothing(tmp_path):
    grid = np.linspace(0, 1, sc.LUT_SIZE, dtype=np.float32)
    b, g, r = np.meshgrid(grid, grid, grid, indexing="ij")
    sc.write_cube(np.stack([r, g, b], -1).reshape(-1, 3), tmp_path / "id.cube", "identity")
    src = write(tmp_path / "s.mp4", [scene(i) for i in range(5)])
    sc.apply_cube(str(src), str(tmp_path / "o.mp4"), tmp_path / "id.cube")
    assert np.mean([psnr(a, b) for a, b in zip(frames_of(src), frames_of(tmp_path / "o.mp4"))]) > 38
