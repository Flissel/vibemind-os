"""Sichtbarer Text und Titel eines HTML-Konzepts fuer die Wissenssuche.

Nur was man auf der Seite liest, kommt in den Index: ``script``, ``style``, Kommentare und Tags bleiben
draussen. Einzige Ausnahme: ein Videokonzept wird per Skript aus seinem Datenblock ``rowboat-konzept``
gezeichnet, die Seite selbst ist fast leer; darum folgen auf den sichtbaren Text die Textfelder von ``video``
(je Feld eine Zeile, siehe ``_video_zeilen``). Gelesen werden hoechstens
``MAX_ZEICHEN`` Zeichen. Der Abtaster ist absichtlich selbst geschrieben (nur ``str.find``, jeder
Schritt rueckt vor): ``html.parser`` ist auf unvollstaendigem Markup (``<a <a <a``, ``<!--<!--``)
super-linear und liesse den Indexer an einer kaputten Datei haengen. Ein ``<``, dessen Tag nicht schliesst,
gilt als Text (hoechstens ``FEHLVERSUCHE_MAX`` Mal, danach endet der Lauf); ein offener Kommentar oder ein
offenes Skript beendet den Lauf. Gross/Klein wird nur fuer ASCII ignoriert und immer auf dem Original
gesucht: ``"İ".lower()`` ist laenger als ``"İ"`` und verschoebe sonst die Positionen.
"""
from __future__ import annotations

import html as _html
import json
import re

MAX_ZEICHEN = 1_048_576
TITEL_MAX = 300             # ein nie geschlossenes <title> nimmt nicht den Rest der Datei mit
TAG_MAX = 2048              # Zeichen eines Tags ausserhalb von Attributwerten in Anfuehrungszeichen
ATTRIBUTE_MAX = 512         # Attributwerte je Tag, die der Abtaster hoechstens ansieht
BLOCK_MAX = 20_000          # laengster Datenblock (Zeichen), der noch zaehlt (wie KONZEPT_BLOCK_MAX in Rowboat)
BLOCK_ID = "rowboat-konzept"
FEHLVERSUCHE_MAX = 100      # so oft gilt ein nicht schliessendes "<" als Text, danach endet der Lauf

_LEERRAUM = re.compile(r"\s+")
_NAME = re.compile(r"/?[a-z][a-z0-9:-]*", re.I | re.A)
_GLEICH_ODER_ENDE = re.compile(r"[=>]")
_WERT_ENDE = re.compile(r"[\s>]")
_ATTRIBUT = re.compile(r"""([^\s=/>"']+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]*)))?""")
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


def _ist_datenblock(tag: str) -> bool:
    """Ist das ``<script ...>`` (Tag-Inneres) der Datenblock: ``id`` und ``type=application/json``?"""
    attribute: dict[str, str] = {}
    name = _NAME.match(tag)
    for m in _ATTRIBUT.finditer(tag, name.end() if name else 0):
        attribute.setdefault(m.group(1).lower(), next((g for g in m.groups()[1:] if g is not None), ""))
    if attribute.get("id") != BLOCK_ID:
        return False
    return attribute.get("type", "").split(";")[0].strip().lower() == "application/json"


def _video_zeilen(roh: str) -> list[str]:
    """Die Textfelder von ``video`` aus dem Block in Dokumentreihenfolge, je Feld eine Zeile.

    Ein zu langer, kaputter oder ``video``-loser Block liefert nichts und wirft nichts. Nur Zeichenketten
    zaehlen; die Werte sind Klartext und werden NICHT als Markup gelesen (``<b>x</b>`` bleibt woertlich,
    Entities bleiben stehen), nur Leerraum wird zusammengezogen.
    """
    if len(roh) > BLOCK_MAX:
        return []
    try:
        daten = json.loads(roh)
    except (ValueError, RecursionError):
        return []
    video = daten.get("video") if isinstance(daten, dict) else None
    if not isinstance(video, dict):
        return []
    zeilen: list[str] = []

    def feld(wert: object) -> None:
        if isinstance(wert, str):
            glatt = _LEERRAUM.sub(" ", wert).strip()
            if glatt:
                zeilen.append(glatt)

    for name in ("thema", "zielgruppe", "hook", "kernaussage"):
        feld(video.get(name))
    stufen = video.get("stufen")
    for stufe in stufen if isinstance(stufen, list) else ():
        if not isinstance(stufe, dict):
            continue
        feld(stufe.get("name"))
        szenen = stufe.get("szenen")
        for szene in szenen if isinstance(szenen, list) else ():
            if isinstance(szene, dict):
                feld(szene.get("kurz"))
                feld(szene.get("notiz"))
    feld(video.get("abschluss"))
    return zeilen


def lesen(html: str) -> tuple[str | None, str]:
    """``(titel, sichtbarer_text)`` in einem linearen Durchlauf."""
    s = html[:MAX_ZEICHEN]
    n = len(s)
    text: list[str] = []
    titel_stuecke: list[str] = []
    titel_zustand = 0       # 0 = noch keiner, 1 = innerhalb, 2 = fertig
    fehlversuche = 0
    block_gesehen = False   # nur der ERSTE Datenblock zaehlt, auch wenn er unbrauchbar ist
    video_zeilen: list[str] = []
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
            ende_schluss = s.find(">", schluss.start())
            if ende_schluss < 0:
                break
            if name == "script" and not block_gesehen and _ist_datenblock(s[lt + 1:ende]):
                block_gesehen = True
                video_zeilen = _video_zeilen(s[i:schluss.start()])
            i = ende_schluss + 1
        elif name == "title" and titel_zustand == 0:
            titel_zustand = 1
            continue
        elif name == "/title" and titel_zustand == 1:
            titel_zustand = 2
        text.append(" ")
        if titel_zustand == 1:
            titel_stuecke.append(" ")
    titel_text = _glatt(titel_stuecke)[:TITEL_MAX].rstrip()
    zeilen = [_glatt(text), *video_zeilen]
    return (titel_text or None), "\n".join(zeilen).strip("\n")


def sichtbarer_text(html: str) -> str:
    """Der lesbare Text (inkl. Titel), Leerraum zusammengezogen."""
    return lesen(html)[1]


def titel(html: str) -> str | None:
    """Inhalt des ersten ``<title>``; ``None``, wenn es keinen (nicht leeren) gibt."""
    return lesen(html)[0]
