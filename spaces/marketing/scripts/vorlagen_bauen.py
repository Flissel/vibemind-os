"""Baut die fuenf Startvorlagen als Blockdokumente (Spec 2026-09-29-newsletter-
bilder-und-gestaltung-design.md §5) und schreibt vorlagen/newsletter/<name>.json.
Eine Quelle fuer alle fuenf: dieselben Bausteine, dieselben Farben ('dunkel').
    python -m spaces.marketing.scripts.vorlagen_bauen"""
from __future__ import annotations

import json
from pathlib import Path

from spaces.marketing.claw.bildplaetze import platzhalter_name

ORDNER = Path(__file__).resolve().parents[1] / "vorlagen" / "newsletter"
FLAECHE, GRUND, KARTE = "#1d3b39", "#0f2422", "#16302d"
TEXT, HELL, LEISE, AKZENT = "#cfe3df", "#e9fbf6", "#8aa3a0", "#5eead4"
LINK = "https://vibemind.space"


def pad(t, b, l=40, r=40):
    return {"top": t, "bottom": b, "left": l, "right": r}


NULL = pad(0, 0, 0, 0)


class Bau:
    def __init__(self):
        self.bloecke: dict = {}
        self.oben: list = []

    def neu(self, bid: str, block: dict, oben: bool = True) -> str:
        assert bid not in self.bloecke, bid
        self.bloecke[bid] = block
        if oben:
            self.oben.append(bid)
        return bid

    def dokument(self) -> dict:
        return {"root": {"type": "EmailLayout", "data": {
            "backdropColor": FLAECHE, "canvasColor": GRUND, "textColor": TEXT,
            "fontFamily": "MODERN_SANS", "childrenIds": list(self.oben)}}, **self.bloecke}


def ueberschrift(text, level="h1", farbe=HELL, p=None, ausr="left", groesse=None):
    style = {"color": farbe, "fontWeight": "bold", "textAlign": ausr, "padding": p or pad(0, 12)}
    if groesse:
        style["fontSize"] = groesse
    return {"type": "Heading", "data": {"style": style, "props": {"level": level, "text": text}}}


def text(t, groesse=16, farbe=None, fett=False, p=None, ausr="left", md=True):
    style = {"fontSize": groesse, "fontWeight": "bold" if fett else "normal", "textAlign": ausr,
             "padding": p or pad(0, 16)}
    if farbe:
        style["color"] = farbe
    return {"type": "Text", "data": {"style": style, "props": {"text": t, "markdown": md}}}


def marke(t, p=None, ausr="left"):
    return text(t.upper(), 12, AKZENT, True, p or pad(0, 8), ausr, md=False)


def bild(w, h, alt, p=None, ausr="center"):
    return {"type": "Image", "data": {"style": {"padding": p or NULL, "textAlign": ausr},
            "props": {"url": "medien:" + platzhalter_name(w, h), "alt": alt, "width": w, "height": h,
                      "contentAlignment": "middle"}}}


def knopf(t, url=LINK, p=None, ausr="left"):
    return {"type": "Button", "data": {"style": {"textAlign": ausr, "padding": p or pad(8, 32), "fontSize": 16},
            "props": {"text": t, "url": url, "buttonBackgroundColor": AKZENT, "buttonTextColor": GRUND,
                      "buttonStyle": "rounded", "size": "large"}}}


def trenner(p=None):
    return {"type": "Divider", "data": {"style": {"padding": p or pad(8, 24)}, "props": {"lineColor": "#2c4f4b", "lineHeight": 1}}}


def abstand(h):
    return {"type": "Spacer", "data": {"style": {}, "props": {"height": h}}}


def spalten(bau: Bau, bid: str, listen: list[list[str]], luecke=16, p=None):
    anzahl = len(listen)
    cols = [{"childrenIds": l} for l in listen] + [{"childrenIds": []}] * (3 - anzahl)
    return bau.neu(bid, {"type": "ColumnsContainer", "data": {"style": {"padding": p or pad(0, 24, 40, 40)},
                   "props": {"columnsCount": anzahl, "columnsGap": luecke, "contentAlignment": "top", "columns": cols}}})


def rahmen(bau: Bau, bid: str, kinder: list[str], p=None):
    return bau.neu(bid, {"type": "Container", "data": {"style": {"backgroundColor": KARTE, "borderRadius": 12,
                   "padding": p or pad(24, 24, 16, 16)}, "props": {"childrenIds": kinder}}})


def kopfzeile(b: Bau, zeile: str):
    b.neu("marke_kopf", text("**VibeMind**", 18, HELL, False, pad(32, 4)))
    b.neu("kopfzeile", text(zeile, 12, LEISE, False, pad(0, 24), md=False))


