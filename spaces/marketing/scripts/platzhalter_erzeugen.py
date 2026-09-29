"""Gestaltete Platzhalterbilder fuer leere Bildplaetze (Spec §4): dunkles
Tuerkis-Verlaufsfeld mit feinem Netz, kein Text, deterministisch (fester
Seed) - erneutes Erzeugen aendert die Dateien nicht.
    python -m spaces.marketing.scripts.platzhalter_erzeugen"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ORDNER = Path(__file__).resolve().parents[1] / "vorlagen" / "newsletter" / "platzhalter"
VERHAELTNISSE = ((2, 1), (3, 1), (4, 3), (16, 9), (1, 1))
LANG = 1200
OBEN, UNTEN, AKZENT = (15, 36, 34), (29, 59, 57), (94, 234, 212)


def bild(a: int, b: int) -> Image.Image:
    w = LANG if a >= b else round(LANG * a / b)
    h = round(w * b / a)
    img = Image.new("RGB", (w, h), OBEN)
    zeichnen = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        zeichnen.line([(0, y), (w, y)], fill=tuple(round(o + (u - o) * t) for o, u in zip(OBEN, UNTEN)))
    netz = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    nz = ImageDraw.Draw(netz)
    rnd = random.Random(a * 100 + b)
    punkte = [(rnd.uniform(0, w), rnd.uniform(0, h)) for _ in range(max(18, (w * h) // 30000))]
    for i, p in enumerate(punkte):
        for q in punkte[i + 1:]:
            if (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 < (min(w, h) * 0.28) ** 2:
                nz.line([p, q], fill=AKZENT + (70,), width=1)
        r = rnd.uniform(1.5, 3.5)
        nz.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=AKZENT + (200,))
    img = Image.alpha_composite(img.convert("RGBA"), netz.filter(ImageFilter.GaussianBlur(0.6))).convert("RGB")
    return img.quantize(colors=64).convert("RGB")


def main() -> int:
    ORDNER.mkdir(parents=True, exist_ok=True)
    for a, b in VERHAELTNISSE:
        ziel = ORDNER / f"platzhalter-{a}x{b}.png"
        bild(a, b).save(ziel, "PNG", optimize=True)
        print(ziel.name, ziel.stat().st_size // 1024, "KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
