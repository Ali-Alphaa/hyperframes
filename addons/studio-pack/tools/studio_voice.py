#!/usr/bin/env python3
"""Studio Pack voice: your own cloned voice for voiceovers (Resolve 21's AI Speech Generator, run locally).

  enrol   check and clean a recording of YOUR voice and store it as a voice (records your consent)
  say     speak one line in a voice
  script  speak a whole script, one file per line, with a manifest of durations for the composition
  voices  list stored voices

Engines: chatterbox (default for cloned voices; local, GPU, adds an inaudible watermark), kokoro (HyperFrames'
built-in stock voices, the project default), elevenlabs (paid; only with your own ELEVENLABS_API_KEY and the id of
a voice you made in your ElevenLabs account, ELEVENLABS_VOICE_ID; nothing is uploaded by this tool).

Only your own voice, or a voice whose owner has agreed in writing: enrol refuses without --consent.

  python studio_voice.py enrol ali recordings/ali-sample.wav --consent "This is my own voice"
  python studio_voice.py say ali "A pullback is not a reversal." out.wav
  python studio_voice.py script ali script.txt --out-dir assets/vo/ali
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_audio import FFMPEG, PIPELINE, clean, level, measure_loudness, run  # noqa: E402

VOICES = Path(os.environ.get("STUDIO_VOICES", PIPELINE / "voices"))
VOICE_PY = PIPELINE / ".voice-venv" / "Scripts" / "python.exe"
WORKER = Path(__file__).resolve().parent / "voice_worker.py"
CLI = Path(__file__).resolve().parents[3] / "packages" / "cli" / "dist" / "cli.js"
CONSENT_WORDS = re.compile(r"\b(my own voice|my voice|i consent|consent|agreed|permission)\b", re.I)


# ------------------------------------------------------------------------ enrol
def check_sample(path: str) -> dict:
    """Duration, level, clipping and background noise of a recording, with plain-English problems."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                           capture_output=True, text=True, check=True)
    seconds = float(probe.stdout.strip())
    loud = measure_loudness(path)
    stats = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", path, "-af",
                            "astats=metadata=0:reset=0,silencedetect=n=-40dB:d=0.4", "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    peak = float(re.search(r"Peak level dB:\s*(-?[\d.inf]+)", stats).group(1).replace("-inf", "-120"))
    noise = float(re.search(r"Noise floor dB:\s*(-?[\d.inf]+)", stats).group(1).replace("-inf", "-120"))
    problems = []
    if seconds < 8:
        problems.append(f"too short ({seconds:.0f} s): record at least 20 seconds, ideally 30-60")
    if seconds > 180:
        problems.append(f"long ({seconds:.0f} s): only the first minute or so helps; that's fine")
    if peak > -0.3:
        problems.append("clipping (the level hit the top): record a little quieter")
    if float(loud["input_i"]) < -40:
        problems.append(f"very quiet ({float(loud['input_i']):.0f} LUFS): move closer to the mic")
    if noise > -50:
        problems.append(f"noisy room (noise floor {noise:.0f} dB): it will be cleaned, but a quieter room clones better")
    return {"seconds": round(seconds, 1), "lufs": round(float(loud["input_i"]), 1), "peak_db": round(peak, 1),
            "noise_floor_db": round(noise, 1), "problems": problems}


