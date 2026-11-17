# -*- coding: utf-8 -*-
"""Generate news-anchor style narration with Edge TTS (Yunyang)."""
import asyncio, json, subprocess
from pathlib import Path
import edge_tts

ROOT = Path(__file__).resolve().parent
AUDIO = ROOT / "audio_broadcast"
AUDIO.mkdir(exist_ok=True)
VOICE = "zh-CN-YunyangNeural"
RATE = "+0%"
PITCH = "-2Hz"

async def main():
    items = json.loads((ROOT / "slides_broadcast" / "narration.json").read_text(encoding="utf-8"))
    for i, text in enumerate(items, 1):
        mp3 = AUDIO / f"slide{i:02d}.mp3"
        wav = AUDIO / f"slide{i:02d}.wav"
        await edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH).save(str(mp3))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
                        "-ar", "24000", "-ac", "1", str(wav)], check=True)
        print(f"{i:02d} {mp3.stat().st_size/1024:.0f} KB")
    total = 0.0
    for wav in sorted(AUDIO.glob("slide*.wav")):
        d = float(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(wav)
        ], text=True).strip())
        total += d
    print(f"audio total: {total:.1f}s = {total/60:.2f} min")
    print(f"video total with 0.9s gaps: {total + 0.9*len(items):.1f}s")

if __name__ == "__main__":
    asyncio.run(main())