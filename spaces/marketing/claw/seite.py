"""Seitenmodell der freien Newsletter-Flaeche ("Collage"): feste Breite 600, Ebenen frei platziert.
Reihenfolge im Array = Z-Reihenfolge, x/y = MITTELPUNKT der Ebene. pruefen() liefert eine normalisierte
Kopie (Vorgaben ergaenzt) oder wirft SeiteFehler mit deutschem Grund."""
from __future__ import annotations

import json
import re

from spaces.marketing.claw import schriften
from spaces.marketing.claw.gestaltung import _FARBE, _ID, _QUELLE

BREITE = 600
HOEHE_MIN = 400
HOEHE_MAX = 6000
MAX_EBENEN = 80
MAX_BYTES = 256 * 1024
MAX_TEXT = 3000

_ARTEN = ("bild", "text", "form", "knopf")
_ROLLEN = ("ANZEIGE", "TEXT", "HAND")
_ANREDE = "{vorname}"
_TEXTLINK = re.compile(r"\[[^\]]*\]\(([^)]*)\)")
_HTTPS = re.compile(r"^https://[^\s\"'<>]+$")
_PFLICHT = object()


class SeiteFehler(ValueError):
    pass


def _zahl(wert, name, lo, hi, ganz=False):
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        raise SeiteFehler(f"{name}: Zahl erwartet")
    if ganz and isinstance(wert, float) and not wert.is_integer():
        raise SeiteFehler(f"{name}: ganze Zahl erwartet")
    if not lo <= wert <= hi:
        raise SeiteFehler(f"{name}: muss zwischen {lo} und {hi} liegen")
    return int(wert) if ganz else wert


def _bool(wert, name):
    if not isinstance(wert, bool):
        raise SeiteFehler(f"{name}: Ja/Nein erwartet")
    return wert


def _farbe(wert, name):
    if not isinstance(wert, str) or not _FARBE.fullmatch(wert):
        raise SeiteFehler(f"{name}: Farbe im Format #rrggbb erwartet")
    return wert


def _quelle(wert, name):
    if not isinstance(wert, str) or not _QUELLE.fullmatch(wert) or ".." in wert:
        raise SeiteFehler(f"{name}: nur Bilder aus der Mediathek (medien:…) erlaubt")
    return wert


def _wahl(wert, name, erlaubt):
    if not isinstance(wert, str) or wert not in erlaubt:
        raise SeiteFehler(f"{name}: erlaubt sind {', '.join(erlaubt)}")
    return wert


def _text(wert, name, lo, hi):
    if not isinstance(wert, str) or not lo <= len(wert) <= hi:
        raise SeiteFehler(f"{name}: Text mit {lo} bis {hi} Zeichen erwartet")
    return wert


def _link(wert, name):
    if not isinstance(wert, str) or not _HTTPS.fullmatch(wert):
        raise SeiteFehler(f"{name}: nur https://-Links erlaubt")
    return wert


def _id(wert, name):
    if not isinstance(wert, str) or not _ID.fullmatch(wert):
        raise SeiteFehler(f"{name}: nur Buchstaben, Ziffern, _ und - (1 bis 32 Zeichen)")
    return wert


def _register(wert, name):
    if not isinstance(wert, str) or wert not in schriften.REGISTER:
        raise SeiteFehler(f"{name}: Schrift nicht im Register")
    return wert


def _z(lo, hi, ganz=False):
    return lambda w, n: _zahl(w, n, lo, hi, ganz)


def _w(*erlaubt):
    return lambda w, n: _wahl(w, n, erlaubt)


def _felder(e, regeln, kontext):
    """regeln: {name: (Vorgabe | _PFLICHT, Pruefer)}; unbekannte Schluessel sind ein Fehler."""
    unbekannt = set(e) - set(regeln)
    if unbekannt:
        raise SeiteFehler(f"{kontext}: unbekannte Felder {', '.join(sorted(map(str, unbekannt)))}")
    aus = {}
    for name, (vorgabe, pruefer) in regeln.items():
        if name in e:
            aus[name] = pruefer(e[name], f"{kontext} {name}")
        elif vorgabe is _PFLICHT:
            raise SeiteFehler(f"{kontext}: Feld {name} fehlt")
        else:
            aus[name] = vorgabe
    return aus


