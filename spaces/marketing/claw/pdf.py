"""PDF-Satz fuer Kampagnen — das erste Format, das sales-claw anhaengen kann.

WARUM PDF UND NICHT DAS MARKDOWN, DAS WIR SCHON HATTEN. Gemessen am
laufenden `sales-mcp` (04.09.2026): `medien.pruefe("post.md")` antwortet
"Endung '.md' ist nicht zugelassen. Erlaubt: .ics, .jpeg, .jpg, .mp3, .mp4,
.ogg, .pdf, .png". Ein Markdown-Entwurf liegt im richtigen Ordner und ist
trotzdem unanhaengbar — `medien_liste` zeigt ihn nicht einmal. PDF ist das
erste Format aus dieser Liste, das sich aus Text erzeugen laesst.

DAS AUSSEHEN KOMMT VOM PITCH-DECK, nicht vom internen Dashboard. Der Deck
(`pitch-deck-2026/vibemind-pitch.html`) ist das, was Aussenstehende von
VibeMind sehen: dunkles Teal `#0f2422`, Mint-Akzent `#5eead4`, Gold
`#fbbf24`, Inter. Ein Newsletter im selben Gewand wird wiedererkannt; einer
im Dashboard-Blau nicht. Dunkler Grund ist Absicht — diese Unterlagen werden
auf Telefonen gelesen und weitergeschickt, nicht gedruckt.

Nur `reportlab` und `PIL`, beide vorhanden. Keine Kopfzeile aus dem Netz,
keine Schriftdatei aus dem Netz: ein Entwurf, der ohne Internet nicht
entsteht, ist im Betrieb wertlos.
"""
import io
import os
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether,
                                PageTemplate, Paragraph, Spacer, Table,
                                TableStyle)

# Farben aus dem Pitch-Deck, nicht geraten.
GRUND = colors.HexColor("#0f2422")
FLAECHE = colors.HexColor("#1d3b39")
AKZENT = colors.HexColor("#5eead4")
GOLD = colors.HexColor("#fbbf24")
TEXT = colors.HexColor("#cfe3df")
TEXT_HELL = colors.HexColor("#e9fbf6")
TEXT_LEISE = colors.HexColor("#8aa3a0")

RAND = 20 * mm
KOPF_HOEHE = 34 * mm
FUSS_HOEHE = 14 * mm

