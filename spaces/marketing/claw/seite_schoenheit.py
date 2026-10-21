"""Schoenheitspruefung fuer Collage-Seiten (freie Flaeche, Stufe 5). Reine Rechnung wie claw/schoenheit: bekommt
Seite, gemessene Huellen und ein PNG, gibt Befunde {schwere, punkt, satz} zurueck, liest keine Datei und ruft keinen
Dienst.

HART (blockiert das Einreichen) ist nur Unlesbarkeit: Text- oder Knopfschrift unter 4,5:1 Kontrast, ab Groesse 24
unter 3:1. Gemessen wird die Textfarbe gegen den MEDIAN der Pixel unter der Schrift - aus einem Rendering der Seite
OHNE Text- und Knopfebenen (seite_ohne_text), innerhalb der Vereinigung der gemessenen Zeilenkaesten der Schrift
(zeilen, seite_bilder.messen_alles: dort stehen die Glyphen; ein kurzes, mittig gesetztes Wort in einem breiten Kasten
misst nur seine Unterlage), ohne Zeilenkaesten innerhalb der gemessenen Huelle der Ebene, auf die Seite beschnitten.
Der Median, nicht der Mittelwert: ein dunkles Foto mit ein paar Lichtreflexen bleibt dunkel. Bringt eine Textebene
ihre eigene Unterlage mit (hintergrund), zaehlt deren Farbe. Knoepfe tragen ihre Unterlage immer selbst (farbe).

WEICH (wird genannt, nicht verhindert) sind Geschmacksregeln: Fliesstext unter 15, mehr als 3 Schriftgroessen, kein
Knopf, mehr als 2 laute Elemente, Text in der Ruhezone am Seitenrand (gemessen an der Glyphen-Ausdehnung, ohne
Zeilenkaesten an der Huelle).

Werte in Saetzen werden ABGERUNDET (4,49 -> 4,4), damit nie 4,5:1 neben "mindestens 4,5:1" steht."""
from __future__ import annotations

import copy
import io
import math

from spaces.marketing.claw import bildplaetze
from spaces.marketing.claw import seite as seite_mod
from spaces.marketing.claw import seite_streifen
from spaces.marketing.claw.schoenheit import KONTRAST_GROSS, KONTRAST_TEXT, befund, kontrast

GROSS_AB = 24          # ab dieser Schriftgroesse gilt KONTRAST_GROSS
FLIESS_MIN = 15        # Fliesstext (Rolle TEXT) darunter: weich
GROESSEN_MAX = 3       # mehr verschiedene Schriftgroessen: weich
LAUT_MAX = 2           # mehr laute Elemente: weich
LAUT_DREHUNG = 2       # ab |drehung| in Grad ist eine Ebene laut
RUHEZONE = 24          # Text naeher an der linken/rechten Seitenkante: weich


def _zahl(wert: float) -> str:
    """Deutsche Schreibweise: 4.5 -> "4,5", 3.0 -> "3"."""
    return f"{wert:g}".replace(".", ",")


def _schwelle(groesse: float) -> float:
    return KONTRAST_GROSS if groesse >= GROSS_AB else KONTRAST_TEXT


def _hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(k))):02x}" for k in rgb)


def _rgb(farbe: str) -> tuple[int, int, int]:
    h = farbe.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _median_aus_histogramm(hist: list[int]) -> int:
    gesamt = sum(hist)
    halb, summe = (gesamt + 1) // 2, 0
    for wert, anzahl in enumerate(hist):
        summe += anzahl
        if summe >= halb:
            return wert
    return 0


def _median(bild, kasten: tuple[int, int, int, int]) -> tuple[int, int, int] | None:
    """Median je Kanal der nicht ganz durchsichtigen Pixel im Kasten; None, wenn keiner uebrig bleibt."""
    teil = bild.crop(kasten)
    maske = teil.getchannel("A").point(lambda a: 255 if a > 0 else 0)
    hist = teil.histogram(mask=maske)
    if not sum(hist[768:1024]):
        return None
    return tuple(_median_aus_histogramm(hist[k * 256:(k + 1) * 256]) for k in range(3))


