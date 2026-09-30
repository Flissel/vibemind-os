"""Echter Lauf am PC: Probebild sehen, Bearbeitungs-Prompt, Bild-zu-Bild mit FLUX,
CLIP-Messung. Schreibt nach E:\\Temp\\ueberarbeiten_probe (nicht ins Repo).
    python -m spaces.marketing.scripts.ueberarbeiten_probe <bild.jpg> "<hinweis>" <staerke>"""
import io
import sys
import time
from pathlib import Path

from PIL import Image

from spaces.marketing.claw import bild_comfy, bild_messen, bild_prompt, bild_sehen


def main(pfad: str, hinweis: str, staerke: int) -> int:
    quelle = Path(pfad).read_bytes()
    with Image.open(io.BytesIO(quelle)) as b:
        breite, hoehe = (b.width // 16) * 16, (b.height // 16) * 16
    t = time.monotonic()
    beschreibung = bild_sehen.beschreiben(quelle)
    print(f"sehen {time.monotonic() - t:.1f} s: {beschreibung}")
    t = time.monotonic()
    text = bild_prompt.bearbeitungs_prompt(beschreibung, {"alt": ""}, "Probe", hinweis)
    print(f"prompt {time.monotonic() - t:.1f} s: {text}")
    t = time.monotonic()
    png = bild_comfy.ueberarbeiten(text, quelle, breite, hoehe, 7, staerke, zeitlimit_s=540)
    print(f"flux {time.monotonic() - t:.1f} s")
    bild_comfy.freigeben()
    t = time.monotonic()
    print(f"messung {bild_messen.messen(quelle, png, hinweis)} ({time.monotonic() - t:.1f} s)")
    ziel = Path(r"E:\Temp\ueberarbeiten_probe")
    ziel.mkdir(parents=True, exist_ok=True)
    (ziel / f"ergebnis-{staerke}.png").write_bytes(png)
    print("geschrieben:", ziel / f"ergebnis-{staerke}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2], int(sys.argv[3])))
