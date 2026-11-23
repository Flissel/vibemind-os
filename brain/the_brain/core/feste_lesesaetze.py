"""Feste Lesesaetze fuer Spaces, die bis zum Rundgang 23.11.2026 keinen Weg hatten.

Ergaenzt die Tabelle in core/verstehen.py (``_FESTE_LESEFRAGEN``, ``FESTE_ANTWORTEN``) um:

* Desktop    "Welche Fenster sind offen?"           -> desktop_fenster (core/desktop_fenster.py)
* The Brain  "Woran denkt das Gehirn gerade?"       -> thebrain_gedanken  (core/thebrain_gedanken.py)
* Rowboat    "Welche Agenten gibt es in Rowboat?"   -> rowboat_spaces  (core/rowboat_spaces.py)
* Augen      "Bestätige: Fenster X ist vorn und zeigt Y" -> desktop_bestaetigen (core/desktop_augen.py)

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
    """Eintraege fuer ``_FESTE_LESEFRAGEN``: (Muster, Faehigkeit, Spaces). ``alle``: jeder Reiter."""
    return (
        (_Bestaetigung(), "desktop_bestaetigen", alle),
        (_nur_mit_desktop(_FENSTER), "desktop_fenster", alle),
        (_FENSTER, "desktop_fenster", ("desktop", None)),
        (_GEDANKEN, "thebrain_gedanken", alle),
        (_AGENTEN_IN_ROWBOAT, "rowboat_spaces", alle),
        (_AGENTEN, "rowboat_spaces", ("roarboot",)),
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


def antworten() -> Dict[str, Callable[[Any], Optional[str]]]:
    """Eintraege fuer ``FESTE_ANTWORTEN``."""
    return {"desktop_fenster": _desktop_fenster, "thebrain_gedanken": _thebrain_gedanken,
            "rowboat_spaces": _rowboat_spaces, "desktop_bestaetigen": _desktop_bestaetigen}


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
            "thebrain_gedanken": _thebrain_gedanken_fehler}


def protokolle() -> Dict[str, Callable[[Any], Optional[str]]]:
    """Satz fuer Plan-Verlauf und Protokolle, wenn die Antwort Bildschirminhalt traegt."""
    return {"desktop_fenster": _desktop_fenster_protokoll, "desktop_bestaetigen": _desktop_bestaetigen_protokoll}
