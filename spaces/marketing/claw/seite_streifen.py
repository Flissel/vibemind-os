"""Streifen-Planer fuer die Mail-Fassung einer Collage: zerlegt die Seite (0..hoehe) lueckenlos in waagerechte
Baender. Ein Band ist "text" (echtes MJML), wenn alle Ebenen darin text-tauglich sind, sonst "bild" (gerendertes
Stueck, hoechstens BILD_MAX hoch). Rein rechnend, ohne Rendern.

Grenze der Planung: Textebenen rendern in seite_html mit min-height, koennen also ueber ihre "hoehe" hinaus
wachsen. Der Planer rechnet mit der deklarierten hoehe; die gerenderte Hoehe zu schaetzen bleibt dem Rendern
(Task 3/4) ueberlassen.

Text-tauglich ist nur Fliesstext/Knopf, der senkrecht mit keiner anderen sichtbaren Ebene ueberlappt (auch nicht
nebeneinander - MJML kennt keine freie Spalte); Beruehren an der Kante ist erlaubt. Eine text-taugliche Ebene, die
naeher als 1 Einheit an einer nicht text-tauglichen Ebene liegt (Beruehren eingeschlossen), wird Teil von deren
Bildband. Diese Aufnahme kaskadiert NICHT ueber aufgenommenen Text weiter: in einer Textspalte, deren letzter
Kasten ein Bild beruehrt, wird nur dieser Kasten Bild."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from spaces.marketing.claw import seite as seite_mod

BILD_MAX = 600
NAH = 1  # Abstand unter 1 Einheit zu einer Bild-Ebene gilt als Beruehren
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


def text_tauglich(e: dict, alle: list[dict], seite: dict) -> bool:
    """Darf die Ebene als echter Mail-Text (bzw. mj-button) stehen? Erwartet normalisierte Ebenen (pruefen)."""
    if seite["hintergrund"].get("bild") is not None:
        return False
    if e["drehung"] != 0:
        return False
    if e["art"] == "text":
        if e["rolle"] != "TEXT" or e["deckkraft"] != 1 or e["hintergrund"] is not None:
            return False
    elif e["art"] != "knopf" or e["deckkraft"] != 1:  # mj-button kennt keine Deckkraft
        return False
    hoehe = seite["hoehe"]
    eigen = _sichtbar(e, hoehe)
    if eigen is None:
        _, o, _, u = rahmen(e)
        eigen = (o, u)
    for andere in alle:
        if andere is e or andere["id"] == e["id"]:
            continue
        bereich = _sichtbar(andere, hoehe)  # Ebenen ganz ausserhalb der Seite zaehlen nicht
        if bereich is not None and bereich[0] < eigen[1] and eigen[0] < bereich[1]:
            return False
    return True


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
    """Vereinigt Bereiche, die ueberlappen oder weniger als NAH auseinanderliegen."""
    aus: list[list[float]] = []
    for o, u in sorted(bereiche):
        if aus and o - aus[-1][1] < NAH:
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
        quellen = [lage[e["id"]] for e in sichtbar if e["id"] not in tauglich]
        # Text, der eine Bild-Ebene ueberlappt oder naeher als NAH an ihr liegt, wird Teil ihres Bildbands.
        # Gemessen nur an den urspruenglichen Bild-Ebenen, damit eine beruehrende Textspalte nicht kaskadiert.
        aufgenommen = [lage[eid] for eid in tauglich
                       if any(max(bo - lage[eid][1], lage[eid][0] - bu) < NAH for bo, bu in quellen)]
        bild = _verschmelzen([list(b) for b in quellen + aufgenommen])

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
