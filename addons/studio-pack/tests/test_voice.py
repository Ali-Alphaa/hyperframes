"""Studio Pack batch 5 tests: voice enrolment rules, sample checks, script splitting, engine switches.

Cloning itself needs the GPU voice environment and is measured on real voices (see the README): speaker
similarity with Resemblyzer, intelligibility with faster-whisper.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import studio_voice as sv  # noqa: E402

SR = 48000


def speechlike(seconds: float, level: float = 0.2) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    tone = sum(np.sin(2 * np.pi * 130 * k * t) / k for k in range(1, 8))
    return level * tone / np.abs(tone).max() * (0.55 + 0.45 * np.sin(2 * np.pi * 4 * t))


@pytest.fixture
def voices(tmp_path, monkeypatch):
    monkeypatch.setattr(sv, "VOICES", tmp_path / "voices")
    return tmp_path / "voices"


def test_enrol_requires_consent(tmp_path, voices):
    sf.write(tmp_path / "s.wav", speechlike(20), SR)
    with pytest.raises(SystemExit, match="consent"):
        sv.enrol("me", str(tmp_path / "s.wav"), "")
    with pytest.raises(SystemExit, match="consent"):
        sv.enrol("me", str(tmp_path / "s.wav"), "whatever")


def test_enrol_stores_a_clean_reference_and_the_consent(tmp_path, voices):
    sf.write(tmp_path / "s.wav", speechlike(20) + np.random.default_rng(0).normal(0, 0.003, 20 * SR), SR)
    folder = sv.enrol("me", str(tmp_path / "s.wav"), "This is my own voice")
    meta = json.loads((folder / "voice.json").read_text())
    assert meta["consent"] == "This is my own voice" and meta["sample"]["seconds"] == pytest.approx(20, abs=0.2)
    assert (folder / "reference.wav").exists()
    assert sv.load_voice("me")["reference"].endswith("reference.wav")


def test_unknown_or_unconsented_voices_are_refused(tmp_path, voices):
    with pytest.raises(SystemExit, match="no voice"):
        sv.load_voice("ghost")
    (voices / "x").mkdir(parents=True)
    (voices / "x" / "voice.json").write_text(json.dumps({"name": "x", "consent": ""}))
    with pytest.raises(SystemExit, match="no consent"):
        sv.load_voice("x")


def test_sample_check_names_the_problems(tmp_path):
    sf.write(tmp_path / "short.wav", speechlike(4), SR)
    assert any("too short" in p for p in sv.check_sample(str(tmp_path / "short.wav"))["problems"])
    clipped = np.clip(speechlike(20, 1.5), -1, 1)
    sf.write(tmp_path / "clip.wav", clipped, SR)
    assert any("clipping" in p for p in sv.check_sample(str(tmp_path / "clip.wav"))["problems"])
    # real speech pauses between phrases; the quiet in those pauses is what the noise check measures
    phrases = [np.concatenate([speechlike(2.5), np.zeros(SR // 2)]) for _ in range(9)]
    sf.write(tmp_path / "good.wav", np.concatenate(phrases), SR)
    assert sv.check_sample(str(tmp_path / "good.wav"))["problems"] == []


def test_script_splits_on_blank_lines_and_skips_comments():
    text = "# notes for me\n\nFirst line,\nstill first.\n\n\nSecond line.\n"
    assert sv.split_script(text) == ["First line, still first.", "Second line."]


def test_elevenlabs_is_off_without_your_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    with pytest.raises(SystemExit, match="paid option"):
        sv.speak_lines(None, [{"text": "hi", "out": str(tmp_path / "o.wav")}], engine="elevenlabs")
