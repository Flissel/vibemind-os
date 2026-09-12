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

from spaces.marketing.claw import stil

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

# LAYOUTS. Der Sinn der Trennung von Inhalt und Aussehen: dasselbe
# `broadcast_proposal` laesst sich in jedem dieser Gewaender setzen, ohne den
# Text neu erzeugen zu lassen. Ein neues Layout ist ein Eintrag hier.
#
# Die Farben von "dunkel" stammen aus dem Pitch-Deck
# (`pitch-deck-2026/vibemind-pitch.html`) — das ist, was Aussenstehende von
# VibeMind sehen. "hell" ist dasselbe Geruest fuer Unterlagen, die gedruckt
# oder weitergeleitet werden, wo dunkle Flaechen stoeren.
# Zur Textfarbe, weil sie danach niemand „aufhellen" soll: `text` ist auf
# beiden Tafeln bewusst NICHT das reinste Weiss bzw. Schwarz. Ein Mensch hat
# das am ersten echten PDF geprueft und bestaetigt (11.09.2026): „Finde gut,
# dass die Schriftfarbe nicht ganz weiss ist, weil leicht graeulich ist laut
# Studien besser fuers Auge zu lesen." Reines #ffffff auf dunklem Grund
# flimmert; #cfe3df nimmt dem Kontrast die Haerte, ohne ihn zu verlieren.
LAYOUTS = {
    "dunkel": {
        "grund": "#0f2422", "flaeche": "#1d3b39", "akzent": "#5eead4",
        "gold": "#fbbf24", "text": "#cfe3df", "text_hell": "#e9fbf6",
        "text_leise": "#8aa3a0", "handlung_text": "#0f2422",
    },
    "hell": {
        "grund": "#ffffff", "flaeche": "#0f2422", "akzent": "#0f7a6c",
        "gold": "#a06a00", "text": "#1f2937", "text_hell": "#0f2422",
        "text_leise": "#6b7280", "handlung_text": "#ffffff",
    },
}
LAYOUT_VORGABE = "dunkel"


def _farben(layout: str) -> dict:
    """Die Farbtafel eines Layouts. Unbekannt -> Vorgabe, kein Absturz."""
    tafel = LAYOUTS.get((layout or "").strip().lower() or LAYOUT_VORGABE,
                        LAYOUTS[LAYOUT_VORGABE])
    return {name: colors.HexColor(wert) for name, wert in tafel.items()}

# Trennpunkte statt Geviertstrich. Die Fusszeile war eine der drei Quellen
# der langen Striche, die ein Leser am 11.09.2026 als zu KI-haft gemeldet hat
# (siehe stil.py) - und die einzige, die in unserem eigenen Code stand. Als
# Konstante, damit ein Test sie pruefen kann, ohne den Quelltext samt
# Kommentaren zu durchsuchen.
FUSSZEILE = "VibeMind · agentisches Betriebssystem · vibemind.space"

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


# `**wort**` wird fett. Rueckmeldung eines Menschen zum ersten echten PDF
# (11.09.2026): „Ich wuerde bei den Stichpunkten die folgenden Woerter fett
# machen: Marketing-Beitraege, Support Antworten, Entwicklung." Ein
# Stichpunkt ohne Betonung liest sich als Aufzaehlung; mit Betonung sieht
# man beim Ueberfliegen, WORUM es geht.
#
# Zwei Sterne, nicht einer: ein einzelner Stern steht in Kampagnentexten
# haeufig als Aufzaehlungszeichen oder als Fussnotenmarke am Zeilenanfang,
# und der duerfte dann nicht auszeichnen.
_FETT = re.compile(r"\*\*(.+?)\*\*", re.S)


def _auszeichnen(text: str) -> str:
    """Erst entschaerfen, DANN auszeichnen — nie umgekehrt.

    Die Reihenfolge ist die ganze Sicherheit dieser Funktion: `_sicher`
    macht aus jedem `<` ein `&lt;`, und erst danach setzt diese Funktion die
    einzigen spitzen Klammern, die reportlab sehen soll. Wer zuerst
    auszeichnete und dann entschaerfte, machte sein eigenes `<b>` wieder
    kaputt; wer `_sicher` wegliesse, oeffnete den Kampagnentext fuer
    beliebiges reportlab-Markup.
    """
    return _FETT.sub(r"<b>\1</b>", _sicher(text or ""))


