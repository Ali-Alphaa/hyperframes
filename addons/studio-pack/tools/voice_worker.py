"""Runs inside ~/video-pipeline/.voice-venv (PyTorch 2.6 + Chatterbox). studio_voice.py starts it with a JSON job on
stdin: {"reference": wav, "exaggeration": 0.5, "cfg": 0.5, "lines": [{"text": ..., "out": ...}, ...]}.
The model loads once per job. Chatterbox adds Resemble AI's inaudible Perth watermark to everything it makes."""
import json
import sys

import torch
import torchaudio
from chatterbox.tts import ChatterboxTTS


def main() -> None:
    job = json.load(sys.stdin)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = ChatterboxTTS.from_pretrained(device=device)
    torch.manual_seed(int(job.get("seed", 1234)))  # same text, same take
    for line in job["lines"]:
        wav = model.generate(line["text"], audio_prompt_path=job["reference"],
                             exaggeration=float(job.get("exaggeration", 0.5)), cfg_weight=float(job.get("cfg", 0.5)))
        torchaudio.save(line["out"], wav.cpu(), model.sr)
        print(json.dumps({"done": line["out"], "seconds": round(wav.shape[-1] / model.sr, 2)}), flush=True)
    print(json.dumps({"device": device}), flush=True)


if __name__ == "__main__":
    main()
