#!/usr/bin/env python3
"""Studio Pack publishing: drafts in your Postiz, scheduled only after you approve each one.

  channels  list the accounts Postiz can post to
  draft     create Postiz drafts from a post plan (JSON); platforms Postiz doesn't have are marked for Buffer
  status    list the drafts this tool made and what happened to them
  approve   schedule one plan's drafts; requires --approved with the user's own words of approval
  cancel    delete one plan's drafts from Postiz

Nothing is ever posted or scheduled by `draft`. `approve` re-creates each draft as a scheduled post (the Postiz API
can't edit a post) and then deletes the draft. Every step is logged to ~/video-pipeline/publishing/log.jsonl.

Post plan (written by Claude, shown to the user):
  {"title": "Pullback explainer", "date": "2026-11-16T15:00:00Z",
   "posts": [{"platform": "x", "video": "renders/platforms/video.x.mp4", "text": "...",
              "settings": {"post_type": "post", "who_can_reply_post": "everyone"}},
             {"platform": "tiktok", "video": "renders/platforms/video.tiktok.mp4", "text": "..."}]}

The Postiz key and address are read from the user's saved settings (POSTIZ_API_KEY, POSTIZ_API_URL) and passed to the
postiz tool; they are never printed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_audio import PIPELINE  # noqa: E402

HOME = Path(os.environ.get("STUDIO_PUBLISH_HOME", PIPELINE / "publishing"))
LIMITS = {  # caption length and longest video each platform takes (seconds)
    "x": (4000, 140), "tiktok": (2200, 600), "instagram": (2200, 180), "youtube": (5000, 43200),
}
POSTIZ_IDS = {"x": {"x"}, "tiktok": {"tiktok"}, "instagram": {"instagram", "instagram-standalone"},
              "youtube": {"youtube"}}


# -------------------------------------------------------------------- postiz cli
def _env() -> dict:
    env = dict(os.environ)
    if sys.platform == "win32" and not (env.get("POSTIZ_API_KEY") and env.get("POSTIZ_API_URL")):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            for name in ("POSTIZ_API_KEY", "POSTIZ_API_URL"):
                try:
                    env[name] = winreg.QueryValueEx(key, name)[0].strip()
                except FileNotFoundError:
                    pass
    if not env.get("POSTIZ_API_KEY") or not env.get("POSTIZ_API_URL"):
        raise SystemExit("Postiz isn't set up: POSTIZ_API_KEY and POSTIZ_API_URL must be saved (see the README)")
    return env


def postiz(*args: str, expect_json: bool = True) -> object:
    exe = shutil.which("postiz") or shutil.which("postiz.cmd")
    if not exe:
        raise SystemExit("the postiz command-line tool isn't installed (npm install -g postiz)")
    proc = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=_env())
    out = proc.stdout
    if proc.returncode == 0 and not expect_json:
        return out.strip()  # e.g. "Post ... deleted successfully!"
    starts = [i for i in (out.find("["), out.find("{")) if i >= 0]
    if proc.returncode != 0 or not starts:
        raise SystemExit(f"postiz {args[0]} failed: {(proc.stderr or out)[-800:]}")
    return json.loads(out[min(starts):])


def channels() -> list[dict]:
    return [{"id": c["id"], "platform": c["identifier"], "name": c["name"], "disabled": c.get("disabled", False)}
            for c in postiz("integrations:list")]


def channel_for(platform: str, chans: list[dict]) -> dict | None:
    return next((c for c in chans if c["platform"] in POSTIZ_IDS.get(platform, {platform}) and not c["disabled"]),
                None)


# ------------------------------------------------------------------------ plans
def validate(plan: dict, base: Path) -> list[str]:
    problems = []
    when = parse_date(plan.get("date", ""))
    if when is None:
        problems.append("date must be ISO 8601 with a timezone, e.g. 2026-11-16T15:00:00Z")
    elif when < dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5):
        problems.append("date is in the past (or under 5 minutes away)")
    if not plan.get("posts"):
        problems.append("no posts in the plan")
    for post in plan.get("posts", []):
        platform = post.get("platform", "?")
        if platform not in LIMITS:
            problems.append(f"{platform}: unknown platform (use {', '.join(LIMITS)})")
            continue
        max_chars, max_secs = LIMITS[platform]
        if len(post.get("text", "")) > max_chars:
            problems.append(f"{platform}: caption is {len(post['text'])} characters, the limit is {max_chars}")
        video = post.get("video")
        if video:
            path = (base / video) if not Path(video).is_absolute() else Path(video)
            if not path.exists():
                problems.append(f"{platform}: video not found: {path}")
            else:
                secs = _seconds(path)
                if secs > max_secs:
                    problems.append(f"{platform}: video is {secs:.0f} s, the limit is {max_secs} s")
    return problems


def parse_date(value: str) -> dt.datetime | None:
    try:
        when = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else None


def _seconds(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def log(event: str, **fields) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    entry = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "event": event, **fields}
    with open(HOME / "log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def record_path(rid: str) -> Path:
    return HOME / "plans" / f"{rid}.json"


def save(record: dict) -> None:
    record_path(record["id"]).parent.mkdir(parents=True, exist_ok=True)
    record_path(record["id"]).write_text(json.dumps(record, indent=1), encoding="utf-8")


def load(rid: str) -> dict:
    path = record_path(rid)
    if not path.exists():
        raise SystemExit(f"no plan {rid!r}; see `status`")
    return json.loads(path.read_text(encoding="utf-8"))


def draft(plan_file: Path) -> dict:
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    problems = validate(plan, plan_file.parent)
    if problems:
        raise SystemExit("plan not drafted:\n  " + "\n  ".join(problems))
    chans = channels()
    record = {"id": dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4], "title": plan.get("title", ""),
              "date": plan["date"], "status": "drafted", "posts": []}
    for post in plan["posts"]:
        entry = {"platform": post["platform"], "text": post.get("text", ""), "settings": post.get("settings", {}),
                 "video": post.get("video")}
        chan = channel_for(post["platform"], chans)
        media = ""
        if post.get("video"):
            # Postiz hosts the upload at a public address on the user's own server; Buffer needs exactly that
            path = Path(post["video"]) if Path(post["video"]).is_absolute() else plan_file.parent / post["video"]
            media = postiz("upload", str(path))["path"]
        if chan is None:
            # not connected in Postiz: Claude makes a Buffer draft (saveToDraft) with this media URL, then records
            # its id with `buffer-id`
            entry.update(route="buffer", media=media, buffer_draft_id=None)
            record["posts"].append(entry)
            continue
        args = ["posts:create", "-c", entry["text"], "-i", chan["id"], "-s", plan["date"], "-t", "draft",
                "--shortLink", "false"]
        if media:
            args += ["-m", media]
        settings = dict(entry["settings"])
        if post["platform"] == "x":
            settings.setdefault("post_type", "post")
            settings.setdefault("who_can_reply_post", "everyone")
        if settings:
            args += ["--settings", json.dumps(settings)]
        created = postiz(*args)
        entry.update(route="postiz", channel=chan["id"], channel_name=chan["name"], media=media,
                     settings=settings, draft_id=created[0]["postId"])
        record["posts"].append(entry)
    save(record)
    log("drafted", plan=record["id"], title=record["title"], date=record["date"],
        posts=[{k: p.get(k) for k in ("platform", "route", "draft_id")} for p in record["posts"]])
    return record


def approve(rid: str, approved: str, date: str | None = None) -> dict:
    if not approved or len(approved.strip()) < 2:
        raise SystemExit("approve needs --approved with the user's own words approving this post")
    record = load(rid)
    if record["status"] != "drafted":
        raise SystemExit(f"plan {rid} is {record['status']}, not a draft")
    when = date or record["date"]
    parsed = parse_date(when)
    if parsed is None or parsed < dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2):
        raise SystemExit(f"schedule time {when} is in the past or too soon; pass --date")
    for post in record["posts"]:
        if post["route"] != "postiz":
            continue
        args = ["posts:create", "-c", post["text"], "-i", post["channel"], "-s", when, "-t", "schedule",
                "--shortLink", "false"]
        if post.get("media"):
            args += ["-m", post["media"]]
        if post.get("settings"):
            args += ["--settings", json.dumps(post["settings"])]
        post["scheduled_id"] = postiz(*args)[0]["postId"]
        _delete(post["draft_id"])
    record.update(status="scheduled", date=when, approved=approved,
                  approved_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    save(record)
    log("approved", plan=rid, approved=approved, date=when,
        posts=[{k: p.get(k) for k in ("platform", "route", "scheduled_id")} for p in record["posts"]])
    return record


def _delete(post_id: str) -> None:
    """Delete a Postiz post; one that's already gone counts as deleted."""
    try:
        postiz("posts:delete", post_id, expect_json=False)
    except SystemExit as err:
        if not any(w in str(err).lower() for w in ("not found", "404", "does not exist")):
            raise