def _stile(f: dict) -> dict:
    normal, fett = schriften()
    return {
        "titel": ParagraphStyle("titel", fontName=fett, fontSize=24, leading=29,
                                textColor=f["text_hell"], alignment=TA_LEFT),
        # FETT seit dem 11.09.2026 („Zweiter Satz in Tuerkis bei der
        # Ueberschrift auch in Fett"). Der Untertitel ist der Satz, der sagt,
        # fuer WEN die Unterlage ist; in der duennen Schrift verschwand er
        # neben dem 24-Punkt-Titel darueber.
        "unter": ParagraphStyle("unter", fontName=fett, fontSize=11.5, leading=16,
                                textColor=f["akzent"], spaceBefore=3),
        "fliess": ParagraphStyle("fliess", fontName=normal, fontSize=11, leading=17,
                                 textColor=f["text"], spaceAfter=9),
        "punkt": ParagraphStyle("punkt", fontName=normal, fontSize=11, leading=17,
                                textColor=f["text"], leftIndent=10, bulletIndent=1,
                                spaceAfter=5),
        "rubrik": ParagraphStyle("rubrik", fontName=fett, fontSize=9.5, leading=13,
                                 textColor=f["gold"], spaceBefore=16, spaceAfter=5),
        "klein": ParagraphStyle("klein", fontName=normal, fontSize=8.5, leading=12.5,
                                textColor=f["text_leise"], spaceAfter=3),
        "handlung": ParagraphStyle("handlung", fontName=fett, fontSize=12.5,
                                   leading=18, textColor=f["handlung_text"],
                                   spaceBefore=2, spaceAfter=2,
                                   leftIndent=6, rightIndent=6),
    }


def _kopf_und_grund(leinwand, dokument, f: dict) -> None:
    """Grundflaeche auf jeder Seite, Kopfband nur auf der ersten."""
    breite, hoehe = A4
    leinwand.saveState()
    leinwand.setFillColor(f["grund"])
    leinwand.rect(0, 0, breite, hoehe, stroke=0, fill=1)
    if dokument.page == 1:
        leinwand.setFillColor(f["flaeche"])
        leinwand.rect(0, hoehe - KOPF_HOEHE, breite, KOPF_HOEHE, stroke=0, fill=1)
        leinwand.setFillColor(f["akzent"])
        leinwand.rect(0, hoehe - KOPF_HOEHE - 1.6, breite, 1.6, stroke=0, fill=1)
        normal, fett = schriften()
        leinwand.setFont(fett, 13)
        # Der Schriftzug sitzt IMMER auf der Kopfflaeche, nicht auf dem Grund
        # — im hellen Layout ist die dunkel, also braucht er dort Kontrast.
        leinwand.setFillColor(colors.HexColor("#e9fbf6"))
        leinwand.drawString(RAND, hoehe - KOPF_HOEHE + 13 * mm, "VibeMind")
        leinwand.setFont(normal, 8.5)
        leinwand.setFillColor(colors.HexColor("#8aa3a0"))
        leinwand.drawRightString(breite - RAND, hoehe - KOPF_HOEHE + 13.6 * mm,
                                 "vibemind.space")
    # Fussband auf JEDER Seite: es schliesst die Seite ab und nimmt dem
    # kurzen Einseiter das Vakuum unter dem Text.
    leinwand.setFillColor(f["flaeche"])
    leinwand.rect(0, 0, breite, FUSS_HOEHE, stroke=0, fill=1)
    leinwand.setFillColor(f["akzent"])
    leinwand.rect(0, FUSS_HOEHE, breite, 1, stroke=0, fill=1)
    normal, _ = schriften()
    leinwand.setFont(normal, 7.5)
    leinwand.setFillColor(colors.HexColor("#8aa3a0"))
    leinwand.drawString(RAND, FUSS_HOEHE / 2 - 2.6, FUSSZEILE)
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
                teile.append(Paragraph(_auszeichnen(re.sub(r"^[-–—•*]\s+", "", z)),
                                       stile["punkt"], bulletText="•"))
        else:
            teile.append(Paragraph(_auszeichnen(" ".join(zeilen)), stile["fliess"]))
    return teile