def _farbe_oder_nichts(w, n):
    return None if w is None else _farbe(w, n)


_EBENE = {
    "id": (_PFLICHT, _id),
    "art": (_PFLICHT, _w(*_ARTEN)),
    "x": (_PFLICHT, _z(-600, 1200)),
    "y": (_PFLICHT, _z(-1000, 7000)),
    "breite": (_PFLICHT, _z(1, 1200)),
    "hoehe": (_PFLICHT, _z(1, 6000)),
    "drehung": (0, _z(-180, 180)),
    "deckkraft": (1, _z(0, 1)),
}

_ART = {
    "bild": {
        "quelle": (_PFLICHT, _quelle),
        "passung": ("fuellen", _w("fuellen", "einpassen")),
        "rundung": (0, _z(0, 600)),
        "kreis": (False, _bool),
        "rahmen_breite": (0, _z(0, 20)),
        "rahmen_farbe": ("#ffffff", _farbe),
        "schatten": (False, _bool),
    },
    "form": {
        "form": (_PFLICHT, _w("rechteck", "kreis")),
        "farbe": (_PFLICHT, _farbe),
        "rundung": (0, _z(0, 600)),
    },
    "text": {
        "text": (_PFLICHT, lambda w, n: _text(w, n, 1, MAX_TEXT)),
        "rolle": (_PFLICHT, _w(*_ROLLEN)),
        "gewicht": (400, _z(300, 900, True)),
        "groesse": (_PFLICHT, _z(10, 160)),
        "zeilenabstand": (1.3, _z(0.8, 2.5)),
        "buchstabenabstand": (0, _z(-0.1, 0.5)),
        "farbe": (_PFLICHT, _farbe),
        "ausrichtung": ("links", _w("links", "mitte", "rechts")),
        "grossbuchstaben": (False, _bool),
        "kursiv": (False, _bool),
        "hintergrund": (None, _farbe_oder_nichts),
    },
    "knopf": {
        "text": (_PFLICHT, lambda w, n: _text(w, n, 1, 60)),
        "link": (_PFLICHT, _link),
        "farbe": (_PFLICHT, _farbe),
        "textfarbe": (_PFLICHT, _farbe),
        "rundung": (6, _z(0, 60)),
        "rolle": ("TEXT", _w("ANZEIGE", "TEXT")),
        "groesse": (16, _z(10, 40)),
        "gewicht": (600, _z(300, 900, True)),
    },
}


def _ebene(e, nr):
    kontext = f"Ebene {nr}"
    if not isinstance(e, dict):
        raise SeiteFehler(f"{kontext}: Objekt erwartet")
    art = e.get("art")
    if not isinstance(art, str) or art not in _ART:
        raise SeiteFehler(f"{kontext} art: erlaubt sind {', '.join(_ARTEN)}")
    aus = _felder(e, {**_EBENE, **_ART[art]}, kontext)
    if art == "text":
        text = aus["text"]
        fliess = aus["rolle"] == "TEXT" and aus["drehung"] == 0
        if _ANREDE in text and not fliess:
            raise SeiteFehler(f"{kontext}: Die Anrede {_ANREDE} geht nur in Fließtext (Rolle TEXT, nicht gedreht)")
        links = _TEXTLINK.findall(text)
        if links and not fliess:
            raise SeiteFehler(f"{kontext}: Links im Text gehen nur in Fließtext (Rolle TEXT, nicht gedreht)")
        for ziel in links:
            _link(ziel, f"{kontext} Link")
    return aus


