"""Collage-Vorlagen (freie Flaeche, Stufe 5): Rollen aus einer gestalteten Seite ableiten und die Seite mit der
Marke einer anderen Firma fuellen. Rein rechnend wie claw/seite_schoenheit: keine Datei, kein Dienst, keine DB.

ROLLEN werden NEBEN der Seite gespeichert (Vorlage, Spalte rollen jsonb), nie in ihr:

    {"farben": {"<pfad>": "<farbrolle>"}, "ebenen": {"<ebenen-id>": "logo" | "name" | "motiv:<Beschreibung>"}}

<pfad> adressiert ein Farbfeld der Seite: "hintergrund.farbe" oder "ebenen.<id>.<feld>" mit <feld> aus farbe,
textfarbe, hintergrund, rahmen_farbe (Ebenen-IDs enthalten keinen Punkt). Farbrollen: akzent, zweitfarbe, grund,
text, auf_akzent (lesbare Schrift auf dem Akzent) und die ABGELEITETEN Rollen akzent_text (der Akzent als lesbare
Schrift auf dem Grund) sowie Toenungen "akzent~hell:0.80" / "akzent~dunkel:0.30" (= mischen(akzent, Weiss bzw.
Schwarz, 0.80), ebenso fuer zweitfarbe). auf_akzent und akzent_text kommen aus vorlagen_marke.rollen - dieselben
Werte, die der Assistent als LESBARE MARKENFARBEN sieht. Farben ohne Rolle bleiben fest.

Flaechen bekommen eine exakt gleiche Markenfarbe, sonst eine Toenung (je Kanal hoechstens TOENUNG_TOLERANZ
Abweichung, Anteil in 0,05-Schritten; neutrale Grau-/Weisstoene nie). Schriftfarben (text farbe, knopf textfarbe)
nie per Zufallsgleichheit eine Flaechenrolle: auf_akzent (gleich auf_akzent UND die Flaeche direkt darunter hat die
Rolle akzent), sonst text (gleich der Markentextfarbe), sonst akzent_text (gleich akzent_text oder dem rohen Akzent,
nicht auf der Akzentflaeche), sonst eine Toenung. Gilt die Marke mit mehreren Akzenten (akzent_auch: Gestalt und
Markenprofil weichen ab), zaehlt beim Ableiten jeder davon als Akzent.

Zu den Rollen gehoert "basis": die Markenwerte beim Ableiten ({akzent: [..], zweitfarbe, grund}). Fuellt man mit
derselben Marke, bleiben abgeleitete Farben, wie sie sind - der Rundlauf ist exakt, obwohl Toenungen mit Toleranz
erkannt werden.

MARKE (Eingabe): wie api/pult._marke_fuer_seite (akzent, schrift_anzeige, schrift_text, webseite), erweitert um
zweitfarbe, grund, text, name, logo und logo_dunkel (Mediennamen mit oder ohne "medien:", oder None). Fehlende
Farben fallen zurueck: akzent <- vorlagen_marke.ERSATZ_AKZENT, zweitfarbe <- hellere (dunkler Akzent) bzw.
dunklere Spielart des Akzents, grund <- #ffffff, text <- #1a1a1a.

FUELLEN: Schriften der Marke (nur Register-IDs), Farben nach Rolle, Logo einsetzen (auf dunkler Unterlage
logo_dunkel, falls vorhanden) oder die Logo-Ebene entfernen, Firmenname in die name-Ebene, Knopf-Links auf die
Webseite der Marke, jede Motiv-Ebene bekommt einen Platzhalter im passenden Seitenverhaeltnis und einen
Bildauftrag {platz, modus: 'neu', hinweis}. Danach kontrast_reparieren. Ohne gueltige Webseite bleiben die
Knopf-Links der Vorlage, ohne Firmenname der Text der name-Ebene - beides als Warnung an den Betreiber.

KONTRAST REPARIEREN: Jede Text- und Knopfschrift, die gegen ihre bekannte Unterlage unter der Schwelle von
seite_schoenheit liegt (4,5:1, ab Groesse 24 3:1), wird auf die lesbarere von text und #ffffff gesetzt. Unterlage:
beim Knopf seine Farbe, bei Text mit hintergrund dieser, sonst die oberste Ebene UNTER dem Text, deren (gedrehte)
Huelle den Textmittelpunkt enthaelt - eine Form zaehlt mit ihrer Farbe, ein Bild heisst "Pixel unbekannt" und der
Text bleibt (die Pixelpruefung in seite_schoenheit.kontraste deckt ihn ab); ohne solche Ebene der Seitengrund,
ausser die Seite hat ein Hintergrundbild. Bewusst die Mittelpunkt-Regel, nicht die Glyphen-Kaesten der
Schoenheitspruefung (seite_bilder.messen_alles): beim Fuellen wird nichts gerendert, es gibt keine Zeilenkaesten.
Die Pruefung vor dem Einreichen misst danach ueber den Glyphen und faengt ab, was die Mitte nicht trifft."""
from __future__ import annotations

