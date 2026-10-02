"""Echter Lauf am PC: ein lokales Bild freistellen (BiRefNet in ComfyUI), PNG schreiben,
Vordergrundanteil und Laufzeit drucken. COMFYUI_URL beachten.
    python -m spaces.marketing.scripts.freistellen_probe <bild> <ausgabe.png>"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from spaces.marketing.claw import bild_comfy
from spaces.marketing.workers import bild_worker

HILFE = "Aufruf: python -m spaces.marketing.scripts.freistellen_probe <bild> <ausgabe.png>"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(HILFE, file=sys.stderr)
        return 2
    quelle_pfad, ziel = Path(argv[0]), Path(argv[1])
    if not quelle_pfad.is_file():
        print(f"Bild nicht gefunden: {quelle_pfad}", file=sys.stderr)
        return 2
    if ziel.suffix.lower() != ".png":
        print("Ausgabe muss auf .png enden (Alpha-Kanal)", file=sys.stderr)
        return 2
    quelle = bild_worker.quelle_normalisieren(quelle_pfad.read_bytes())
    if quelle is None:
        print("Bild nicht lesbar", file=sys.stderr)
        return 2
    t = time.monotonic()
    png = bild_comfy.freistellen(quelle, zeitlimit_s=540)
    sekunden = time.monotonic() - t
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_bytes(png)
    anteil = bild_worker.vordergrund_anteil(png)
    print(f"vordergrund {anteil:.1%} | {sekunden:.1f} s | {len(png)} Bytes | {ziel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