def fuss(b: Bau):
    b.neu("fuss_trenner", trenner(pad(24, 16)))
    b.neu("fuss_gruss", text("Bis bald,  \n**Felix** · VibeMind", 15, TEXT, False, pad(0, 8)))
    b.neu("fuss_link", text(f"[vibemind.space]({LINK})", 13, LEISE, False, pad(0, 40)))


def newsletter() -> Bau:
    b = Bau()
    kopfzeile(b, "NEWSLETTER · AUSGABE [Nr] · [Monat Jahr]")
    b.neu("kopf_bild", bild(600, 300, "Stimmungsbild zum Thema dieser Ausgabe"))
    b.neu("kopf_marke", marke("Diese Ausgabe", pad(32, 8)))
    b.neu("kopf_titel", ueberschrift("Dein Thema in einem starken Satz"))
    b.neu("einleitung", text("Schön, dass du dabei bist. Erzähl in zwei, drei Sätzen, worum es geht "
                             "und warum es sich lohnt, weiterzulesen.", 17, TEXT, False, pad(0, 32)))
    for n, alt in ((1, "Bild zum ersten Thema"), (2, "Bild zum zweiten Thema")):
        b.neu(f"t{n}_bild", bild(252, 189, alt, pad(0, 12, 0, 0), "left"), oben=False)
        b.neu(f"t{n}_marke", marke(f"Thema {n}", pad(0, 6, 0, 0)), oben=False)
        b.neu(f"t{n}_titel", ueberschrift(["Erstes", "Zweites"][n - 1] + " Thema", "h3", HELL, pad(0, 8, 0, 0)), oben=False)
        b.neu(f"t{n}_text", text("Zwei Sätze: was passiert ist und was es für dich bedeutet.", 15, TEXT, False,
                                 pad(0, 8, 0, 0)), oben=False)
    spalten(b, "themen", [["t1_bild", "t1_marke", "t1_titel", "t1_text"], ["t2_bild", "t2_marke", "t2_titel", "t2_text"]])
    b.neu("zahl_rahmen_zahl", text("**3×** schneller", 34, AKZENT, False, pad(0, 4, 0, 0), "center"), oben=False)
    b.neu("zahl_rahmen_text", text("Eine Zahl, die hängen bleibt – mit einem Satz Einordnung.", 15, TEXT, False,
                                   pad(0, 0, 0, 0), "center"), oben=False)
    rahmen(b, "zahl", ["zahl_rahmen_zahl", "zahl_rahmen_text"])
    b.neu("abstand_1", abstand(24))
    b.neu("ausblick_bild", bild(520, 293, "Bild zum Ausblick", pad(0, 16)))
    b.neu("ausblick_marke", marke("Ausblick"))
    b.neu("ausblick_titel", ueberschrift("Was als Nächstes kommt", "h2"))
    b.neu("ausblick_text", text("Ein Absatz über das, worauf man sich freuen kann.", 16, TEXT))
    b.neu("knopf", knopf("Mehr erfahren"))
    fuss(b)
    return b


def ankuendigung() -> Bau:
    b = Bau()
    kopfzeile(b, "ANKÜNDIGUNG")
    b.neu("kopf_bild", bild(600, 300, "Großes Bild zur Neuigkeit"))
    b.neu("kopf_marke", marke("Neu bei VibeMind", pad(32, 8)))
    b.neu("kopf_titel", ueberschrift("Die Neuigkeit in einem Satz", "h1", HELL, pad(0, 16), groesse=36))
    b.neu("einleitung", text("Was ist neu, für wen ist es gedacht, ab wann gilt es? Drei Sätze reichen.",
                             18, TEXT, False, pad(0, 24)))
    b.neu("knopf_oben", knopf("Jetzt ansehen", p=pad(0, 32)))
    b.neu("detail_bild", bild(520, 293, "Detailbild zur Neuigkeit", pad(0, 16)))
    b.neu("zitat_text", text("„Ein Satz von jemandem, der es schon ausprobiert hat.“", 20, HELL, False,
                             pad(0, 8, 0, 0)), oben=False)
    b.neu("zitat_von", text("— Name, Rolle", 13, LEISE, False, pad(0, 0, 0, 0), md=False), oben=False)
    rahmen(b, "zitat", ["zitat_text", "zitat_von"])
    b.neu("abstand_1", abstand(16))
    fuss(b)
    return b


