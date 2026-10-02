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


import hashlib
import json
import tempfile
import time

from PIL import Image, ImageDraw, ImageFont

from spaces.marketing.claw.schoenheit import kontrast

RENDERER = 1
MAX_BYTES = 1024 * 1024
MAX_PIXEL = 40_000_000
AUFRAEUM_ALTER_S = 7 * 86400


def schluessel(g: dict) -> str:
    roh = json.dumps({"r": RENDERER, "g": pruefen(g)}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:12]


def name_fuer(g: dict) -> str:
    return f"gs-{schluessel(g)}.jpg"


def _pfad(quelle: str, quellen: list[str]) -> str:
    name = quelle[len("medien:"):]
    for o in quellen:
        if not o:
            continue
        p = os.path.realpath(os.path.join(o, name))
        if os.path.commonpath([p, os.path.realpath(o)]) == os.path.realpath(o) and os.path.isfile(p):
            return p
    raise GestaltungFehler(f"Bild {name} fehlt in den Medien")


def _laden(pfad: str, name: str) -> Image.Image:
    try:
        with Image.open(pfad) as roh:
            if roh.width * roh.height > MAX_PIXEL:
                raise GestaltungFehler(f"Bild {name} ist zu groß")
            return roh.convert("RGBA")
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise GestaltungFehler(f"Bild {name} ist zu groß")
    except OSError:
        raise GestaltungFehler(f"Bild {name} ist nicht lesbar")


def _text_ebene(e: dict) -> Image.Image:
    font = ImageFont.truetype(schrift_datei(e["schrift"], e["gewicht"], e["kursiv"]), int(round(e["groesse"] * FAKTOR)))
    zeilen = e["text"].split("\n")
    zh = e["groesse"] * FAKTOR * e["zeilenabstand"]
    breiten = [font.getlength(z) for z in zeilen]
    w = max(1, int(math.ceil(max(breiten))))
    h = max(1, int(math.ceil(zh * len(zeilen))))
    ebene = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ebene)
    anker = {"links": ("la", 0), "mitte": ("ma", w / 2), "rechts": ("ra", w)}[e["ausrichtung"]]
    for i, z in enumerate(zeilen):
        d.text((anker[1], i * zh + (zh - e["groesse"] * FAKTOR) / 2), z, font=font, fill=e["farbe"], anchor=anker[0])
    return ebene


def _einsetzen(leinwand: Image.Image, ebene: Image.Image, e: dict) -> tuple[int, int, int, int]:
    if e["drehung"]:
        ebene = ebene.rotate(-e["drehung"], resample=Image.BICUBIC, expand=True)
    cx, cy = e["x"] * FAKTOR, e["y"] * FAKTOR
    links, oben = int(round(cx - ebene.width / 2)), int(round(cy - ebene.height / 2))
    leinwand.paste(ebene, (links, oben), ebene)
    return links, oben, links + ebene.width, oben + ebene.height


def _ausserhalb(box, w, h) -> float:
    l, o, r, u = box
    flaeche = max(1, (r - l) * (u - o))
    innen = max(0, min(r, w) - max(l, 0)) * max(0, min(u, h) - max(o, 0))
    return 1 - innen / flaeche


