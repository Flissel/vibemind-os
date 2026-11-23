"""Vorschlag bei geänderter Karte (Dritter Lauf: Schutz ging, der Vorschlag kam nie an).

Hat der Mensch eine Notiz geändert, während ein Modellauftrag lief, überschreibt das
Gehirn sie nicht (core/ideen_rueckweg). Das Ergebnis liegt dann als Vorschlag in der
Auftragszeile (``pruefung.vorschlag``, ``pruefung.werte``, ``pruefung.titel``). Die
Meldung dazu nennt drei feste Sätze:

- "Vorschlag zeigen": der Text des jüngsten offenen Vorschlags, nichts geändert;
- "Vorschlag übernehmen": schreibt ihn mit denselben festen Aufrufen wie der Rückweg;
- "Vorschlag verwerfen": markiert ihn als erledigt, nichts geändert.

Gilt für den jüngsten offenen Vorschlag der offenen Bubble (sonst: überhaupt), höchstens
``GILT_H`` Stunden alt. Erledigt (übernommen oder verworfen) heißt
``pruefung.vorschlag_erledigt``; danach ist er nicht mehr "offen".
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

GILT_H = 24
ZEIGEN_GRENZE = 1500

_I = re.IGNORECASE
_ZEIGEN = re.compile(r"^\s*(?:vorschlag\s+zeigen|zeig(?:e)?\s+(?:mir\s+)?(?:den\s+)?vorschlag)\s*[.!]?\s*$", _I)
_UEBERNEHMEN = re.compile(r"^\s*(?:vorschlag\s+(?:übernehmen|uebernehmen)|(?:übernimm|uebernimm)\s+den\s+vorschlag)"
                          r"\s*[.!]?\s*$", _I)
_VERWERFEN = re.compile(r"^\s*(?:vorschlag\s+verwerfen|verwirf\s+den\s+vorschlag)\s*[.!]?\s*$", _I)
# Lauf 5: "Vorschlag übernehmen" ersetzte den Text der Notiz samt der Änderung des Menschen,
# ohne Warnung. Wo der Vorschlag die Notiz überschreibt, warnt der erste Satz; erst dieser schreibt.
_TROTZDEM = re.compile(r"^\s*(?:vorschlag\s+trotzdem\s+(?:übernehmen|uebernehmen)|"
                       r"(?:übernimm|uebernimm)\s+den\s+vorschlag\s+trotzdem)\s*[.!]?\s*$", _I)
TROTZDEM_SATZ = "Vorschlag trotzdem übernehmen"

KEINER = "Es liegt gerade kein offener Vorschlag vor."
NICHT_LESBAR = "Die Vorschläge lassen sich gerade nicht lesen. Versuch es gleich noch einmal."


def satz_art(satz: Any) -> Optional[str]:
    """``"zeigen"``, ``"uebernehmen"``, ``"trotzdem"``, ``"verwerfen"`` oder ``None``."""
    if not isinstance(satz, str):
        return None
    for art, muster in (("zeigen", _ZEIGEN), ("uebernehmen", _UEBERNEHMEN), ("trotzdem", _TROTZDEM),
                        ("verwerfen", _VERWERFEN)):
        if muster.match(satz):
            return art
    return None


def _name(titel: Any) -> str:
    titel = re.sub(r"\s+", " ", str(titel or "")).strip()[:80]
    return f"„{titel}“" if titel else "ohne Titel"


def meldung_geaendert(titel: Any, uebernehmbar: bool = True, ueberschreibt: bool = False) -> str:
    """Die Meldung, wenn die Notiz inzwischen geändert wurde (für Chat und Standzeile)."""
    satz = f"Die Notiz {_name(titel)} wurde inzwischen geändert; mein Text liegt als Vorschlag vor. " \
           "Sag „Vorschlag zeigen“, dann zeige ich ihn"
    if uebernehmbar and ueberschreibt:
        satz += (", oder „Vorschlag übernehmen“; das würde deinen jetzigen Text ersetzen, darum frage ich "
                 "vorher noch einmal nach")
    elif uebernehmbar:
        satz += ", oder „Vorschlag übernehmen“, dann schreibe ich ihn an die Notiz"
    return satz + "."


def meldung_fehlt() -> str:
    return ("Die Notiz gibt es nicht mehr; mein Text liegt als Vorschlag vor. "
            "Sag „Vorschlag zeigen“, dann zeige ich ihn.")


def seit_iso(jetzt: Optional[datetime] = None) -> str:
    return ((jetzt or datetime.now(timezone.utc)) - timedelta(hours=GILT_H)).isoformat()


def _teile(zeile: Mapping[str, Any]) -> tuple[dict, dict]:
    pruefung = zeile.get("pruefung") if isinstance(zeile.get("pruefung"), Mapping) else {}
    uebergabe = zeile.get("uebergabe") if isinstance(zeile.get("uebergabe"), Mapping) else {}
    rueckweg = uebergabe.get("rueckweg") if isinstance(uebergabe.get("rueckweg"), Mapping) else {}
    return dict(pruefung), dict(rueckweg)


def zeigen(zeile: Optional[Mapping[str, Any]]) -> str:
    """Der Satz für "Vorschlag zeigen"."""
    if not zeile:
        return KEINER
    pruefung, _rueckweg = _teile(zeile)
    text = str(pruefung.get("vorschlag") or "").strip()
    if not text:
        return KEINER
    if len(text) > ZEIGEN_GRENZE:
        text = text[:ZEIGEN_GRENZE - 1].rstrip() + "…"
    if not uebernehmen(zeile)[0]:
        nachsatz = " Sag „Vorschlag verwerfen“, wenn du ihn nicht brauchst."
    elif ueberschreibt(zeile):
        nachsatz = (f" Er würde den jetzigen Text der Notiz ersetzen, auch deine Änderung. Sag „{TROTZDEM_SATZ}“, "
                    "dann ersetze ich ihn, oder „Vorschlag verwerfen“.")
    else:
        nachsatz = " Sag „Vorschlag übernehmen“, dann schreibe ich ihn an die Notiz, oder „Vorschlag verwerfen“."
    return f"Vorschlag für {_name(pruefung.get('titel'))}: {text}{nachsatz}"


SCHON_UEBERNOMMEN = ("Der Vorschlag ist schon übernommen; verwerfen lässt er sich nicht mehr. "
                     "Mit „Zurück“ nimmst du die Änderung zurück.")
SCHON_VERWORFEN = "Der Vorschlag ist schon verworfen; an der Notiz ist nichts geändert."
VERWORFEN = "Der Vorschlag ist verworfen; an der Notiz ist nichts geändert."


def verworfen_satz(zeile: Optional[Mapping[str, Any]], schon: bool = False) -> str:
    """Lauf 10 (36): "Vorschlag verwerfen" nennt, welcher Vorschlag verworfen ist (Titel der Notiz)."""
    try:
        pruefung, _rueckweg = _teile(zeile or {})
        titel = re.sub(r"\s+", " ", str(pruefung.get("titel") or "")).strip()
    except Exception:  # noqa: BLE001
        titel = ""
    if not titel:
        return SCHON_VERWORFEN if schon else VERWORFEN
    return (f"Der Vorschlag für {_name(titel)} ist {'schon ' if schon else ''}verworfen; "
            "an der Notiz ist nichts geändert.")


def schon_erledigt(art: str, erledigte: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Der ehrliche Satz, wenn kein Vorschlag mehr offen ist, der letzte aber schon erledigt wurde.

    Lauf 7: "Vorschlag verwerfen" nach dem Übernehmen sagte "nichts geändert". ``None``: kein
    erledigter Vorschlag bekannt, oder der Satz passt nicht (Zeigen sagt weiter "kein offener").
    """
    if art == "zeigen" or not erledigte:
        return None
    wie = (erledigte.get("pruefung") or {}).get("vorschlag_erledigt") if isinstance(
        erledigte.get("pruefung"), Mapping) else None
    if wie == "uebernommen":
        return SCHON_UEBERNOMMEN
    if wie == "verworfen":
        return verworfen_satz(erledigte, schon=True)
    return None


