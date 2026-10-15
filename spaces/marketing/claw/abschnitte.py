"""Fertige Abschnitte fuer den Gestaltungs-Agenten - dieselben Bauplaene wie im Editor.

abschnitte.json ist eine Kopie von sales-claw editor/test/abschnitte.golden.json (erzeugt von
editor/scripts/abschnitte_golden.mjs aus editor/src/abschnitte.ts). Die Bauplaene tragen Farb-Marken
wie "{{akzent}}"; farben() rechnet die Ladenfarben mit derselben Regel wie farbenAus im Editor.
Gleichlauf: tests/test_abschnitte.py (Farbproben + Datei-Vergleich mit SALES_CLAW_DIR)."""
from __future__ import annotations

import copy
import json
import math
import os
import re
from typing import Callable
from urllib.parse import unquote

DATEI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "abschnitte.json")
_GOLDEN = json.loads(open(DATEI, encoding="utf-8").read())
ANZEIGE: str = _GOLDEN["anzeige"]
BAUPLAENE: dict = _GOLDEN["abschnitte"]
SCHLUESSEL: tuple[str, ...] = tuple(BAUPLAENE)
_FARBE = re.compile(r"^#[0-9a-fA-F]{6}$")
_MARKE = re.compile(r"^\{\{([A-Za-z]+)\}\}$")


def _ist_farbe(w) -> bool:
    return isinstance(w, str) and bool(_FARBE.match(w))


def _objekt(w) -> dict:
    return w if isinstance(w, dict) else {}


def _kanal(f: str, i: int) -> int:
    return int(f[1 + 2 * i:3 + 2 * i], 16)


def mischen(a: str, b: str, anteil: float) -> str:
    """Wie mischen() im Editor: floor(x + 0.5) statt round (Python rundet sonst auf gerade)."""
    return "#" + "".join(f"{math.floor(_kanal(a, i) * (1 - anteil) + _kanal(b, i) * anteil + 0.5):02x}"
                         for i in range(3))


def _dunkel(f: str) -> bool:
    return (0.2126 * _kanal(f, 0) + 0.7152 * _kanal(f, 1) + 0.0722 * _kanal(f, 2)) / 255 < 0.4


def _wert_an(dok: dict, pfad: str):
    x = dok
    for teil in pfad.split("/"):
        x = _objekt(x).get(teil)
    return x


def farben(dok: dict) -> dict[str, str]:
    """Ladenfarben wie farbenAus im Editor: Rollen aus root.data.rollen, sonst erster Knopf, sonst Petrol."""
    d = _objekt(_objekt(dok.get("root")).get("data"))

    def s(k: str, std: str) -> str:
        return d[k].lower() if _ist_farbe(d.get(k)) else std

    def rolle(name: str) -> str | None:
        for pfad, r in _objekt(d.get("rollen")).items():
            w = _wert_an(dok, pfad) if r == name else None
            if _ist_farbe(w):
                return w.lower()
        return None

    knopf = None
    for b in dok.values():
        w = _objekt(_objekt(_objekt(b).get("data")).get("props")).get("buttonBackgroundColor")
        if _objekt(b).get("type") == "Button" and _ist_farbe(w):
            knopf = w.lower()
            break
    text, grund = s("textColor", "#cfe3df"), s("canvasColor", "#0f2422")
    akzent = rolle("akzent") or knopf or "#5eead4"
    return {"text": text, "grund": grund, "akzent": akzent,
            "aufAkzent": rolle("auf_akzent") or grund,
            "akzentText": rolle("akzent_text") or akzent,
            "hell": mischen(text, "#ffffff", 0.6) if _dunkel(grund) else text,
            "karte": mischen(grund, text, 0.06)}


def _fuellen(wert, f: dict, ids: dict):
    if isinstance(wert, dict):
        return {k: _fuellen(v, f, ids) for k, v in wert.items()}
    if isinstance(wert, list):
        return [_fuellen(v, f, ids) for v in wert]
    if isinstance(wert, str):
        m = _MARKE.match(wert)
        if m:
            return f[m.group(1)]
        if wert in ids:
            return ids[wert]
        if wert.startswith(ANZEIGE):  # Editor zeigt Medien unter ANZEIGE, gespeichert wird "medien:<datei>"
            return "medien:" + unquote(wert[len(ANZEIGE):])
    return wert


def bauen(schluessel: str, dok: dict, neue_id: Callable[[], str]) -> tuple[str, dict]:
    """(oberste Block-id, neue Bloecke) des Abschnitts in den Farben des Newsletters. KeyError bei unbekanntem."""
    plan = BAUPLAENE[schluessel]
    ids = {alt: neue_id() for alt in plan["bloecke"]}
    bloecke = {ids[alt]: _fuellen(copy.deepcopy(b), farben(dok), ids) for alt, b in plan["bloecke"].items()}
    return ids[plan["oben"]], bloecke
