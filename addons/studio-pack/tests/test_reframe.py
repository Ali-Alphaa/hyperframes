"""Studio Pack batch 2 tests: smart reframe, plus DeepFilterNet cleanup when it is set up.

Generated footage only: a bright subject on a textured background, so the movement detector is what frames
it (the face and person detectors are checked on real clips, see the README).
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import studio_audio as sa  # noqa: E402
import studio_export as se  # noqa: E402
import studio_reframe as sr  # noqa: E402

W, H, FPS = 1280, 720, 30


def subject_video(path: Path, positions, backgrounds) -> None:
    """positions[i] = subject x for frame i; backgrounds[i] = 0/1, a change marks a hard cut."""
    rng = np.random.default_rng(3)
    textures = [rng.integers(20, 60, (H, W, 3), dtype=np.uint8), rng.integers(60, 110, (H, W, 3), dtype=np.uint8)]
    textures[1][:, :, 0] //= 3  # a different colour cast for the second shot
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for i, (x, bg) in enumerate(zip(positions, backgrounds)):
        frame = textures[bg].copy()
        y = H // 2 + int(25 * np.sin(i / 3))  # keeps moving a little so movement detection sees it
        cv2.circle(frame, (int(x), y), 60, (240, 240, 240), -1)
        writer.write(frame)
    writer.release()


def crop_centres(p: dict) -> list[tuple[float, float, bool]]:
    cw = p["crop"][0]
    return [(s["t"], s["x"] + cw / 2, s["cut"]) for s in p["samples"]]


def test_follows_a_moving_subject_and_relocks_after_a_cut(tmp_path):
    # shot 1 (3 s): subject walks from x=250 to x=1000; shot 2 (2 s): subject waits at x=300
    walk = list(np.linspace(250, 1000, 3 * FPS))
    positions = walk + [300] * (2 * FPS)
    backgrounds = [0] * (3 * FPS) + [1] * (2 * FPS)
    src = tmp_path / "walk.mp4"
    subject_video(src, positions, backgrounds)
    samples, info = sr.analyse(str(src))
    p = sr.plan(samples, info, "9:16")
    cw = p["crop"][0]
    assert p["crop"] == [404, 720]
    cuts = [t for t, _, cut in crop_centres(p) if cut]
    assert len(cuts) == 2 and cuts[1] == pytest.approx(3.0, abs=0.25), "one cut detected, at 3 s"
    centres = crop_centres(p)
    radius = 60
    for t, c, _ in centres:
        if 0.4 <= t < 3.0:  # the whole subject stays inside the crop throughout the walk
            true_x = positions[int(round(t * FPS))]
            assert abs(c - true_x) < cw / 2 - radius, f"subject at {true_x:.0f} outside crop centred {c:.0f} at {t}s"
    second = [c for t, c, _ in centres if t >= 3.4]
    assert all(abs(c - 300) < cw * 0.2 for c in second), "framing re-locks onto the subject after the cut"


def test_holds_still_while_the_subject_only_fidgets(tmp_path):
    jitter = [640 + 20 * np.sin(i) for i in range(3 * FPS)]  # +-20 px, inside the dead zone
    src = tmp_path / "fidget.mp4"
    subject_video(src, jitter, [0] * len(jitter))
    samples, info = sr.analyse(str(src))
    xs = [s["x"] for s in sr.plan(samples, info, "9:16")["samples"]]
    assert max(xs) - min(xs) <= 4, f"camera moved {max(xs) - min(xs)} px for a fidget"


def test_camera_path_never_leaves_the_frame():
    path = sr.camera_path([5000, -300, None, 640], lo=200, hi=1080, dead_zone=40)
    assert all(200 <= x <= 1080 for x in path)
    assert sr.camera_path([None, None], lo=200, hi=1080, dead_zone=40) == [640, 640]


def test_reframe_renders_vertical_with_sound(tmp_path):
    src = tmp_path / "walk.mp4"
    subject_video(src, list(np.linspace(300, 900, 2 * FPS)), [0] * (2 * FPS))
    with_sound = tmp_path / "walk_audio.mp4"
    sa.run([sa.FFMPEG, "-y", "-v", "error", "-i", str(src), "-f", "lavfi", "-i", "sine=f=330:d=2", "-shortest",
            "-c:v", "libx264", "-c:a", "aac", str(with_sound)])
    out, preview = tmp_path / "vertical.mp4", tmp_path / "preview.mp4"
    p = sr.reframe(str(with_sound), str(out), "9:16", preview=str(preview))
    info = se.probe(out)
    assert (info["width"], info["height"]) == (1080, 1920) and info["has_audio"]
    assert info["duration"] == pytest.approx(2.0, abs=0.1)
    assert preview.exists() and p["summary"]["shots"] == 1


def test_export_smart_fit(tmp_path):
    src = tmp_path / "walk.mp4"
    subject_video(src, list(np.linspace(300, 900, 2 * FPS)), [0] * (2 * FPS))
    with_sound = tmp_path / "walk_audio.mp4"
    sa.run([sa.FFMPEG, "-y", "-v", "error", "-i", str(src), "-f", "lavfi", "-i", "sine=f=330:d=2", "-af",
            "volume=0.3", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(with_sound)])
    out = se.export(with_sound, "shorts", "smart", tmp_path)
    info = se.probe(out)
    assert (info["width"], info["height"]) == (1080, 1920)


@pytest.mark.skipif(sa.find_deepfilter() is None, reason="DeepFilterNet isn't set up on this machine")
def test_deepfilter_engine_removes_noise(tmp_path):
    sr_ = 48000
    t = np.arange(2 * sr_) / sr_
    voice = 0.3 * np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t))
    signal = np.concatenate([voice, np.zeros(sr_)]) + np.random.default_rng(0).normal(0, 0.01, 3 * sr_)
    src, out = tmp_path / "n.wav", tmp_path / "c.wav"
    sf.write(src, signal, sr_)
    assert sa.clean(str(src), str(out), engine="deepfilter") == "deepfilter"
    y, _ = sf.read(out)
    pause_before = 20 * np.log10(np.sqrt(np.mean(signal[int(2.3 * sr_):int(2.9 * sr_)] ** 2)))
    pause_after = 20 * np.log10(np.sqrt(np.mean(y[int(2.3 * sr_):int(2.9 * sr_)] ** 2)) + 1e-12)
    assert pause_after < pause_before - 15