def ueberschreibt(zeile: Optional[Mapping[str, Any]]) -> bool:
    """Ersetzt das Übernehmen den Text der Notiz (Ausformulieren)? Dann warnt der erste Satz."""
    if not zeile:
        return False
    _pruefung, rueckweg = _teile(zeile)
    return rueckweg.get("vertrag") == "ausformulieren"


def warnung(zeile: Optional[Mapping[str, Any]]) -> str:
    """Der Satz auf "Vorschlag übernehmen", wenn das Übernehmen die Notiz überschreibt."""
    pruefung, _rueckweg = _teile(zeile or {})
    return (f"Achtung: Der Vorschlag ersetzt den jetzigen Text der Notiz {_name(pruefung.get('titel'))}, "
            f"auch deine Änderung von vorhin. Sag „{TROTZDEM_SATZ}“, dann ersetze ich ihn "
            "(direkt danach holt „Mach das rückgängig“ den alten Text zurück), oder „Vorschlag zeigen“ "
            "bzw. „Vorschlag verwerfen“. Geändert habe ich nichts.")


def uebernehmen(zeile: Optional[Mapping[str, Any]]) -> tuple[list[tuple[str, dict[str, Any]]], str]:
    """(feste Aufrufe, Antwortsatz) oder ([], Satz, warum nicht). Schreibt nichts."""
    from .ideen_rueckweg import schreibplan

    if not zeile:
        return [], KEINER
    pruefung, rueckweg = _teile(zeile)
    if pruefung.get("reason") == "card missing":
        return [], "Die Notiz gibt es nicht mehr; den Vorschlag kann ich nur zeigen."
    werte = pruefung.get("werte")
    vertrag = rueckweg.get("vertrag")
    if not isinstance(werte, Mapping) and isinstance(pruefung.get("vorschlag"), str) and vertrag in (
            "ausformulieren", "einschaetzen"):
        werte = {"text": pruefung["vorschlag"]}         # ältere Zeilen ohne "werte"
    if not isinstance(werte, Mapping) or not rueckweg.get("karte_id"):
        return [], "Diesen Vorschlag kann ich nur zeigen."
    aufrufe = schreibplan(rueckweg, dict(werte))
    if len(aufrufe) != 1:
        return [], "Diesen Vorschlag kann ich nur zeigen; die Bewertung setz bitte mit „Bewerte … mit …“."
    antwort = {"ausformulieren": f"Der Vorschlag steht jetzt in {_name(pruefung.get('titel'))}.",
               "einschaetzen": "Die Einschätzung steht jetzt an der Bubble, mit Verweis auf die Notiz."}
    return aufrufe, antwort.get(str(vertrag), "Der Vorschlag ist übernommen.")
