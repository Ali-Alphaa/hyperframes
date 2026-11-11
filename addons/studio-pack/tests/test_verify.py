"""Render checks: word diff, pauses, audio/video length."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import studio_verify as sv  # noqa: E402


def heard(*words, step=0.4):
    return [{"start": i * step, "end": i * step + 0.3, "word": w, "p": 0.9} for i, w in enumerate(words)]


def test_norm_spoken_forms():
    assert sv.norm("Open .env, then /tmp!") == ["open", "dot", "env", "then", "slash", "tmp"]


def test_diff_finds_extra_missing_changed():
    issues = sv.diff_words(sv.norm("a pullback is not a reversal"),
                           heard("a", "um", "pullback", "is", "a", "reversal"))
    kinds = {i["kind"]: i for i in issues}
    assert kinds["extra"]["heard"] == "um" and kinds["extra"]["at"] == 0.4
    assert kinds["missing"]["expected"] == "not"
    assert not sv.diff_words(sv.norm("Hello, world."), heard("hello", "world"))


def test_long_pauses():
    words = heard("one", "two") + [{"start": 2.0, "end": 2.3, "word": "three", "p": 0.9}]
    assert [g["between"] for g in sv.gaps(words)] == ["two ... three"]


def test_av_lengths(tmp_path):
    out = tmp_path / "t.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=64x64:d=2", "-f", "lavfi", "-i",
                    "sine=d=3", "-c:v", "libx264", "-c:a", "aac", str(out)], check=True)
    r = sv.check_av(out)
    assert r["flagged"] and r["difference"] > 0.9
