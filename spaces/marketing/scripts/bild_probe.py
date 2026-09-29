"""Echter Messlauf am PC: ein Bild mit dem eingestellten Arbeitsablauf, Dauer und
Grafikspeicher gemessen. Schreibt nach E:\\Temp (nicht ins Repo).
    python -m spaces.marketing.scripts.bild_probe"""
import subprocess
import time
from pathlib import Path

from spaces.marketing.claw import bild_comfy

PROMPT = ("abstract glowing turquoise neural network on a dark deep-teal background, soft cinematic light, "
          "editorial, high detail, no text, no letters, no words, no logos, no watermark")


def vram_mib() -> str:
    try:
        return subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except OSError:
        return "?"


def main() -> int:
    ziel = Path(r"E:\Temp\bild_probe")
    ziel.mkdir(parents=True, exist_ok=True)
    for breite, hoehe in ((1104, 560), (544, 400)):
        t = time.monotonic()
        png = bild_comfy.erzeugen(PROMPT, breite, hoehe, 7)
        dauer = time.monotonic() - t
        print(f"{breite}x{hoehe}: {dauer:.1f} s, {len(png)//1024} KB, VRAM {vram_mib()}")
        (ziel / f"probe-{breite}x{hoehe}.png").write_bytes(png)
    bild_comfy.freigeben()
    print(f"nach /free: VRAM {vram_mib()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
