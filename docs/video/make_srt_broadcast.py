# -*- coding: utf-8 -*-
"""Build the SRT for the broadcast cut."""
from pathlib import Path
import json, subprocess, re

ROOT = Path(__file__).resolve().parent
items = json.loads((ROOT / "slides_broadcast" / "narration.json").read_text(encoding="utf-8"))


def dur(path):
    return float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path)
    ], text=True).strip())


def ts(sec):
    h = int(sec // 3600); m = int((sec % 3600) // 60); s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def wrap(text, width=34):
    parts = re.split(r"(?<=[，。；：！？])", text)
    lines, cur = [], ""
    for p in parts:
        if len(cur) + len(p) > width and cur:
            lines.append(cur); cur = p
        else:
            cur += p
    if cur:
        lines.append(cur)
    return "\n".join(lines)


out, t = [], 0.0
for i, text in enumerate(items, 1):
    d = dur(ROOT / "audio_broadcast" / f"slide{i:02d}.wav")
    out.append(f"{i}\n{ts(t)} --> {ts(t + d + 0.25)}\n{wrap(text)}\n")
    t += d + 0.9

path = ROOT / "警务遥感_Vibe_Coding_演示_播音版.srt"
path.write_text("\n".join(out), encoding="utf-8-sig")
print(path)
print(f"cues: {len(out)}, total: {t:.1f}s")