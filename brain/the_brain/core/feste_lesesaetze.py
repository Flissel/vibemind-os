"""Feste Lesesaetze fuer Spaces, die bis zum Rundgang 23.11.2026 keinen Weg hatten.

Ergaenzt die Tabelle in core/verstehen.py (``_FESTE_LESEFRAGEN``, ``FESTE_ANTWORTEN``) um:

* Desktop    "Welche Fenster sind offen?"           -> desktop_fenster (core/desktop_fenster.py)
* The Brain  "Woran denkt das Gehirn gerade?"       -> thebrain_gedanken  (core/thebrain_gedanken.py)
* Rowboat    "Welche Agenten gibt es in Rowboat?"   -> rowboat_spaces  (core/rowboat_spaces.py)
* Augen      "Bestätige: Fenster X ist vorn und zeigt Y" -> desktop_bestaetigen (core/desktop_augen.py)
* Project Space "Wie ist der Stand im Project Space?" -> coding_stand, "Zeig mir die letzten Läufe"
  -> coding_laeufe, "Zeig mir das Ergebnis des letzten Laufs" -> coding_ergebnis (core/coding_stand.py)

Agent Farm bekommt keinen Satz: hinter agentfarm_list_teams laeuft kein Dienst (siehe
docs im Bericht an die Koordination), der Satz bleibt "noch kein Weg".

Ohne Modell. Die Saetze, die einen Space beim Namen nennen ("in Rowboat", "auf dem Desktop",
"das Gehirn"), gelten aus jedem Reiter; die kurzen nur im eigenen Reiter oder ohne Reiter.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional, Tuple

_W = r"[\wäöüß-]+"

_FENSTER = re.compile(
    rf"^(?:welche|was für|was fuer)\s+fenster\s+(?:sind|habe ich|hab ich)(?:\s+{_W}){{0,5}}?\s+"
    r"(?:offen|geöffnet|geoeffnet)$", re.IGNORECASE)
_AUF_DEM_DESKTOP = re.compile(r"\b(?:auf|am)\s+(?:dem\s+|meinem\s+)?desktop\b", re.IGNORECASE)
_GEDANKEN = re.compile(
    r"^(?:woran|an was|worüber|worueber)\s+denkt\s+(?:das\s+gehirn|the\s+brain)"
    r"(?:\s+(?:gerade|jetzt|im moment|gerade jetzt))?$", re.IGNORECASE)
_AGENTEN = re.compile(r"^welche\s+(?:agenten|spaces|vibemind-spaces)\s+(?:gibt es|stehen|sind)(?:\s+da)?$",
                      re.IGNORECASE)
_AGENTEN_IN_ROWBOAT = re.compile(
    r"^welche\s+(?:agenten|spaces|vibemind-spaces)\s+(?:gibt es|stehen|sind)\s+(?:in|im)\s+(?:rowboat|roarboot)$",
    re.IGNORECASE)


# Project Space (Coding), 24.11.2026: Stand, letzte Laeufe, Ergebnis (core/coding_stand.py).
_PS = r"(?:(?:im|vom|in dem|im bereich)\s+)?(?:project[- ]?space|projekt[- ]?space|coding(?:[- ]?space)?)"
_LETZTE = r"(?:der|des|den|dem)\s+(?:letzten?|neuesten?|aktuellen?)\s+(?:lauf|laufs|bau|baus)"
_CODING_STAND_BENANNT = re.compile(
    rf"^(?:wie\s+ist\s+der\s+stand\s+{_PS}"
    rf"|was\s+(?:baut|macht|tut)\s+(?:der\s+)?(?:project[- ]?space|projekt[- ]?space|coding)(?:\s+(?:gerade|jetzt|im moment))?"
    r"|wie\s+ist\s+der\s+(?:code|coding)[- ]?status"
    r"|(?:code|coding)[- ]?(?:status|stand))$", re.IGNORECASE)
_CODING_STAND = re.compile(
    rf"^(?:(?:ist|läuft|laeuft)\s+{_LETZTE.replace('(?:der|des|den|dem)', 'der')}\s+(?:fertig|noch|durch)"
    r"|(?:läuft|laeuft)\s+(?:gerade\s+)?(?:noch\s+)?ein\s+(?:bau|lauf)"
    rf"|wie\s+lange\s+(?:hat|dauerte)\s+{_LETZTE.replace('(?:der|des|den|dem)', 'der')}(?:\s+gedauert)?)$",
    re.IGNORECASE)
_CODING_STAND_KURZ = re.compile(r"^(?:wie\s+ist\s+der\s+stand|was\s+läuft\s+gerade|was\s+laeuft\s+gerade)$",
                                re.IGNORECASE)
_CODING_LAEUFE = re.compile(
    r"^(?:(?:zeig(?:e)?|liste|nenn(?:e)?)\s+(?:mir\s+)?(?:die\s+|alle\s+)?(?:letzten\s+|neuesten\s+)?läufe"
    r"|(?:zeig(?:e)?|liste|nenn(?:e)?)\s+(?:mir\s+)?(?:die\s+|alle\s+)?(?:letzten\s+|neuesten\s+)?laeufe"
    r"|welche\s+(?:läufe|laeufe)\s+gibt\s+es"
    r"|was\s+wurde\s+zuletzt\s+gebaut)$", re.IGNORECASE)
_CODING_LAEUFE_BENANNT = re.compile(
    rf"^(?:welche\s+(?:läufe|laeufe|projekte|apps)\s+gibt\s+es\s+{_PS}"
    rf"|(?:zeig(?:e)?|liste)\s+(?:mir\s+)?(?:die\s+)?(?:läufe|laeufe|projekte)\s+{_PS})$", re.IGNORECASE)
_CODING_PROJEKTE = re.compile(r"^welche\s+projekte\s+gibt\s+es$", re.IGNORECASE)
_CODING_ERGEBNIS = re.compile(
    rf"^(?:zeig(?:e)?\s+(?:mir\s+)?(?:das\s+ergebnis\s+{_LETZTE}|{_LETZTE.replace('(?:der|des|den|dem)', 'den')})"
    rf"|wie\s+ist\s+{_LETZTE.replace('(?:der|des|den|dem)', 'der')}\s+ausgegangen"
    rf"|warum\s+ist\s+{_LETZTE.replace('(?:der|des|den|dem)', 'der')}\s+(?:gescheitert|fehlgeschlagen)"
    r"|wie\s+(?:starte|startet)\s+(?:ich|man)\s+die\s+(?:zuletzt\s+gebaute\s+|neue\s+|gebaute\s+)?app)$",
    re.IGNORECASE)


def _nur_mit_desktop(muster: re.Pattern) -> re.Pattern:
    class _Beide:
        def search(self, text: str):
            return muster.search(text) if _AUF_DEM_DESKTOP.search(text) else None
    return _Beide()  # type: ignore[return-value]


class _Bestaetigung:
    """Muster-Ersatz: "Bestätige: Fenster X ist vorn (und zeigt Y)" (core/desktop_augen.py)."""

    def search(self, text: str):
        from .desktop_augen import auftrag_aus_satz
        return auftrag_aus_satz(text)


def lesefragen(alle: Tuple[Any, ...]) -> tuple:
    """Eintraege fuer ``_FESTE_LESEFRAGEN``: (Muster, Faehigkeit, Spaces).

    Saetze, die den Space beim Namen nennen ("auf dem Desktop", "in Rowboat", "das Gehirn",
    "Bestätige: Fenster ..."), gelten in JEDEM Reiter, auch in Ideen (Spaces ``None``; Lauf 17:
    aus Ideen lief "... auf dem Desktop offen?" sonst ueber die alte Abkuerzung und endete
    "nicht lesbar"). Die mehrdeutigen Kurzformen bleiben im eigenen Reiter. ``alle`` (jeder
    Reiter ausser Ideen) bleibt Parameter des Aufrufers in core/verstehen.py.
    """
    return (
        (_Bestaetigung(), "desktop_bestaetigen", None),
        (_nur_mit_desktop(_FENSTER), "desktop_fenster", None),
        (_FENSTER, "desktop_fenster", ("desktop", None)),
        (_GEDANKEN, "thebrain_gedanken", None),
        (_AGENTEN_IN_ROWBOAT, "rowboat_spaces", None),
        (_AGENTEN, "rowboat_spaces", ("roarboot",)),
        # Project Space: mit Namen ueberall, die kurzen Lauf-Saetze im eigenen Reiter oder ohne
        # Reiter ("Lauf" sagt nur der Project Space), "Welche Projekte gibt es?" nur im eigenen.
        (_CODING_STAND_BENANNT, "coding_stand", None),
        (_CODING_LAEUFE_BENANNT, "coding_laeufe", None),
        (_CODING_STAND, "coding_stand", ("projects", None)),
        (_CODING_LAEUFE, "coding_laeufe", ("projects", None)),
        (_CODING_ERGEBNIS, "coding_ergebnis", ("projects", None)),
        (_CODING_PROJEKTE, "coding_laeufe", ("projects",)),
        (_CODING_STAND_KURZ, "coding_stand", ("projects",)),
    )


def _desktop_fenster(ergebnis: Any) -> Optional[str]:
    from .desktop_fenster import antwort
    return antwort(ergebnis)


def _desktop_fenster_protokoll(ergebnis: Any) -> Optional[str]:
    from .desktop_fenster import protokoll
    return protokoll(ergebnis)


def _desktop_bestaetigen(ergebnis: Any) -> Optional[str]:
    from .desktop_augen import antwort
    return antwort(ergebnis)


def _desktop_bestaetigen_protokoll(ergebnis: Any) -> Optional[str]:
    from .desktop_augen import protokoll
    return protokoll(ergebnis)


def _thebrain_gedanken(ergebnis: Any) -> Optional[str]:
    from .thebrain_gedanken import antwort
    return antwort(ergebnis)


def _rowboat_spaces(ergebnis: Any) -> Optional[str]:
    from .rowboat_spaces import antwort
    return antwort(ergebnis)


def _coding(name: str) -> Callable[..., Optional[str]]:
    def aufruf(*args: Any) -> Optional[str]:
        from . import coding_stand
        return getattr(coding_stand, name)(*args)
    return aufruf


def antworten() -> Dict[str, Callable[[Any], Optional[str]]]:
    """Eintraege fuer ``FESTE_ANTWORTEN``."""
    return {"desktop_fenster": _desktop_fenster, "thebrain_gedanken": _thebrain_gedanken,
            "rowboat_spaces": _rowboat_spaces, "desktop_bestaetigen": _desktop_bestaetigen,
            "coding_stand": _coding("antwort_stand"), "coding_laeufe": _coding("antwort_laeufe"),
            "coding_ergebnis": _coding("antwort_ergebnis")}


def _desktop_fenster_fehler(ergebnis: Any, fehler: Any = None) -> Optional[str]:
    from .desktop_fenster import fehlersatz
    return fehlersatz(ergebnis, fehler)


def _rowboat_spaces_fehler(ergebnis: Any, fehler: Any = None) -> Optional[str]:
    from .rowboat_spaces import fehlersatz
    return fehlersatz(ergebnis, fehler)


def _thebrain_gedanken_fehler(ergebnis: Any, fehler: Any = None) -> Optional[str]:
    return "Die Gedanken des Gehirns kann ich gerade nicht lesen: seine eigene Schnittstelle antwortet nicht."


def fehlersaetze() -> Dict[str, Callable[..., Optional[str]]]:
    """Genauer Satz statt des allgemeinen Fehlersatzes, wenn klar ist, was fehlt."""
    return {"desktop_fenster": _desktop_fenster_fehler, "rowboat_spaces": _rowboat_spaces_fehler,
            "thebrain_gedanken": _thebrain_gedanken_fehler, "coding_stand": _coding("fehlersatz"),
            "coding_laeufe": _coding("fehlersatz"), "coding_ergebnis": _coding("fehlersatz")}


def _rowboat_spaces_vorher() -> Optional[str]:
    from .rowboat_spaces import vorpruefung
    return vorpruefung()


def vorpruefungen() -> Dict[str, Callable[[], Optional[str]]]:
    """Vor dem Plan: Satz, wenn der Weg sicher nicht geht. Dann kein Plan und kein Sprung."""
    return {"rowboat_spaces": _rowboat_spaces_vorher}


def protokolle() ->Dict[str, Callable[[Any], Optional[str]]]:
    """Satz fuer Plan-Verlauf und Protokolle, wenn die Antwort Bildschirminhalt traegt."""
    return {"desktop_fenster": _desktop_fenster_protokoll, "desktop_bestaetigen": _desktop_bestaetigen_protokoll}
