"""Bildplaetze eines Newsletter-Blockdokuments (Spec 2026-09-29-newsletter-bilder-
und-gestaltung-design.md §4). Ein Platz ist ein Image-Block mit width > 0 und
height > 0; das Seitenverhaeltnis folgt aus beiden. Die Pixelmasse rechnet
dieses Modul aus dem Layout so, wie bloecke_mjml die Mail setzt: 600 px Breite,
Standardabstand links/rechts 24 (bloecke_mjml._polster), Spaltenluecke nach
_spalten_polster, Karte mit KARTEN_RAND. Erzeugt wird in doppelter Aufloesung,
Kanten auf 16 aufgerundet (FLUX verlangt Vielfache von 16)."""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass

from spaces.marketing.claw.bloecke_mjml import KARTEN_RAND, _spalten_polster, _zahl

MAIL_BREITE = 600
PLATZHALTER = re.compile(r"^medien:platzhalter-(\d{1,3})x(\d{1,3})\.png$")
_MD = re.compile(r"[*_\[\]()#>`]")
KONTEXT_MAX = 600


@dataclass(frozen=True)
class Platz:
    id: str
    anzeige_breite: int
    anzeige_hoehe: int
    erzeug_breite: int
    erzeug_hoehe: int
    verhaeltnis: str
    leer: bool
    url: str
    alt: str
    kontext: str
    flaeche: str = ""   # Farbe der Flaeche hinter dem Bild (Bild > Container > canvasColor)

    def als_dict(self) -> dict:
        return asdict(self)


def ist_leer(url) -> bool:
    return not isinstance(url, str) or not url.strip() or bool(PLATZHALTER.match(url))


def _gekuerzt(breite: int, hoehe: int) -> tuple[int, int]:
    g = math.gcd(int(breite), int(hoehe)) or 1
    a, b = int(breite) // g, int(hoehe) // g
    # krumme Verhaeltnisse (z. B. 552:311) auf das naechste uebliche runden
    if a > 21 or b > 21:
        ziel = breite / hoehe
        a, b = min(((x, y) for x, y in ((1, 1), (2, 1), (3, 1), (4, 3), (3, 2), (16, 9), (3, 4), (2, 3), (9, 16))),
                   key=lambda v: abs(v[0] / v[1] - ziel))
    return a, b


def platzhalter_name(breite: int, hoehe: int) -> str:
    a, b = _gekuerzt(breite, hoehe)
    return f"platzhalter-{a}x{b}.png"


def _lr(style: dict, standard: int) -> tuple[int, int]:
    p = style.get("padding") if isinstance(style, dict) else None
    if not isinstance(p, dict):
        return standard, standard
    return _zahl(p.get("left"), 24), _zahl(p.get("right"), 24)


def _daten(b) -> tuple[dict, dict]:
    data = (b or {}).get("data") or {}
    return (data.get("style") or {}), (data.get("props") or {})


def _textinhalt(b) -> str:
    if not isinstance(b, dict) or b.get("type") not in ("Heading", "Text"):
        return ""
    return _MD.sub("", str(_daten(b)[1].get("text") or "")).strip()


def _auf16(x: float) -> int:
    return max(16, int(math.ceil(x / 16.0)) * 16)


def _platz(dok: dict, bid: str, verfuegbar: float, geschwister: list, flaeche: str = "") -> Platz | None:
    b = dok.get(bid)
    if not isinstance(b, dict) or b.get("type") not in ("Image", "Container"):
        return None
    style, props = _daten(b)
    if props.get("grafik") is True:      # erzeugte Grafik (Pillow), kein Foto-Platz
        return None
    container = b.get("type") == "Container"
    if container and not props.get("url"):
        return None
    w, h = _zahl(props.get("width"), 0), _zahl(props.get("height"), 0)
    if w <= 0 or h <= 0:
        return None
    links, rechts = (0, 0) if container else _lr(style, 24)
    platz_breite = max(16, int(verfuegbar - links - rechts))
    anzeige_b = min(w, platz_breite)
    anzeige_h = max(1, round(h * anzeige_b / w))
    erzeug_b = _auf16(anzeige_b * 2)
    erzeug_h = _auf16(erzeug_b * h / w)
    a, c = _gekuerzt(w, h)
    i = geschwister.index(bid) if bid in geschwister else 0
    if container:   # Kontext aus den Kindtexten des Containers
        nachbarn = [_textinhalt(dok.get(x)) for x in geschwister[:4]]
    else:
        nachbarn = [_textinhalt(dok.get(x)) for x in geschwister[max(0, i - 2):i] + geschwister[i + 1:i + 3]]
    kontext = " | ".join(t for t in nachbarn if t)[:KONTEXT_MAX]
    url = str(props.get("url") or "")
    return Platz(bid, int(anzeige_b), int(anzeige_h), erzeug_b, erzeug_h, f"{a}:{c}",
                 ist_leer(url), url, str(props.get("alt") or ""), kontext,
                 str(style.get("backgroundColor") or flaeche))


def finde(dok: dict) -> list[Platz]:
    if not isinstance(dok, dict):
        return []
    wurzel = (dok.get("root") or {}).get("data") or {}
    oben = [x for x in (wurzel.get("childrenIds") or []) if isinstance(x, str)]
    innen = str(wurzel.get("canvasColor") or "#ffffff")   # Standard wie bloecke_mjml
    plaetze: list[Platz] = []
    for bid in oben:
        b = dok.get(bid)
        if not isinstance(b, dict):
            continue
        style, props = _daten(b)
        typ = b.get("type")
        if typ == "ColumnsContainer":
            anzahl = 3 if _zahl(props.get("columnsCount"), 2) == 3 else 2
            luecke = max(0, _zahl(props.get("columnsGap"), 0))
            sl, sr = _lr(style, 0) if isinstance(style.get("padding"), dict) else (0, 0)
            spalte = (MAIL_BREITE - sl - sr) / anzahl
            for i, c in enumerate(list(props.get("columns") or [])[:anzahl]):
                kinder = [x for x in ((c or {}).get("childrenIds") or []) if isinstance(x, str)]
                vor, nach = _spalten_polster(i, anzahl, luecke)
                for k in kinder:
                    if (p := _platz(dok, k, spalte - vor - nach, kinder, style.get("backgroundColor") or innen)):
                        plaetze.append(p)
        elif typ == "Container":
            kinder = [x for x in (props.get("childrenIds") or []) if isinstance(x, str)]
            if (p := _platz(dok, bid, MAIL_BREITE, kinder, style.get("backgroundColor") or innen)):
                plaetze.append(p)
            cl, cr = _lr(style, 0) if isinstance(style.get("padding"), dict) else (0, 0)
            for k in kinder:
                if (p := _platz(dok, k, MAIL_BREITE - 2 * KARTEN_RAND - cl - cr, kinder,
                                style.get("backgroundColor") or innen)):
                    plaetze.append(p)
        elif (p := _platz(dok, bid, MAIL_BREITE, oben, innen)):
            plaetze.append(p)
    return plaetze