def pruefen(s: object) -> dict:
    if not isinstance(s, dict):
        raise SeiteFehler("Seite: Objekt erwartet")
    unbekannt = set(s) - {"version", "breite", "hoehe", "hintergrund", "schriften", "ebenen"}
    if unbekannt:
        raise SeiteFehler(f"Seite: unbekannte Felder {', '.join(sorted(map(str, unbekannt)))}")
    try:
        groesse = len(json.dumps(s, ensure_ascii=False).encode())
    except (TypeError, ValueError):
        raise SeiteFehler("Seite: kein gültiges JSON") from None
    if groesse > MAX_BYTES:
        raise SeiteFehler(f"Seite: größer als {MAX_BYTES // 1024} KB")
    v = s.get("version")
    if isinstance(v, bool) or v != 1:
        raise SeiteFehler("Seite version: muss 1 sein")
    b = s.get("breite")
    if isinstance(b, bool) or b != BREITE:
        raise SeiteFehler(f"Seite breite: muss {BREITE} sein")
    hoehe = _zahl(s.get("hoehe"), "Seite hoehe", HOEHE_MIN, HOEHE_MAX, True)
    hg, sch, eb = s.get("hintergrund"), s.get("schriften"), s.get("ebenen")
    if not isinstance(hg, dict):
        raise SeiteFehler("Seite hintergrund: Objekt erwartet")
    if not isinstance(sch, dict):
        raise SeiteFehler("Seite schriften: Objekt erwartet")
    if not isinstance(eb, list):
        raise SeiteFehler("Seite ebenen: Liste erwartet")
    hintergrund = _felder(hg, {"farbe": (_PFLICHT, _farbe),
                               "bild": (None, lambda w, n: None if w is None else _quelle(w, n))}, "Hintergrund")
    schr = _felder(sch, {"anzeige": (_PFLICHT, _register), "text": (_PFLICHT, _register)}, "Schriften")
    if len(eb) > MAX_EBENEN:
        raise SeiteFehler(f"Seite: höchstens {MAX_EBENEN} Ebenen")
    ebenen, ids = [], set()
    for nr, e in enumerate(eb, 1):
        aus = _ebene(e, nr)
        if aus["id"] in ids:
            raise SeiteFehler(f"Ebene {nr}: ID {aus['id']} kommt doppelt vor")
        ids.add(aus["id"])
        ebenen.append(aus)
    return {"version": 1, "breite": BREITE, "hoehe": hoehe, "hintergrund": hintergrund,
            "schriften": schr, "ebenen": ebenen}


def rolle_schrift(s: dict, rolle: str) -> str:
    """Schrift-ID je Rolle: HAND ist fest (Caveat), ANZEIGE/TEXT kommen aus der Seite."""
    if rolle == "HAND":
        return schriften.HAND_ROLLE
    if rolle == "ANZEIGE":
        return s["schriften"]["anzeige"]
    if rolle == "TEXT":
        return s["schriften"]["text"]
    raise SeiteFehler(f"Rolle {rolle}: erlaubt sind {', '.join(_ROLLEN)}")


def leer(marke: dict | None) -> dict:
    """Startseite (besteht pruefen): Ueberschrift, Fliesstext, Knopf in Markenfarbe."""
    marke = marke or {}
    anzeige, text = marke.get("schrift_anzeige"), marke.get("schrift_text")
    akzent, web = marke.get("akzent"), marke.get("webseite")
    return {
        "version": 1, "breite": BREITE, "hoehe": 1200,
        "hintergrund": {"farbe": "#ffffff", "bild": None},
        "schriften": {
            "anzeige": anzeige if anzeige in schriften.REGISTER else "dm-sans",
            "text": text if text in schriften.REGISTER else "dm-sans",
        },
        "ebenen": [
            {"id": "titel", "art": "text", "x": 300, "y": 120, "breite": 520, "hoehe": 120,
             "text": "Überschrift", "rolle": "ANZEIGE", "groesse": 48, "farbe": "#111111",
             "ausrichtung": "mitte"},
            {"id": "text", "art": "text", "x": 300, "y": 300, "breite": 480, "hoehe": 120,
             "text": "Hier steht Ihr Text.", "rolle": "TEXT", "groesse": 17, "farbe": "#333333"},
            {"id": "knopf", "art": "knopf", "x": 300, "y": 460, "breite": 220, "hoehe": 48,
             "text": "Mehr erfahren",
             "link": web if isinstance(web, str) and _HTTPS.fullmatch(web) else "https://example.com",
             "farbe": akzent if isinstance(akzent, str) and _FARBE.fullmatch(akzent) else "#5b3fd6",
             "textfarbe": "#ffffff"},
        ],
    }