def _median_ueber(bild, kaesten: list[tuple[int, int, int, int]]) -> tuple[int, int, int] | None:
    """Median je Kanal ueber die Vereinigung der Pixel-Kaesten (jedes Pixel zaehlt einmal)."""
    from PIL import Image, ImageChops, ImageDraw
    l = min(k[0] for k in kaesten)
    o = min(k[1] for k in kaesten)
    r = max(k[2] for k in kaesten)
    u = max(k[3] for k in kaesten)
    teil = bild.crop((l, o, r, u))
    maske = Image.new("L", teil.size, 0)
    zeichnen = ImageDraw.Draw(maske)
    for kl, ko, kr, ku in kaesten:
        zeichnen.rectangle((kl - l, ko - o, kr - l - 1, ku - o - 1), fill=255)
    maske = ImageChops.multiply(maske, teil.getchannel("A").point(lambda a: 255 if a > 0 else 0))
    hist = teil.histogram(mask=maske)
    if not sum(hist[768:1024]):
        return None
    return tuple(_median_aus_histogramm(hist[k * 256:(k + 1) * 256]) for k in range(3))


def _pixel_kasten(l: float, o: float, r: float, u: float, hoehe: int, faktor: int, bildgroesse: tuple[int, int]
                  ) -> tuple[int, int, int, int] | None:
    """Kasten in Seiteneinheiten -> Pixel, auf Seite und Bild beschnitten; None, wenn nichts davon sichtbar ist."""
    l, o, r, u = max(l, 0), max(o, 0), min(r, seite_mod.BREITE), min(u, hoehe)
    kasten = (max(0, int(l * faktor)), max(0, int(o * faktor)),
              min(bildgroesse[0], int(round(r * faktor))), min(bildgroesse[1], int(round(u * faktor))))
    if kasten[2] <= kasten[0] or kasten[3] <= kasten[1]:
        return None
    return kasten


def _glyphen(e: dict, zeilen: dict | None) -> list:
    """Zeilenkaesten der Schrift der Ebene (leer = unbekannt)."""
    return list((zeilen or {}).get(e["id"]) or [])


def glyphen_ausdehnung(e: dict, masse, zeilen: dict | None) -> tuple[float, float, float, float]:
    """Achsenparallele Ausdehnung der Glyphen (Vereinigung der Zeilenkaesten); ohne Zeilenkaesten die Huelle."""
    kaesten = _glyphen(e, zeilen)
    if not kaesten:
        return seite_streifen.huelle(e, masse)
    return (min(k[0] for k in kaesten), min(k[1] for k in kaesten), max(k[2] for k in kaesten),
            max(k[3] for k in kaesten))


def _sichtbarer_kasten(e: dict, masse, hoehe: int, faktor: int, bildgroesse: tuple[int, int]
                       ) -> tuple[int, int, int, int] | None:
    """Huelle der Ebene in Pixeln, auf Seite und Bild beschnitten; None, wenn nichts davon auf der Seite liegt."""
    return _pixel_kasten(*seite_streifen.huelle(e, masse), hoehe, faktor, bildgroesse)


def _gemischt(vorne: str, hinten: str, deckkraft: float) -> str:
    """Sichtbare Schriftfarbe einer halb durchsichtigen Ebene ueber ihrer Unterlage."""
    if deckkraft >= 1:
        return vorne
    v, h = _rgb(vorne), _rgb(hinten)
    return _hex(deckkraft * a + (1 - deckkraft) * b for a, b in zip(v, h))


def _abgerundet(wert: float) -> str:
    """Eine Nachkommastelle, ABGERUNDET: 4,49 -> "4,4" (gerundet stuende "4,5" neben "mindestens 4,5")."""
    return f"{math.floor(wert * 10 + 1e-9) / 10:.1f}".replace(".", ",")


def _kontrast_befund(eid: str, wert: float, schwelle: float, wo: str) -> dict:
    gemessen = _abgerundet(wert)
    return befund("hart", eid, f"{eid}: Kontrast {gemessen}:1 {wo} – mindestens {_zahl(schwelle)}:1")