def bauen(titel: str, text: str, untertitel: str = "", belege=None,
          zu_klaeren=None, handlung: str = "",
          layout: str = LAYOUT_VORGABE) -> bytes:
    """Setzt eine Kampagnen-Unterlage als PDF. Gibt die Bytes zurueck.

    Wirft `ValueError` bei leerem Text — anders als die Werkzeuge ringsum,
    denn hier ist es ein Programmierfehler des Aufrufers, kein Ausfall eines
    Fremddienstes.
    """
    if not text or not text.strip():
        raise ValueError("Ohne Text gibt es kein PDF.")

    # Hausstil auf ALLES, was ein Autor geschrieben hat. Er wird auch schon
    # beim Entwerfen angewandt (werkzeuge.kampagne_entwerfen), damit
    # Entwurf, E-Mail und PDF denselben Text tragen — hier steht er trotzdem
    # ein zweites Mal, weil `pdf_aus_entwurf` auch BESTEHENDE Entwuerfe
    # setzt, die vor dieser Regel entstanden sind. Dieselbe Funktion, zwei
    # Aufrufstellen: das ist kein zweiter Ort fuer die Regel, sondern
    # derselbe Ort zweimal benutzt.
    titel = stil.striche_kuerzen(titel or "")
    untertitel = stil.striche_kuerzen(untertitel or "")
    text = stil.striche_kuerzen(text)
    handlung = stil.striche_kuerzen(handlung or "")
    belege = [stil.striche_kuerzen(str(b)) for b in (belege or [])]
    zu_klaeren = [stil.striche_kuerzen(str(z)) for z in (zu_klaeren or [])]

    f = _farben(layout)
    stile = _stile(f)
    puffer = io.BytesIO()
    dokument = BaseDocTemplate(
        puffer, pagesize=A4, title=titel or "VibeMind", author="VibeMind",
        leftMargin=RAND, rightMargin=RAND,
        topMargin=KOPF_HOEHE + 12 * mm, bottomMargin=FUSS_HOEHE + 8 * mm)
    rahmen = Frame(RAND, FUSS_HOEHE + 8 * mm, A4[0] - 2 * RAND,
                   A4[1] - KOPF_HOEHE - FUSS_HOEHE - 20 * mm, id="haupt",
                   leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    dokument.addPageTemplates([PageTemplate(
        id="seite", frames=[rahmen],
        onPage=lambda leinwand, dok: _kopf_und_grund(leinwand, dok, f))])

    fluss = [Paragraph(_auszeichnen(titel or ""), stile["titel"])]
    if untertitel.strip():
        fluss.append(Paragraph(_auszeichnen(untertitel), stile["unter"]))
    fluss.append(Spacer(1, 9 * mm))
    fluss.extend(_absaetze(text, stile))

    if handlung.strip():
        # Der einzige helle Block auf der Seite — was zu tun ist, soll man
        # finden, ohne zu suchen.
        fluss.append(Spacer(1, 4 * mm))
        fluss.append(KeepTogether([Table(
            [[Paragraph(_auszeichnen(handlung), stile["handlung"])]],
            colWidths=[A4[0] - 2 * RAND],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), f["akzent"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]))]))

    if zu_klaeren:
        # Zusammenhalten: eine Rubrik allein am Seitenfuss ist ein Fehler,
        # den der Leser dem Absender ankreidet.
        block = [Paragraph("ZU KLÄREN", stile["rubrik"])]
        block += [Paragraph(_auszeichnen(str(z)), stile["klein"], bulletText="·")
                  for z in zu_klaeren]
        fluss.append(KeepTogether(block))
    if belege:
        block = [Paragraph("BELEGE", stile["rubrik"])]
        block += [Paragraph(_auszeichnen(str(b)), stile["klein"], bulletText="·")
                  for b in belege]
        fluss.append(KeepTogether(block))

    dokument.build(fluss)
    return puffer.getvalue()


def seitenzahl(roh: bytes) -> int:
    """Wie viele Seiten das erzeugte PDF hat — fuer Tests und Ausgaben."""
    from pypdf import PdfReader
    return len(PdfReader(io.BytesIO(roh)).pages)
