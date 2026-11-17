# -*- coding: utf-8 -*-
"""Render the 1080p demo video from slides + SAPI narration."""
from pathlib import Path
import subprocess, json

ROOT = Path(__file__).resolve().parent
SEG = ROOT / "segments"
SEG.mkdir(exist_ok=True)

def run(cmd):
    print(" ".join(str(x) for x in cmd))
    subprocess.run(cmd, check=True)

def dur(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path)
    ], text=True).strip()
    return float(out)

segments = []
total = 0.0
for i in range(1, 15):
    slide = ROOT / "slides" / f"slide{i:02d}.png"
    audio = ROOT / "audio" / f"slide{i:02d}.wav"
    seg = SEG / f"seg{i:02d}.mp4"
    d = dur(audio)
    length = d + 0.9
    total += length
    vf = f"scale=1920:1080,format=yuv420p,fade=t=in:st=0:d=0.35,fade=t=out:st={length-0.35:.3f}:d=0.35"
    af = "apad=pad_dur=0.9"
    run([
        "ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-i", str(slide),
        "-i", str(audio), "-filter_complex", f"[0:v]{vf}[v];[1:a]{af}[a]",
        "-map", "[v]", "-map", "[a]", "-t", f"{length:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", str(seg)
    ])
    segments.append(seg)
    print(f"  slide {i:02d}: audio {d:.1f}s -> segment {length:.1f}s")

list_file = SEG / "list.txt"
list_file.write_text("".join(f"file '{(s.relative_to(SEG)).as_posix()}'\n" for s in segments), encoding="utf-8")
out = ROOT / "警务遥感_Vibe_Coding_演示.mp4"
run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
     "-c", "copy", "-movflags", "+faststart", str(out)])
print(f"video: {out}")
print(f"duration: {total:.1f}s = {total/60:.2f} min")
print(f"size: {out.stat().st_size/1024/1024:.1f} MiB")