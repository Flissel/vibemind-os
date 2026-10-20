"""Streifen-Planer fuer die Mail-Fassung einer Collage: zerlegt die Seite (0..hoehe) lueckenlos in waagerechte
Baender. Ein Band ist "text" (echtes MJML), wenn alle Ebenen darin text-tauglich sind, sonst "bild" (gerendertes
Stueck, hoechstens BILD_MAX hoch). Rein rechnend, ohne Rendern.

Grenze der Planung: Textebenen rendern in seite_html mit min-height, koennen also ueber ihre "hoehe" hinaus
wachsen. Der Planer rechnet mit der deklarierten hoehe; die gerenderte Hoehe zu schaetzen bleibt dem Rendern
(Task 3/4) ueberlassen."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from spaces.marketing.claw import seite as seite_mod

BILD_MAX = 600
_STELLEN = 6  # Rundung gegen Gleitkomma-Rauschen (cos 90° ist nicht genau 0)


@dataclass
class Band:
    art: Literal["text", "bild"]
    oben: float
    unten: float
    ebenen: list[str] = field(default_factory=list)


def _r(wert: float) -> float:
    gerundet = round(wert, _STELLEN)
    return int(gerundet) if float(gerundet).is_integer() else gerundet


def rahmen(e: dict) -> tuple[float, float, float, float]:
    """Achsenparallele Huelle der um den Mittelpunkt gedrehten Ebene: (links, oben, rechts, unten)."""
    w = math.radians(e.get("drehung", 0) or 0)
    c, s = abs(math.cos(w)), abs(math.sin(w))
    hb = (e["breite"] * c + e["hoehe"] * s) / 2
    hh = (e["breite"] * s + e["hoehe"] * c) / 2
    return _r(e["x"] - hb), _r(e["y"] - hh), _r(e["x"] + hb), _r(e["y"] + hh)


def _schneiden(a: tuple, b: tuple) -> bool:
    """Echte Flaechen-Ueberlappung; blosses Beruehren an einer Kante zaehlt nicht."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def text_tauglich(e: dict, alle: list[dict], seite: dict) -> bool:
    """Darf die Ebene als echter Mail-Text (bzw. mj-button) stehen? Erwartet normalisierte Ebenen (pruefen)."""
    if seite["hintergrund"].get("bild") is not None:
        return False
    if e["drehung"] != 0:
        return False
    if e["art"] == "text":
        if e["rolle"] != "TEXT" or e["deckkraft"] != 1 or e["hintergrund"] is not None:
            return False
    elif e["art"] != "knopf":
        return False
    eigen = rahmen(e)
    return not any(_schneiden(eigen, rahmen(o)) for o in alle if o is not e and o["id"] != e["id"])


def lesereihenfolge(ebenen: list[dict]) -> list[dict]:
    """Sortiert nach (oben, links) des Rahmens."""
    return sorted(ebenen, key=lambda e: (rahmen(e)[1], rahmen(e)[0]))


def _sichtbar(e: dict, hoehe: float) -> tuple[float, float] | None:
    """Senkrechter Bereich der Ebene, auf die Seite beschnitten; None, wenn sie ganz ausserhalb liegt."""
    l, o, r, u = rahmen(e)
    o, u = max(o, 0), min(u, hoehe)
    if u <= o or r <= 0 or l >= seite_mod.BREITE:
        return None
    return o, u


def _verschmelzen(bereiche: list[list[float]]) -> list[list[float]]:
    """Vereinigt sich ueberlappende oder beruehrende Bereiche."""
    aus: list[list[float]] = []
    for o, u in sorted(bereiche):
        if aus and o <= aus[-1][1]:
            aus[-1][1] = max(aus[-1][1], u)
        else:
            aus.append([o, u])
    return aus


