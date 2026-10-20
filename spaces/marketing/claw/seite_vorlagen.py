"""Collage-Vorlagen (freie Flaeche, Stufe 5): Rollen aus einer gestalteten Seite ableiten und die Seite mit der
Marke einer anderen Firma fuellen. Rein rechnend wie claw/seite_schoenheit: keine Datei, kein Dienst, keine DB.

ROLLEN werden NEBEN der Seite gespeichert (Vorlage, Spalte rollen jsonb), nie in ihr:

    {"farben": {"<pfad>": "<farbrolle>"}, "ebenen": {"<ebenen-id>": "logo" | "name" | "motiv:<Beschreibung>"}}

<pfad> adressiert ein Farbfeld der Seite: "hintergrund.farbe" oder "ebenen.<id>.<feld>" mit <feld> aus farbe,
textfarbe, hintergrund, rahmen_farbe (Ebenen-IDs enthalten keinen Punkt). Farbrollen: akzent, zweitfarbe, grund,
text, auf_akzent (die lesbarere von text und Weiss auf dem Akzent). Farben ohne Rolle bleiben fest.

MARKE (Eingabe): wie api/pult._marke_fuer_seite (akzent, schrift_anzeige, schrift_text, webseite), erweitert um
zweitfarbe, grund, text, name, logo und logo_dunkel (Mediennamen mit oder ohne "medien:", oder None). Fehlende
Farben fallen zurueck: akzent <- vorlagen_marke.ERSATZ_AKZENT, zweitfarbe <- hellere (dunkler Akzent) bzw.
dunklere Spielart des Akzents, grund <- #ffffff, text <- #1a1a1a.

FUELLEN: Schriften der Marke (nur Register-IDs), Farben nach Rolle, Logo einsetzen (auf dunkler Unterlage
logo_dunkel, falls vorhanden) oder die Logo-Ebene entfernen, Firmenname in die name-Ebene, Knopf-Links auf die
Webseite der Marke, jede Motiv-Ebene bekommt einen Platzhalter im passenden Seitenverhaeltnis und einen
Bildauftrag {platz, modus: 'neu', hinweis}. Danach kontrast_reparieren.

KONTRAST REPARIEREN: Jede Text- und Knopfschrift, die gegen ihre bekannte Unterlage unter der Schwelle von
seite_schoenheit liegt (4,5:1, ab Groesse 24 3:1), wird auf die lesbarere von text und #ffffff gesetzt. Unterlage:
beim Knopf seine Farbe, bei Text mit hintergrund dieser, sonst die oberste Ebene UNTER dem Text, deren (gedrehte)
Huelle den Textmittelpunkt enthaelt - eine Form zaehlt mit ihrer Farbe, ein Bild heisst "Pixel unbekannt" und der
Text bleibt (die Pixelpruefung in seite_schoenheit.kontraste deckt ihn ab); ohne solche Ebene der Seitengrund,
ausser die Seite hat ein Hintergrundbild."""
from __future__ import annotations

import copy
import re

from spaces.marketing.claw import bildplaetze, schriften
from spaces.marketing.claw import seite as seite_mod
from spaces.marketing.claw import seite_streifen
from spaces.marketing.claw.schoenheit import kontrast, leuchtdichte
from spaces.marketing.claw.seite_schoenheit import _gemischt, _schwelle
from spaces.marketing.claw.vorlagen_marke import ERSATZ_AKZENT, FAST_SCHWARZ, ist_dunkel, mischen

FARBROLLEN = ("akzent", "zweitfarbe", "grund", "text", "auf_akzent")
WEISS = "#ffffff"
MAX_HINWEIS = 500
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
# erzeugte Motive: Dateiname aus api/bilder.py (pult_bild_einsetzen, 063)
_ERZEUGT = re.compile(r"^medien:nl-[0-9a-f]{8}-[A-Za-z0-9_-]{1,64}(\.jpg|-frei\.png)$")
_FARBFELDER = {"form": ("farbe",), "text": ("farbe", "hintergrund"), "knopf": ("farbe", "textfarbe"),
               "bild": ("rahmen_farbe",)}
