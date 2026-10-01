"""Erzeugte Grafiken fuer die Vorlage tech (Spec 2026-10-01 §6): Signal-Karte
mit Halo, Ringen und Kern sowie ein radialer Lichtschein - im Ladenakzent,
mit Pillow gezeichnet (kein Modell, darf auf der VM laufen). Gleicher Akzent
= gleiche Datei."""
from __future__ import annotations

import os
import re
import tempfile

from PIL import Image, ImageDraw, ImageFilter

from spaces.marketing.claw.vorlagen_marke import INK, _rgb, mischen

SURFACE = "#111725"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _ziel(ordner: str, name: str) -> str | None:
    """Pfad zusammenstellen und Ordner validieren."""
    if not ordner or not os.path.isdir(ordner):
        return None
    return os.path.join(ordner, name)


def _speichern(bild: Image.Image, pfad: str, fmt: str, ordner: str) -> bool:
    """Bild atomar speichern mit Tempfile-Muster.

    Returns:
        True bei Erfolg, False bei OSError (TMP wird aufgeräumt).
    """
    fd = None
    tmp = None
    try:
        # Tempfile im selben Ordner erzeugen
        fd, tmp = tempfile.mkstemp(dir=ordner, suffix=".tmp")
        os.close(fd)  # Datei-Deskriptor schließen vor Pillow-Save
        fd = None

        # Speichern mit optimalen Einstellungen
        bild.save(tmp, fmt, **({"quality": 88} if fmt == "JPEG" else {"optimize": True}))

        # Atomar ersetzen
        os.replace(tmp, pfad)
        return True
    except OSError:
        # Bei Fehler Tempfile aufräumen
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _halo(breite: int, hoehe: int, mitte: tuple[int, int], radius: int, innen: str, aussen: str) -> Image.Image:
    """Radialer Halo-Gradient (elliptisch gestreckt)."""
    bild = Image.new("RGB", (breite, hoehe), _rgb(aussen))
    zeichner = ImageDraw.Draw(bild)
    for i in range(40, 0, -1):
        r = radius * i / 40
        farbe = mischen(innen, aussen, i / 40)
        zeichner.ellipse([mitte[0] - r, mitte[1] - r * 0.75, mitte[0] + r, mitte[1] + r * 0.75], fill=_rgb(farbe))
    return bild.filter(ImageFilter.GaussianBlur(24))


def signal(akzent: str, ordner: str) -> str | None:
    """Signal-Karte: Halo, zwei Ringe, Kern, Funkel-Zeichen im Ladenakzent.

    Returns:
        "medien:tech-signal-<hex6>.png" oder None (ungültige Farbe, fehlender Ordner, Speicherfehler).
    """
    if not _HEX.match(akzent or ""):
        return None
    a = akzent.lower()
    name = f"tech-signal-{a[1:]}.png"
    pfad = _ziel(ordner, name)
    if pfad is None:
        return None
    if not os.path.exists(pfad):
        w, h, m = 1072, 760, (536, 380)
        bild = _halo(w, h, m, 520, mischen(a, SURFACE, 0.72), "#101622")
        z = ImageDraw.Draw(bild)
        for r, farbe in ((280, mischen(a, INK, 0.55)), (190, mischen(a, INK, 0.40))):
            z.ellipse([m[0] - r, m[1] - r, m[0] + r, m[1] + r], outline=_rgb(farbe), width=2)
        z.ellipse([m[0] - 100, m[1] - 100, m[0] + 100, m[1] + 100], fill=_rgb(a))
        # Funkel-Zeichen: vier spitze Rauten in Ink
        for dx, dy, s in ((0, -6, 34), (40, -40, 14), (-38, 30, 10)):
            cx, cy = m[0] + dx, m[1] + dy
            z.polygon([(cx, cy - s), (cx + s * 0.28, cy), (cx, cy + s), (cx - s * 0.28, cy)], fill=_rgb(INK))
            z.polygon([(cx - s, cy), (cx, cy - s * 0.28), (cx + s, cy), (cx, cy + s * 0.28)], fill=_rgb(INK))
        if not _speichern(bild, pfad, "PNG", ordner):
            return None
    return f"medien:{name}"


def glow(akzent: str, ordner: str) -> str | None:
    """Radialer Lichtschein-Hintergrund im Ladenakzent.

    Returns:
        "medien:tech-glow-<hex6>.jpg" oder None (ungültige Farbe, fehlender Ordner, Speicherfehler).
    """
    if not _HEX.match(akzent or ""):
        return None
    a = akzent.lower()
    name = f"tech-glow-{a[1:]}.jpg"
    pfad = _ziel(ordner, name)
    if pfad is None:
        return None
    if not os.path.exists(pfad):
        bild = _halo(1200, 900, (984, 378), 620, mischen(a, INK, 0.80), INK)
        if not _speichern(bild, pfad, "JPEG", ordner):
            return None
    return f"medien:{name}"