import copy
import re

from spaces.marketing.claw import bildplaetze, schriften
from spaces.marketing.claw import seite as seite_mod
from spaces.marketing.claw import seite_streifen
from spaces.marketing.claw.schoenheit import kontrast, leuchtdichte
from spaces.marketing.claw.seite_schoenheit import _gemischt, _schwelle
from spaces.marketing.claw import vorlagen_marke
from spaces.marketing.claw.vorlagen_marke import ERSATZ_AKZENT, FAST_SCHWARZ, _rgb, ist_dunkel, mischen

FARBROLLEN = ("akzent", "zweitfarbe", "grund", "text", "auf_akzent")
AKZENT_TEXT = "akzent_text"
TOENUNG_TOLERANZ = 6          # je Kanal, 0..255
_TOENUNG = re.compile(r"^(akzent|zweitfarbe)~(hell|dunkel):(0\.[0-9]{2})$")
_ZIEL = {"hell": "#ffffff", "dunkel": "#000000"}
WEISS = "#ffffff"
MAX_HINWEIS = 500
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
# erzeugte Motive: Dateiname aus api/bilder.py (pult_bild_einsetzen, 063)
_ERZEUGT = re.compile(r"^medien:nl-[0-9a-f]{8}-[A-Za-z0-9_-]{1,64}(\.jpg|-frei\.png)$")
_FARBFELDER = {"form": ("farbe",), "text": ("farbe", "hintergrund"), "knopf": ("farbe", "textfarbe"),
               "bild": ("rahmen_farbe",)}
_SCHRIFTFELD = {"text": "farbe", "knopf": "textfarbe"}
# Reihenfolge bei gleicher Farbe in mehreren Rollen. Schrift kennt nur text und auf_akzent (siehe rollen_ableiten),
# der Seitengrund zuerst grund, Flaechen von Ebenen zuerst akzent.
_VORRANG_GRUND = ("grund", "akzent", "zweitfarbe", "text")
_VORRANG_FLAECHE = ("akzent", "zweitfarbe", "grund", "text")
WARNUNG_WEBSEITE = "Keine Webseite der Firma – Knopf-Link bitte setzen"
WARNUNG_NAME = "Kein Firmenname – Text '{id}' bitte anpassen"


def _farbe_oder(wert, ersatz):
    return wert.lower() if isinstance(wert, str) and _HEX.match(wert) else ersatz


def _medium(wert) -> str | None:
    if not isinstance(wert, str) or not wert.strip():
        return None
    w = wert.strip()
    return w if w.startswith("medien:") else f"medien:{w}"


def markenfarben(marke: dict | None) -> dict[str, str]:
    """Farbwerte aller Rollen (klein geschrieben), fehlende mit Rueckfall, dazu akzent_text."""
    m = marke if isinstance(marke, dict) else {}
    akzent = _farbe_oder(m.get("akzent"), ERSATZ_AKZENT)
    text = _farbe_oder(m.get("text"), FAST_SCHWARZ)
    zweit_ersatz = (mischen(akzent, WEISS, 0.6) if leuchtdichte(akzent) < 0.2
                    else mischen(akzent, "#000000", 0.55))
    grund = _farbe_oder(m.get("grund"), WEISS)
    return {
        "akzent": akzent,
        "zweitfarbe": _farbe_oder(m.get("zweitfarbe"), zweit_ersatz),
        "grund": grund,
        "text": text,
        **_lesbar(akzent, grund),
    }


def _lesbar(akzent: str, grund: str) -> dict[str, str]:
    """auf_akzent und akzent_text aus vorlagen_marke.rollen - eine Definition fuer Assistent (chat_worker,
    LESBARE MARKENFARBEN), Block-Vorlagen und Collage-Vorlagen."""
    r = vorlagen_marke.rollen({"akzent": akzent}, grund)
    return {"auf_akzent": r["auf_akzent"], AKZENT_TEXT: r["akzent_text"]}


def _akzente(marke: dict | None, werte: dict[str, str]) -> list[str]:
    """Akzent der Marke und weitere (akzent_auch), ohne Doppelte."""
    m = marke if isinstance(marke, dict) else {}
    auch = m.get("akzent_auch") if isinstance(m.get("akzent_auch"), (list, tuple)) else []
    return list(dict.fromkeys([werte["akzent"], *(f for f in (_farbe_oder(a, None) for a in auch) if f)]))


def _neutral(farbe: str) -> bool:
    k = _rgb(farbe)
    return max(k) - min(k) <= TOENUNG_TOLERANZ