def einladung() -> Bau:
    b = Bau()
    kopfzeile(b, "EINLADUNG")
    b.neu("kopf_bild", bild(600, 300, "Stimmungsbild zur Veranstaltung"))
    b.neu("kopf_marke", marke("Du bist eingeladen", pad(32, 8)))
    b.neu("kopf_titel", ueberschrift("Name der Veranstaltung"))
    b.neu("eckdaten", text("**Wann:** [Datum], [Uhrzeit]  \n**Wo:** [Ort oder Online-Link]  \n**Dauer:** [Dauer]",
                           16, TEXT, False, pad(0, 24)))
    b.neu("erwartet_titel", ueberschrift("Was dich erwartet", "h2", HELL, pad(8, 16)))
    for n, alt in ((1, "Bild zum ersten Programmpunkt"), (2, "Bild zum zweiten Programmpunkt"),
                   (3, "Bild zum dritten Programmpunkt")):
        b.neu(f"p{n}_bild", bild(162, 162, alt, pad(0, 10, 0, 0), "left"), oben=False)
        b.neu(f"p{n}_titel", ueberschrift(f"Punkt {n}", "h3", HELL, pad(0, 4, 0, 0)), oben=False)
        b.neu(f"p{n}_text", text("Ein Satz dazu.", 14, TEXT, False, pad(0, 0, 0, 0)), oben=False)
    spalten(b, "programm", [[f"p{n}_bild", f"p{n}_titel", f"p{n}_text"] for n in (1, 2, 3)])
    b.neu("knopf", knopf("Jetzt zusagen", p=pad(8, 32), ausr="center"))
    fuss(b)
    return b


def produkt_neuheit() -> Bau:
    b = Bau()
    kopfzeile(b, "PRODUKT-NEUHEIT")
    b.neu("kopf_marke", marke("Neu", pad(8, 8)))
    b.neu("kopf_titel", ueberschrift("Produktname – was es besser macht", "h1", HELL, pad(0, 16)))
    b.neu("produkt_bild", bild(520, 293, "Das neue Produkt im Einsatz", pad(0, 24)))
    b.neu("einleitung", text("Zwei Sätze: welches Problem es löst und für wen.", 17, TEXT, False, pad(0, 24)))
    for n, alt in ((1, "Symbolbild erster Vorteil"), (2, "Symbolbild zweiter Vorteil"), (3, "Symbolbild dritter Vorteil")):
        b.neu(f"v{n}_bild", bild(162, 162, alt, pad(0, 10, 0, 0), "left"), oben=False)
        b.neu(f"v{n}_titel", ueberschrift(f"Vorteil {n}", "h3", HELL, pad(0, 4, 0, 0)), oben=False)
        b.neu(f"v{n}_text", text("Ein kurzer Satz.", 14, TEXT, False, pad(0, 0, 0, 0)), oben=False)
    spalten(b, "vorteile", [[f"v{n}_bild", f"v{n}_titel", f"v{n}_text"] for n in (1, 2, 3)])
    b.neu("knopf", knopf("Ausprobieren", p=pad(8, 32), ausr="center"))
    fuss(b)
    return b


def kurzer_hinweis() -> Bau:
    b = Bau()
    kopfzeile(b, "KURZ NOTIERT")
    b.neu("banner", bild(552, 184, "Schmales Bannerbild zum Hinweis", pad(0, 24, 24, 24)))
    b.neu("titel", ueberschrift("Der Hinweis in einem Satz", "h2"))
    b.neu("text", text("Zwei, drei Sätze. Mehr braucht ein kurzer Hinweis nicht.", 16, TEXT))
    b.neu("knopf", knopf("Details"))
    fuss(b)
    return b


VORLAGEN = {
    "newsletter": ("Klassischer Newsletter: großes Kopfbild, zwei Themen mit Bildern, Zahl des Monats, Ausblick.", newsletter),
    "ankuendigung": ("Ankündigung: Kopfbild über die volle Breite, starke Überschrift, Detailbild, Zitat.", ankuendigung),
    "einladung": ("Einladung: Stimmungsbild, Eckdaten, drei Programmpunkte mit Bildern, Zusage-Knopf.", einladung),
    "produkt-neuheit": ("Produkt-Neuheit: Produktbild, Nutzen, drei Vorteile mit Symbolbildern.", produkt_neuheit),
    "kurzer-hinweis": ("Kurzer Hinweis: schmales Banner, ein Satz, ein Knopf.", kurzer_hinweis),
}


def main() -> int:
    for name, (beschreibung, baue) in VORLAGEN.items():
        daten = {"name": name, "beschreibung": beschreibung, "bloecke": baue().dokument()}
        (ORDNER / f"{name}.json").write_text(json.dumps(daten, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(name, len(daten["bloecke"]), "Bloecke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
