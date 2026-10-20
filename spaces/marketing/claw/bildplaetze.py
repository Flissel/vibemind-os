"""Bildplaetze eines Newsletter-Blockdokuments (Spec 2026-09-29-newsletter-bilder-
und-gestaltung-design.md §4). Ein Platz ist ein Image-Block mit width > 0 und
height > 0; das Seitenverhaeltnis folgt aus beiden. Die Pixelmasse rechnet
dieses Modul aus dem Layout so, wie bloecke_mjml die Mail setzt: 600 px Breite,
Standardabstand links/rechts 24 (bloecke_mjml._polster), Spaltenluecke nach
_spalten_polster, Karte mit KARTEN_RAND. Erzeugt wird in doppelter Aufloesung,
Kanten auf 16 aufgerundet (FLUX verlangt Vielfache von 16)."""
from __future__ import annotations

import copy
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
    if isinstance(props.get("gestaltung"), dict):   # Gestaltungs-Flaeche, kein Foto-Platz
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


ERZEUG_SEITE_MAX = 1536   # lange Kante beim Erzeugen fuer Collage-Bilder (Ebenen bis 1200 x 6000 Einheiten)


def _seite_zahl(wert) -> float:
    return float(wert) if isinstance(wert, (int, float)) and not isinstance(wert, bool) else 0.0


def _seite_kasten(e: dict) -> tuple[float, float, float, float]:
    """(links, oben, rechts, unten) einer Ebene; x/y sind die Mitte (wie seite_html)."""
    x, y, b, h = (_seite_zahl(e.get(k)) for k in ("x", "y", "breite", "hoehe"))
    return x - b / 2, y - h / 2, x + b / 2, y + h / 2


def _seite_flaeche(ebenen: list, nr: int, grund: str) -> str:
    """Farbe unter der Bildmitte: die oberste Form (oder Text mit Hintergrund) darunter, sonst die Seitenfarbe."""
    l, o, r, u = _seite_kasten(ebenen[nr])
    mx, my = (l + r) / 2, (o + u) / 2
    for e in reversed(ebenen[:nr]):
        if not isinstance(e, dict):
            continue
        farbe = e.get("farbe") if e.get("art") == "form" else e.get("hintergrund") if e.get("art") == "text" else None
        if not isinstance(farbe, str) or not farbe:
            continue
        el, eo, er, eu = _seite_kasten(e)
        if el <= mx <= er and eo <= my <= eu:
            return farbe
    return grund


def _seite_kontext(ebenen: list, nr: int) -> str:
    """Texte (und Knopfbeschriftungen) der Seite, die naechsten zuerst - wie die Nachbartexte bei Bloecken."""
    l, o, r, u = _seite_kasten(ebenen[nr])
    mx, my = (l + r) / 2, (o + u) / 2
    texte = []
    for e in ebenen:
        if isinstance(e, dict) and e.get("art") in ("text", "knopf") and isinstance(e.get("text"), str):
            el, eo, er, eu = _seite_kasten(e)
            abstand = ((el + er) / 2 - mx) ** 2 + ((eo + eu) / 2 - my) ** 2
            texte.append((abstand, _MD.sub("", e["text"]).strip()))
    texte.sort(key=lambda t: t[0])
    return " | ".join(t for _, t in texte[:4] if t)[:KONTEXT_MAX]


def finde_seite(seite: dict) -> list[Platz]:
    """Bildplaetze einer Collage (format 'seite'): jede Bild-Ebene mit Breite und Hoehe > 0 ist ein Platz, ihre ID
    ist die Platz-ID (wie die Block-ID bei Bloecken). Anzeigemass = Ebenenmass in Seiteneinheiten (Seite 600 breit
    wie die Mail); erzeugt wird in doppelter Aufloesung, Kanten auf 16, lange Kante hoechstens ERZEUG_SEITE_MAX."""
    if not isinstance(seite, dict) or not isinstance(seite.get("ebenen"), list):
        return []
    ebenen = seite["ebenen"]
    hg = seite.get("hintergrund") if isinstance(seite.get("hintergrund"), dict) else {}
    grund = str(hg.get("farbe") or "#ffffff")
    plaetze: list[Platz] = []
    for nr, e in enumerate(ebenen):
        if not isinstance(e, dict) or e.get("art") != "bild" or not isinstance(e.get("id"), str):
            continue
        w, h = _seite_zahl(e.get("breite")), _seite_zahl(e.get("hoehe"))
        if w <= 0 or h <= 0:
            continue
        anzeige_b, anzeige_h = max(1, round(w)), max(1, round(h))
        erzeug_b = _auf16(anzeige_b * 2)
        erzeug_h = _auf16(erzeug_b * h / w)
        if max(erzeug_b, erzeug_h) > ERZEUG_SEITE_MAX:
            f = ERZEUG_SEITE_MAX / max(erzeug_b, erzeug_h)
            erzeug_b, erzeug_h = (max(16, int(x * f) // 16 * 16) for x in (erzeug_b, erzeug_h))
        a, c = _gekuerzt(anzeige_b, anzeige_h)
        url = str(e.get("quelle") or "")
        plaetze.append(Platz(e["id"], anzeige_b, anzeige_h, erzeug_b, erzeug_h, f"{a}:{c}", ist_leer(url), url, "",
                             _seite_kontext(ebenen, nr), _seite_flaeche(ebenen, nr, grund)))
    return plaetze


def platzhalter_einsetzen(dok: dict, name: str) -> tuple[dict, int]:
    """Jeden Bildplatz mit "medien:<name>" (exakt) durch seinen Platzhalter ersetzen (Plan 2026-10-19 E4).
    Image-Block -> medien:platzhalter-AxB.png nach width/height (fehlen sie: 4x3); Container mit diesem
    Hintergrund -> props.url entfernt. Gibt eine neue Kopie und die Zahl der ersetzten Plaetze zurueck;
    das Original bleibt unveraendert. Andere Stellen (Text-Links, Knoepfe) bleiben stehen - der Aufrufer
    prueft danach, ob der Name noch vorkommt."""
    neu = copy.deepcopy(dok) if isinstance(dok, dict) else {}
    ziel = "medien:" + name
    n = 0
    for b in neu.values():
        if not isinstance(b, dict) or b.get("type") not in ("Image", "Container"):
            continue
        props = (b.get("data") or {}).get("props")
        if not isinstance(props, dict) or props.get("url") != ziel:
            continue
        if b["type"] == "Container":
            del props["url"]
        else:
            w, h = _zahl(props.get("width"), 0), _zahl(props.get("height"), 0)
            props["url"] = "medien:" + (platzhalter_name(w, h) if w > 0 and h > 0 else "platzhalter-4x3.png")
        n += 1
    return neu, n