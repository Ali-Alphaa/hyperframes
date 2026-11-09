"""Studio Pack batch 6 tests: footage search logic. Transcription and CLIP are measured on real footage (README);
these tests cover matching, grouping, blank frames and the index bookkeeping, with no models needed."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import studio_search as ss  # noqa: E402

DATA = {"version": 1, "files": {
    "talk.mp4": {"key": "k1", "duration": 30, "frames": [], "segments": [
        {"start": 1.0, "end": 4.0, "text": "A pullback is a pause inside a trend."},
        {"start": 5.0, "end": 8.0, "text": "Pullbacks end when price closes above the swing high."},
        {"start": 9.0, "end": 12.0, "text": "Nothing to see here."}]},
    "other.wav": {"key": "k2", "duration": 10, "frames": [], "segments": [
        {"start": 0.5, "end": 3.0, "text": "The swing high matters."}]},
}}


def test_spoken_search_finds_word_forms():
    hits = ss.spoken_hits(DATA, "pullback")
    assert [(h["file"], h["start"]) for h in hits] == [("talk.mp4", 1.0), ("talk.mp4", 5.0)]


def test_phrase_ranks_above_scattered_words():
    hits = ss.spoken_hits(DATA, "swing high")
    assert hits[0]["score"] > 1 and {h["file"] for h in hits} == {"talk.mp4", "other.wav"}


def test_no_spoken_hit_for_unsaid_words():
    assert ss.spoken_hits(DATA, "public health") == []


def test_blank_frames_are_found(tmp_path):
    Image.new("RGB", (64, 36), (5, 5, 5)).save(tmp_path / "00001.jpg")
    noise = (np.random.default_rng(0).random((36, 64, 3)) * 255).astype("uint8")
    Image.fromarray(noise).save(tmp_path / "00002.jpg")
    assert ss.blank_frames(tmp_path) == [0]


def test_visual_hits_group_neighbouring_frames(tmp_path, monkeypatch):
    store = tmp_path / ".studio-index"
    (store / "frames" / "k1").mkdir(parents=True)
    for i in range(6):  # all textured, so none count as blank
        arr = (np.random.default_rng(i).random((36, 64, 3)) * 255).astype("uint8")
        Image.fromarray(arr).save(store / "frames" / "k1" / f"{i + 1:05d}.jpg")
    emb = np.zeros((6, 4), np.float16)
    emb[:, 1] = 1
    emb[2:4] = [1, 0, 0, 0]  # frames 3-4 match the query
    np.save(store / "k1.npy", emb)
    data = {"files": {"v.mp4": {"key": "k1", "duration": 12, "frames": [1, 3, 5, 7, 9, 11], "segments": []}}}

    def fake_embed(job):
        np.save(job["out"], np.array([[1, 0, 0, 0]] * 3, np.float32))
    monkeypatch.setattr(ss, "_clip_embed", fake_embed)
    hits = ss.visual_hits(tmp_path, data, "anything")
    assert len(hits) == 1 and (hits[0]["start"], hits[0]["end"]) == (4.0, 8.0)
    assert hits[0]["thumb"].endswith("00003.jpg")


def test_search_needs_an_index(tmp_path):
    with pytest.raises(SystemExit, match="isn't indexed"):
        ss.search(tmp_path, "x")


def test_stem_keeps_short_words():
    assert ss.stem("pullbacks") == "pullback" and ss.stem("is") == "is" and ss.stem("traders") == "trad"
    assert ss.words("Traders' trading") == ["trad", "trad"]


def test_index_skips_unchanged_files_and_drops_deleted(tmp_path, monkeypatch):
    (tmp_path / "a.wav").write_bytes(b"x" * 10)
    calls = []
    monkeypatch.setattr(ss, "_duration", lambda p: 1.0)
    monkeypatch.setattr(ss, "_has_audio", lambda p: (calls.append(p.name), False)[1])
    ss.index(tmp_path, log=lambda *_: None)
    ss.index(tmp_path, log=lambda *_: None)
    assert calls == ["a.wav"], "unchanged file indexed only once"
    (tmp_path / "a.wav").unlink()
    data = ss.index(tmp_path, log=lambda *_: None)
    assert data["files"] == {}
    assert json.loads((tmp_path / ".studio-index" / "index.json").read_text())["files"] == {}
