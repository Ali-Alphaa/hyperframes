"""Runs inside ~/video-pipeline/.venv (faster-whisper). Prints JSON sentence segments of one audio file:
[{"start": s, "end": s, "text": "..."}], or with --words every word: [{"start", "end", "word", "p"}].
Audio is decoded with FFmpeg (that environment's PyAV is too old)."""
import json
import subprocess
import sys

import numpy as np
from faster_whisper import WhisperModel


def main() -> None:
    words = "--words" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--words"]
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", args[0], "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    model = WhisperModel(args[1] if len(args) > 1 else "small.en", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(np.frombuffer(pcm, np.float32), word_timestamps=True)
    if words:
        print(json.dumps([{"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word.strip(),
                           "p": round(w.probability, 2)} for s in segments for w in s.words]))
        return
    print(json.dumps([{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments]))


if __name__ == "__main__":
    main()
