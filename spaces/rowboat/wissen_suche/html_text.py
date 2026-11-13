"""Sichtbarer Text und Titel eines HTML-Konzepts fuer die Wissenssuche.

Nur was man auf der Seite liest, kommt in den Index: ``script`` (auch der Datenblock
``rowboat-konzept``), ``style``, Kommentare und Tags bleiben draussen. Gelesen werden hoechstens
``MAX_ZEICHEN`` Zeichen. Der Abtaster ist absichtlich selbst geschrieben (nur ``str.find``, jeder
Schritt rueckt vor): ``html.parser`` ist auf unvollstaendigem Markup (``<a <a <a``, ``<!--<!--``)
super-linear und liesse den Indexer an einer kaputten Datei haengen. Ein ``<``, dessen Tag nicht schliesst,
gilt als Text (hoechstens ``FEHLVERSUCHE_MAX`` Mal, danach endet der Lauf); ein offener Kommentar oder ein
offenes Skript beendet den Lauf. Gross/Klein wird nur fuer ASCII ignoriert und immer auf dem Original
gesucht: ``"İ".lower()`` ist laenger als ``"İ"`` und verschoebe sonst die Positionen.
"""
from __future__ import annotations

import html as _html
import re

MAX_ZEICHEN = 1_048_576
TITEL_MAX = 300             # ein nie geschlossenes <title> nimmt nicht den Rest der Datei mit
TAG_MAX = 2048              # Zeichen eines Tags ausserhalb von Attributwerten in Anfuehrungszeichen
ATTRIBUTE_MAX = 512         # Attributwerte je Tag, die der Abtaster hoechstens ansieht
FEHLVERSUCHE_MAX = 100      # so oft gilt ein nicht schliessendes "<" als Text, danach endet der Lauf

_LEERRAUM = re.compile(r"\s+")
_NAME = re.compile(r"/?[a-z][a-z0-9:-]*", re.I | re.A)
_GLEICH_ODER_ENDE = re.compile(r"[=>]")
_WERT_ENDE = re.compile(r"[\s>]")
_SCHLUSS = {name: re.compile("</" + name, re.I | re.A) for name in ("script", "style")}


def _glatt(stuecke: list[str]) -> str:
    return _LEERRAUM.sub(" ", _html.unescape("".join(stuecke))).strip()


def _tag_ende(s: str, start: int) -> int:
    """Index von ``>`` des Tags ab ``start`` oder -1, wenn es nicht schliesst.

    Anfuehrungszeichen zaehlen wie im Browser nur als Attributwert (direkt nach ``=``); ein ``'`` im
    Fliesstext ("it's") oeffnet nichts. Ausserhalb solcher Werte hoechstens ``TAG_MAX`` Zeichen, damit lange
    ``data:``-Bilder in Anfuehrungszeichen ein Tag bleiben, ein verirrtes ``<`` aber nicht den Rest schluckt.
    """
    n = len(s)
    i = start
    in_werten = 0
    for _ in range(ATTRIBUTE_MAX):
        grenze = min(n, start + TAG_MAX + in_werten)
        m = _GLEICH_ODER_ENDE.search(s, i, grenze)
        if m is None:
            return -1
        if m.group() == ">":
            return m.start()
        j = m.start() + 1
        while j < grenze and s[j] in " \t\n\r\f":
            j += 1
        if j < n and s[j] in "\"'":
            schluss = s.find(s[j], j + 1)
            if schluss < 0:
                return -1
            in_werten += schluss - j + 1
            i = schluss + 1
        else:
            w = _WERT_ENDE.search(s, j, grenze)
            if w is None:
                return -1
            i = w.start()
    return -1


def lesen(html: str) -> tuple[str | None, str]:
    """``(titel, sichtbarer_text)`` in einem linearen Durchlauf."""
    s = html[:MAX_ZEICHEN]
    n = len(s)
    text: list[str] = []
    titel_stuecke: list[str] = []
    titel_zustand = 0       # 0 = noch keiner, 1 = innerhalb, 2 = fertig
    fehlversuche = 0
    i = 0
    while i < n:
        lt = s.find("<", i)
        if lt < 0:
            rest = s[i:]
            text.append(rest)
            if titel_zustand == 1:
                titel_stuecke.append(rest)
            break
        if lt > i:
            stueck = s[i:lt]
            text.append(stueck)
            if titel_zustand == 1:
                titel_stuecke.append(stueck)
        if lt + 1 >= n or not ((s[lt + 1].isascii() and s[lt + 1].isalpha()) or s[lt + 1] in "/!?"):
            text.append("<")        # kein Tag (z. B. "3 < 5"): als Text behandeln
            if titel_zustand == 1:
                titel_stuecke.append("<")
            i = lt + 1
            continue
        if s.startswith("<!--", lt):
            ende = s.find("-->", lt + 4)
            if ende < 0:
                break
            i = ende + 3
            text.append(" ")
            continue
        ende = _tag_ende(s, lt + 1)
        if ende < 0:
            fehlversuche += 1
            if fehlversuche > FEHLVERSUCHE_MAX:
                break
            text.append("<")        # Tag schliesst nicht (z. B. "<b x='..." ohne Ende): als Text lesen
            if titel_zustand == 1:
                titel_stuecke.append("<")
            i = lt + 1
            continue
        name = _NAME.match(s, lt + 1, ende)
        name = name.group(0).lower() if name else ""
        i = ende + 1
        if name in _SCHLUSS:
            schluss = _SCHLUSS[name].search(s, i)
            if schluss is None:
                break
            ende = s.find(">", schluss.start())
            if ende < 0:
                break
            i = ende + 1
        elif name == "title" and titel_zustand == 0:
            titel_zustand = 1
            continue
        elif name == "/title" and titel_zustand == 1:
            titel_zustand = 2
        text.append(" ")
        if titel_zustand == 1:
            titel_stuecke.append(" ")
    titel_text = _glatt(titel_stuecke)[:TITEL_MAX].rstrip()
    return (titel_text or None), _glatt(text)


def sichtbarer_text(html: str) -> str:
    """Der lesbare Text (inkl. Titel), Leerraum zusammengezogen."""
    return lesen(html)[1]


def titel(html: str) -> str | None:
    """Inhalt des ersten ``<title>``; ``None``, wenn es keinen (nicht leeren) gibt."""
    return lesen(html)[0]
