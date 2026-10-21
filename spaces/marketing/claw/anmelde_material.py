"""Druck- und Webmaterial fuer die Newsletter-Anmeldung per Mail (21.10.2026).

Betreiber-Entscheid: die Anmeldung laeuft ueber die Mail (Double-Opt-in in
sales-claw, sales-mcp/newsletter_anmeldung.py). Wer anmelden will, schickt eine
Mail mit festem Betreff an das Postfach des Ladens und bestaetigt danach per
Antwort. Dieses Modul baut, was Menschen dorthin fuehrt:

  * ein Aufsteller (A6 hoch) und eine Flyer-Rueckseite (A6 quer) als PDF mit
    QR-Code - fuer Messe, Termin, Theke;
  * Link und Knopftext fuer die Website (kein Formular, ein mailto-Link).

FEST, NICHT VOM AGENTEN: Adresse, Betreff, QR-Inhalt, Rueckfallzeile und
Datenschutzsatz. Ein gedruckter QR-Code mit falscher Adresse ist nicht mehr
zurueckzuholen - deshalb kommt die Adresse aus der Umgebung
(NEWSLETTER_ANMELDE_ADRESSE), und der Agent darf nur Ueberschrift und Satz
vorschlagen.

ENGLISCH ERST MIT SCHALTER: sales-claw erkennt am 21.10.2026 nur den Betreff
"Anmelden". Ein englischer Aufsteller mit "Subscribe" fuehrte ins Leere. Er
wird erst mit NEWSLETTER_ANMELDE_EN=1 gesetzt (wenn sales-claw "Subscribe"
versteht). Zweisprachig nutzt bis dahin den deutschen Betreff.
"""
import io
import os
from urllib.parse import quote

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A6, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle

from spaces.marketing.claw import pdf

ARTEN = ("aufsteller", "flyer", "website")
SPRACHEN = ("de", "en", "de_en")
TITEL_MAX = 40
SATZ_MAX = 140

BETREFF = {"de": "Anmelden", "en": "Subscribe"}

TEXTE = {
    "de": {
        "titel": "Unser Newsletter",
        "satz": "QR-Code scannen, Mail abschicken. Die Bestätigung kommt per Mail.",
        "rueckfall": "Ohne Kamera: Mail an {adresse}, Betreff „{betreff}“",
        "datenschutz": ("Ihre Adresse nutzen wir nur für den Newsletter. "
                        "Abmelden jederzeit mit dem Betreff „{abmelden}“."),
        "mailtext": "Bitte nehmen Sie mich in den Newsletter auf.",
        "knopf": "Newsletter abonnieren",
        "hinweis": "Öffnet Ihr Mailprogramm. Danach kommt eine Mail zum Bestätigen.",
    },
    "en": {
        "titel": "Our newsletter",
        "satz": "Scan the code, send the email. Confirmation arrives by email.",
        "rueckfall": "No camera: email {adresse}, subject “{betreff}”",
        "datenschutz": ("We use your address for the newsletter only. "
                        "Unsubscribe any time with the subject “{abmelden}”."),
        "mailtext": "Please add me to the newsletter.",
        "knopf": "Subscribe to the newsletter",
        "hinweis": "Opens your email app. You will then get an email to confirm.",
    },
}


def adresse() -> str:
    return os.environ.get("NEWSLETTER_ANMELDE_ADRESSE", "").strip()


def englisch_frei() -> bool:
    return os.environ.get("NEWSLETTER_ANMELDE_EN", "").strip() == "1"


def marke() -> str:
    return os.environ.get("NEWSLETTER_MARKE", "VibeMind").strip() or "VibeMind"


def pruefen(art: str, sprache: str) -> str:
    """'' = in Ordnung, sonst der Grund (fuer den Agenten lesbar)."""
    if art not in ARTEN:
        return f"Art '{art}' gibt es nicht. Erlaubt: {', '.join(ARTEN)}."
    if sprache not in SPRACHEN:
        return f"Sprache '{sprache}' gibt es nicht. Erlaubt: {', '.join(SPRACHEN)}."
    if "@" not in adresse():
        return ("Keine Anmeldeadresse eingerichtet (NEWSLETTER_ANMELDE_ADRESSE). "
                "Ohne Adresse entsteht kein QR-Code - die Adresse waehlt nicht der Agent.")
    if sprache == "en" and not englisch_frei():
        return ("Die englische Fassung ist noch gesperrt: sales-claw erkennt bisher nur den "
                "Betreff 'Anmelden'. Nimm 'de' oder 'de_en' (zweisprachig, deutscher Betreff).")
    return ""