def kontraste(seite: dict, masse: dict, png_ohne_text: bytes, faktor: int,
              ohne_pixel: frozenset[str] | set[str] = frozenset(), zeilen: dict | None = None) -> list[dict]:
    """Harte Kontrastbefunde. png_ohne_text: Rendering von seite_ohne_text(seite) (600 x hoehe Einheiten, mal
    faktor Pixel). masse: gemessene Huellen je Ebenen-id aus dem Rendering MIT Text (seite_bilder.render_und_messen),
    fehlende Eintraege fallen auf den deklarierten Rahmen zurueck. ohne_pixel: Ids von Textebenen, deren Unterlage
    (noch) unbekannt ist (z. B. ueber einem Platzhalter) - ihr Pixel-Kontrast wird nicht gemessen. zeilen
    (seite_bilder.zeilen_pruefen): Zeilenkaesten der Schrift je Ebene; gemessen wird ueber ihre Vereinigung, ohne
    sie ueber die Huelle."""
    from PIL import Image

    s = seite_mod.pruefen(seite)
    m = seite_streifen.masse_pruefen(masse) or None
    befunde: list[dict] = []
    bild = None
    for e in s["ebenen"]:
        if e["art"] == "knopf":
            schwelle = _schwelle(e["groesse"])
            wert = kontrast(e["textfarbe"], e["farbe"])
            if wert < schwelle:
                befunde.append(_kontrast_befund(e["id"], wert, schwelle, "auf der Knopffarbe"))
            continue
        if e["art"] != "text":
            continue
        schwelle = _schwelle(e["groesse"])
        if e["hintergrund"] is not None:
            wert = kontrast(e["farbe"], e["hintergrund"])
            if wert < schwelle:
                befunde.append(_kontrast_befund(e["id"], wert, schwelle, "auf der eigenen Hintergrundfarbe"))
            continue
        if e["id"] in ohne_pixel:
            continue
        if bild is None:
            with Image.open(io.BytesIO(png_ohne_text)) as roh:
                bild = roh.convert("RGBA")
        glyphen = _glyphen(e, zeilen)
        if glyphen:
            kaesten = [k for k in (_pixel_kasten(*g, s["hoehe"], faktor, bild.size) for g in glyphen) if k]
            if not kaesten:
                continue  # keine Zeile liegt auf der Seite, niemand sieht sie
            median = _median_ueber(bild, kaesten)
        else:
            kasten = _sichtbarer_kasten(e, m, s["hoehe"], faktor, bild.size)
            if kasten is None:
                continue  # liegt ganz ausserhalb der Seite, niemand sieht ihn
            median = _median(bild, kasten)
        unterlage = _hex(median) if median is not None else s["hintergrund"]["farbe"]
        wert = kontrast(_gemischt(e["farbe"], unterlage, e["deckkraft"]), unterlage)
        if wert < schwelle:
            befunde.append(_kontrast_befund(e["id"], wert, schwelle, "auf dem Untergrund"))
    return befunde


def _auf(e: dict, f: dict) -> bool:
    """Liegt der Mittelpunkt von e im (gedrehten) Rahmen der Form f? Wie seite_prompt.komposition_kurz."""
    l, o, r, u = seite_streifen.rahmen(f)
    return l <= e["x"] <= r and o <= e["y"] <= u


def regeln(seite: dict, masse: dict | None, zeilen: dict | None = None) -> list[dict]:
    """Weiche, rein geometrische und typografische Befunde. masse optional (ohne: deklarierte Rahmen). zeilen
    optional: die Ruhezone misst dann die Glyphen-Ausdehnung statt der Huelle."""
    s = seite_mod.pruefen(seite)
    m = seite_streifen.masse_pruefen(masse) or None
    ebenen = s["ebenen"]
    texte = [e for e in ebenen if e["art"] == "text"]
    befunde: list[dict] = []

    for e in texte:
        if e["rolle"] == "TEXT" and e["groesse"] < FLIESS_MIN:
            befunde.append(befund("weich", e["id"], f"{e['id']}: Fließtext in Größe {_zahl(e['groesse'])} – "
                                                    f"lesbar ab {FLIESS_MIN}"))

    for e in texte:
        l, o, r, u = glyphen_ausdehnung(e, m, zeilen)
        if u <= 0 or o >= s["hoehe"] or r <= 0 or l >= seite_mod.BREITE:
            continue
        abstand = min(l, seite_mod.BREITE - r)
        if abstand < RUHEZONE:
            befunde.append(befund("weich", e["id"], f"{e['id']}: nur {_zahl(round(max(abstand, 0), 1))} Einheiten "
                                                    f"bis zum Seitenrand – Text hält mindestens {RUHEZONE} Abstand"))

    groessen = sorted({e["groesse"] for e in texte})
    if len(groessen) > GROESSEN_MAX:
        befunde.append(befund("weich", "seite", f"seite: {len(groessen)} Schriftgrößen "
                                                f"({', '.join(_zahl(g) for g in groessen)}) – höchstens "
                                                f"{GROESSEN_MAX} wirken ruhig"))

    if not any(e["art"] == "knopf" for e in ebenen):
        befunde.append(befund("weich", "seite", "seite: kein Knopf – die Leserin weiß nicht, was sie tun soll"))

    gedreht = [e for e in ebenen if abs(e["drehung"]) >= LAUT_DREHUNG]
    sticker = [f for f in gedreht if f["art"] == "form"]
    laut = 0
    for e in ebenen:
        if not (e in gedreht or (e["art"] == "text" and e["rolle"] == "HAND")):
            continue
        if e["art"] == "text" and any(_auf(e, f) for f in sticker):
            continue  # Text auf einer gedrehten Form gehoert zu deren Sticker
        laut += 1
    if laut > LAUT_MAX:
        befunde.append(befund("weich", "seite", f"seite: {laut} laute Elemente (gedreht oder Handschrift) – "
                                                f"höchstens {LAUT_MAX}, sonst konkurrieren sie"))
    return befunde


