"""Schaufenster — jedes Artefakt als Datei, damit der Betreiber Qualitaet
am Ergebnis beurteilen kann (Leitplanke 2 der Spec). Nur schreiben, nie
loeschen; Ordnernamen tragen Zeitstempel, damit nichts ueberschrieben wird.
"""
import os
import re
import time
from pathlib import Path

_VORGABE = Path(__file__).resolve().parent / "schaufenster"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "ohne-titel"


def ablegen(unterordner: str, dateiname: str, inhalt: str) -> str:
    wurzel = Path(os.environ.get("SCHAUFENSTER_DIR", str(_VORGABE)))
    ordner = wurzel / f"{time.strftime('%Y-%m-%d')}--{_slug(unterordner)}"
    ordner.mkdir(parents=True, exist_ok=True)
    # Nie ueberschreiben: derselbe Tag + Slug + Dateiname kommt vor (zwei
    # Kampagnenlaeufe zum selben Ziel) — der zweite bekommt ein -2, -3, …
    # Gemessen 03.09.2026: ohne das verlor das 01-Uhr-Briefing seinen Beweis.
    ziel = ordner / dateiname
    stamm, endung = os.path.splitext(dateiname)
    lauf = 2
    while ziel.exists():
        ziel = ordner / f"{stamm}-{lauf}{endung}"
        lauf += 1
    ziel.write_text(inhalt, encoding="utf-8")
    return str(ziel)
