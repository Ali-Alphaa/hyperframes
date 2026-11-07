"""Studio Pack batch 4 tests: audio envelopes and installing the motion blocks.

The blocks' rendering is checked end to end by installing them into a project and running `hyperframes check`
and a render (see the README); these tests cover what the installer writes.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import studio_motion as sm  # noqa: E402

SR = 48000


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    t = np.arange(4 * SR) / SR
    # a burst of sound every second, silence between: the envelope should rise on each burst and fall after
    signal = np.sin(2 * np.pi * 220 * t) * ((t % 1.0) < 0.25) * 0.5
    sf.write(tmp_path / "assets" / "beat.wav", signal, SR)
    return tmp_path


def embedded(html: str, kind: str) -> dict:
    match = re.search(rf'<script type="application/json" id="[^"]+-{kind}">(.*?)</script>', html, re.S)
    return json.loads(match.group(1))


def test_envelope_follows_the_sound(project):
    env = sm.envelope(str(project / "assets" / "beat.wav"), fps=30, bands=16)
    level = np.array(env["level"])
    assert len(level) == pytest.approx(120, abs=2) and len(env["bands"][0]) == 16
    assert level.min() >= 0 and level.max() <= 1
    on = level[[int(s * 30) + 4 for s in range(3)]]       # inside each burst
    off = level[[int(s * 30) + 22 for s in range(3)]]     # in the silence after it
    assert np.all(on > 0.6) and np.all(off < on - 0.4)


def test_add_audio_bars_builds_the_data_in(project):
    snippet = sm.add("audio-bars", project, "beat-bars", audio="assets/beat.wav")
    html = (project / "compositions" / "beat-bars.html").read_text(encoding="utf-8")
    assert not re.search(r"__(ID|DATA|OFFSET|SRC|LOOP|DURATION)__", html)
    assert 'data-composition-id="beat-bars"' in html and 'window.__timelines["beat-bars"]' in html
    assert embedded(html, "envelope")["fps"] == 30
    assert 'data-composition-src="compositions/beat-bars.html"' in snippet
    duration = float(re.search(r'data-duration="([0-9.]+)"', snippet).group(1))
    assert duration == pytest.approx(4.0, abs=0.1)  # the audio's length, to the frame


def test_offset_shortens_the_block(project):
    snippet = sm.add("audio-pulse", project, "pulse", audio="assets/beat.wav", offset=1.5)
    assert re.search(r'data-duration="2\.5', snippet)
    assert "var offset = 1.5;" in (project / "compositions" / "pulse.html").read_text(encoding="utf-8")


def test_reactive_blocks_need_audio(project):
    with pytest.raises(SystemExit, match="needs --audio"):
        sm.add("audio-bars", project)


def test_picture_in_picture_points_at_the_project_video(project, monkeypatch):
    (project / "assets" / "cam.mp4").write_bytes(b"not really a video")
    monkeypatch.setattr(sm, "_probe_duration", lambda path: 5.0)
    snippet = sm.add("picture-in-picture", project, "cam", src="assets/cam.mp4")
    html = (project / "compositions" / "cam.html").read_text(encoding="utf-8")
    assert 'src="../assets/cam.mp4"' in html and "muted" in html
    assert 'data-duration="5"' in snippet


def test_lottie_player_uses_the_sample_and_loops(project):
    sm.add("lottie-player", project, "icon", loop=True, duration=8)
    html = (project / "compositions" / "icon.html").read_text(encoding="utf-8")
    anim = embedded(html, "lottie")
    assert anim["fr"] == 30 and anim["op"] == 60
    assert '(true ? "loop" : "hold")' in html


def test_lottie_player_takes_a_project_file(project):
    anim = json.loads((sm.BLOCKS / "lottie-player" / "assets" / "sample-check.json").read_text())
    anim["nm"] = "mine"
    (project / "assets" / "mine.json").write_text(json.dumps(anim))
    sm.add("lottie-player", project, "mine", lottie="assets/mine.json")
    assert embedded((project / "compositions" / "mine.html").read_text(encoding="utf-8"), "lottie")["nm"] == "mine"


def test_studio_loops_installs_the_helper(project):
    usage = sm.add("studio-loops", project)
    assert (project / "compositions" / "lib" / "studio-loops.js").exists()
    assert "StudioLoops.add" in usage


def test_ids_are_checked(project):
    with pytest.raises(SystemExit, match="lowercase"):
        sm.add("lottie-player", project, "Bad Id")


def test_every_block_is_listed_and_valid():
    found = sm.blocks()
    assert {"audio-bars", "audio-pulse", "picture-in-picture", "lottie-player", "studio-loops"} <= set(found)
    for meta in found.values():
        for f in meta["files"]:
            assert (meta["_dir"] / f["path"]).exists(), f"{meta['name']}: missing {f['path']}"