_SCHRIFTFELD = {"text": "farbe", "knopf": "textfarbe"}
# Reihenfolge bei gleicher Farbe in mehreren Rollen: Schrift zuerst als text, Flaechen zuerst als akzent
_VORRANG_SCHRIFT = ("text", "auf_akzent", "akzent", "zweitfarbe", "grund")
_VORRANG_FLAECHE = ("akzent", "zweitfarbe", "grund", "text", "auf_akzent")


def _farbe_oder(wert, ersatz: str) -> str:
    return wert.lower() if isinstance(wert, str) and _HEX.match(wert) else ersatz


def _medium(wert) -> str | None:
    if not isinstance(wert, str) or not wert.strip():
        return None
    w = wert.strip()
    return w if w.startswith("medien:") else f"medien:{w}"


def markenfarben(marke: dict | None) -> dict[str, str]:
    """Farbwerte aller Rollen (klein geschrieben), fehlende mit Rueckfall."""
    m = marke if isinstance(marke, dict) else {}
    akzent = _farbe_oder(m.get("akzent"), ERSATZ_AKZENT)
    text = _farbe_oder(m.get("text"), FAST_SCHWARZ)
    zweit_ersatz = (mischen(akzent, WEISS, 0.6) if leuchtdichte(akzent) < 0.2
                    else mischen(akzent, "#000000", 0.55))
    return {
        "akzent": akzent,
        "zweitfarbe": _farbe_oder(m.get("zweitfarbe"), zweit_ersatz),
        "grund": _farbe_oder(m.get("grund"), WEISS),
        "text": text,
        "auf_akzent": lesbarer(text, akzent),
    }


def lesbarer(text: str, unterlage: str) -> str:
    """Die lesbarere von Textfarbe und Weiss auf der Unterlage (bei Gleichstand die Textfarbe)."""
    return WEISS if kontrast(WEISS, unterlage) > kontrast(text, unterlage) else text


def _pfad_lesen(s: dict, pfad: str):
    teile = pfad.split(".")
    if teile == ["hintergrund", "farbe"]:
        return s["hintergrund"].get("farbe")
    if len(teile) == 3 and teile[0] == "ebenen":
        for e in s["ebenen"]:
            if e["id"] == teile[1]:
                return e.get(teile[2]) if teile[2] in _FARBFELDER.get(e["art"], ()) else None
    return None


def _pfad_setzen(s: dict, pfad: str, wert: str) -> None:
    teile = pfad.split(".")
    if teile == ["hintergrund", "farbe"]:
        s["hintergrund"]["farbe"] = wert
        return
    if len(teile) == 3 and teile[0] == "ebenen":
        for e in s["ebenen"]:
            if e["id"] == teile[1] and teile[2] in _FARBFELDER.get(e["art"], ()):
                if teile[2] == "hintergrund" and e.get("hintergrund") is None:
                    return          # keine eigene Unterlage anlegen, die die Vorlage nicht hatte
                e[teile[2]] = wert


def _unterlage(s: dict, i: int, punkt: tuple[float, float] | None = None) -> str | None:
    """Farbe unter der Mitte der Ebene i (oder unter punkt, nur Ebenen unter i): oberste Form darunter, sonst
    Seitengrund; None = Pixel unbekannt."""
    x, y = punkt if punkt is not None else (s["ebenen"][i]["x"], s["ebenen"][i]["y"])
    for j in range(i - 1, -1, -1):
        unten = s["ebenen"][j]
        if unten["art"] not in ("form", "bild"):
            continue
        l, o, r, u = seite_streifen.rahmen(unten)
        if not (l <= x <= r and o <= y <= u):
            continue
        if unten["art"] == "bild":
            return None
        if unten["deckkraft"] >= 1:
            return unten["farbe"].lower()
        rest = _unterlage(s, j, (x, y))
        return None if rest is None else _gemischt(unten["farbe"].lower(), rest, unten["deckkraft"])
    if s["hintergrund"].get("bild") is not None:
        return None
    return s["hintergrund"]["farbe"].lower()


