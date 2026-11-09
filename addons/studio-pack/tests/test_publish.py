"""Studio Pack batch 7 tests: publishing plans, drafts and the approval gate, with Postiz simulated.
Nothing here talks to a real account. The live draft-and-cancel path was checked against the user's Postiz."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import studio_publish as sp  # noqa: E402


class FakePostiz:
    def __init__(self):
        self.calls = []
        self.n = 0

    def __call__(self, *args, expect_json=True):
        self.calls.append(args)
        if args[0] == "integrations:list":
            return [{"id": "x1", "identifier": "x", "name": "Ali", "disabled": False}]
        if args[0] == "upload":
            return {"path": "https://postiz.example/uploads/v.mp4"}
        if args[0] == "posts:create":
            self.n += 1
            return [{"postId": f"p{self.n}", "integration": "x1"}]
        if args[0] == "posts:delete":
            return "deleted"
        raise AssertionError(args)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    fake = FakePostiz()
    monkeypatch.setattr(sp, "postiz", fake)
    monkeypatch.setattr(sp, "HOME", tmp_path / "publishing")
    monkeypatch.setattr(sp, "_seconds", lambda p: 60.0)
    (tmp_path / "v.mp4").write_bytes(b"video")
    when = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan = {"title": "Pullback", "date": when, "posts": [
        {"platform": "x", "video": "v.mp4", "text": "Pullback or reversal?"},
        {"platform": "tiktok", "video": "v.mp4", "text": "Pullback or reversal? #trading"}]}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    return fake, tmp_path


def test_draft_makes_postiz_drafts_and_marks_buffer(setup):
    fake, tmp = setup
    record = sp.draft(tmp / "plan.json")
    x, tiktok = record["posts"]
    assert x["route"] == "postiz" and x["draft_id"] == "p1"
    create = next(c for c in fake.calls if c[0] == "posts:create")
    assert create[create.index("-t") + 1] == "draft", "draft never schedules"
    assert json.loads(create[create.index("--settings") + 1])["who_can_reply_post"] == "everyone"
    assert tiktok["route"] == "buffer" and tiktok["media"].startswith("https://")
    assert record["status"] == "drafted"
    log = (tmp / "publishing" / "log.jsonl").read_text().splitlines()
    assert json.loads(log[-1])["event"] == "drafted"


def test_approve_requires_the_users_words(setup):
    _, tmp = setup
    rid = sp.draft(tmp / "plan.json")["id"]
    with pytest.raises(SystemExit, match="user's own words"):
        sp.approve(rid, "")


def test_approve_schedules_then_deletes_the_draft(setup):
    fake, tmp = setup
    rid = sp.draft(tmp / "plan.json")["id"]
    record = sp.approve(rid, "yes, post the pullback one")
    creates = [c for c in fake.calls if c[0] == "posts:create"]
    assert creates[-1][creates[-1].index("-t") + 1] == "schedule"
    assert ("posts:delete", "p1") in fake.calls
    assert record["status"] == "scheduled" and record["approved"] == "yes, post the pullback one"
    with pytest.raises(SystemExit, match="not a draft"):
        sp.approve(rid, "again")


def test_approve_refuses_a_past_time(setup):
    _, tmp = setup
    rid = sp.draft(tmp / "plan.json")["id"]
    with pytest.raises(SystemExit, match="past"):
        sp.approve(rid, "yes", date="2020-01-01T10:00:00Z")


def test_cancel_deletes_drafts(setup):
    fake, tmp = setup
    rid = sp.draft(tmp / "plan.json")["id"]
    assert sp.cancel(rid)["status"] == "cancelled"
    assert ("posts:delete", "p1") in fake.calls


def test_plan_problems_are_caught(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "_seconds", lambda p: 300.0)
    (tmp_path / "v.mp4").write_bytes(b"v")
    plan = {"date": "2020-01-01T00:00:00Z", "posts": [
        {"platform": "x", "video": "v.mp4", "text": "a" * 5000}, {"platform": "myspace", "text": "hi"},
        {"platform": "tiktok", "video": "missing.mp4", "text": "hi"}]}
    problems = " | ".join(sp.validate(plan, tmp_path))
    for expected in ("in the past", "limit is 4000", "the limit is 140 s", "unknown platform", "not found"):
        assert expected in problems, expected


def test_dates_need_a_timezone():
    assert sp.parse_date("2026-11-16T15:00:00") is None
    assert sp.parse_date("2026-11-16T15:00:00Z") is not None
