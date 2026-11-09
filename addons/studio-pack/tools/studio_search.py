#!/usr/bin/env python3
"""Studio Pack footage search (Resolve 21's IntelliSearch, run locally): find the moment in your footage where
something is said or shown.

  index   transcribe every clip in a folder (faster-whisper) and fingerprint a frame every 2 s (CLIP); only new or
          changed files are processed again
  search  find a phrase that was spoken, or something that is on screen, and list the moments
  export  cut one of the moments out as its own clip

  python studio_search.py index footage/
  python studio_search.py search footage/ "pullback" --spoken
  python studio_search.py search footage/ "a soldier holding a rifle" --visual --sheet hits.jpg
  python studio_search.py export footage/ "a soldier holding a rifle" --visual --pick 1 --out rifle.mp4

The index lives in <folder>/.studio-index/ and never changes your footage. Spoken search matches words (any order;
"pullbacks" finds "pullback"); visual search matches meaning ("a red chart going down", "two people talking").
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_audio import FFMPEG, PIPELINE, run  # noqa: E402

VOICE_PY = PIPELINE / ".voice-venv" / "Scripts" / "python.exe"
WHISPER_PY = PIPELINE / ".venv" / "Scripts" / "python.exe"
TOOLS = Path(__file__).resolve().parent
VIDEO = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
AUDIO = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}


# ----------------------------------------------------------------------- index
def media_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in VIDEO | AUDIO
                  and ".studio-index" not in p.parts and p.is_file())


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def _has_audio(path: Path) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    return bool(out.strip())


def _key(rel: str) -> str:
    return hashlib.sha1(rel.encode()).hexdigest()[:12]


def blank_frames(frames_dir: Path) -> list[int]:
    """Indexes of frames that are (nearly) one flat colour: fades, black or blank screens. CLIP matches them weakly
    to almost any query, so visual search leaves them out."""
    from PIL import Image
    out = []
    for i, shot in enumerate(sorted(frames_dir.glob("*.jpg"))):
        if float(np.asarray(Image.open(shot).convert("L"), np.float32).std()) < 8:
            out.append(i)
    return out


def _clip_embed(job: dict) -> None:
    proc = subprocess.run([str(VOICE_PY), str(TOOLS / "clip_worker.py")], input=json.dumps(job), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"CLIP failed: {proc.stderr[-1200:]}")


def index(folder: Path, every: float = 2.0, log=print) -> dict:
    store = folder / ".studio-index"
    store.mkdir(exist_ok=True)
    path = store / "index.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": 1, "files": {}}
    present = set()
    pending_frames: list[tuple[str, Path, list[float]]] = []
    for media in media_files(folder):
        rel = media.relative_to(folder).as_posix()
        present.add(rel)
        stat = media.stat()
        entry = data["files"].get(rel)
        if entry and entry["size"] == stat.st_size and entry["mtime"] == int(stat.st_mtime) and entry["every"] == every:
            continue
        key = _key(rel)
        log(f"indexing {rel}")
        entry = {"size": stat.st_size, "mtime": int(stat.st_mtime), "every": every, "key": key,
                 "duration": round(_duration(media), 2), "segments": [], "frames": []}
        if _has_audio(media):
            proc = subprocess.run([str(WHISPER_PY), str(TOOLS / "transcribe_worker.py"), str(media)],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace")
            if proc.returncode == 0 and proc.stdout.strip():
                entry["segments"] = json.loads(proc.stdout.strip().splitlines()[-1])
        if media.suffix.lower() in VIDEO:
            frames_dir = store / "frames" / key
            shutil.rmtree(frames_dir, ignore_errors=True)
            frames_dir.mkdir(parents=True)
            run([FFMPEG, "-y", "-v", "error", "-i", str(media), "-vf", f"fps=1/{every},scale=320:-2", "-q:v", "4",
                 str(frames_dir / "%05d.jpg")])
            shots = sorted(frames_dir.glob("*.jpg"))
            # the n-th frame of fps=1/every sits at (n - 0.5) * every seconds
            entry["frames"] = [round((i + 0.5) * every, 2) for i in range(len(shots))]
            entry["blank"] = blank_frames(frames_dir)
            if shots:
                pending_frames.append((key, frames_dir, entry["frames"]))
        data["files"][rel] = entry
    for key, frames_dir, _ in pending_frames:
        _clip_embed({"images": [str(p) for p in sorted(frames_dir.glob("*.jpg"))], "out": str(store / f"{key}.npy")})
    for rel in set(data["files"]) - present:  # deleted footage
        key = data["files"].pop(rel)["key"]
        shutil.rmtree(store / "frames" / key, ignore_errors=True)
        (store / f"{key}.npy").unlink(missing_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


# ---------------------------------------------------------------------- search
def stem(word: str) -> str:
    word = word.lower().strip("'")
    for suffix in ("ing", "ers", "er", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def words(text: str) -> list[str]:
    return [stem(w) for w in re.findall(r"[A-Za-z0-9']+", text)]


def spoken_hits(data: dict, query: str, top: int = 10) -> list[dict]:
    want = [w for w in words(query) if w not in {"the", "a", "an", "and", "of", "to"}] or words(query)
    phrase = " ".join(words(query))
    hits = []
    for rel, entry in data["files"].items():
        for seg in entry["segments"]:
            have = words(seg["text"])
            found = sum(1 for w in want if w in have)
            if not found:
                continue
            score = found / len(want) + (0.5 if phrase and phrase in " ".join(have) else 0)
            if score >= 0.5:
                hits.append({"file": rel, "start": seg["start"], "end": seg["end"], "score": round(score, 3),
                             "text": seg["text"], "kind": "spoken"})
    return sorted(hits, key=lambda h: (-h["score"], h["file"], h["start"]))[:top]


def visual_hits(folder: Path, data: dict, query: str, top: int = 10, every: float = 2.0) -> list[dict]:
    store = folder / ".studio-index"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "q.npy"
        # prompt ensembling: CLIP matches "a photo of ..." better than a bare phrase
        _clip_embed({"texts": [query, f"a photo of {query}", f"a video frame showing {query}"], "out": str(out)})
        q = np.load(out).mean(axis=0)
    q /= np.linalg.norm(q)
    rows = []
    for rel, entry in data["files"].items():
        npy = store / f"{entry['key']}.npy"
        if entry["frames"] and npy.exists():
            sims = np.load(npy).astype(np.float32) @ q
            blank = entry.get("blank")
            if blank is None:  # indexed before blank detection existed
                blank = blank_frames(store / "frames" / entry["key"])
            sims[blank] = -1.0
            rows.append((rel, entry, sims))
    if not rows:
        return []
    all_sims = np.concatenate([r[2] for r in rows])
    floor = max(float(np.percentile(all_sims, 90)), float(all_sims.max()) - 0.06)
    hits = []
    for rel, entry, sims in rows:
        # a run of neighbouring frames above the floor is one moment; it scores by its best frame
        i = 0
        while i < len(sims):
            if sims[i] < floor:
                i += 1
                continue
            j = i
            while j + 1 < len(sims) and sims[j + 1] >= floor:
                j += 1
            best = i + int(np.argmax(sims[i:j + 1]))
            t = entry["frames"]
            hits.append({"file": rel, "start": round(max(0.0, t[i] - every / 2), 2),
                         "end": round(min(entry["duration"], t[j] + every / 2), 2), "score": round(float(sims[best]), 3),
                         "kind": "visual", "thumb": str(store / "frames" / entry["key"] / f"{best + 1:05d}.jpg")})
            i = j + 1
    return sorted(hits, key=lambda h: -h["score"])[:top]


def search(folder: Path, query: str, mode: str = "both", top: int = 10) -> list[dict]:
    path = folder / ".studio-index" / "index.json"
    if not path.exists():
        raise SystemExit(f"{folder} isn't indexed yet: run  studio_search.py index {folder}")
    data = json.loads(path.read_text(encoding="utf-8"))
    hits = []
    if mode in ("both", "spoken"):
        hits += spoken_hits(data, query, top)
    if mode in ("both", "visual"):
        hits += visual_hits(folder, data, query, top)
    return hits


def thumb_for(folder: Path, hit: dict) -> str | None:
    if hit.get("thumb"):
        return hit["thumb"]
    data = json.loads((folder / ".studio-index" / "index.json").read_text(encoding="utf-8"))
    entry = data["files"][hit["file"]]
    if not entry["frames"]:
        return None
    mid = (hit["start"] + hit["end"]) / 2
    n = min(range(len(entry["frames"])), key=lambda k: abs(entry["frames"][k] - mid))
    return str(folder / ".studio-index" / "frames" / entry["key"] / f"{n + 1:05d}.jpg")


def contact_sheet(folder: Path, hits: list[dict], out: Path) -> None:
    from PIL import Image, ImageDraw
    tiles = []
    for n, hit in enumerate(hits, 1):
        thumb = thumb_for(folder, hit)
        img = Image.open(thumb).convert("RGB").resize((320, 180)) if thumb else Image.new("RGB", (320, 180), "#222")
        canvas = Image.new("RGB", (320, 220), "#0F1115")
        canvas.paste(img, (0, 0))
        label = f"{n}. {Path(hit['file']).name[:26]} {hit['start']:.0f}s ({hit['kind'][0]} {hit['score']:.2f})"
        ImageDraw.Draw(canvas).text((6, 190), label, fill="white")
        tiles.append(canvas)
    if not tiles:
        return
    cols = min(4, len(tiles))
    sheet = Image.new("RGB", (cols * 320, -(-len(tiles) // cols) * 220), "#0F1115")
    for k, tile in enumerate(tiles):
        sheet.paste(tile, ((k % cols) * 320, (k // cols) * 220))
    sheet.save(out, quality=88)


def export(folder: Path, hit: dict, out: Path, pad: float = 0.5) -> None:
    src = folder / hit["file"]
    start = max(0.0, hit["start"] - pad)
    length = hit["end"] + pad - start
    run([FFMPEG, "-y", "-v", "error", "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{length:.2f}",
         "-c:v", "libx264", "-crf", "16", "-preset", "fast", "-c:a", "aac", "-b:a", "256k", str(out)])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("index", help="index a footage folder")
    i.add_argument("folder")
    i.add_argument("--every", type=float, default=2.0, help="seconds between fingerprinted frames (default 2)")
    for name in ("search", "export"):
        p = sub.add_parser(name, help=f"{name} moments")
        p.add_argument("folder"), p.add_argument("query")
        g = p.add_mutually_exclusive_group()
        g.add_argument("--spoken", action="store_true", help="only what was said")
        g.add_argument("--visual", action="store_true", help="only what is on screen")
        p.add_argument("--top", type=int, default=8)
    sub.choices["search"].add_argument("--sheet", help="save a contact sheet of the hits (.jpg)")
    sub.choices["search"].add_argument("--json", action="store_true")
    sub.choices["export"].add_argument("--pick", type=int, default=1, help="which hit (1 = best)")
    sub.choices["export"].add_argument("--out", required=True)
    sub.choices["export"].add_argument("--pad", type=float, default=0.5)
    a = ap.parse_args(argv)
    folder = Path(a.folder).resolve()
    if a.cmd == "index":
        data = index(folder, a.every)
        files = data["files"].values()
        print(f"{len(data['files'])} files, {sum(len(f['segments']) for f in files)} spoken sentences, "
              f"{sum(len(f['frames']) for f in files)} frames indexed")
        return 0
    mode = "spoken" if a.spoken else "visual" if a.visual else "both"
    hits = search(folder, a.query, mode, a.top)
    if a.cmd == "export":
        if not hits or a.pick > len(hits):
            raise SystemExit("no such hit")
        export(folder, hits[a.pick - 1], Path(a.out))
        h = hits[a.pick - 1]
        print(f"{a.out}: {h['file']} {h['start']:.1f}-{h['end']:.1f}s")
        return 0
    if a.json:
        print(json.dumps(hits, indent=1))
    else:
        for n, h in enumerate(hits, 1):
            extra = f'  "{h["text"]}"' if h["kind"] == "spoken" else ""
            print(f"{n:2d}. [{h['kind']:6s} {h['score']:.2f}] {h['file']}  {h['start']:.1f}-{h['end']:.1f}s{extra}")
        if not hits:
            print("nothing found")
    if a.sheet and hits:
        contact_sheet(folder, hits, Path(a.sheet))
        print(f"sheet: {a.sheet}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