def _toenung(farbe: str, quellen: list[tuple[str, str]]) -> str | None:
    """Rolle "<quelle>~hell|dunkel:<anteil>" der naechstliegenden Toenung (je Kanal <= TOENUNG_TOLERANZ), sonst
    None. Neutrale Farben (Grau, fast Weiss/Schwarz) bekommen nie eine."""
    if not _HEX.match(farbe) or _neutral(farbe):
        return None
    ist = _rgb(farbe.lower())
    beste: tuple[int, str] | None = None
    for name, basis in quellen:
        for richtung, ziel in _ZIEL.items():
            for k in range(1, 20):
                d = max(abs(a - b) for a, b in zip(ist, _rgb(mischen(basis, ziel, k / 20))))
                if d <= TOENUNG_TOLERANZ and (beste is None or d < beste[0]):
                    beste = (d, f"{name}~{richtung}:{k / 20:.2f}")
    return beste[1] if beste else None


def farbe_fuer_rolle(rolle, werte: dict[str, str]) -> str | None:
    """Farbwert einer Rolle (einfach, akzent_text oder Toenung) zur Marke; None bei unbekannter Rolle."""
    if not isinstance(rolle, str):
        return None
    if rolle in werte:
        return werte[rolle]
    t = _TOENUNG.match(rolle)
    if t and 0 < float(t.group(3)) < 1:
        return mischen(werte[t.group(1)], _ZIEL[t.group(2)], float(t.group(3)))
    return None


def _abgeleitet(rolle) -> bool:
    return rolle == AKZENT_TEXT or (isinstance(rolle, str) and bool(_TOENUNG.match(rolle)))


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


def _unterlage_pfad(s: dict, i: int) -> str | None:
    """Rollen-Pfad der Flaeche direkt unter der Schrift der Ebene i (wie _schrift_unterlage, aber nur voll deckend);
    None, wenn die Unterlage ein Bild, halb durchsichtig oder unbekannt ist."""
    e = s["ebenen"][i]
    if e["art"] == "knopf":
        return f"ebenen.{e['id']}.farbe"
    if e.get("hintergrund") is not None:
        return f"ebenen.{e['id']}.hintergrund"
    for j in range(i - 1, -1, -1):
        unten = s["ebenen"][j]
        if unten["art"] not in ("form", "bild"):
            continue
        l, o, r, u = seite_streifen.rahmen(unten)
        if l <= e["x"] <= r and o <= e["y"] <= u:
            return f"ebenen.{unten['id']}.farbe" if unten["art"] == "form" and unten["deckkraft"] >= 1 else None
    return "hintergrund.farbe" if s["hintergrund"].get("bild") is None else None