def cancel(rid: str) -> dict:
    record = load(rid)
    if record["status"] != "drafted":
        raise SystemExit(f"plan {rid} is {record['status']}; only drafts can be cancelled here (delete scheduled "
                         "posts in Postiz)")
    for post in record["posts"]:
        if post["route"] == "postiz":
            _delete(post["draft_id"])
    record["status"] = "cancelled"
    save(record)
    log("cancelled", plan=rid)
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("channels")
    d = sub.add_parser("draft")
    d.add_argument("plan", help="post plan JSON")
    d.add_argument("--check", action="store_true", help="validate only, create nothing")
    sub.add_parser("status")
    b = sub.add_parser("buffer-id", help="record the Buffer draft Claude made for a platform in a plan")
    b.add_argument("plan_id"), b.add_argument("platform"), b.add_argument("buffer_id")
    a = sub.add_parser("approve")
    a.add_argument("plan_id")
    a.add_argument("--approved", required=True, help="the user's own words approving it")
    a.add_argument("--date", help="new schedule time (ISO 8601 with timezone)")
    c = sub.add_parser("cancel")
    c.add_argument("plan_id")
    args = ap.parse_args(argv)
    if args.cmd == "channels":
        for ch in channels():
            print(f"{ch['platform']:10s} {ch['name']}  ({ch['id']}){'  DISABLED' if ch['disabled'] else ''}")
    elif args.cmd == "draft":
        plan_file = Path(args.plan)
        if args.check:
            problems = validate(json.loads(plan_file.read_text(encoding="utf-8")), plan_file.parent)
            print("plan OK" if not problems else "problems:\n  " + "\n  ".join(problems))
            return 1 if problems else 0
        record = draft(plan_file)
        print(f"plan {record['id']}: drafted for {record['date']}")
        for p in record["posts"]:
            where = f"Postiz draft {p['draft_id']} on {p['channel_name']}" if p["route"] == "postiz" else \
                f"not in Postiz: Claude drafts it in Buffer with video {p['media'] or '(none)'}"
            print(f"  {p['platform']:10s} {where}")
    elif args.cmd == "status":
        for path in sorted((HOME / "plans").glob("*.json")) if (HOME / "plans").exists() else []:
            r = json.loads(path.read_text(encoding="utf-8"))
            platforms = ", ".join(f"{p['platform']}({p['route']})" for p in r["posts"])
            print(f"{r['id']}  {r['status']:10s} {r['date']}  {r['title'][:40]}  [{platforms}]")
    elif args.cmd == "buffer-id":
        record = load(args.plan_id)
        post = next((p for p in record["posts"] if p["platform"] == args.platform and p["route"] == "buffer"), None)
        if post is None:
            raise SystemExit(f"plan {args.plan_id} has no Buffer post for {args.platform}")
        post["buffer_draft_id"] = args.buffer_id
        save(record)
        log("buffer-draft", plan=args.plan_id, platform=args.platform, buffer_id=args.buffer_id)
        print(f"recorded Buffer draft {args.buffer_id} for {args.platform}")
    elif args.cmd == "approve":
        r = approve(args.plan_id, args.approved, args.date)
        print(f"plan {r['id']} scheduled in Postiz for {r['date']}")
        for p in r["posts"]:
            if p["route"] == "buffer":
                print(f"  {p['platform']}: now convert Buffer draft {p.get('buffer_draft_id')} to scheduled "
                      f"(create_post draftId=..., mode=customScheduled, dueAt={r['date']})")
    elif args.cmd == "cancel":
        cancel(args.plan_id)
        print(f"plan {args.plan_id} cancelled; its Postiz drafts are deleted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
