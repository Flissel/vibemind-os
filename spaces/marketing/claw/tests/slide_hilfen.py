"""Gemeinsame Hilfen der Slide-Tests (Bildplan-Schritte, Saetze, HTML-Aufruf)."""
from spaces.marketing.claw.video_slides import komposition, zeitplan
from spaces.marketing.workers.video_stimme import Satz

SAETZE_1_2_3 = [Satz("Eins.", 0.0, 2.0), Satz("Zwei.", 2.15, 2.0), Satz("Drei.", 4.3, 1.7)]


def S(vorlage, ab, **felder):
    standard = {
        "titel": {"zeile": "Die Zeile", "unterzeile": "Darunter"},
        "stichpunkte": {"ueberschrift": "Kopf", "punkte": ["Eins", "Zwei"]},
        "zahl": {"zahl": "40 %", "einheit": "weniger", "erklaerung": "weil"},
        "zitat": {"zitat": "Ein Satz", "von": "Jemand"},
        "vergleich": {"links": {"titel": "A", "punkte": ["a"]}, "rechts": {"titel": "B", "punkte": ["b"]}},
        "agenda": {"punkte": ["x", "y", "z"], "aktiv": 1},
    }[vorlage]
    return {"vorlage": vorlage, "ab_satz": ab, **{**standard, **felder}}


def szene(**extra):
    return {"bildart": "slide", "uebergang": "schnitt", "variante": 0, **extra}


def html_fuer(schritte, saetze=SAETZE_1_2_3, dauer=6.0, marke=None, **extra):
    bild = {"schritte": schritte}
    return komposition(szene(**extra), zeitplan(bild, saetze, dauer), marke or {}, "ton.wav", dauer)