def bild_rechnen(g: dict, quellen: list[str]) -> tuple[Image.Image, list[str]]:
    g = pruefen(g)
    w, h = BREITE * FAKTOR, hoehe(g["format"]) * FAKTOR
    leinwand = Image.new("RGBA", (w, h), g["hintergrund"])
    hinweise: list[str] = []
    for e in g["ebenen"]:
        if e["art"] == "bild":
            name = e["quelle"][len("medien:"):]
            quelle = _laden(_pfad(e["quelle"], quellen), name)
            bw = max(1, int(round(e["breite"] * FAKTOR)))
            bh = max(1, int(round(quelle.height * bw / quelle.width)))
            if bw * bh > MAX_PIXEL:
                raise GestaltungFehler(f"Bild {name} ist zu groß")
            ebene = quelle.resize((bw, bh), Image.LANCZOS)
            box = _einsetzen(leinwand, ebene, e)
            beschreibung = f"Bild {name}"
        else:
            ebene = _text_ebene(e)
            cl, co = max(0, int(e["x"] * FAKTOR - ebene.width / 2)), max(0, int(e["y"] * FAKTOR - ebene.height / 2))
            cr, cu = min(w, int(e["x"] * FAKTOR + ebene.width / 2) + 1), min(h, int(e["y"] * FAKTOR + ebene.height / 2) + 1)
            unter = leinwand.crop((cl, co, cr, cu)) if cl < cr and co < cu else None
            box = _einsetzen(leinwand, ebene, e)
            kurz = e["text"].split("\n")[0][:30]
            beschreibung = f"Text „{kurz}“"
            if e["groesse"] < 22:
                hinweise.append(f"{beschreibung} ist am Handy unter 12 px")
            if unter is not None and unter.width and unter.height:
                mittel = unter.convert("RGB").resize((1, 1), Image.BOX).getpixel((0, 0))
                grund = "#%02x%02x%02x" % mittel
                if kontrast(e["farbe"].lower(), grund) < 3.0:
                    hinweise.append(f"{beschreibung}: Kontrast zum Untergrund unter 3:1")
        if _ausserhalb(box, w, h) > 0.15:
            hinweise.append(f"{beschreibung} liegt zu mehr als 15 % außerhalb der Fläche")
    return leinwand.convert("RGB"), hinweise


def _speichern(bild: Image.Image, pfad: str, ordner: str) -> None:
    for q in range(88, 63, -6):
        fd, tmp = tempfile.mkstemp(dir=ordner, suffix=".tmp")
        os.close(fd)
        try:
            bild.save(tmp, "JPEG", quality=q, optimize=True)
            if os.path.getsize(tmp) <= MAX_BYTES:
                os.chmod(tmp, 0o644)
                os.replace(tmp, pfad)
                return
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    raise GestaltungFehler("Bild wird zu groß (über 1 MB)")


def rechnen(g: dict, quellen: list[str], ziel: str) -> dict:
    g = pruefen(g)
    name = name_fuer(g)
    pfad = os.path.join(ziel, name)
    hinweise: list[str]
    bild, hinweise = bild_rechnen(g, quellen)
    if not os.path.exists(pfad):
        _speichern(bild, pfad, ziel)
    return {"url": f"medien:{name}", "name": name, "width": BREITE, "height": hoehe(g["format"]), "hinweise": hinweise}


def umformatieren(g: dict, fmt: str) -> dict:
    g = pruefen(g)
    if fmt not in FORMATE:
        raise GestaltungFehler("Format muss quer, quadrat, hoch oder banner sein")
    h1, h2 = hoehe(g["format"]), hoehe(fmt)
    s = min(BREITE, h2) / min(BREITE, h1)
    neu = []
    for e in g["ebenen"]:
        e = dict(e)
        e["x"] = round(BREITE / 2 + (e["x"] - BREITE / 2) * s, 1)
        e["y"] = round(h2 / 2 + (e["y"] - h1 / 2) * s, 1)
        if e["art"] == "bild":
            e["breite"] = round(min(3000, max(8, e["breite"] * s)), 1)
        else:
            e["groesse"] = round(min(160, max(10, e["groesse"] * s)), 1)
        neu.append(e)
    return pruefen({**g, "format": fmt, "ebenen": neu})


def aufraeumen(ordner: str, verwiesen: set[str], jetzt: float | None = None) -> list[str]:
    jetzt = time.time() if jetzt is None else jetzt
    weg = []
    for n in sorted(os.listdir(ordner)):
        if not re.fullmatch(r"gs-[0-9a-f]{12}\.jpg", n) or n in verwiesen:
            continue
        p = os.path.join(ordner, n)
        if jetzt - os.path.getmtime(p) > AUFRAEUM_ALTER_S:
            os.remove(p)
            weg.append(n)
    return weg