def enrol(name: str, sample: str, consent: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,30}", name):
        raise SystemExit("voice name: lowercase letters, digits and dashes")
    if not consent or not CONSENT_WORDS.search(consent):
        raise SystemExit('enrol needs --consent saying this is your own voice, e.g. --consent "This is my own voice"')
    report = check_sample(sample)
    folder = VOICES / name
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        cleaned = Path(tmp) / "clean.wav"
        engine = clean(sample, str(cleaned))  # DeepFilterNet when set up
        trimmed = Path(tmp) / "trim.wav"
        # drop long silences (keeps the voice dense) and use at most the first 60 s
        run([FFMPEG, "-y", "-v", "error", "-i", str(cleaned), "-af",
             "silenceremove=start_periods=1:start_threshold=-45dB:stop_periods=-1:stop_duration=0.6:stop_threshold=-45dB",
             "-t", "60", "-ac", "1", "-ar", "24000", str(trimmed)])
        level(str(trimmed), str(folder / "reference.wav"), target=-20)
    meta = {"name": name, "created": dt.datetime.now().isoformat(timespec="seconds"), "source": str(Path(sample).name),
            "consent": consent, "cleaned_with": engine, "sample": report}
    (folder / "voice.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return folder


# ----------------------------------------------------------------------- record
def input_devices() -> list[str]:
    out = subprocess.run([FFMPEG, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    return re.findall(r'"([^"]+)" \(audio\)', out)


def _levels(path: str) -> tuple[float, float]:
    """(peak dB, loudness of the speech in LUFS-ish RMS dB) of a short test recording."""
    err = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", path, "-af", "astats", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    num = lambda key: float(re.search(rf"{key}:\s*(-?[\d.inf]+)", err).group(1).replace("-inf", "-120"))  # noqa: E731
    return num("Peak level dB"), num("RMS level dB")


def record(out: Path, device: str | None = None, max_seconds: int = 180) -> Path:
    """Interactive: a 5-second level test, then record until Enter. 48 kHz mono 24-bit WAV."""
    devices = input_devices()
    if not devices:
        raise SystemExit("No microphone found. Plug the mic in (and pick 'Microphone' if Windows asks), then retry.")
    device = device or devices[0]
    print(f"\nMicrophone: {device}")
    capture = [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "dshow", "-audio_buffer_size", "50",
               "-sample_rate", "48000", "-channels", "1", "-i", f"audio={device}"]
    while True:
        input("\nLevel test: press Enter, then say 'Testing, one, two, three, this is my normal voice' "
              "at your recording volume (5 seconds)...")
        with tempfile.TemporaryDirectory() as tmp:
            test = str(Path(tmp) / "test.wav")
            run(capture + ["-t", "5", "-y", test])
            peak, rms = _levels(test)
        if peak > -1:
            print(f"  Too loud: it clipped (peak {peak:.1f} dB). Lower the mic volume in Windows Sound settings "
                  "(Input), or move back a little.")
        elif peak < -24:
            print(f"  Too quiet (peak {peak:.1f} dB). Check the Rode is switched on (if it has a switch) and plugged "
                  "into the mic jack, move closer, or raise the mic volume in Windows Sound settings (Input).")
        else:
            print(f"  Good level (peak {peak:.1f} dB).")
            if input("  Record now? Enter = yes, r = test again: ").strip().lower() != "r":
                break
            continue
        if input("  Enter = test again, s = record anyway: ").strip().lower() == "s":
            break
    out.parent.mkdir(parents=True, exist_ok=True)
    print("\nRecording. Read the script at your normal pace. Press Enter when you've finished.")
    proc = subprocess.Popen(capture + ["-t", str(max_seconds), "-c:a", "pcm_s24le", "-y", str(out)],
                            stdin=subprocess.PIPE)
    try:
        input()
    finally:
        if proc.poll() is None:
            proc.stdin.write(b"q")
            proc.stdin.flush()
        proc.wait(timeout=30)
    report = check_sample(str(out))
    print(f"\nSaved {out}  ({report['seconds']} s)")
    print("  " + ("No problems found." if not report["problems"] else "Notes: " + "; ".join(report["problems"])))
    return out


def load_voice(name: str) -> dict:
    meta_path = VOICES / name / "voice.json"
    if not meta_path.exists():
        raise SystemExit(f"no voice {name!r}. Enrol it first: studio_voice.py enrol {name} sample.wav --consent ...")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not CONSENT_WORDS.search(meta.get("consent", "")):
        raise SystemExit(f"voice {name!r} has no consent record; re-enrol it")
    meta["reference"] = str(VOICES / name / "reference.wav")
    return meta


# ---------------------------------------------------------------------- speaking
def speak_lines(voice: str | None, lines: list[dict], engine: str = "auto", exaggeration: float = 0.5,
                cfg: float = 0.5, kokoro_voice: str = "am_michael") -> str:
    """lines: [{"text", "out"}]. Writes 48 kHz WAVs at -16 LUFS (the level the audio tools expect)."""
    if engine == "auto":
        engine = "chatterbox" if voice else "kokoro"
    with tempfile.TemporaryDirectory() as tmp:
        raw = [dict(line, raw=str(Path(tmp) / f"{i:03d}.wav")) for i, line in enumerate(lines)]
        if engine == "chatterbox":
            if not VOICE_PY.exists():
                raise SystemExit("the voice environment isn't set up (see the Studio Pack README)")
            meta = load_voice(voice)
            job = {"reference": meta["reference"], "exaggeration": exaggeration, "cfg": cfg,
                   "lines": [{"text": r["text"], "out": r["raw"]} for r in raw]}
            proc = subprocess.run([str(VOICE_PY), str(WORKER)], input=json.dumps(job), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")
            if proc.returncode != 0:
                raise SystemExit(f"voice generation failed: {proc.stderr[-1500:]}")
        elif engine == "kokoro":
            for r in raw:
                run(["node", str(CLI), "tts", r["text"], "-v", kokoro_voice, "-o", r["raw"]])
        elif engine == "elevenlabs":
            _elevenlabs(raw)
        else:
            raise SystemExit(f"unknown engine {engine}")
        for r in raw:
            # trim the silence models leave at both ends, then a consistent level
            trimmed = r["raw"] + ".trim.wav"
            run([FFMPEG, "-y", "-v", "error", "-i", r["raw"], "-af",
                 # gentle: a soft first word ("The ...") must survive; keep 80 ms of lead-in and tail
                 "silenceremove=start_periods=1:start_threshold=-60dB:start_silence=0.08,areverse,"
                 "silenceremove=start_periods=1:start_threshold=-60dB:start_silence=0.08,areverse",
                 "-ar", "48000", "-ac", "1", trimmed])
            level(trimmed, r["out"], target=-16)
    return engine


def _elevenlabs(raw: list[dict]) -> None:
    key, voice_id = os.environ.get("ELEVENLABS_API_KEY"), os.environ.get("ELEVENLABS_VOICE_ID")
    if not key or not voice_id:
        raise SystemExit("ElevenLabs is the paid option and is off: it needs ELEVENLABS_API_KEY and "
                         "ELEVENLABS_VOICE_ID (a voice you made in your ElevenLabs account), set by you")
    import urllib.request
    for r in raw:
        body = json.dumps({"text": r["text"], "model_id": "eleven_multilingual_v2"}).encode()
        req = urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}", data=body,
                                     method="POST", headers={"xi-api-key": key, "Content-Type": "application/json",
                                                             "Accept": "audio/mpeg"})
        mp3 = r["raw"] + ".mp3"
        with urllib.request.urlopen(req, timeout=300) as resp:
            Path(mp3).write_bytes(resp.read())
        run([FFMPEG, "-y", "-v", "error", "-i", mp3, r["raw"]])


def split_script(text: str) -> list[str]:
    """One line per paragraph (blank-line separated); # comments ignored."""
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    return [p for p in paras if p and not p.startswith("#")]


def duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout
    return round(float(out.strip()), 3)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enrol", aliases=["enroll"], help="store a recording of your own voice")
    e.add_argument("name"), e.add_argument("sample")
    e.add_argument("--consent", required=True, help='e.g. "This is my own voice"')
    c = sub.add_parser("check", help="check a recording without enrolling it")
    c.add_argument("sample")
    for p in (s := sub.add_parser("say", help="speak one line")), (sc := sub.add_parser("script", help="speak a script")):
        p.add_argument("voice", help="an enrolled voice, or 'kokoro'")
        p.add_argument("--engine", choices=["auto", "chatterbox", "kokoro", "elevenlabs"], default="auto")
        p.add_argument("--exaggeration", type=float, default=0.5, help="expressiveness 0.25-1 (default 0.5)")
        p.add_argument("--cfg", type=float, default=0.5, help="pace/stability 0.2-0.8 (default 0.5)")
        p.add_argument("--kokoro-voice", default="am_michael")
    s.add_argument("text"), s.add_argument("output")
    sc.add_argument("script"), sc.add_argument("--out-dir", required=True)
    sub.add_parser("voices", help="list stored voices")
    r = sub.add_parser("record", help="record a voice sample from the microphone (interactive)")
    r.add_argument("--out", help="default: Desktop/video-pipeline/recordings/voice-sample-<date>.wav")
    r.add_argument("--device", help="microphone name (default: the first one)")
    r.add_argument("--list", action="store_true", help="list microphones and exit")
    a = ap.parse_args(argv)
    if a.cmd == "record":
        if a.list:
            print("\n".join(input_devices()) or "no microphones")
            return 0
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
        desk = Path(os.environ.get("USERPROFILE", Path.home())) / "OneDrive" / "Desktop"
        desk = desk if desk.exists() else Path.home() / "Desktop"
        out = Path(a.out) if a.out else desk / "video-pipeline" / "recordings" / f"voice-sample-{stamp}.wav"
        record(out, a.device)
        print("\nNext: tell Claude the recording is ready.")
        return 0
    if a.cmd in ("enrol", "enroll"):
        folder = enrol(a.name, a.sample, a.consent)
        meta = json.loads((folder / "voice.json").read_text(encoding="utf-8"))
        print(json.dumps(meta["sample"], indent=1))
        print(f"voice {a.name!r} stored in {folder}")
    elif a.cmd == "check":
        print(json.dumps(check_sample(a.sample), indent=1))
    elif a.cmd == "voices":
        for meta_path in sorted(VOICES.glob("*/voice.json")):
            m = json.loads(meta_path.read_text(encoding="utf-8"))
            print(f"{m['name']:14s} enrolled {m['created']}  sample {m['sample']['seconds']} s")
    else:
        voice = None if a.voice == "kokoro" else a.voice
        engine = "kokoro" if a.voice == "kokoro" else a.engine
        if a.cmd == "say":
            used = speak_lines(voice, [{"text": a.text, "out": a.output}], engine, a.exaggeration, a.cfg, a.kokoro_voice)
            print(f"{used}: {a.output} ({duration(a.output)} s)")
        else:
            lines = split_script(Path(a.script).read_text(encoding="utf-8"))
            out_dir = Path(a.out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            jobs = [{"text": t, "out": str(out_dir / f"l{i:02d}.wav")} for i, t in enumerate(lines)]
            used = speak_lines(voice, jobs, engine, a.exaggeration, a.cfg, a.kokoro_voice)
            manifest = [{"id": f"l{i:02d}", "text": j["text"], "file": Path(j["out"]).name,
                         "duration": duration(j["out"])} for i, j in enumerate(jobs)]
            (out_dir / "manifest.json").write_text(json.dumps({"engine": used, "voice": a.voice, "lines": manifest},
                                                              indent=1), encoding="utf-8")
            print(f"{used}: {len(manifest)} lines, {sum(m['duration'] for m in manifest):.1f} s -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
