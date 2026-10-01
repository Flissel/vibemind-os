"""Baut die sieben Newsletter-Vorlagen fuer Laeden (sales-claw Spec
2026-10-01-newsletter-vorlagen-profi-design.md §3, §3.1) als Blockdokumente und
schreibt vorlagen/newsletter/<name>.json. Aenderungen hier, nie in den JSONs.

Jede Vorlage hat ein festes Schriftpaar (root.data.schriften), feste neutrale
Toene und Farbrollen (root.data.rollen = {"<block>/<pfad>": "<rolle>"}), die
"Neu aus Vorlage" mit den Farben des Ladens fuellt (vorlagen_marke.einsetzen).
An jeder Rollenstelle steht ein gueltiger Musterwert (Akzent Terrakotta
#c2410c, Zweitfarbe Schiefer #2f4858), damit die Vorlage selbst validiert und
als Vorschau zeigbar ist. Akzentfarbiger TEXT auf hellem Grund nimmt immer
`akzent_text` (wird fuer helle Akzente abgedunkelt), Text auf Akzent-/
Zweitflaechen `auf_akzent`/`auf_zweit`.

    python -m spaces.marketing.scripts.vorlagen_bauen
"""
from __future__ import annotations

import json
from pathlib import Path

from spaces.marketing.claw import vorlagen_marke
from spaces.marketing.claw.bildplaetze import platzhalter_name

ORDNER = Path(__file__).resolve().parents[1] / "vorlagen" / "newsletter"
PLATZHALTER = {"platzhalter-16x9.png", "platzhalter-1x1.png", "platzhalter-2x1.png", "platzhalter-3x1.png",
               "platzhalter-4x3.png"}
LINK = "https://www.example.de"
MUSTER_GESTALT = {"akzent": "#c2410c", "flaeche": "#2f4858"}
RAND = 32          # Textkante aller Vorlagen
BREITE = 600 - 2 * RAND   # 536: Inhaltsbreite zwischen den Raendern


def pad(t, b, l=RAND, r=RAND):
    return {"top": t, "bottom": b, "left": l, "right": r}


def spalten_breite(anzahl: int, luecke: int, links=RAND, rechts=RAND) -> int:
    """Nutzbare Breite der schmalsten Spalte (wie bloecke_mjml._spalten_polster verteilt)."""
    spalte = (600 - links - rechts) / anzahl
    abzug = luecke / 2 if anzahl == 2 else 2 * luecke / 3
    return int(spalte - abzug)


class Bau:
    def __init__(self, name, beschreibung, grund, text, aussen, anzeige, textschrift, dunkel=False):
        self.name, self.beschreibung = name, beschreibung
        self.bloecke = {"root": {"type": "EmailLayout", "data": {
            "backdropColor": aussen, "canvasColor": grund, "textColor": text, "childrenIds": [],
            "schriften": {"anzeige": anzeige, "text": textschrift}, "rollen": {}, **({"dunkel": True} if dunkel else {})}}}
        # Musterwerte je Rolle - dieselbe Rechnung wie beim Anlegen, mit dem Vorlagengrund
        self.muster = {**vorlagen_marke.rollen(MUSTER_GESTALT, grund), "laden": "[Laden]",
                       "logo": "medien:platzhalter-3x1.png",
                       "signal_bild": "medien:tech-signal-c2410c.png", "glow_bild": "medien:tech-glow-c2410c.jpg"}

    @property
    def rollen(self):
        return self.bloecke["root"]["data"]["rollen"]

    def add(self, bid, typ, style=None, props=None, rollen=None, oben=True):
        assert bid not in self.bloecke, bid
        self.bloecke[bid] = {"type": typ, "data": {"style": style or {}, "props": props or {}}}
        for pfad, rolle in (rollen or {}).items():
            self.rollen[f"{bid}/{pfad}"] = rolle
            knoten = self.bloecke[bid]
            teile = pfad.split("/")
            for t in teile[:-1]:
                knoten = knoten.setdefault(t, {})
            if rolle != "laden" or not knoten.get(teile[-1]):   # "*[Laden]*" bleibt in der Vorschau kursiv
                knoten[teile[-1]] = self.muster[rolle]
        if oben:
            self.bloecke["root"]["data"]["childrenIds"].append(bid)
        return bid

    def neu(self, bid, spec, oben=True):
        """spec = (typ, style, props, rollen) aus den Hilfen unten. Die Wortmarke traegt immer die Rolle laden."""
        typ, style, props, rollen = spec
        if bid == "marke_wort":
            rollen = {**rollen, "data/props/text": "laden"}
        return self.add(bid, typ, style, props, rollen, oben=oben)

    def spalten(self, bid, listen, luecke=24, p=None, hg=None, hg_rolle=None, ausr="top", breiten=None):
        anzahl = len(listen)
        style = {"padding": p or pad(0, 0)}
        if hg:
            style["backgroundColor"] = hg
        props = {"columnsCount": anzahl, "columnsGap": luecke, "contentAlignment": ausr,
                 "columns": [{"childrenIds": list(l)} for l in listen] + [{"childrenIds": []}] * (3 - anzahl)}
        if breiten:
            props["fixedWidths"] = list(breiten) + [None] * (3 - len(breiten))
        return self.add(bid, "ColumnsContainer", style, props,
                        {"data/style/backgroundColor": hg_rolle} if hg_rolle else None)

    def karte(self, bid, kinder, p, hg, hg_rolle=None, rand=None, rand_rolle=None, rund=0):
        style = {"padding": p, "backgroundColor": hg, "borderRadius": rund}
        if rand:
            style["borderColor"] = rand
        rollen = {}
        if hg_rolle:
            rollen["data/style/backgroundColor"] = hg_rolle
        if rand_rolle:
            rollen["data/style/borderColor"] = rand_rolle
        return self.add(bid, "Container", style, {"childrenIds": list(kinder)}, rollen)

    def json(self):
        return {"name": self.name, "beschreibung": self.beschreibung, "bloecke": self.bloecke}