def _betreff_sprache(sprache: str) -> str:
    return "en" if sprache == "en" else "de"


def anmelde_link(sprache: str, mit_text: bool = True) -> str:
    """mailto mit festem Betreff. Gedruckt OHNE Mailtext (`mit_text=False`):
    ein kurzer Inhalt gibt einen QR-Code mit groeberen Punkten, der auf A6
    sicherer gelesen wird; der Betreff allein loest die Anmeldung aus."""
    s = _betreff_sprache(sprache)
    link = f"mailto:{adresse()}?subject={quote(BETREFF[s])}"
    return link + (f"&body={quote(TEXTE[s]['mailtext'])}" if mit_text else "")


def website(sprache: str) -> dict:
    """Link, Knopftext und ein HTML-Schnipsel fuer die Website."""
    s = _betreff_sprache(sprache)
    link = anmelde_link(sprache)
    t = TEXTE[s]
    schnipsel = (f'<a href="{link}">{t["knopf"]}</a>'
                 f'<br><small>{t["hinweis"]}</small>')
    return {"link": link, "knopf": t["knopf"], "hinweis": t["hinweis"], "html": schnipsel,
            "betreff": BETREFF[s], "adresse": adresse()}


def _texte(sprache: str, titel: str, satz: str) -> list[dict]:
    """Eine oder zwei Textgruppen (zweisprachig: deutsch zuerst)."""
    betreff = BETREFF[_betreff_sprache(sprache)]
    gruppen = []
    for s in (("de", "en") if sprache == "de_en" else (sprache,)):
        t = dict(TEXTE[s])
        if s == ("de" if sprache != "en" else "en"):
            t["titel"] = titel or t["titel"]
            t["satz"] = satz or t["satz"]
        t["rueckfall"] = t["rueckfall"].format(adresse=adresse(), betreff=betreff)
        abmelden = "Unsubscribe" if (s == "en" and englisch_frei()) else "Abmelden"
        t["datenschutz"] = t["datenschutz"].format(abmelden=abmelden)
        gruppen.append(t)
    return gruppen


def _qr(leinwand, text: str, x: float, y: float, groesse: float) -> None:
    """QR auf weissem Feld mit Ruhezone - dunkel auf hell liest jede Kamera."""
    leinwand.setFillColor(colors.white)
    leinwand.roundRect(x - 3 * mm, y - 3 * mm, groesse + 6 * mm, groesse + 6 * mm, 2 * mm,
                       stroke=0, fill=1)
    w = QrCodeWidget(text, barLevel="M")
    x0, y0, x1, y1 = w.getBounds()
    d = Drawing(groesse, groesse, transform=[groesse / (x1 - x0), 0, 0, groesse / (y1 - y0), 0, 0])
    d.add(w)
    renderPDF.draw(d, leinwand, x, y)


def _absatz(leinwand, text: str, stil: ParagraphStyle, x: float, y_oben: float, breite: float) -> float:
    p = Paragraph(pdf._sicher(text), stil)
    _, h = p.wrap(breite, 200 * mm)
    p.drawOn(leinwand, x, y_oben - h)
    return y_oben - h


def _hoehe(texte: list, stil: ParagraphStyle, breite: float, abstand: float) -> float:
    return sum(Paragraph(pdf._sicher(t), stil).wrap(breite, 200 * mm)[1] + abstand for t in texte)


def _block(leinwand, texte: list, stil: ParagraphStyle, x: float, y_oben: float, breite: float,
           abstand: float) -> float:
    for t in texte:
        y_oben = _absatz(leinwand, t, stil, x, y_oben, breite) - abstand
    return y_oben


