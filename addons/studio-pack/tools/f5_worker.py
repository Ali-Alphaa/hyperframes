"""Runs inside ~/video-pipeline/.f5-venv (F5-TTS). studio_voice.py starts it with a JSON job on stdin:
{"reference": wav (5-12 s), "reference_text": exact words in it, "speed": 1.0, "seed": 1234,
 "lines": [{"text": ..., "out": ...}, ...]}.
F5-TTS continues from the reference recording itself, so it keeps the speaker's accent and rhythm closely."""
import json
import sys

from f5_tts.api import F5TTS


def main() -> None:
    job = json.load(sys.stdin)
    tts = F5TTS(model="F5TTS_v1_Base")
    for line in job["lines"]:
        _, sr, _ = tts.infer(ref_file=job["reference"], ref_text=job["reference_text"], gen_text=line["text"],
                             speed=float(job.get("speed", 1.0)), seed=int(job.get("seed", 1234)),
                             nfe_step=int(job.get("steps", 32)), file_wave=line["out"], show_info=lambda *a: None)
        print(json.dumps({"done": line["out"], "sr": sr}), flush=True)


if __name__ == "__main__":
    main()