# Inter gibt es hier nicht; Segoe UI ist die naechste Verwandte und liegt auf
# jedem Windows. Fehlt auch die, faellt der Satz auf Helvetica zurueck — nicht
# so schoen, aber immer vorhanden. Ein PDF, das an einer fehlenden Schrift
# scheitert, waere die schlechtere Antwort.
_SCHRIFT_KANDIDATEN = (
    ("VibeSans", r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
    ("VibeSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
)
_registriert = None


def schriften() -> tuple:
    """(normal, fett) — einmal registriert, danach gemerkt."""
    global _registriert
    if _registriert is not None:
        return _registriert
    for name, normal, fett in _SCHRIFT_KANDIDATEN:
        if os.path.exists(normal) and os.path.exists(fett):
            try:
                pdfmetrics.registerFont(TTFont(name, normal))
                pdfmetrics.registerFont(TTFont(name + "-Bold", fett))
                _registriert = (name, name + "-Bold")
                return _registriert
            except Exception:  # noqa: BLE001 — Rueckfall ist der Zweck
                continue
    _registriert = ("Helvetica", "Helvetica-Bold")
    return _registriert


def _sicher(text: str) -> str:
    """Text fuer einen reportlab-Absatz entschaerfen.

    `Paragraph` liest ein Mini-Markup: ein `<` aus einem Entwurf wuerde als
    Auszeichnung gelesen und im besten Fall verschluckt, im schlechteren
    bricht der Satz ab. Ein Kampagnentext ist Fliesstext, kein Markup.
    """
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _stile() -> dict:
    normal, fett = schriften()
    return {
        "titel": ParagraphStyle("titel", fontName=fett, fontSize=24, leading=29,
                                textColor=TEXT_HELL, alignment=TA_LEFT),
        "unter": ParagraphStyle("unter", fontName=normal, fontSize=11.5, leading=16,
                                textColor=AKZENT, spaceBefore=3),
        "fliess": ParagraphStyle("fliess", fontName=normal, fontSize=11, leading=17,
                                 textColor=TEXT, spaceAfter=9),
        "punkt": ParagraphStyle("punkt", fontName=normal, fontSize=11, leading=17,
                                textColor=TEXT, leftIndent=10, bulletIndent=1,
                                spaceAfter=5),
        "rubrik": ParagraphStyle("rubrik", fontName=fett, fontSize=9.5, leading=13,
                                 textColor=GOLD, spaceBefore=16, spaceAfter=5),
        "klein": ParagraphStyle("klein", fontName=normal, fontSize=8.5, leading=12.5,
                                textColor=TEXT_LEISE, spaceAfter=3),
        "handlung": ParagraphStyle("handlung", fontName=fett, fontSize=12.5,
                                   leading=18, textColor=GRUND,
                                   spaceBefore=2, spaceAfter=2,
                                   leftIndent=6, rightIndent=6),
    }


def _kopf_und_grund(leinwand, dokument) -> None:
    """Dunkler Grund auf jeder Seite, Kopfband nur auf der ersten."""
    breite, hoehe = A4
    leinwand.saveState()
    leinwand.setFillColor(GRUND)
    leinwand.rect(0, 0, breite, hoehe, stroke=0, fill=1)
    if dokument.page == 1:
        leinwand.setFillColor(FLAECHE)
        leinwand.rect(0, hoehe - KOPF_HOEHE, breite, KOPF_HOEHE, stroke=0, fill=1)
        leinwand.setFillColor(AKZENT)
        leinwand.rect(0, hoehe - KOPF_HOEHE - 1.6, breite, 1.6, stroke=0, fill=1)
        normal, fett = schriften()
        leinwand.setFont(fett, 13)
        leinwand.setFillColor(TEXT_HELL)
        leinwand.drawString(RAND, hoehe - KOPF_HOEHE + 13 * mm, "VibeMind")
        leinwand.setFont(normal, 8.5)
        leinwand.setFillColor(TEXT_LEISE)
        leinwand.drawRightString(breite - RAND, hoehe - KOPF_HOEHE + 13.6 * mm,
                                 "vibemind.space")
    # Fussband auf JEDER Seite: es schliesst die Seite ab und nimmt dem
    # kurzen Einseiter das Vakuum unter dem Text.
    leinwand.setFillColor(FLAECHE)
    leinwand.rect(0, 0, breite, FUSS_HOEHE, stroke=0, fill=1)
    leinwand.setFillColor(AKZENT)
    leinwand.rect(0, FUSS_HOEHE, breite, 1, stroke=0, fill=1)
    normal, _ = schriften()
    leinwand.setFont(normal, 7.5)
    leinwand.setFillColor(TEXT_LEISE)
    leinwand.drawString(RAND, FUSS_HOEHE / 2 - 2.6,
                        "VibeMind — agentisches Betriebssystem · vibemind.space")
    if dokument.page > 1:
        leinwand.drawRightString(breite - RAND, FUSS_HOEHE / 2 - 2.6,
                                 str(dokument.page))
    leinwand.restoreState()


def _absaetze(text: str, stile: dict) -> list:
    """Leerzeilen trennen Absaetze; Zeilen mit -, – oder • werden Punkte."""
    teile = []
    for block in re.split(r"\n\s*\n", text.strip()):
        zeilen = [z.strip() for z in block.splitlines() if z.strip()]
        if zeilen and all(re.match(r"^[-–—•*]\s+", z) for z in zeilen):
            for z in zeilen:
                teile.append(Paragraph(_sicher(re.sub(r"^[-–—•*]\s+", "", z)),
                                       stile["punkt"], bulletText="•"))
        else:
            teile.append(Paragraph(_sicher(" ".join(zeilen)), stile["fliess"]))
    return teile


def bauen(titel: str, text: str, untertitel: str = "", belege=None,
          zu_klaeren=None, handlung: str = "") -> bytes:
    """Setzt eine Kampagnen-Unterlage als PDF. Gibt die Bytes zurueck.

    Wirft `ValueError` bei leerem Text — anders als die Werkzeuge ringsum,
    denn hier ist es ein Programmierfehler des Aufrufers, kein Ausfall eines
    Fremddienstes.
    """
    if not text or not text.strip():
        raise ValueError("Ohne Text gibt es kein PDF.")
    stile = _stile()
    puffer = io.BytesIO()
    dokument = BaseDocTemplate(
        puffer, pagesize=A4, title=titel or "VibeMind", author="VibeMind",
        leftMargin=RAND, rightMargin=RAND,
        topMargin=KOPF_HOEHE + 12 * mm, bottomMargin=FUSS_HOEHE + 8 * mm)
    rahmen = Frame(RAND, FUSS_HOEHE + 8 * mm, A4[0] - 2 * RAND,
                   A4[1] - KOPF_HOEHE - FUSS_HOEHE - 20 * mm, id="haupt",
                   leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    dokument.addPageTemplates([PageTemplate(id="seite", frames=[rahmen],
                                            onPage=_kopf_und_grund)])

    fluss = [Paragraph(_sicher(titel or ""), stile["titel"])]
    if untertitel.strip():
        fluss.append(Paragraph(_sicher(untertitel), stile["unter"]))
    fluss.append(Spacer(1, 9 * mm))
    fluss.extend(_absaetze(text, stile))

    if handlung.strip():
        # Der einzige helle Block auf der Seite — was zu tun ist, soll man
        # finden, ohne zu suchen.
        fluss.append(Spacer(1, 4 * mm))
        fluss.append(KeepTogether([Table(
            [[Paragraph(_sicher(handlung), stile["handlung"])]],
            colWidths=[A4[0] - 2 * RAND],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), AKZENT),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]))]))

    if zu_klaeren:
        # Zusammenhalten: eine Rubrik allein am Seitenfuss ist ein Fehler,
        # den der Leser dem Absender ankreidet.
        block = [Paragraph("ZU KLÄREN", stile["rubrik"])]
        block += [Paragraph(_sicher(str(z)), stile["klein"], bulletText="·")
                  for z in zu_klaeren]
        fluss.append(KeepTogether(block))
    if belege:
        block = [Paragraph("BELEGE", stile["rubrik"])]
        block += [Paragraph(_sicher(str(b)), stile["klein"], bulletText="·")
                  for b in belege]
        fluss.append(KeepTogether(block))

    dokument.build(fluss)
    return puffer.getvalue()


def seitenzahl(roh: bytes) -> int:
    """Wie viele Seiten das erzeugte PDF hat — fuer Tests und Ausgaben."""
    from pypdf import PdfReader
    return len(PdfReader(io.BytesIO(roh)).pages)
