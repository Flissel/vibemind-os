"""Galerie fuer die Sichtabnahme (Spec 2026-10-01 §10): jede Vorlage mit einer
Ladenpalette gefuellt, in 600 und 380 px, nebeneinander in index.html.
    python -m spaces.marketing.scripts.vorlagen_galerie <ziel> [--akzent #hex] [--flaeche #hex]
           [--bild-basis https://.../] [--schrift-basis https://.../]"""
from __future__ import annotations

import argparse
import html
import json
import pathlib
import shutil
import sys

from spaces.marketing.claw import bloecke_mjml, vorlagen_grafik, vorlagen_marke

VORLAGEN = pathlib.Path(__file__).resolve().parents[1] / "vorlagen" / "newsletter"
NAMEN = ["studio", "zeitung", "firmenblatt", "minimal", "klassik", "bildkopf", "tech"]
PFLICHT = {"impressum": "[Laden] · [Straße] · [PLZ Ort]", "abmelde_hinweis": "Abmelden: {abmeldelink}"}


def main(argv: list[str]) -> int:
    a = argparse.ArgumentParser()
    a.add_argument("ziel")
    a.add_argument("--akzent", default="#c2410c")
    a.add_argument("--flaeche", default="#2f4858")
    a.add_argument("--bild-basis", default="")
    a.add_argument("--schrift-basis", default="")
    o = a.parse_args(argv)
    ziel = pathlib.Path(o.ziel)
    ziel.mkdir(parents=True, exist_ok=True)
    for p in (VORLAGEN / "platzhalter").glob("*.png"):
        shutil.copy(p, ziel / p.name)
    basis = o.bild_basis or "https://galerie.invalid/"
    zellen = []
    for name in NAMEN:
        d = json.loads((VORLAGEN / f"{name}.json").read_text(encoding="utf-8"))["bloecke"]
        grund = d["root"]["data"].get("canvasColor") or "#ffffff"
        werte = {**vorlagen_marke.rollen({"akzent": o.akzent, "flaeche": o.flaeche}, grund), "laden": "[Laden]",
                 "signal_bild": vorlagen_grafik.signal(o.akzent, str(ziel)) or "medien:platzhalter-4x3.png",
                 "glow_bild": vorlagen_grafik.glow(o.akzent, str(ziel)) or "medien:platzhalter-4x3.png"}
        fertig = vorlagen_marke.einsetzen(d, werte)
        for breite, handy in ((600, False), (380, True)):
            h = bloecke_mjml.rendern(fertig, name, "", PFLICHT, bild_basis=basis, handy=handy,
                                     schrift_basis=o.schrift_basis)
            if not o.bild_basis:
                h = h.replace(basis, "")
            (ziel / f"{name}-{breite}.html").write_text(h, encoding="utf-8")
        zellen.append(f'<section><h2>{html.escape(name)}</h2><iframe src="{name}-600.html" width="620" height="1400">'
                      f'</iframe><iframe src="{name}-380.html" width="400" height="1400"></iframe></section>')
    (ziel / "index.html").write_text("<!doctype html><meta charset=utf-8><title>Vorlagen</title>"
                                     "<style>section{display:flex;gap:16px;align-items:flex-start}</style>"
                                     + "".join(zellen), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