def bauen(art: str, sprache: str, titel: str = "", satz: str = "", gestalt: dict | None = None) -> bytes:
    """Das PDF (eine Seite). Wirft ValueError bei unzulaessigen Eingaben.

    Gesetzt wird von unten nach oben: erst wird gemessen, was unten stehen
    MUSS (Datenschutz, Rueckfallzeile), der QR-Code bekommt den Rest. So
    ueberlappt nichts, auch nicht zweisprachig oder mit langem Satz."""
    grund = pruefen(art, sprache)
    if grund or art == "website":
        raise ValueError(grund or "website ist kein PDF")
    titel = " ".join(str(titel or "").split())[:TITEL_MAX]
    satz = " ".join(str(satz or "").split())[:SATZ_MAX]
    f = pdf._farben("dunkel", gestalt)
    normal, fett = pdf.schriften()
    seite = A6 if art == "aufsteller" else landscape(A6)
    breite, hoehe = seite
    puffer = io.BytesIO()
    c = canvas.Canvas(puffer, pagesize=seite)
    c.setTitle(f"{marke()} Newsletter")
    c.setFillColor(f["grund"])
    c.rect(0, 0, breite, hoehe, stroke=0, fill=1)
    rand = 7 * mm
    luft = 3 * mm                     # Ruhezone um den QR (weisses Feld)
    gruppen = _texte(sprache, titel, satz)
    zwei = len(gruppen) == 2
    stil_titel = ParagraphStyle("t", fontName=fett, fontSize=13 if zwei else 16,
                                leading=(15 if zwei else 19), textColor=f["text_hell"])
    stil_satz = ParagraphStyle("s", fontName=normal, fontSize=8 if zwei else 9,
                               leading=(10 if zwei else 12), textColor=f["text"])
    stil_klein = ParagraphStyle("k", fontName=normal, fontSize=6, leading=7.5,
                                textColor=f["text_leise"])
    link = anmelde_link(sprache, mit_text=False)
    rueckfall = [g["rueckfall"] for g in gruppen]
    datenschutz = [g["datenschutz"] for g in gruppen]

    c.setFillColor(f["akzent"])
    c.setFont(fett, 9)
    marken_y = hoehe - rand - 3 * mm
    c.drawString(rand, marken_y, marke())
    oben = marken_y - 4 * mm          # alles darunter

    if art == "aufsteller":
        tb = breite - 2 * rand
        y = oben
        for g in gruppen:
            y = _absatz(c, g["titel"], stil_titel, rand, y, tb) - 1.5 * mm
            y = _absatz(c, g["satz"], stil_satz, rand, y, tb) - 2.5 * mm
        h_ds = _hoehe(datenschutz, stil_klein, tb, 1 * mm)
        h_rf = _hoehe(rueckfall, stil_klein, tb, 1 * mm)
        _block(c, datenschutz, stil_klein, rand, rand + h_ds, tb, 1 * mm)
        rf_oben = rand + h_ds + 2 * mm + h_rf
        _block(c, rueckfall, stil_klein, rand, rf_oben, tb, 1 * mm)
        platz = y - (rf_oben + 3 * mm) - 2 * luft
        qr = min(tb - 2 * luft - 6 * mm, platz)
        _qr(c, link, (breite - qr) / 2, rf_oben + 3 * mm + luft, qr)
    else:
        tb_voll = breite - 2 * rand
        h_ds = _hoehe(datenschutz, stil_klein, tb_voll, 1 * mm)
        _block(c, datenschutz, stil_klein, rand, rand + h_ds, tb_voll, 1 * mm)
        unten = rand + h_ds + 2 * mm
        qr = oben - unten - 2 * luft
        _qr(c, link, rand + luft, unten + luft, qr)
        x = rand + qr + 2 * luft + 5 * mm
        tb = breite - x - rand
        y = oben
        for g in gruppen:
            y = _absatz(c, g["titel"], stil_titel, x, y, tb) - 1.5 * mm
            y = _absatz(c, g["satz"], stil_satz, x, y, tb) - 2.5 * mm
        _block(c, rueckfall, stil_klein, x, y, tb, 1 * mm)
    c.showPage()
    c.save()
    return puffer.getvalue()
