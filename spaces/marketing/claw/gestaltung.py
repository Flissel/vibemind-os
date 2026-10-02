"""Gestaltungsflaeche (sales-claw Spec 2026-10-02-newsletter-gestaltung-und-agent-design.md §1, §5).
Ebenen als Daten pruefen und mit Pillow zu einem JPEG rechnen. Kein Modell."""
from __future__ import annotations

import math
import os
import re

from spaces.marketing.claw import schriften

FORMATE: dict[str, tuple[int, int]] = {"quer": (3, 2), "quadrat": (1, 1), "hoch": (4, 5), "banner": (3, 1)}
BREITE = 600
FAKTOR = 2
MAX_EBENEN = 20
ORDNER_SCHRIFTEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schriftdateien")
_FARBE = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_QUELLE = re.compile(r"^medien:[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.(png|jpe?g|gif|webp)$")
_AUSRICHTUNG = ("links", "mitte", "rechts")


class GestaltungFehler(ValueError):
    pass


def hoehe(fmt: str) -> int:
    a, b = FORMATE[fmt]
    return round(BREITE * b / a)


def _zahl(wert, name: str, lo: float, hi: float) -> float:
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not math.isfinite(wert):
        raise GestaltungFehler(f"{name} muss eine Zahl sein")
    if not lo <= wert <= hi:
        raise GestaltungFehler(f"{name} muss zwischen {lo:g} und {hi:g} liegen")
    return float(wert)


def _farbe(wert, name: str) -> str:
    if not isinstance(wert, str) or not _FARBE.fullmatch(wert):
        raise GestaltungFehler(f"Farbe {name} muss #RRGGBB sein")
    return wert.upper()


def schrift_datei(sid: str, gewicht: int, kursiv: bool) -> str:
    eintrag = schriften.REGISTER.get(sid)
    if not eintrag:
        raise GestaltungFehler(f"Schrift {sid} gibt es nicht")
    stil = "italic" if kursiv else "normal"
    if (gewicht, stil) not in [tuple(d) for d in eintrag["dateien"]]:
        raise GestaltungFehler(f"Schnitt {gewicht} {stil} gibt es für {sid} nicht")
    return os.path.join(ORDNER_SCHRIFTEN, f"{sid}-{gewicht}-{stil}.woff2")


def _ebene(e, gesehen: set) -> dict:
    if not isinstance(e, dict):
        raise GestaltungFehler("Ebene muss ein Objekt sein")
    eid = e.get("id")
    if not isinstance(eid, str) or not _ID.fullmatch(eid):
        raise GestaltungFehler("Ebenen-id ungültig")
    if eid in gesehen:
        raise GestaltungFehler(f"Ebene {eid} doppelt")
    gesehen.add(eid)
    basis = {"id": eid, "x": _zahl(e.get("x"), "x (Zahl)", -600, 1200),
             "y": _zahl(e.get("y"), "y (Zahl)", -750, 1500),
             "drehung": _zahl(e.get("drehung", 0), "Drehung", -180, 180)}
    if e.get("art") == "bild":
        q = e.get("quelle")
        if not isinstance(q, str) or not _QUELLE.fullmatch(q) or ".." in q:
            raise GestaltungFehler(f"Bild nur aus den Medien (medien:<datei>) in Ebene {eid}")
        return {**basis, "art": "bild", "quelle": q, "breite": _zahl(e.get("breite"), "Breite", 8, 3000)}
    if e.get("art") == "text":
        text = e.get("text")
        if not isinstance(text, str) or not text.strip():
            raise GestaltungFehler(f"Text fehlt in Ebene {eid}")
        if len(text) > 200:
            raise GestaltungFehler("Text höchstens 200 Zeichen")
        if text.count("\n") > 5:
            raise GestaltungFehler("Text höchstens 6 Zeilen")
        if e.get("ausrichtung", "links") not in _AUSRICHTUNG:
            raise GestaltungFehler("Ausrichtung links, mitte oder rechts")
        gewicht = e.get("gewicht")
        if isinstance(gewicht, bool) or not isinstance(gewicht, int):
            raise GestaltungFehler("Schnitt (gewicht) muss eine ganze Zahl sein")
        kursiv = e.get("kursiv", False) is True
        schrift_datei(str(e.get("schrift")), gewicht, kursiv)
        return {**basis, "art": "text", "text": text, "schrift": e["schrift"], "gewicht": gewicht,
                "kursiv": kursiv, "groesse": _zahl(e.get("groesse"), "Größe", 10, 160),
                "farbe": _farbe(e.get("farbe"), "Text"), "ausrichtung": e.get("ausrichtung", "links"),
                "zeilenabstand": _zahl(e.get("zeilenabstand", 1.2), "Zeilenabstand", 0.8, 2.0)}
    raise GestaltungFehler(f"Art der Ebene {eid} muss bild oder text sein")


def pruefen(g) -> dict:
    if not isinstance(g, dict):
        raise GestaltungFehler("Gestaltung muss ein Objekt sein")
    if g.get("version") != 1:
        raise GestaltungFehler("Version muss 1 sein")
    if g.get("format") not in FORMATE:
        raise GestaltungFehler("Format muss quer, quadrat, hoch oder banner sein")
    ebenen = g.get("ebenen")
    if not isinstance(ebenen, list):
        raise GestaltungFehler("Ebenen müssen eine Liste sein")
    if len(ebenen) > MAX_EBENEN:
        raise GestaltungFehler("Höchstens 20 Ebenen")
    gesehen: set = set()
    return {"version": 1, "format": g["format"], "hintergrund": _farbe(g.get("hintergrund"), "Hintergrund"),
            "ebenen": [_ebene(e, gesehen) for e in ebenen]}
