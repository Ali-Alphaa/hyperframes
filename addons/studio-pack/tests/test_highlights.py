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
    assert args == ["--provider", "gemini", "--model", "gemini-2.5-flash"]
    assert env == {"AUTOCLIP_API_KEY": "secret"} and "secret" not in args


def test_no_model_explains_the_choices(monkeypatch):
    monkeypatch.setattr(sh, "ollama_running", lambda: False)
    monkeypatch.setattr(sh, "user_setting", lambda n: None)
    with pytest.raises(SystemExit, match="Ollama.*GEMINI_API_KEY"):
        sh.pick_provider(None, None)
