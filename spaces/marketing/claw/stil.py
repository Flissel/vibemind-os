"""Hausstil fuer alles, was dieser Space nach draussen gibt.

EINE Regel, ein Ort. Sie wird an drei Stellen gerufen — beim Entwerfen einer
Kampagne, beim Beauftragen eines Versands und beim Setzen eines PDFs — und
genau deshalb steht sie hier und nicht dreimal dort. Dieselbe Ueberlegung wie
bei `sperrliste.kennung_*`: zwei Orte, die dasselbe entscheiden sollen,
laufen irgendwann auseinander.


LANGE GEDANKENSTRICHE
---------------------
Rueckmeldung eines Menschen zum PDF `email-early-access-solo-gruender.pdf`
(11.09.2026, per WhatsApp):

    „Die langen Gedankenstriche wegmachen oder kuerzen.
     Sieht zu sehr nach KI aus."

Das ist keine Geschmacksfrage, sondern ein gemessener Eindruck beim Leser —
und der Leser ist der Zweck der ganzen Unterlage. Nachgezaehlt in genau
diesem PDF: vier Geviertstriche (U+2014), null Halbgeviertstriche. Sie kamen
aus DREI Quellen: der Fusszeile dieses Spaces selbst, dem Untertitel der
Kampagne und dem Fliesstext des Sprachmodells.

GEKUERZT, NICHT ENTFERNT. Die Rueckmeldung liess beides zu; Kuerzen ist die
Variante, die nie einen Satz kaputt macht. „wegmachen" hiesse, den
Gedankenstrich durch ein Komma, einen Doppelpunkt oder gar nichts zu
ersetzen — und welches davon richtig ist, entscheidet die Grammatik des
Satzes, nicht ein Suchen-und-Ersetzen. Ein automatischer Eingriff, der
manchmal falsch liegt, ist schlechter als einer, der immer nur harmlos ist.

WAS STEHEN BLEIBT: ein Halbgeviertstrich OHNE Leerzeichen. `10–12 Uhr` ist
ein Bereich, kein Gedankenstrich, und den zu zerschneiden waere ein Fehler,
den niemand bestellt hat. Der Geviertstrich wird auch eng gekuerzt — als
Bereichszeichen ist er im Deutschen ohnehin falsch.

DIESER DOCSTRING SELBST verwendet lange Striche. Das ist kein Widerspruch:
die Regel gilt fuer das, was HINAUSGEHT, nicht fuer Code, den nur wir lesen.
"""
import re

# Der klassische Einschub: langer Strich mit Leerzeichen auf BEIDEN Seiten.
# Wird zu einem Bindestrich mit Leerzeichen — die kurze Form derselben Geste.
_EINSCHUB = re.compile(r"[ \t]+[–—][ \t]+")

# Was danach an Geviertstrichen uebrig ist (eng gesetzt oder nur einseitig
# mit Leerzeichen), wird ein blosser Bindestrich. Der Halbgeviertstrich
# bleibt hier ausdruecklich verschont: eng gesetzt ist er ein Bereich.
_GEVIERT_REST = re.compile(r"[ \t]*—[ \t]*")


def striche_kuerzen(text: str) -> str:
    """Lange Gedankenstriche zu Bindestrichen. Laesst Bereiche (10–12) stehen."""
    if not text:
        return text
    return _GEVIERT_REST.sub("-", _EINSCHUB.sub(" - ", text))


def lange_striche(text: str) -> int:
    """Wie viele lange Striche gekuerzt WUERDEN.

    In zwei Schritten gezaehlt, genau wie ersetzt wird — sonst zaehlte ein
    Geviertstrich mit Leerzeichen doppelt, weil er auf beide Muster passt.
    Der Agent soll erfahren, dass an seinem Text etwas geaendert wurde,
    statt es beim naechsten Lesen zu entdecken.
    """
    if not text:
        return 0
    einschuebe = _EINSCHUB.findall(text)
    rest = _GEVIERT_REST.findall(_EINSCHUB.sub(" - ", text))
    return len(einschuebe) + len(rest)


def hausstil(text: str) -> tuple:
    """(bereinigter Text, Anzahl der Eingriffe). Wirft nie."""
    if not text:
        return text, 0
    n = lange_striche(text)
    return (striche_kuerzen(text), n) if n else (text, 0)
