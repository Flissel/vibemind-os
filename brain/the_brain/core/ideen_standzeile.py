"""Was die Standzeile der App fuer einen Schritt des Ideen-Space zu lesen bekommt.

Arbeitslauf 2, Punkt 6: Die Hülle zeigt aus den Ereignissen ``hop_started`` und
``hop_completed`` die Felder ``description``, ``error`` und ``result_preview``. Für
Ideen-Schritte standen dort Rohtexte: "idea.rate via Ideen-Abkuerzung",
"RuntimeError: Ideen-Werkzeug meldet ...", "runtime authority approval pending",
ein Python-repr des Werkzeugergebnisses und ein roher Zeitüberschreitungstext.

Hier entstehen stattdessen deutsche Sätze. Die Rohtexte bleiben im Plan-Schnappschuss
(Recorder, Trace), nur nicht im Strom an die App. Wirft nie.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

_NAMEN = {
    "idea.create": "Notiz anlegen", "idea.update": "Notiz ändern", "idea.list": "Notizen lesen",
    "idea.format": "Darstellung ändern", "idea.arrange": "Karten ordnen",
    "idea.auto_link": "Verbindungen suchen", "idea.connect": "Karten verbinden", "idea.rate": "Bewerten",
    "idea.rate_reason": "Begründung festhalten", "bubble.rate": "Bubble bewerten",
    "bubble.promote": "Bubble befördern", "aenderung_zurueck": "Zurücknehmen",
    "idea.aenderung_zurueck": "Zurücknehmen", "idea.expand": "Ausformulieren", "idea.explain": "Einschätzen",
    "idea.summarize": "Zusammenfassen", "idea.whitepaper": "White Paper", "idea.document": "Dokument schreiben",
    "bubble_list": "Bubbles zeigen", "bubble.list": "Bubbles zeigen", "idea.root": "Wurzel festlegen",
    "idea.group": "Nach Thema gruppieren", "idea.group_layout": "Gruppen anordnen",
}


def _kanonisch(capability: Any) -> str:
    name = str(capability or "")
    if name in _NAMEN:
        return name
    return name.replace("_", ".", 1) if name.startswith(("idea_", "bubble_")) else name


def ist_ideen_schritt(capability: Any, space: Any) -> bool:
    from .ideen_abkuerzung import ist_ideen_faehigkeit
    return space == "ideas" or ist_ideen_faehigkeit(capability)


def schrittname(capability: Any) -> str:
    return _NAMEN.get(_kanonisch(capability), "Ideen-Schritt")


def fehlertext(fehler: Any, capability: Any) -> str:
    if not fehler:
        return ""
    from .ideen_abkuerzung import fehlersatz
    return fehlersatz(fehler, _kanonisch(capability))


def ergebnistext(capability: Any, ok: bool, ergebnis: Any) -> str:
    if not ok:
        return ""
    try:
        from .ideen_abkuerzung import antwort_fuer_schritt
        satz: Optional[str] = antwort_fuer_schritt(_kanonisch(capability), True, ergebnis, None)
    except Exception:  # noqa: BLE001
        satz = None
    return satz or "erledigt"


_NICHT_ERREICHBAR = re.compile(r"ConnectionError|HTTPConnectionPool|Max retries|Connection refused|"
                               r"Name or service not known|getaddrinfo|unreachable|nicht erreichbar",
                               re.IGNORECASE)
_ZEIT = re.compile(r"timeout|timed out|Zeitüberschreitung", re.IGNORECASE)


def allgemeiner_fehlertext(fehler: Any) -> Optional[str]:
    """Deutscher Satz fuer den Fehler eines Schritts ausserhalb des Ideen-Space (nie Rohtext)."""
    if not fehler:
        return None
    text = str(fehler)
    if _NICHT_ERREICHBAR.search(text):
        return "Der Dienst ist gerade nicht erreichbar; geändert habe ich nichts."
    if _ZEIT.search(text):
        return "Der Dienst hat nicht rechtzeitig geantwortet."
    return "Der Schritt hat nicht geklappt."


def _allgemein(ereignis: Dict[str, Any]) -> Dict[str, Any]:
    """Schritt eines anderen Space: Fehler als deutscher Satz, kein Rohtext im Strom.

    Nachmessung 23.11.2026: In der Standzeile stand "ConnectionError: HTTPConnectionPool(...)".
    Der Rohtext bleibt im Plan-Schnappschuss (Recorder, Trace).
    """
    if not ereignis.get("error"):
        return ereignis
    neu = dict(ereignis)
    neu["error"] = allgemeiner_fehlertext(neu.get("error"))
    if not neu.get("ok") and "result_preview" in neu:
        neu["result_preview"] = ""
    return neu


def fuer_app(ereignis: Dict[str, Any], *, ergebnis: Any = None) -> Dict[str, Any]:
    """Ereignis eines Schritts mit deutschen Saetzen statt Rohtext (Kopie)."""
    try:
        capability, space = ereignis.get("capability"), ereignis.get("space")
        if not ist_ideen_schritt(capability, space):
            return _allgemein(ereignis)
        neu = dict(ereignis)
        neu["description"] = schrittname(capability)
        if neu.get("pending"):
            # Ein angenommener Auftrag ist kein Fehler; eine wartende Freigabe sagt das so.
            if isinstance(ergebnis, dict) and ergebnis.get("auftrag_id"):
                neu.update({"ok": True, "status": "angenommen", "error": None,
                            "result_preview": "angenommen, ein Modell arbeitet daran"})
            else:
                neu.update({"error": "Wartet auf Freigabe; geändert ist noch nichts.", "result_preview": ""})
            return neu
        if "error" in neu:
            neu["error"] = fehlertext(neu.get("error"), capability) or None
        if "result_preview" in neu:
            neu["result_preview"] = ergebnistext(capability, bool(neu.get("ok")), ergebnis)
        return neu
    except Exception:  # noqa: BLE001
        return ereignis
