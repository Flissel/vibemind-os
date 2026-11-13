"""Sichtbarer Text und Titel eines HTML-Konzepts fuer die Wissenssuche.

Nur was man auf der Seite liest, kommt in den Index: ``script`` (auch der Datenblock
``rowboat-konzept``), ``style``, Kommentare und Tags bleiben draussen. Gelesen werden hoechstens
``MAX_ZEICHEN`` Zeichen. Der Abtaster ist absichtlich selbst geschrieben (nur ``str.find``, jeder
Schritt rueckt vor): ``html.parser`` ist auf unvollstaendigem Markup (``<a <a <a``, ``<!--<!--``)
super-linear und liesse den Indexer an einer kaputten Datei haengen. Etwas Unvollstaendiges beendet den Lauf.
"""
from __future__ import annotations

import html as _html
import re

MAX_ZEICHEN = 1_048_576

_LEERRAUM = re.compile(r"\s+")
_ROHTEXT = ("script", "style")


def _glatt(stuecke: list[str]) -> str:
    return _LEERRAUM.sub(" ", _html.unescape("".join(stuecke))).strip()


def _tag_ende(s: str, start: int) -> int:
    """Index von ``>`` des Tags ab ``start`` (Anfuehrungszeichen in Attributen beachtet) oder -1."""
    i, n = start, len(s)
    anf = ""
    while i < n:
        c = s[i]
        if anf:
            if c == anf:
                anf = ""
        elif c in "\"'":
            anf = c
        elif c == ">":
            return i
        i += 1
    return -1


def lesen(html: str) -> tuple[str | None, str]:
    """``(titel, sichtbarer_text)`` in einem linearen Durchlauf."""
    s = html[:MAX_ZEICHEN]
    kleinbuchstaben = s.lower()
    n = len(s)
    text: list[str] = []
    titel_stuecke: list[str] = []
    titel_zustand = 0       # 0 = noch keiner, 1 = innerhalb, 2 = fertig
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
        if lt + 1 >= n or not (s[lt + 1].isalpha() or s[lt + 1] in "/!?"):
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
            break
        inneres = kleinbuchstaben[lt + 1:ende].strip()
        name = re.match(r"/?[a-z][a-z0-9:-]*", inneres)
        name = name.group(0) if name else ""
        i = ende + 1
        if name in _ROHTEXT:
            schluss = kleinbuchstaben.find(f"</{name}", i)
            if schluss < 0:
                break
            ende = s.find(">", schluss)
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
    titel_text = _glatt(titel_stuecke)
    return (titel_text or None), _glatt(text)


def sichtbarer_text(html: str) -> str:
    """Der lesbare Text (inkl. Titel), Leerraum zusammengezogen."""
    return lesen(html)[1]


def titel(html: str) -> str | None:
    """Inhalt des ersten ``<title>``; ``None``, wenn es keinen (nicht leeren) gibt."""
    return lesen(html)[0]
