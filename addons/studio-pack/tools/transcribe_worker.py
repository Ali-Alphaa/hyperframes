"""Runs inside ~/video-pipeline/.venv (faster-whisper). Prints JSON sentence segments of one audio file:
[{"start": s, "end": s, "text": "..."}]. Audio is decoded with FFmpeg (that environment's PyAV is too old)."""
import json
import subprocess
import sys

import numpy as np
from faster_whisper import WhisperModel


def main() -> None:
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", sys.argv[1], "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    model = WhisperModel(sys.argv[2] if len(sys.argv) > 2 else "small.en", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(np.frombuffer(pcm, np.float32), word_timestamps=True)
    print(json.dumps([{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments]))


if __name__ == "__main__":
    main()