def _freie_stellen(oben: float, unten: float, belegt: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Abschnitte von [oben, unten], die keine Ebene durchschneidet (Kanten zaehlen als frei)."""
    frei, cur = [], oben
    for o, u in sorted(belegt):
        if o > cur:
            frei.append((cur, min(o, unten)))
        cur = max(cur, u)
        if cur >= unten:
            break
    if cur <= unten:
        frei.append((cur, unten))
    # Kanten-Punkte zwischen beruehrenden Ebenen (u == naechstes o) sind auch frei.
    for o, u in belegt:
        for kante in (o, u):
            if oben <= kante <= unten and not any(a < kante < b for a, b in belegt):
                frei.append((kante, kante))
    return frei


def _naechste(frei: list[tuple[float, float]], lo: float, hi: float, ziel: float, cur: float) -> float | None:
    """Freie Stelle in [lo, hi] (echt unterhalb von cur), die ziel am naechsten liegt."""
    bester = None
    for a, b in frei:
        a, b = max(a, lo), min(b, hi)
        if a > b:
            continue
        kandidat = min(max(ziel, a), b)
        if kandidat > cur and (bester is None or abs(kandidat - ziel) < abs(bester - ziel)):
            bester = kandidat
    return bester


def _teilen(oben: float, unten: float, belegt: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Teilt einen Bildbereich in moeglichst gleich lange Stuecke <= BILD_MAX, bevorzugt an freien Stellen."""
    frei = _freie_stellen(oben, unten, belegt)
    stuecke, cur = [], oben
    while unten - cur > BILD_MAX:
        rest = unten - cur
        anzahl = math.ceil(rest / BILD_MAX)
        ziel = cur + rest / anzahl
        hi = cur + BILD_MAX
        # 1. freie Stelle, die die kleinste Stueckzahl haelt, moeglichst nah an der gleichmaessigen Teilung
        bester = _naechste(frei, max(cur, unten - (anzahl - 1) * BILD_MAX), hi, ziel, cur)
        if bester is None:
            # 2. lieber ein Stueck mehr als mitten durch eine Ebene: die weiteste freie Stelle
            bester = _naechste(frei, cur, hi, hi, cur)
        schnitt = _r(bester if bester is not None else ziel)  # 3. keine freie Stelle: mitten durch, fuer Bilder unschaedlich
        stuecke.append((cur, schnitt))
        cur = schnitt
    stuecke.append((cur, unten))
    return stuecke


def plan(seite: dict) -> list[Band]:
    """Lueckenlose, nach oben sortierte Baender ueber 0..hoehe."""
    s = seite_mod.pruefen(seite)
    hoehe = s["hoehe"]
    alle = s["ebenen"]
    lage: dict[str, tuple[float, float]] = {}
    for e in alle:
        bereich = _sichtbar(e, hoehe)
        if bereich is not None:
            lage[e["id"]] = bereich
    sichtbar = [e for e in alle if e["id"] in lage]

    if s["hintergrund"]["bild"] is not None:
        bild = [[0, hoehe]]
    else:
        tauglich = {e["id"] for e in sichtbar if text_tauglich(e, alle, s)}
        bild = _verschmelzen([list(lage[e["id"]]) for e in sichtbar if e["id"] not in tauglich])
        # Text, der ein Bildband beruehrt oder hineinragt, wird Teil davon - bis nichts mehr dazukommt.
        geaendert = True
        while geaendert:
            geaendert = False
            for eid in list(tauglich):
                o, u = lage[eid]
                if any(o <= bu and bo <= u for bo, bu in bild):
                    tauglich.discard(eid)
                    bild = _verschmelzen(bild + [[o, u]])
                    geaendert = True

    baender: list[Band] = []
    cur = 0
    for bo, bu in bild:
        if bo > cur:
            baender.append(_textband(cur, bo, sichtbar, lage))
        belegt = [lage[e["id"]] for e in sichtbar if bo <= lage[e["id"]][0] and lage[e["id"]][1] <= bu]
        for so, su in _teilen(bo, bu, belegt):
            ids = [e["id"] for e in sichtbar if lage[e["id"]][0] < su and so < lage[e["id"]][1]]
            baender.append(Band("bild", so, su, ids))
        cur = bu
    if cur < hoehe:
        baender.append(_textband(cur, hoehe, sichtbar, lage))
    return baender


def _textband(oben: float, unten: float, sichtbar: list[dict], lage: dict) -> Band:
    ids = [e["id"] for e in sichtbar if oben <= lage[e["id"]][0] and lage[e["id"]][1] <= unten]
    return Band("text", oben, unten, ids)
