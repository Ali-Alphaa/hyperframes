"""AutoClip bridge: subtitle writing and model choice. AutoClip itself isn't run here."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import studio_highlights as sh  # noqa: E402


def test_srt_times_and_numbering():
    srt = sh.to_srt([{"start": 0.0, "end": 1.5, "text": "Hello"}, {"start": 1.5, "end": 2, "text": " "},
                     {"start": 3661.25, "end": 3662.0, "text": "Later"}])
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,500\nHello\n")
    assert "2\n01:01:01,250 --> 01:01:02,000\nLater" in srt, "blank lines skipped and renumbered"


def test_prefers_local_ollama(monkeypatch):
    monkeypatch.setattr(sh, "ollama_running", lambda: True)
    monkeypatch.setattr(sh, "user_setting", lambda n: "secret")
    args, env = sh.pick_provider(None, None)
    assert args == ["--provider", "ollama"] and env == {}, "a local model sends nothing out"


def test_gemini_key_goes_to_env_not_args(monkeypatch):
    monkeypatch.setattr(sh, "ollama_running", lambda: False)
    monkeypatch.setattr(sh, "user_setting", lambda n: "secret" if n == "GEMINI_API_KEY" else None)
    args, env = sh.pick_provider(None, None)
    assert args == ["--provider", "gemini", "--model", "gemini-3.5-flash"]
    assert env == {"AUTOCLIP_API_KEY": "secret"} and "secret" not in args


def test_no_model_explains_the_choices(monkeypatch):
    monkeypatch.setattr(sh, "ollama_running", lambda: False)
    monkeypatch.setattr(sh, "user_setting", lambda n: None)
    with pytest.raises(SystemExit, match="Ollama.*GEMINI_API_KEY"):
        sh.pick_provider(None, None)


def test_keys_are_blanked_after_a_run(tmp_path):
    settings = tmp_path / "cli-settings.json"
    settings.write_text('{"llm_provider": "gemini", "gemini_api_key": "secret", "openai_api_key": "x", "model_name": "m"}')
    sh.forget_keys(settings)
    text = settings.read_text()
    assert "secret" not in text and '"x"' not in text and '"gemini"' in text


def test_collect_names_clips_best_first_with_their_words(tmp_path):
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"v")
    (tmp_path / "talk.srt").write_text(sh.to_srt([{"start": 1, "end": 2, "text": "Hello there."},
                                                  {"start": 65, "end": 66, "text": "Best bit."}]), encoding="utf-8")
    clip = tmp_path / "x.mp4"
    clip.write_bytes(b"c")
    result = {"clips": [{"id": "1", "start_time": "00:00:01,000", "end_time": "00:00:02,000", "score": 0.7,
                         "score_100": 70, "title": "标题", "file": str(clip)},
                        {"id": "2", "start_time": "00:01:05,000", "end_time": "00:01:06,000", "score": 0.9,
                         "score_100": 90, "title": "最好", "file": str(clip)}], "collections": []}
    out = sh.collect(result, video)
    import json
    rows = json.loads((out / "highlights.json").read_text(encoding="utf-8"))["clips"]
    assert [r["file"] for r in rows] == ["01-score90-1m05s.mp4", "02-score70-0m01s.mp4"]
    assert rows[0]["spoken"] == "Best bit." and (out / "01-score90-1m05s.mp4").exists()
