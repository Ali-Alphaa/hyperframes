"""Studio Pack batch 1 tests. Self-contained: every signal is generated, nothing depends on a user's files.

Run: ~/video-pipeline/.ai-venv/Scripts/python.exe -m pytest addons/studio-pack/tests -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import studio_audio as sa  # noqa: E402
import studio_export as se  # noqa: E402

SR = 48000


def rms_db(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12)


def speechlike(seconds: float, level: float) -> np.ndarray:
    """Voiced bursts: a 140 Hz harmonic tone, syllable-modulated, so gates and normalisers see speech."""
    t = np.arange(int(seconds * SR)) / SR
    tone = sum(np.sin(2 * np.pi * 140 * k * t) / k for k in range(1, 8))
    envelope = 0.55 + 0.45 * np.sin(2 * np.pi * 4 * t)
    return level * tone * envelope / np.max(np.abs(tone))


@pytest.fixture
def noisy_voice(tmp_path: Path) -> tuple[Path, slice, slice]:
    rng = np.random.default_rng(1)
    voice = np.concatenate([speechlike(2.0, 0.3), np.zeros(SR), speechlike(2.0, 0.3)])
    noise = rng.normal(0, 0.006, voice.size) + 0.01 * np.sin(2 * np.pi * 50 * np.arange(voice.size) / SR)
    path = tmp_path / "noisy.wav"
    sf.write(path, voice + noise, SR)
    return path, slice(int(2.3 * SR), int(2.8 * SR)), slice(int(0.3 * SR), int(1.7 * SR))


def test_clean_quiets_pauses_and_keeps_speech(noisy_voice, tmp_path):
    path, gap, speech = noisy_voice
    out = tmp_path / "clean.wav"
    assert sa.clean(str(path), str(out), engine="ffmpeg", strength="medium") == "ffmpeg"
    before, _ = sf.read(path)
    after, _ = sf.read(out)
    assert rms_db(after[gap]) < rms_db(before[gap]) - 15, "pauses should be at least 15 dB quieter"
    assert abs(rms_db(after[speech]) - rms_db(before[speech])) < 3, "speech level should stay within 3 dB"


def test_paid_engine_is_off_without_a_key(noisy_voice, tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="paid option is off"):
        sa.clean(str(noisy_voice[0]), str(tmp_path / "x.wav"), engine="elevenlabs")


def test_level_evens_phrases_and_hits_target(tmp_path):
    quiet_then_loud = np.concatenate([speechlike(3.0, 0.03), np.zeros(SR // 2), speechlike(3.0, 0.5)])
    src, out = tmp_path / "uneven.wav", tmp_path / "even.wav"
    sf.write(src, quiet_then_loud, SR)
    sa.level(str(src), str(out), target=-16)
    y, _ = sf.read(out)
    spread_before = rms_db(quiet_then_loud[3 * SR + SR // 2:]) - rms_db(quiet_then_loud[: 3 * SR])
    spread_after = rms_db(y[3 * SR + SR // 2 + SR // 2:]) - rms_db(y[SR // 2: 3 * SR])
    assert spread_before > 20
    assert spread_after < spread_before - 10, f"spread {spread_before:.1f} dB -> {spread_after:.1f} dB"
    assert abs(float(sa.measure_loudness(out)["input_i"]) + 16) < 1.0


def test_beats_found_on_a_click_track_and_written_for_studio(tmp_path):
    clicks = np.zeros(10 * SR)
    for k in range(20):  # 120 bpm
        start = int(k * 0.5 * SR)
        clicks[start:start + 400] = np.hanning(400) * np.sin(2 * np.pi * 1000 * np.arange(400) / SR)
    (tmp_path / "music").mkdir()
    sf.write(tmp_path / "music" / "clicks.wav", clicks, SR)
    times, strengths, bpm = sa.detect_beats(str(tmp_path / "music" / "clicks.wav"))
    assert bpm == pytest.approx(120, abs=3)
    assert np.median(np.diff(times)) == pytest.approx(0.5, abs=0.02)
    path = sa.write_beats(tmp_path, "music/clicks.wav", times, strengths)
    data = json.loads(path.read_text())
    assert data["version"] == 1 and data["audio"] == "music/clicks.wav"
    assert path == tmp_path / "beats" / "music" / "clicks.wav.json"
    assert all(0 <= b["strength"] <= 1 for b in data["beats"])


def test_snap_moves_only_within_reach():
    beats = [0.5, 1.0, 1.5, 2.0]
    assert sa.snap_times([0.62, 1.9, 3.0], beats, max_shift=0.25) == [(0.62, 0.5), (1.9, 2.0), (3.0, 3.0)]


def test_snap_and_duck_edit_a_composition(tmp_path):
    (tmp_path / "beats").mkdir()
    (tmp_path / "beats" / "m.json").write_text(json.dumps({"version": 1, "audio": "m", "beats": [
        {"time": 4.0, "strength": 1}]}))
    (tmp_path / "index.html").write_text(
        '<div id="scene-a" class="clip" data-start="3.9" data-duration="2"></div>\n'
        '<audio id="bed" data-start="1" data-duration="20" src="m.wav"></audio>\n'
        '<audio id="vo-1" data-start="2" data-duration="1.5" src="a.wav"></audio>\n'
        '<audio id="vo-2" data-start="3.8" data-duration="2" src="b.wav"></audio>\n', encoding="utf-8")
    assert sa.main(["snap", "--project", str(tmp_path), "--beats", "beats/m.json", "--clips", "#scene-*",
                    "--apply"]) == 0
    assert 'data-start="4.000"' in (tmp_path / "index.html").read_text() or "data-start='4.000'" in (
        tmp_path / "index.html").read_text()
    assert sa.main(["duck", "--project", str(tmp_path), "--apply"]) == 0
    text = (tmp_path / "index.html").read_text()
    lane = json.loads(sa._attr(next(m.group(0) for m in sa.TAG_RE.finditer(text) if 'id="bed"' in m.group(0)),
                               "data-automation"))
    points = lane["lanes"][0]["points"]
    assert points[0] == {"t": 0.0, "v": 1.0}, "bed must start at full level, not already ducked"
    # the two voice clips (2-3.5 and 3.8-5.8, gap 0.3 s) merge into one span; clip-local = minus bed start 1 s
    assert {"t": 1.0, "v": 0.3} in points and {"t": 4.8, "v": 0.3} in points
    assert points[-1] == {"t": 5.2, "v": 1.0}
    assert (tmp_path / "index.html.studio-pack.bak").exists()


def test_duck_lane_never_goes_back_in_time():
    lane = sa.duck_lane([(0.1, 1.0), (1.2, 2.0)], music_start=0, music_end=10)
    ts = [p["t"] for p in lane["lanes"][0]["points"]]
    assert ts == sorted(ts) and len(ts) == len(set(ts))


@pytest.fixture(scope="module")
def landscape_clip(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("v") / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:d=2",
                    "-f", "lavfi", "-i", "sine=f=440:d=2", "-af", "volume=0.2", "-shortest", "-c:v", "libx264",
                    "-c:a", "aac", str(path)], check=True)
    return path


@pytest.mark.parametrize("platform,size", [("shorts", (1080, 1920)), ("youtube", (1920, 1080))])
@pytest.mark.parametrize("fit", ["blur", "crop", "pad"])
def test_export_matches_platform_spec(landscape_clip, tmp_path, platform, size, fit):
    out = se.export(landscape_clip, platform, fit, tmp_path)
    info = se.probe(out)
    assert (info["width"], info["height"]) == size
    assert info["fps"] == pytest.approx(30)
    assert abs(float(sa.measure_loudness(out)["input_i"]) + 14) < 1.0


def test_export_refuses_too_long(landscape_clip, tmp_path, monkeypatch):
    monkeypatch.setitem(se.PLATFORMS["x"], "max_seconds", 1)
    with pytest.raises(SystemExit, match="the limit is 1s"):
        se.export(landscape_clip, "x", "blur", tmp_path)