def _schrift_unterlage(s: dict, i: int) -> str | None:
    e = s["ebenen"][i]
    if e["art"] == "knopf":
        return e["farbe"].lower()
    if e.get("hintergrund") is not None:
        return e["hintergrund"].lower()
    return _unterlage(s, i)


def _beschreibung(eid: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", eid)).strip() or eid


def rollen_ableiten(seite: dict, marke: dict, bildauftraege: list[dict] | None = None) -> dict:
    """Rollen einer gestalteten Seite zur Marke, mit der sie gestaltet wurde. Farben, die exakt (ohne Ruecksicht
    auf Gross-/Kleinschreibung) einer Markenfarbe entsprechen, bekommen deren Rolle. bildauftraege (optional,
    Form wie seite_werkzeuge: {platz, hinweis, ...}) liefern die Motiv-Beschreibung, sonst die Ebenen-ID."""
    s = seite_mod.pruefen(seite)
    werte = markenfarben(marke)
    m = marke if isinstance(marke, dict) else {}
    nach_farbe: dict[str, list[str]] = {}
    for rolle in FARBROLLEN:
        nach_farbe.setdefault(werte[rolle], []).append(rolle)

    def rolle_fuer(farbe, vorrang, auf_akzent_erlaubt: bool) -> str | None:
        kandidaten = nach_farbe.get(str(farbe).lower(), [])
        for r in vorrang:
            if r in kandidaten and (r != "auf_akzent" or auf_akzent_erlaubt):
                return r
        return None

    farben: dict[str, str] = {}
    r = rolle_fuer(s["hintergrund"]["farbe"], _VORRANG_FLAECHE, False)
    if r:
        farben["hintergrund.farbe"] = r
    for i, e in enumerate(s["ebenen"]):
        schriftfeld = _SCHRIFTFELD.get(e["art"])
        for feld in _FARBFELDER[e["art"]]:
            wert = e.get(feld)
            if wert is None:
                continue
            if feld == schriftfeld:
                unter = _schrift_unterlage(s, i)
                r = rolle_fuer(wert, _VORRANG_SCHRIFT, unter is not None and unter == werte["akzent"])
            else:
                r = rolle_fuer(wert, _VORRANG_FLAECHE, False)
            if r:
                farben[f"ebenen.{e['id']}.{feld}"] = r

    hinweise = {}
    for a in bildauftraege or []:
        if isinstance(a, dict) and isinstance(a.get("platz"), str) and isinstance(a.get("hinweis"), str) \
                and a["hinweis"].strip():
            hinweise[a["platz"]] = a["hinweis"].strip()
    logos = {_medium(m.get("logo")), _medium(m.get("logo_dunkel"))} - {None}
    name = m.get("name").strip().casefold() if isinstance(m.get("name"), str) and m["name"].strip() else None
    ebenen: dict[str, str] = {}
    for e in s["ebenen"]:
        if e["art"] == "bild":
            if e["quelle"] in logos:
                ebenen[e["id"]] = "logo"
            elif e["id"] in hinweise or _ERZEUGT.match(e["quelle"]) or bildplaetze.PLATZHALTER.match(e["quelle"]):
                ebenen[e["id"]] = "motiv:" + hinweise.get(e["id"], _beschreibung(e["id"]))[:MAX_HINWEIS]
        elif e["art"] == "text" and name is not None and e["text"].strip().casefold() == name:
            ebenen[e["id"]] = "name"
    return {"farben": farben, "ebenen": ebenen}


def fuellen(seite: dict, rollen: dict, marke: dict, logo_medium: str | None) -> tuple[dict, list[dict]]:
    """Vorlage mit der Marke fuellen. Gibt die gepruefte Seite und die Bildauftraege der Motiv-Ebenen zurueck."""
    s = copy.deepcopy(seite_mod.pruefen(seite))
    m = marke if isinstance(marke, dict) else {}
    werte = markenfarben(m)
    r_farben = (rollen or {}).get("farben") or {}
    r_ebenen = (rollen or {}).get("ebenen") or {}

    for k, schluessel in (("anzeige", "schrift_anzeige"), ("text", "schrift_text")):
        if m.get(schluessel) in schriften.REGISTER:
            s["schriften"][k] = m[schluessel]
    for pfad, rolle in r_farben.items():
        if rolle in werte and isinstance(pfad, str):
            _pfad_setzen(s, pfad, werte[rolle])

    web = m.get("webseite")
    if isinstance(web, str) and seite_mod._HTTPS.fullmatch(web):
        for e in s["ebenen"]:
            if e["art"] == "knopf":
                e["link"] = web

    logo = _medium(logo_medium)
    logo_dunkel = _medium(m.get("logo_dunkel"))
    name = m.get("name").strip()[:seite_mod.MAX_TEXT] if isinstance(m.get("name"), str) else ""
    auftraege: list[dict] = []
    weg: set[str] = set()
    for i, e in enumerate(s["ebenen"]):
        rolle = r_ebenen.get(e["id"])
        if not isinstance(rolle, str):
            continue
        if rolle == "logo" and e["art"] == "bild":
            if logo is None:
                weg.add(e["id"])
                continue
            unter = _unterlage(s, i)
            e["quelle"] = logo_dunkel if logo_dunkel and ist_dunkel(unter) else logo
            e["passung"] = "einpassen"          # ein Logo wird nie beschnitten
        elif rolle == "name" and e["art"] == "text" and name:
            e["text"] = name
        elif rolle.startswith("motiv:") and e["art"] == "bild":
            e["quelle"] = "medien:" + bildplaetze.platzhalter_name(max(1, round(e["breite"])),
                                                                     max(1, round(e["hoehe"])))
            hinweis = rolle[len("motiv:"):].strip()[:MAX_HINWEIS] or _beschreibung(e["id"])
            auftraege.append({"platz": e["id"], "modus": "neu", "hinweis": hinweis})
    s["ebenen"] = [e for e in s["ebenen"] if e["id"] not in weg]
    return kontrast_reparieren(s, rollen, text=werte["text"]), auftraege


def _textfarbe(s: dict, rollen: dict) -> str:
    for pfad, rolle in ((rollen or {}).get("farben") or {}).items():
        if rolle == "text" and isinstance(pfad, str):
            wert = _pfad_lesen(s, pfad)
            if isinstance(wert, str) and _HEX.match(wert):
                return wert.lower()
    return FAST_SCHWARZ


def kontrast_reparieren(seite: dict, rollen: dict, text: str | None = None) -> dict:
    """Unlesbare Schrift auf bekannter Unterlage auf die lesbarere von text und Weiss setzen. text: Textfarbe der
    Marke; ohne wird sie ueber die Rolle "text" aus der Seite gelesen (Rueckfall #1a1a1a)."""
    s = copy.deepcopy(seite_mod.pruefen(seite))
    textfarbe = _farbe_oder(text, "") or _textfarbe(s, rollen)
    for i, e in enumerate(s["ebenen"]):
        feld = _SCHRIFTFELD.get(e["art"])
        if feld is None:
            continue
        unter = _schrift_unterlage(s, i)
        if unter is None:
            continue
        sichtbar = _gemischt(e[feld].lower(), unter, e["deckkraft"]) if e["art"] == "text" else e[feld]
        if kontrast(sichtbar, unter) < _schwelle(e["groesse"]):
            e[feld] = lesbarer(textfarbe, unter)
    return seite_mod.pruefen(s)