# ---------- Bausteine: geben (typ, style, props, rollen) zurueck ----------

def _farbe(style, farbe, rolle, rollen, feld="color"):
    if farbe:
        style[feld] = farbe
    if rolle:
        rollen[f"data/style/{feld}"] = rolle


def ueber(text, groesse, farbe=None, rolle=None, anzeige=True, versal=False, sperr=None, zh=None,
          ausr="left", p=None, gewicht="bold", level="h2"):
    style = {"fontFamily": "ANZEIGE" if anzeige else "TEXT", "fontSize": groesse, "fontWeight": gewicht,
             "textAlign": ausr, "padding": p or pad(0, 12)}
    rollen: dict = {}
    _farbe(style, farbe, rolle, rollen)
    if versal:
        style["textTransform"] = "uppercase"
    if sperr is not None:
        style["letterSpacing"] = sperr
    if zh is not None:
        style["lineHeight"] = zh
    return "Heading", style, {"level": level, "text": text}, rollen


def text(t, groesse=15, farbe=None, rolle=None, anzeige=False, fett=False, ausr="left", p=None, zh=None,
         sperr=None, versal=False, md=True, hg=None, hg_rolle=None, text_rolle=None):
    style = {"fontFamily": "ANZEIGE" if anzeige else "TEXT", "fontSize": groesse,
             "fontWeight": "bold" if fett else "normal", "textAlign": ausr, "padding": p or pad(0, 16)}
    rollen: dict = {}
    _farbe(style, farbe, rolle, rollen)
    _farbe(style, hg, hg_rolle, rollen, "backgroundColor")
    if versal:
        style["textTransform"] = "uppercase"
    if sperr is not None:
        style["letterSpacing"] = sperr
    if zh is not None:
        style["lineHeight"] = zh
    if text_rolle:
        rollen["data/props/text"] = text_rolle
    return "Text", style, {"text": t, "markdown": md}, rollen


def meta(t, farbe=None, rolle=None, ausr="left", p=None, fett=False, groesse=11, text_rolle=None, sperr=2.5):
    return text(t, groesse, farbe, rolle, fett=fett, ausr=ausr, p=p or pad(0, 8), sperr=sperr, versal=True,
                md=False, text_rolle=text_rolle)


def bild(w, h, alt, p=None, sw=False, ausr="center"):
    name = platzhalter_name(w, h)
    assert name in PLATZHALTER, (w, h, name)
    props = {"url": f"medien:{name}", "alt": alt, "width": w, "height": h, "contentAlignment": "middle"}
    if sw:
        props["sw"] = True
    return "Image", {"padding": p or pad(0, 0, 0, 0), "textAlign": ausr}, props, {}