def _beschreibung(eid: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", eid)).strip() or eid


def rollen_ableiten(seite: dict, marke: dict, bildauftraege: list[dict] | None = None) -> dict:
    """Rollen einer gestalteten Seite zur Marke, mit der sie gestaltet wurde. Farben, die exakt (ohne Ruecksicht
    auf Gross-/Kleinschreibung) einer Markenfarbe entsprechen, bekommen deren Rolle. bildauftraege (optional,
    Form wie seite_werkzeuge: {platz, hinweis, ...}) liefern die Motiv-Beschreibung, sonst die Ebenen-ID."""
    s = seite_mod.pruefen(seite)
    werte = markenfarben(marke)
    m = marke if isinstance(marke, dict) else {}
    akzente = _akzente(m, werte)
    nach_farbe: dict[str, list[str]] = {}
    for rolle in FARBROLLEN:
        nach_farbe.setdefault(werte[rolle], []).append(rolle)
    auf_akzent, akzent_text = {werte["auf_akzent"]}, {werte[AKZENT_TEXT], *akzente}
    for a in akzente[1:]:                      # weitere Akzente der Marke zaehlen beim Ableiten mit
        nach_farbe.setdefault(a, []).append("akzent")
        lesbar = _lesbar(a, werte["grund"])
        auf_akzent.add(lesbar["auf_akzent"])
        akzent_text.add(lesbar[AKZENT_TEXT])
    quellen = [("akzent", a) for a in akzente] + [("zweitfarbe", werte["zweitfarbe"])]

    def rolle_fuer(farbe, vorrang) -> str | None:
        kandidaten = nach_farbe.get(str(farbe).lower(), [])
        return next((r for r in vorrang if r in kandidaten), None) or _toenung(str(farbe), quellen)

    def schrift_rolle(farbe, unter_pfad) -> str | None:
        """Schrift nie per Zufallsgleichheit mit einer Flaechenfarbe: auf_akzent nur genau auf der Akzentflaeche,
        sonst text, sonst akzent_text (nicht auf der Akzentflaeche), sonst eine Toenung, sonst keine Rolle (bleibt
        fest, z. B. Weiss auf einem Foto)."""
        f = str(farbe).lower()
        auf_flaeche = unter_pfad is not None and farben.get(unter_pfad) == "akzent"
        if auf_flaeche and f in auf_akzent:
            return "auf_akzent"
        if f == werte["text"]:
            return "text"
        if f in akzent_text and not auf_flaeche:
            return AKZENT_TEXT
        return _toenung(f, quellen)

    farben: dict[str, str] = {}
    r = rolle_fuer(s["hintergrund"]["farbe"], _VORRANG_GRUND)
    if r:
        farben["hintergrund.farbe"] = r
    for e in s["ebenen"]:                      # erst die Flaechen ...
        for feld in _FARBFELDER[e["art"]]:
            if feld != _SCHRIFTFELD.get(e["art"]) and e.get(feld) is not None:
                r = rolle_fuer(e[feld], _VORRANG_FLAECHE)
                if r:
                    farben[f"ebenen.{e['id']}.{feld}"] = r
    for i, e in enumerate(s["ebenen"]):        # ... dann die Schrift, die auf ihnen liegt
        feld = _SCHRIFTFELD.get(e["art"])
        if feld is not None:
            r = schrift_rolle(e[feld], _unterlage_pfad(s, i))
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
    basis = {"akzent": akzente, "zweitfarbe": werte["zweitfarbe"], "grund": werte["grund"]}
    return {"farben": farben, "ebenen": ebenen, "basis": basis}


def _gleiche_basis(rollen: dict, werte: dict[str, str]) -> bool:
    """Fuellt man mit der Marke, mit der die Rollen abgeleitet wurden (Akzent einer der Akzente von damals,
    Zweitfarbe und Grund gleich)?"""
    b = (rollen or {}).get("basis")
    if not isinstance(b, dict) or not isinstance(b.get("akzent"), list):
        return False
    return (werte["akzent"] in b["akzent"] and b.get("zweitfarbe") == werte["zweitfarbe"]
            and b.get("grund") == werte["grund"])


def fuellen(seite: dict, rollen: dict, marke: dict, logo_medium: str | None
            ) -> tuple[dict, list[dict], list[str]]:
    """Vorlage mit der Marke fuellen. Gibt die gepruefte Seite, die Bildauftraege der Motiv-Ebenen und Warnungen
    fuer den Betreiber zurueck (fehlende Webseite: Knopf-Links bleiben die der Vorlage; fehlender Firmenname: der
    Text der name-Ebene bleibt)."""
    s = copy.deepcopy(seite_mod.pruefen(seite))
    m = marke if isinstance(marke, dict) else {}
    werte = markenfarben(m)
    r_farben = (rollen or {}).get("farben") or {}
    r_ebenen = (rollen or {}).get("ebenen") or {}

    for k, schluessel in (("anzeige", "schrift_anzeige"), ("text", "schrift_text")):
        if m.get(schluessel) in schriften.REGISTER:
            s["schriften"][k] = m[schluessel]
    warnungen: list[str] = []
    gleich = _gleiche_basis(rollen, werte)
    for pfad, rolle in r_farben.items():
        wert = farbe_fuer_rolle(rolle, werte)
        if wert is None or not isinstance(pfad, str) or (gleich and _abgeleitet(rolle)):
            continue        # abgeleitete Farben bleiben bei derselben Marke exakt die der Vorlage
        _pfad_setzen(s, pfad, wert)

    web = m.get("webseite")
    knoepfe = [e for e in s["ebenen"] if e["art"] == "knopf"]
    if isinstance(web, str) and seite_mod._HTTPS.fullmatch(web):
        for e in knoepfe:
            e["link"] = web
    elif knoepfe:
        warnungen.append(WARNUNG_WEBSEITE)

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
        elif rolle == "name" and e["art"] == "text":
            if name:
                e["text"] = name
            else:
                warnungen.append(WARNUNG_NAME.format(id=e["id"]))
        elif rolle.startswith("motiv:") and e["art"] == "bild":
            e["quelle"] = "medien:" + bildplaetze.platzhalter_name(max(1, round(e["breite"])),
                                                                     max(1, round(e["hoehe"])))
            hinweis = rolle[len("motiv:"):].strip()[:MAX_HINWEIS] or _beschreibung(e["id"])
            auftraege.append({"platz": e["id"], "modus": "neu", "hinweis": hinweis})
    s["ebenen"] = [e for e in s["ebenen"] if e["id"] not in weg]
    return kontrast_reparieren(s, rollen, text=werte["text"]), auftraege, warnungen


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
    textfarbe = _farbe_oder(text, None)
    if textfarbe is None:
        textfarbe = _textfarbe(s, rollen)
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
