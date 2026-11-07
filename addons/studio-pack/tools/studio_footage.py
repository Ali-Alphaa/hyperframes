#!/usr/bin/env python3
"""Studio Pack footage fixes, for camera footage and screen recordings (Resolve: Stabilizer, UltraNR, Speed Warp,
Super Scale). Local first; every command keeps the sound unless it can't (slow motion).

  stabilise  steady handheld shake (FFmpeg vid.stab, two passes, zooms just enough to hide the edges)
  denoise    reduce grain and sensor noise (FFmpeg hqdn3d, or nlmeans for strong)
  slowmo     smooth slow motion or higher frame rate by inventing in-between frames (RIFE on the GPU;
             FFmpeg minterpolate as the fallback)
  upscale    sharper enlargement (Real-ESRGAN on the GPU; FFmpeg lanczos as the fallback; Topaz if installed)

  python studio_footage.py stabilise shaky.mp4 steady.mp4
  python studio_footage.py slowmo clip.mp4 slow.mp4 --factor 4
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_audio import FFMPEG, run  # noqa: E402
from studio_export import probe  # noqa: E402

# GPU (Vulkan) objects crash Windows if Python frees them; they're kept here until the process exits via os._exit.
_KEEP_ALIVE: list = []

ENCODE = ["-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]


def _audio_args(info: dict) -> list[str]:
    return ["-c:a", "aac", "-b:a", "256k"] if info["has_audio"] else []


# -------------------------------------------------------------------- stabilise
STABILISE = {  # (shakiness 1-10, smoothing in frames)
    "light": (4, 15),
    "medium": (6, 30),
    "strong": (9, 60),
}


def stabilise(src: str, out: str, strength: str = "medium") -> None:
    shakiness, smoothing = STABILISE[strength]
    info = probe(src)
    src_abs, out_abs = str(Path(src).resolve()), str(Path(out).resolve())
    with tempfile.TemporaryDirectory() as tmp:  # vid.stab's file option can't take a Windows drive path
        def ff(args: list[str]) -> None:
            proc = subprocess.run([FFMPEG, "-y", "-v", "error", *args], cwd=tmp, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            if proc.returncode != 0:
                raise SystemExit(f"ffmpeg failed: {proc.stderr[-1200:]}")
        ff(["-i", src_abs, "-vf", f"vidstabdetect=shakiness={shakiness}:accuracy=15:result=t.trf", "-f", "null", "-"])
        ff(["-i", src_abs, "-vf", f"vidstabtransform=input=t.trf:smoothing={smoothing}:optzoom=1:zoomspeed=0.25:"
            "interpol=bicubic,unsharp=5:5:0.6:3:3:0.3", *ENCODE, *_audio_args(info), out_abs])


# ---------------------------------------------------------------------- denoise
# atadenoise averages each pixel with the matching pixels of neighbouring frames, but only where they agree, so
# moving parts aren't smeared; hqdn3d / nlmeans then clean what is left within the frame. Measured on a real
# interview with added grain (26.3 dB against the clean original): light 28.1 dB, medium 34.0 dB, strong 35.3 dB.
# hqdn3d alone (the first version here) only reached 26.6 dB.
DENOISE = {
    "light": "atadenoise=0a=0.1:0b=0.2:1a=0.1:1b=0.2:2a=0.1:2b=0.2:s=5,hqdn3d=2:1.5:3:3",
    "medium": "atadenoise=0a=0.2:0b=0.3:1a=0.2:1b=0.3:2a=0.2:2b=0.3:s=9,hqdn3d=4:3:6:4.5",
    "strong": "atadenoise=0a=0.2:0b=0.3:1a=0.2:1b=0.3:2a=0.2:2b=0.3:s=9,nlmeans=s=4:p=5:r=11",
}


def denoise(src: str, out: str, strength: str = "medium") -> None:
    info = probe(src)
    run([FFMPEG, "-y", "-v", "error", "-i", src, "-vf", DENOISE[strength], *ENCODE, *_audio_args(info), out])


# ------------------------------------------------------------- frame streaming
def read_frames(src: str, w: int, h: int, vf: str | None = None):
    cmd = [FFMPEG, "-v", "error", "-i", src, *(["-vf", vf] if vf else []), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    size = w * h * 3
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    proc.wait()


def frame_writer(out: str, w: int, h: int, fps: float, audio_from: str | None = None, audio_filter: str | None = None):
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", f"{fps:g}",
           "-i", "-"]
    if audio_from:
        cmd += ["-i", audio_from, "-map", "0:v", "-map", "1:a?", *(["-af", audio_filter] if audio_filter else []),
                "-c:a", "aac", "-b:a", "256k", "-shortest"]
    cmd += [*ENCODE, out]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


# ----------------------------------------------------------------------- slowmo
def slowmo(src: str, out: str, factor: int = 2, mode: str = "slow", engine: str = "auto") -> str:
    """mode slow: same frame rate, `factor` times longer (sound dropped).
    mode smooth: same length, `factor` times the frame rate (sound kept)."""
    info = probe(src)
    fps_out = info["fps"] * (factor if mode == "smooth" else 1)
    if engine == "auto":
        engine = "rife" if _rife_available() else "ffmpeg"
    audio = src if mode == "smooth" and info["has_audio"] else None
    if engine == "ffmpeg":
        vf = f"minterpolate=fps={info['fps'] * factor:g}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
        if mode == "slow":
            vf += f",setpts={factor}*PTS,fps={info['fps']:g}"
        run([FFMPEG, "-y", "-v", "error", "-i", src, "-vf", vf, *ENCODE,
             *(_audio_args(info) if audio else ["-an"]), out])
        return "ffmpeg"
    from PIL import Image
    from rife_ncnn_vulkan_python import Rife
    w, h = info["width"], info["height"]
    rife = Rife(gpuid=0, model="rife-v4.6", width=w, height=h)
    _KEEP_ALIVE.append(rife)
    writer = frame_writer(out, w, h, fps_out, audio)
    prev = None
    for frame in read_frames(src, w, h):
        if prev is not None:
            a, b = Image.fromarray(prev), Image.fromarray(frame)
            writer.stdin.write(prev.tobytes())
            for k in range(1, factor):
                writer.stdin.write(np.asarray(rife.process(a, b, timestep=k / factor)).tobytes())
        prev = frame
    if prev is not None:
        writer.stdin.write(prev.tobytes())
    writer.stdin.close()
    writer.wait()
    if writer.returncode != 0:
        raise SystemExit("encoding the slow-motion video failed")
    return "rife"


def _rife_available() -> bool:
    try:
        import rife_ncnn_vulkan_python
    except ImportError:
        return False
    return (Path(rife_ncnn_vulkan_python.__file__).parent / "models" / "rife-v4.6" / "flownet.bin").exists()


# ---------------------------------------------------------------------- upscale
TOPAZ_FFMPEG = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Topaz Labs LLC" / "Topaz Video AI" / "ffmpeg.exe"


def upscale(src: str, out: str, scale: int = 2, engine: str = "auto", model: str = "photo") -> str:
    """Video or still image. model photo = realesrgan-x4plus (camera footage, slow), fast = realesr-animevideov3
    (screen recordings and graphics, quick)."""
    if engine == "auto":
        engine = "realesrgan" if _realesrgan_available() else "ffmpeg"
    is_image = Path(src).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    info = None if is_image else probe(src)
    if engine == "topaz":
        if not TOPAZ_FFMPEG.exists():
            raise SystemExit("Topaz Video AI isn't installed (it's the paid option); use --engine realesrgan")
        run([str(TOPAZ_FFMPEG), "-y", "-v", "error", "-i", src, "-vf", f"tvai_up=model=prob-4:scale={scale}",
             *ENCODE, *(_audio_args(info) if info else []), out])
        return "topaz"
    if engine == "ffmpeg":
        run([FFMPEG, "-y", "-v", "error", "-i", src, "-vf", f"scale=iw*{scale}:ih*{scale}:flags=lanczos",
             *([] if is_image else [*ENCODE, *_audio_args(info)]), out])
        return "ffmpeg"
    from PIL import Image
    from realesrgan_ncnn_py import Realesrgan
    net_scale = 4 if model == "photo" else {2: 2, 3: 3, 4: 4}.get(scale, 4)
    index = 4 if model == "photo" else {2: 0, 3: 1, 4: 2}[net_scale]
    net = Realesrgan(gpuid=0, model=index, tilesize=256)
    _KEEP_ALIVE.append(net)

    def enlarge(frame: Image.Image) -> Image.Image:
        big = net.process_pil(frame)
        target = (frame.width * scale, frame.height * scale)
        return big if big.size == target else big.resize(target, Image.LANCZOS)

    if is_image:
        enlarge(Image.open(src).convert("RGB")).save(out)
        return "realesrgan"
    w, h = info["width"], info["height"]
    writer = frame_writer(out, w * scale, h * scale, info["fps"], src if info["has_audio"] else None)
    for frame in read_frames(src, w, h):
        writer.stdin.write(np.asarray(enlarge(Image.fromarray(frame))).tobytes())
    writer.stdin.close()
    writer.wait()
    return "realesrgan"


def _realesrgan_available() -> bool:
    try:
        import realesrgan_ncnn_py  # noqa: F401
    except ImportError:
        return False
    return True


# -------------------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stabilise", aliases=["stabilize"], help="steady shaky footage")
    s.add_argument("input"), s.add_argument("output")
    s.add_argument("--strength", choices=list(STABILISE), default="medium")
    d = sub.add_parser("denoise", help="reduce grain and sensor noise")
    d.add_argument("input"), d.add_argument("output")
    d.add_argument("--strength", choices=list(DENOISE), default="medium")
    m = sub.add_parser("slowmo", help="slow motion or a higher frame rate")
    m.add_argument("input"), m.add_argument("output")
    m.add_argument("--factor", type=int, choices=[2, 3, 4, 8], default=2)
    m.add_argument("--mode", choices=["slow", "smooth"], default="slow")
    m.add_argument("--engine", choices=["auto", "rife", "ffmpeg"], default="auto")
    u = sub.add_parser("upscale", help="sharper enlargement of a video or image")
    u.add_argument("input"), u.add_argument("output")
    u.add_argument("--scale", type=int, choices=[2, 3, 4], default=2)
    u.add_argument("--model", choices=["photo", "fast"], default="photo")
    u.add_argument("--engine", choices=["auto", "realesrgan", "ffmpeg", "topaz"], default="auto")
    a = ap.parse_args(argv)
    if a.cmd in ("stabilise", "stabilize"):
        stabilise(a.input, a.output, a.strength)
        print(f"stabilised ({a.strength}): {a.output}")
    elif a.cmd == "denoise":
        denoise(a.input, a.output, a.strength)
        print(f"denoised ({a.strength}): {a.output}")
    elif a.cmd == "slowmo":
        used = slowmo(a.input, a.output, a.factor, a.mode, a.engine)
        print(f"{a.mode} x{a.factor} with {used}: {a.output}")
    elif a.cmd == "upscale":
        used = upscale(a.input, a.output, a.scale, a.engine, a.model)
        print(f"upscaled x{a.scale} with {used}: {a.output}")
    sys.stdout.flush()
    return 0


def _exit_skipping_gpu_teardown(code: int) -> None:
    """The GPU (Vulkan) libraries crash in their own shutdown code, after all work is written. On Windows even
    os._exit runs that code (DLL detach), so end the process with TerminateProcess, which doesn't."""
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32" and _KEEP_ALIVE:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE  # 64-bit handle; ctypes' int default truncates it
        kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code)
    os._exit(code)


if __name__ == "__main__":
    _exit_skipping_gpu_teardown(main())