def logo(w=140, ausr="left", p=None):
    """marke_logo: ersetzt die Wortmarke, wenn der Laden ein Logo hat; kein Foto-Bildplatz (grafik)."""
    return ("Image", {"padding": p or pad(0, 12), "textAlign": ausr},
            {"url": "", "alt": "Logo", "width": w, "height": w // 3, "grafik": True, "contentAlignment": "middle"},
            {"data/props/url": "logo"})


def knopf(t, p=None, ausr="left", stil="rectangle", groesse=15, gross="medium"):
    return ("Button", {"fontFamily": "TEXT", "fontSize": groesse, "fontWeight": "bold", "textAlign": ausr,
                       "padding": p or pad(8, 32)},
            {"text": t, "url": LINK, "buttonStyle": stil, "size": gross},
            {"data/props/buttonBackgroundColor": "akzent", "data/props/buttonTextColor": "auf_akzent"})


def linie(farbe=None, rolle=None, p=None, dicke=1):
    rollen = {"data/props/lineColor": rolle} if rolle else {}
    props = {"lineHeight": dicke}
    if farbe:
        props["lineColor"] = farbe
    return "Divider", {"padding": p or pad(0, 0)}, props, rollen


def abstand(h):
    return "Spacer", {}, {"height": h}, {}


# ---------- 1 studio (M1) ----------

def studio() -> Bau:
    tx, leise, linie_f = "#2b2724", "#6b6259", "#2b2724"
    b = Bau("studio", "Studio: ruhig und hochwertig - schmale Kopfleiste mit Haarlinie, geteilter Kopf "
            "(Bild | Titel), Fotostreifen, Angebotskarte.", "#faf7f2", tx, "#efe9e0", "cormorant", "dm-sans")
    b.neu("kopf_ausgabe", meta("Ausgabe [Nr]", leise, p=pad(0, 0, 0, 0)), oben=False)
    b.neu("marke_logo", logo(132, "center", pad(0, 0, 0, 0)), oben=False)
    b.neu("marke_wort", ueber("[Laden]", 26, tx, anzeige=True, versal=True, sperr=4, ausr="center",
                              p=pad(0, 0, 0, 0), gewicht="normal", zh=1.1, level="h1"), oben=False)
    b.neu("kopf_monat", meta("[Monat]", leise, ausr="right", p=pad(0, 0, 0, 0)), oben=False)
    b.spalten("kopf", [["kopf_ausgabe"], ["marke_logo", "marke_wort"], ["kopf_monat"]], 0, pad(28, 18),
              ausr="middle", breiten=[120, 296, 120])
    b.neu("kopf_linie", linie(linie_f, p=pad(0, 0)))

    w = spalten_breite(2, 24)          # 256
    b.neu("held_bild", bild(w, w, "Stimmungsbild zum Thema dieser Ausgabe"), oben=False)
    b.neu("held_marke", meta("Diese Ausgabe", rolle="akzent_text", p=pad(0, 10, 0, 0)), oben=False)
    b.neu("held_titel", ueber("Willkommen zum *[Monat]*brief", 34, tx, zh=1.05, gewicht="normal",
                              p=pad(0, 14, 0, 0), level="h1"), oben=False)
    b.neu("held_text", text("[Ein, zwei Sätze, worum es diesen Monat geht – Neuheiten, ein Angebot "
                            "und ein Termin, den man sich merken sollte.]", 15, p=pad(0, 0, 0, 0), zh=1.6), oben=False)
    b.spalten("held", [["held_bild"], ["held_marke", "held_titel", "held_text"]], 24, pad(32, 32), ausr="middle")
    b.neu("held_linie", linie("#d8d0c5", p=pad(0, 0)))

    b.neu("thema_titel", ueber("[Das zweite Thema in einer Zeile]", 26, tx, zh=1.1, gewicht="normal",
                               p=pad(0, 0, 0, 0)), oben=False)
    b.neu("thema_text", text("[Zwei, drei Sätze dazu: was neu ist, für wen es gedacht ist und wo man "
                             "mehr erfährt.]", 15, p=pad(0, 0, 0, 0), zh=1.6), oben=False)
    b.spalten("thema", [["thema_titel"], ["thema_text"]], 24, pad(32, 24))

    b.neu("streifen_titel", meta("Aus dem Laden", rolle="akzent_text", p=pad(8, 12)))
    bw = spalten_breite(3, 12)         # 170 -> 168 x 126
    reihe = []
    for n, alt in ((1, "Foto aus dem Laden"), (2, "Foto eines Produkts"), (3, "Foto vom Team bei der Arbeit")):
        b.neu(f"streifen_bild{n}", bild(168, 126, alt, pad(0, 8, 0, 0)), oben=False)
        b.neu(f"streifen_text{n}", text(f"[Bildunterschrift {n}]", 12, leise, p=pad(0, 0, 0, 0)), oben=False)
        reihe.append([f"streifen_bild{n}", f"streifen_text{n}"])
    assert bw >= 168
    b.spalten("streifen", reihe, 12, pad(0, 36))

    b.neu("angebot_marke", meta("Angebot", rolle="akzent_text", p=pad(0, 8, 0, 0)), oben=False)
    b.neu("angebot_titel", ueber("[Das Angebot des Monats in einem Satz]", 28, tx, zh=1.1, gewicht="normal",
                                 p=pad(0, 10, 0, 0)), oben=False)
    b.neu("angebot_text", text("[Was es gibt, bis wann es gilt und wie man es bekommt.]", 15,
                               p=pad(0, 8, 0, 0)), oben=False)
    b.neu("knopf", knopf("[Jetzt vorbeikommen]", pad(8, 0, 0, 0)), oben=False)
    b.karte("angebot", ["angebot_marke", "angebot_titel", "angebot_text", "knopf"], pad(28, 28, 24, 24),
            "#ffffff", rand=linie_f)

    b.neu("gruss", text("Bis bald,  \n[Name] und das Team", 15, p=pad(32, 28), zh=1.6))
    b.neu("fuss_linie", linie(linie_f, p=pad(0, 0)))
    b.neu("fuss", meta("[Telefon] · [Website] · @[instagram]", leise, ausr="center", p=pad(16, 24)))
    return b


# ---------- 2 zeitung (M2) ----------

def zeitung() -> Bau:
    tx = "#2a2522"
    b = Bau("zeitung", "Zeitung: kräftig - riesiger Serifentitel in Ladenfarbe zwischen zwei Linien, Leitband, "
            "Farbblock, Fotos in Schwarz-Weiß.", "#f8f1e6", tx, "#ebe2d2", "playfair", "poppins")
    b.neu("kopf_ausgabe", meta("Ausgabe [Nr] · [Monat]", rolle="akzent_text", p=pad(0, 0, 0, 0)), oben=False)
    b.neu("kopf_handle", meta("@[instagram]", rolle="akzent_text", ausr="right", p=pad(0, 0, 0, 0)), oben=False)
    b.spalten("kopf", [["kopf_ausgabe"], ["kopf_handle"]], 16, pad(24, 8), ausr="middle")
    b.neu("kopf_linie_oben", linie(rolle="akzent", p=pad(0, 0)))
    b.neu("marke_logo", logo(180, "center", pad(14, 10)))
    b.neu("marke_wort", ueber("[Laden]", 56, rolle="akzent_text", ausr="center", zh=1.0, sperr=-1,
                              p=pad(12, 12), level="h1"))
    b.neu("kopf_linie_unten", linie(rolle="akzent", p=pad(0, 12)))
    b.neu("band", text("Neues aus Laden & Werkstatt", 12, rolle="auf_akzent", fett=True, ausr="center",
                       p=pad(9, 9), sperr=3, versal=True, md=False, hg_rolle="akzent"))

    w = spalten_breite(2, 24)          # 256 -> 256 x 192
    b.neu("haupt_marke", meta("Top-Thema", rolle="akzent_text", p=pad(0, 8, 0, 0), fett=True), oben=False)
    b.neu("haupt_titel", ueber("[Die Schlagzeile zum Hauptthema]", 28, tx, zh=1.1, p=pad(0, 12, 0, 0),
                               level="h2"), oben=False)
    b.neu("haupt_text", text("[Drei, vier Sätze: was es Neues gibt, warum es sich lohnt und bis wann. "
                             "Kurz und konkret wie eine Zeitungsmeldung.]", 15, p=pad(0, 0, 0, 0), zh=1.6), oben=False)
    b.neu("haupt_bild", bild(w, 192, "Foto zum Hauptthema (wird schwarz-weiß gezeigt)", sw=True), oben=False)
    b.spalten("haupt", [["haupt_marke", "haupt_titel", "haupt_text"], ["haupt_bild"]], 24, pad(32, 28))

    b.neu("aktion_titel", ueber("[Angebot: ein Satz, der neugierig macht]", 24, rolle="auf_akzent", versal=True,
                                zh=1.15, sperr=0.5, p=pad(0, 10, 0, 0), anzeige=False), oben=False)
    b.neu("aktion_text", text("[Was es gibt, bis wann, und wie man es bekommt.]", 15, rolle="auf_akzent",
                              p=pad(0, 0, 0, 0)), oben=False)
    b.karte("aktion", ["aktion_titel", "aktion_text"], pad(24, 24, 8, 8), "#c2410c", hg_rolle="akzent")

    b.neu("neben_bild", bild(w, 192, "Foto zum zweiten Thema (wird schwarz-weiß gezeigt)", sw=True), oben=False)
    b.neu("neben_marke", meta("[Rubrik]", rolle="akzent_text", p=pad(0, 8, 0, 0), fett=True), oben=False)
    b.neu("neben_titel", ueber("[Das zweite Thema]", 22, tx, zh=1.15, p=pad(0, 8, 0, 0), level="h3"), oben=False)
    b.neu("neben_text", text("[Zwei Sätze dazu – was, wann, wo.]", 15, p=pad(0, 0, 0, 0), zh=1.6), oben=False)
    b.spalten("neben", [["neben_bild"], ["neben_marke", "neben_titel", "neben_text"]], 24, pad(32, 24),
              ausr="middle")

    b.neu("schluss_linie", linie(rolle="akzent", p=pad(8, 0)))
    b.neu("schluss_text", text("[Ein Satz zum Schluss – eine Einladung, vorbeizukommen.]", 16, tx, p=pad(24, 8),
                               zh=1.6))
    b.neu("knopf", knopf("[Jetzt vorbeischauen]", pad(8, 32)))
    b.neu("fuss", meta("[Telefon] · [Website] · @[instagram]", rolle="akzent_text", ausr="center", p=pad(8, 24)))
    return b


# ---------- 3 firmenblatt (M3) ----------

def firmenblatt() -> Bau:
    tx, leise = "#2b2724", "#5f5a55"
    b = Bau("firmenblatt", "Firmenblatt: strukturiert - Kopf in der Zweitfarbe mit Serifentitel, Kontaktleiste, "
            "Rubriken, getöntes Feld.", "#ffffff", tx, "#eceef0", "young-serif", "poppins")
    b.neu("marke_logo", logo(150, "left", pad(0, 12, 0, 0)), oben=False)
    b.neu("marke_wort", ueber("[Laden]", 34, rolle="auf_zweit", zh=1.05, gewicht="normal", p=pad(0, 0, 0, 0),
                              level="h1"), oben=False)
    b.neu("kopf_titel", ueber("*Rundbrief*", 34, rolle="akzent_hell", zh=1.05, gewicht="normal",
                              p=pad(0, 0, 0, 0), level="h2"), oben=False)
    b.neu("kopf_ausgabe", meta("[Monat]  \nAusgabe [Nr]", rolle="auf_zweit", ausr="right", p=pad(0, 4, 0, 0)),
          oben=False)
    b.spalten("kopf", [["marke_logo", "marke_wort", "kopf_titel"], ["kopf_ausgabe"]], 16, pad(32, 28),
              hg="#2f4858", hg_rolle="zweit", ausr="bottom", breiten=[372, 164])
    b.neu("kontakt1", text("[Telefon]", 11, tx, fett=True, ausr="center", p=pad(0, 0, 0, 0), md=False, sperr=1),
          oben=False)
    b.neu("kontakt2", text("[Website]", 11, tx, fett=True, ausr="center", p=pad(0, 0, 0, 0), md=False, sperr=1),
          oben=False)
    b.neu("kontakt3", text("@[instagram]", 11, tx, fett=True, ausr="center", p=pad(0, 0, 0, 0), md=False, sperr=1),
          oben=False)
    b.spalten("kontakt", [["kontakt1"], ["kontakt2"], ["kontakt3"]], 0, pad(10, 10), hg="#f9ece7",
              hg_rolle="akzent_hell", ausr="middle")
    b.neu("haupt_bild", bild(600, 300, "Großes Bild zum Top-Thema"))

    b.neu("haupt_marke", meta("Top-Thema", rolle="akzent_text", fett=True, p=pad(32, 6)))
    b.neu("haupt_titel", ueber("[Die Überschrift zum Top-Thema]", 28, rolle="zweit", zh=1.15, gewicht="normal",
                               p=pad(0, 12), level="h2"))
    b.neu("haupt_text", text("[Drei, vier Sätze: was es Neues gibt und warum es eure Kundschaft interessiert. "
                             "Ein konkretes Detail macht es greifbar.]", 15, p=pad(0, 28), zh=1.65))

    for n, rubrik in ((1, "[Rubrik eins]"), (2, "[Rubrik zwei]")):
        b.neu(f"rubrik{n}_marke", meta(rubrik, rolle="akzent_text", fett=True, p=pad(0, 6, 0, 0)), oben=False)
        b.neu(f"rubrik{n}_titel", ueber(f"[Kurzes Thema {n}]", 20, rolle="zweit", zh=1.2, gewicht="normal",
                                        p=pad(0, 8, 0, 0), level="h3"), oben=False)
        b.neu(f"rubrik{n}_text", text("[Zwei Sätze dazu.]", 14, p=pad(0, 0, 0, 0), zh=1.6), oben=False)
    b.spalten("rubriken", [[f"rubrik{n}_{t}" for t in ("marke", "titel", "text")] for n in (1, 2)], 32,
              pad(4, 32))

    w = spalten_breite(2, 24)          # 256 x 192
    b.neu("feld_marke", meta("Service", rolle="zweit", fett=True, p=pad(0, 6, 0, 0)), oben=False)
    b.neu("feld_titel", ueber("[Ein Service, den man kennen sollte]", 22, rolle="zweit", zh=1.2, gewicht="normal",
                              p=pad(0, 8, 0, 0), level="h3"), oben=False)
    b.neu("feld_text", text("[Wie es funktioniert – in zwei Sätzen.]", 14, tx, p=pad(0, 0, 0, 0), zh=1.6),
          oben=False)
    b.neu("feld_bild", bild(w, 192, "Foto zum Service"), oben=False)
    b.spalten("feld", [["feld_marke", "feld_titel", "feld_text"], ["feld_bild"]], 24, pad(28, 28),
              hg="#f9ece7", hg_rolle="akzent_hell", ausr="middle")

    b.neu("schluss_text", text("[Ein Satz zum Schluss: wann ihr erreichbar seid und wie man einen Termin bekommt.]",
                               15, p=pad(32, 8), zh=1.6))
    b.neu("knopf", knopf("[Termin vereinbaren]", pad(8, 32), stil="rounded"))
    b.neu("fuss_linie", linie("#e4e1dd", p=pad(0, 0)))
    b.neu("fuss", meta("[Laden] · [Ort]", leise, ausr="center", p=pad(16, 24)))
    return b


# ---------- 4 minimal (M4) ----------

def minimal() -> Bau:
    tx, leise = "#111111", "#55534e"
    b = Bau("minimal", "Minimal: modern und reduziert - Linkzeile, riesiger dünner Titel, fast schwarz-weiß, "
            "Akzent nur im Pfeil.", "#f4f3ef", tx, "#e8e6e0", "manrope", "manrope")
    b.neu("nav1", meta("Laden", tx, p=pad(0, 0, 0, 0)), oben=False)
    b.neu("nav2", meta("Angebote", tx, ausr="center", p=pad(0, 0, 0, 0)), oben=False)
    b.neu("nav3", meta("Kontakt", tx, ausr="right", p=pad(0, 0, 0, 0)), oben=False)
    b.spalten("nav", [["nav1"], ["nav2"], ["nav3"]], 0, pad(24, 10), ausr="middle")
    b.neu("nav_linie", linie(tx, p=pad(0, 0)))
    b.neu("marke_logo", logo(170, "left", pad(28, 20)))
    b.neu("marke_wort", ueber("[Laden]", 52, tx, versal=True, sperr=-1, zh=1.0, gewicht="normal",
                              p=pad(24, 24), level="h1"))

    w = spalten_breite(2, 24)          # 256 x 256
    b.neu("haupt_titel", ueber("[Die Überschrift zum Hauptthema]", 20, tx, versal=True, zh=1.2, gewicht="normal",
                               sperr=0.5, p=pad(0, 12, 0, 0), level="h2"), oben=False)
    b.neu("haupt_text", text("[Drei Sätze: was neu ist, für wen und bis wann. Mehr braucht es nicht.]", 15,
                             p=pad(0, 0, 0, 0), zh=1.65), oben=False)
    b.neu("haupt_bild", bild(w, w, "Ruhiges Foto zum Hauptthema"), oben=False)
    b.spalten("haupt", [["haupt_titel", "haupt_text"], ["haupt_bild"]], 24, pad(8, 32))
    b.neu("zitat_linie", linie(tx, p=pad(0, 0)))
    b.neu("zitat_pfeil", text("→", 28, rolle="akzent_text", p=pad(0, 0, 0, 0), zh=1.0, md=False), oben=False)
    b.neu("zitat_text", text("[Ein Satz, der hängen bleibt – ein Versprechen an eure Kundschaft.]", 16, tx,
                             versal=True, zh=1.35, sperr=0.5, p=pad(0, 0, 0, 0), md=False), oben=False)
    b.spalten("zitat", [["zitat_pfeil"], ["zitat_text"]], 8, pad(28, 28), ausr="middle", breiten=[56, 480])
    b.neu("neben_bild", bild(BREITE, BREITE // 2, "Breites Foto zum zweiten Thema", pad(0, 20)))
    b.neu("neben_titel", ueber("[Das zweite Thema]", 20, tx, versal=True, zh=1.2, gewicht="normal", sperr=0.5,
                               p=pad(0, 10)))
    b.neu("neben_text", text("[Zwei Sätze dazu.]", 15, p=pad(0, 20), zh=1.65))
    b.neu("knopf", knopf("[Mehr erfahren] →", pad(4, 36)))
    b.neu("fuss_linie", linie(tx, p=pad(0, 0)))
    b.neu("fuss_links", meta("[Website]", leise, p=pad(0, 0, 0, 0)), oben=False)
    b.neu("fuss_rechts", meta("Ausgabe [Nr]", leise, ausr="right", p=pad(0, 0, 0, 0)), oben=False)
    b.spalten("fuss", [["fuss_links"], ["fuss_rechts"]], 16, pad(14, 24))
    return b


# ---------- 5 klassik (M5) ----------

def klassik() -> Bau:
    tx, leise = "#2b2724", "#6b645e"
    b = Bau("klassik", "Klassik: elegant und zeitlos - zentriert, kursive Didone, gesperrte Versalien, Bildreihe, "
            "Autorzeile, Fußband.", "#ffffff", tx, "#f1eeea", "bodoni", "montserrat")
    b.neu("ausgabe_l", linie(rolle="akzent", p=pad(0, 0, 0, 0), dicke=4), oben=False)
    b.neu("ausgabe_text", meta("[Monat] · Ausgabe [Nr]", rolle="akzent_text", ausr="center", p=pad(0, 0, 8, 8),
                               fett=True), oben=False)
    b.neu("ausgabe_r", linie(rolle="akzent", p=pad(0, 0, 0, 0), dicke=4), oben=False)
    b.spalten("ausgabe", [["ausgabe_l"], ["ausgabe_text"], ["ausgabe_r"]], 0, pad(32, 8), ausr="middle",
              breiten=[120, 296, 120])
    b.neu("marke_logo", logo(180, "center", pad(16, 8)))
    b.neu("marke_wort", ueber("*[Laden]*", 44, tx, ausr="center", zh=1.1, gewicht="normal", p=pad(12, 4),
                              level="h1"))
    b.neu("unterzeile", meta("[Der Rundbrief aus eurem Laden]", rolle="akzent_text", ausr="center", p=pad(4, 28),
                             sperr=3))
    b.neu("inhalt_linie", linie("#e6e1db", p=pad(0, 0)))
    b.neu("inhalt_titel", meta("Was drin ist …", tx, ausr="center", fett=True, p=pad(24, 14), sperr=3))
    reihe = []
    for n in (1, 2, 3):
        b.neu(f"reihe_bild{n}", bild(168, 168, f"Quadratisches Foto zu Thema {n}", pad(0, 8, 0, 0)), oben=False)
        b.neu(f"reihe_text{n}", text(f"[Thema {n}]", 12, leise, ausr="center", p=pad(0, 0, 0, 0), md=False,
                                     versal=True, sperr=1.5), oben=False)
        reihe.append([f"reihe_bild{n}", f"reihe_text{n}"])
    b.spalten("reihe", reihe, 12, pad(0, 12))

    b.neu("haupt_titel", ueber("[Überschrift zum Hauptthema]", 16, tx, anzeige=False, versal=True, sperr=3,
                               ausr="center", zh=1.4, p=pad(32, 6), level="h2"))
    b.neu("haupt_autor", meta("von [Name]", rolle="akzent_text", ausr="center", p=pad(0, 18)))
    b.neu("haupt_text", text("[Ein persönlicher Absatz: was euch diesen Monat beschäftigt, was neu im Laden ist "
                             "und worauf ihr euch freut. Drei, vier Sätze in eurem Ton.]", 15, p=pad(0, 24), zh=1.75))
    b.neu("neben_titel", ueber("[Zweites Thema]", 16, tx, anzeige=False, versal=True, sperr=3, ausr="center",
                               zh=1.4, p=pad(8, 12), level="h3"))
    b.neu("neben_text", text("[Zwei Sätze dazu.]", 15, ausr="center", p=pad(0, 24), zh=1.75))
    b.neu("knopf", knopf("[Jetzt vorbeikommen]", pad(4, 36), ausr="center"))
    b.neu("fussband", text("[Laden]", 12, rolle="auf_akzent", fett=True, ausr="center", p=pad(12, 12), sperr=3,
                           versal=True, md=False, hg_rolle="akzent", text_rolle="laden"))
    return b


# ---------- 6 bildkopf (M6) ----------

def bildkopf() -> Bau:
    tx = "#2b2724"
    b = Bau("bildkopf", "Bildkopf: stimmungsvoll - Foto als Kopf mit Farbfeld und Titel, Farbband mit Bild, "
            "Tipp-Kasten.", "#efecea", tx, "#e3dfda", "josefin", "josefin")
    innen = 28   # Innenabstand der Farbtafel
    b.neu("marke_logo", logo(150, "left", pad(0, 12, innen, innen)), oben=False)
    b.neu("marke_wort", ueber("[Laden]", 24, rolle="auf_zweit", gewicht="normal", zh=1.1, p=pad(0, 4, innen, innen),
                              level="h2"), oben=False)
    b.neu("kopf_titel", ueber("Newsletter", 30, rolle="auf_zweit", versal=True, sperr=6, zh=1.1,
                              p=pad(0, 12, innen, innen), level="h1"), oben=False)
    b.neu("kopf_linie", linie(rolle="auf_zweit", p=pad(0, 10, innen, innen)), oben=False)
    b.neu("kopf_ausgabe", meta("[Monat] / Ausgabe [Nr]", rolle="auf_zweit", p=pad(0, 32, innen, innen)), oben=False)
    # Farbtafel links ueber dem Foto (rechts 80 px = Hoechstabstand frei), unten 48 px Foto
    b.add("kopf", "Container",
          {"padding": pad(0, 48, RAND, 80), "backgroundColor": "#2f4858",
           "overlay": {"farbe": "#2f4858", "deckkraft": 86}},
          {"url": "medien:platzhalter-2x1.png", "alt": "Stimmungsfoto als Hintergrund des Kopfes", "width": 600,
           "height": 300, "childrenIds": ["marke_logo", "marke_wort", "kopf_titel", "kopf_linie", "kopf_ausgabe"]},
          {"data/style/backgroundColor": "zweit", "data/style/overlay/farbe": "zweit"})

    b.neu("band_titel", ueber("[Die Überschrift zum Hauptthema]", 22, rolle="auf_zweit", zh=1.2,
                              p=pad(28, 10, 0, 24), level="h2"), oben=False)
    b.neu("band_text", text("[Zwei, drei Sätze: was es Neues gibt und warum es sich lohnt.]", 15,
                            rolle="auf_zweit", p=pad(0, 28, 0, 24), zh=1.6), oben=False)
    b.neu("band_bild", bild(284, 284, "Foto zum Hauptthema"), oben=False)
    b.spalten("band", [["band_titel", "band_text"], ["band_bild"]], 0, pad(0, 0, RAND, 0), hg="#2f4858",
              hg_rolle="zweit", ausr="middle")

    w = spalten_breite(2, 24)          # 256 x 192
    b.neu("neben_titel", ueber("[Das zweite Thema]", 20, tx, zh=1.2, p=pad(0, 8, 0, 0), level="h3"), oben=False)
    b.neu("neben_text", text("[Zwei Sätze dazu – zum Beispiel ein Service, den man online buchen kann.]", 15,
                             p=pad(0, 0, 0, 0), zh=1.6), oben=False)
    b.neu("neben_bild", bild(w, 192, "Foto zum zweiten Thema"), oben=False)
    b.spalten("neben", [["neben_titel", "neben_text"], ["neben_bild"]], 24, pad(32, 32), ausr="middle")

    b.neu("tipps_titel", ueber("[Drei Tipps für …]", 20, rolle="auf_akzent", zh=1.2, p=pad(0, 10, 0, 0),
                               level="h3"), oben=False)
    b.neu("tipps_text", text("**1** · [Erster Tipp]  \n**2** · [Zweiter Tipp]  \n**3** · [Dritter Tipp]", 15,
                             rolle="auf_akzent", p=pad(0, 0, 0, 0), zh=1.8), oben=False)
    b.karte("tipps", ["tipps_titel", "tipps_text"], pad(24, 24, 8, 8), "#c2410c", hg_rolle="akzent")

    b.neu("schluss_text", text("[Ein Satz zum Schluss – eine Einladung, vorbeizukommen.]", 16, p=pad(32, 8), zh=1.6))
    b.neu("knopf", knopf("[Jetzt vorbeikommen]", pad(8, 32), stil="rounded"))
    b.neu("fuss", meta("[Telefon] · [Website] · @[instagram]", "#5f5a55", ausr="center", p=pad(0, 24)))
    return b


# ---------- 7 tech (M7, Spec §3.1) ----------

INK, SURFACE, LINE, HELL, LEISE = "#080b13", "#111725", "#263044", "#f3f7fc", "#9caabe"


def tech() -> Bau:
    b = Bau("tech", "Tech: dunkel und präzise - Wortmarke mit Lichtschein, Rubrik-Pill, nummerierte Dachzeilen, "
            "Signal-Grafik, Karten.", INK, LEISE, INK, "oxanium", "rajdhani", dunkel=True)
    b.neu("marke_logo", logo(140, "left", pad(0, 40, 0, 0)), oben=False)
    b.neu("marke_wort", ueber("[Laden]", 20, HELL, versal=True, sperr=2, zh=1.1, p=pad(0, 40, 0, 0), level="h3"),
          oben=False)
    b.neu("kopf_pill", text("● [Rubrik] × [Thema]", 12, rolle="akzent_text", anzeige=True, fett=True,
                            p=pad(0, 18, 0, 0), sperr=2, versal=True, md=False), oben=False)
    b.neu("kopf_titel", ueber("[Eure Überschrift: was diesen Monat neu ist]", 40, HELL, sperr=-1, zh=1.05,
                              p=pad(0, 18, 0, 0), level="h1"), oben=False)
    b.neu("kopf_text", text("[Ein, zwei Sätze, die das Versprechen dieser Ausgabe auf den Punkt bringen.]", 20,
                            LEISE, p=pad(0, 28, 0, 0), zh=1.45), oben=False)
    b.neu("knopf", knopf("[Gespräch starten] ↗", pad(0, 0, 0, 0), stil="rounded", groesse=16), oben=False)
    b.add("kopf", "Container", {"padding": pad(0, 72), "backgroundColor": INK},
          {"url": "", "alt": "", "width": 600, "height": 420, "grafik": True,
           "childrenIds": ["marke_logo", "marke_wort", "kopf_pill", "kopf_titel", "kopf_text", "knopf"]},
          {"data/props/url": "glow_bild"})

    def dachzeile(bid, nr, t, oben_abstand=56):
        b.neu(bid, ueber(f"{nr} / {t}", 12, rolle="akzent_text", versal=True, sperr=2, zh=1.3,
                         p=pad(oben_abstand, 14), level="h3"))

    b.neu("linie1", linie(LINE, p=pad(0, 0)))
    dachzeile("a1_dach", "01", "[Was wir tun]")
    b.neu("a1_titel", ueber("[Ein Satz, der euer Angebot zusammenfasst]", 34, HELL, sperr=-1, zh=1.1, p=pad(0, 16)))
    b.neu("a1_text", text("[Zwei Sätze Einordnung: für wen, was anders ist.]", 20, LEISE, p=pad(0, 28), zh=1.45))
    for n in (1, 2):
        b.neu(f"karte{n}_titel", ueber(f"[Leistung {n}]", 24, HELL, zh=1.2, p=pad(0, 8, 0, 0), level="h3"),
              oben=False)
        b.neu(f"karte{n}_text", text("[Ein, zwei Sätze, was man davon hat.]", 18, LEISE, p=pad(0, 0, 0, 0), zh=1.45),
              oben=False)
        b.karte(f"karte{n}", [f"karte{n}_titel", f"karte{n}_text"], pad(28, 28, 28, 28), SURFACE, rand=LINE, rund=12)
        b.neu(f"karte{n}_abstand", abstand(16))

    dachzeile("a2_dach", "02", "[So funktioniert's]", 40)
    b.neu("a2_titel", ueber("[Was als Nächstes kommt]", 34, HELL, sperr=-1, zh=1.1, p=pad(0, 24)))
    b.add("signal", "Image", {"padding": pad(0, 24), "textAlign": "center"},
          {"url": "", "alt": "", "width": BREITE, "height": 380, "grafik": True, "contentAlignment": "middle"},
          {"data/props/url": "signal_bild"})
    b.neu("a2_text", text("[Drei kurze Sätze, wie es abläuft – vom ersten Kontakt bis zum Ergebnis.]", 20, LEISE,
                          p=pad(0, 8), zh=1.45))

    dachzeile("a3_dach", "03", "[Kontakt]")
    b.neu("kontakt_titel", ueber("[Lasst uns sprechen]", 24, HELL, zh=1.2, p=pad(0, 10, 0, 0), level="h3"),
          oben=False)
    b.neu("kontakt_text", text("[Telefon]  \n[Website]  \n@[instagram]", 18, LEISE, p=pad(0, 0, 0, 0), zh=1.6),
          oben=False)
    b.karte("kontakt", ["kontakt_titel", "kontakt_text"], pad(28, 28, 28, 28), SURFACE, rand=LINE,
            rand_rolle="akzent_rahmen", rund=12)

    b.neu("fuss_linie", linie(LINE, p=pad(56, 0)))
    b.neu("fuss_laden", text("[Laden]", 14, HELL, anzeige=True, fett=True, p=pad(0, 0, 0, 0), sperr=2, versal=True,
                             md=False, text_rolle="laden"), oben=False)
    b.neu("fuss_web", meta("[Website]", LEISE, ausr="right", p=pad(0, 0, 0, 0)), oben=False)
    b.spalten("fuss", [["fuss_laden"], ["fuss_web"]], 16, pad(20, 32), ausr="middle")
    return b


VORLAGEN = {"studio": studio, "zeitung": zeitung, "firmenblatt": firmenblatt, "minimal": minimal,
            "klassik": klassik, "bildkopf": bildkopf, "tech": tech}


def main() -> int:
    for name, baue in VORLAGEN.items():
        daten = baue().json()
        assert daten["name"] == name
        (ORDNER / f"{name}.json").write_text(json.dumps(daten, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(name, len(daten["bloecke"]), "Bloecke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