def pruefen(seite: dict, masse: dict, png_ohne_text: bytes, faktor: int,
            ohne_pixel: frozenset[str] | set[str] = frozenset(), zeilen: dict | None = None) -> list[dict]:
    """Harte Kontrastbefunde, dann weiche Regeln. zeilen: Zeilenkaesten der Schrift (siehe kontraste)."""
    alle = kontraste(seite, masse, png_ohne_text, faktor, ohne_pixel, zeilen) + regeln(seite, masse, zeilen)
    return sorted(alle, key=lambda b: b["schwere"] != "hart")


def platzhalter_ids(seite: dict) -> list[str]:
    """Ids der Bild-Ebenen, deren Quelle noch ein Platzhalter ist."""
    return [e["id"] for e in seite.get("ebenen") or []
            if e.get("art") == "bild" and bildplaetze.PLATZHALTER.match(str(e.get("quelle") or ""))]


def ohne_pixel(seite: dict, masse: dict | None, fehlend=()) -> set[str]:
    """Textebenen ohne eigene Unterlage, deren Huelle einen Platzhalter ueberlappt (Hintergrund-Platzhalter: alle) -
    ihre Unterlage entsteht erst noch, ihr Pixel-Kontrast wird danach geprueft (Argument ohne_pixel von pruefen).
    fehlend: Namen echter Medien (ohne "medien:"), die beim Rendern fehlten und als graue Kachel stehen (Pruefbild am
    PC, render.fehlt) - auch ihre Unterlage ist unbekannt und zaehlt wie ein Platzhalter."""
    texte = [e for e in seite.get("ebenen") or [] if e.get("art") == "text" and e.get("hintergrund") is None]
    weg = {"medien:" + str(n) for n in fehlend or ()}
    hg = str((seite.get("hintergrund") or {}).get("bild") or "")
    if bildplaetze.PLATZHALTER.match(hg) or hg in weg:
        return {e["id"] for e in texte}
    m = seite_streifen.masse_pruefen(masse) or None
    ids = set(platzhalter_ids(seite)) | {e["id"] for e in seite.get("ebenen") or []
                                         if e.get("art") == "bild" and str(e.get("quelle") or "") in weg}
    plaetze = [seite_streifen.huelle(e, m) for e in seite.get("ebenen") or [] if e["id"] in ids]
    aus = set()
    for e in texte:
        l, o, r, u = seite_streifen.huelle(e, m)
        if any(l < r2 and l2 < r and o < u2 and o2 < u for l2, o2, r2, u2 in plaetze):
            aus.add(e["id"])
    return aus


def braucht_pixel(seite: dict, ohne: set[str]) -> bool:
    """Gibt es eine Textebene, deren Kontrast aus den Pixeln von seite_ohne_text gemessen wird?"""
    return any(e.get("art") == "text" and e.get("hintergrund") is None and e["id"] not in ohne
               for e in seite.get("ebenen") or [])


def seite_ohne_text(seite: dict) -> dict:
    """Tiefe Kopie ohne Text- und Knopfebenen - fuer das Rendering, aus dem kontraste die Unterlage misst."""
    kopie = copy.deepcopy(seite)
    kopie["ebenen"] = [e for e in kopie.get("ebenen") or [] if not (isinstance(e, dict)
                                                                    and e.get("art") in ("text", "knopf"))]
    return kopie
