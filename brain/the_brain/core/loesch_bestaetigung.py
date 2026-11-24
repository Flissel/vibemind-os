"""Löschen im Ideen-Space nie direkt (Prüflauf 24.11.2026: "Lösche die Karte X" fiel an den Planer).

Der Satz "Lösche die Karte X" (oder "Lösche diese Bubble") antwortet zuerst mit einer Rückfrage,
die die Karte nennt (bzw. die Bubble und die Zahl ihrer Karten).
Erst "Ja, löschen" löst den festen Aufruf aus, und nur:
- binnen ``GILT_S`` Sekunden nach genau dieser Rückfrage,
- wenn dazwischen kein anderer Satz kam (jeder andere Satz verwirft sie).

Der bestätigte Plan trägt einen Schlüssel, den der Plan-Ausführer genau einmal einlöst, und
nur für genau das Ereignis und die Kennung, nach denen gefragt wurde. Ein Lösch-Schritt ohne
gültigen Schlüssel (z. B. vom Planer) läuft nicht. Gelöscht wird unter der Kennung der Änderung
des Plans; "Zurück" holt die Karte mit ihren Verbindungen wieder.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from typing import Any, Dict, Optional

GILT_S = 120
SCHLUESSEL_GILT_S = 120
EREIGNISSE = frozenset({"idea.delete", "bubble.delete"})

_JA_LOESCHEN = re.compile(
    r"^\s*ja\s*[,!.]?\s*(?:bitte\s+)?l(?:ö|oe)sch(?:e|en)?(?:\s+(?:sie|es|die\s+karte|die\s+bubble))?\s*[.!]?\s*$",
    re.IGNORECASE)
OHNE_FRAGE = ("Es ist gerade kein Löschen zur Bestätigung offen. "
              "Sag „Lösche die Karte …“ oder „Lösche diese Bubble“, dann frage ich zuerst nach.")
OHNE_SCHLUESSEL = ("Gelöscht wird nur nach Rückfrage. "
                   "Sag „Lösche die Karte …“ oder „Lösche diese Bubble“, dann frage ich zuerst nach.")

_sperre = threading.Lock()
_offen: Dict[str, Any] = {}                  # {"ereignis", "argumente", "antwort", "bubble_id", "bis"}
_schluessel: Dict[str, Dict[str, Any]] = {}  # Schlüssel -> {"ereignis", "id", "bis"}


def _jetzt() -> float:
    return time.time()


def ist_bestaetigung(satz: Any) -> bool:
    return isinstance(satz, str) and bool(_JA_LOESCHEN.match(satz))


def frage_karte(titel: str) -> str:
    name = f"die Karte „{titel}“" if titel else "diese Karte"
    return (f"Soll ich {name} löschen? Ihre Verbindungen gehen mit. Mit „Zurück“ holst du sie wieder. "
            "Sag „Ja, löschen“.")


def frage_bubble(titel: str, karten: Optional[int]) -> str:
    """Rückfrage vor dem Löschen der offenen Bubble, mit der Zahl ihrer Karten.

    Die Datenbank löscht nur die Bubble; ihre Karten bleiben gespeichert, gehören dann aber zu
    keiner Bubble mehr (canvas_nodes.linked_idea_id: ON DELETE SET NULL). "Zurück" hängt sie
    wieder an (Änderungsbuch, supabase/tests/zurueck).
    """
    name = f"die Bubble „{titel}“" if titel else "diese Bubble"
    if karten is None:
        inhalt = "Wie viele Karten sie hat, weiß ich gerade nicht. "
    elif karten == 0:
        inhalt = "Sie hat keine Karten. "
    else:
        zahl = "eine Karte" if karten == 1 else f"{karten} Karten"
        inhalt = (f"Sie hat {zahl}; die bleiben gespeichert, gehören dann aber zu keiner Bubble mehr. ")
    return (f"Soll ich {name} löschen? {inhalt}Mit „Zurück“ holst du alles wieder. "
            "Sag „Ja, löschen“.")


def fragen(ereignis: str, argumente: Dict[str, Any], antwort: str, bubble_id: str = "") -> None:
    """Die Rückfrage ist gestellt; nur "Ja, löschen" direkt danach löst genau diesen Aufruf aus."""
    with _sperre:
        _offen.clear()
        _offen.update({"ereignis": ereignis, "argumente": dict(argumente), "antwort": antwort,
                       "bubble_id": bubble_id, "bis": _jetzt() + GILT_S})


def verwerfen() -> None:
    with _sperre:
        _offen.clear()


def satz_pruefen(satz: Any) -> Optional[Dict[str, Any]]:
    """Für jeden Satz vor allem anderen aufrufen.

    ``None``: keine Bestätigung; eine offene Rückfrage verfällt (gebunden an genau sie).
    ``{"ereignis", "argumente", "antwort", "bubble_id"}``: bestätigt, Argumente mit Schlüssel.
    ``{"satz": ...}``: "Ja, löschen" ohne gültige Rückfrage, nichts gelöscht.
    """
    jetzt = _jetzt()
    with _sperre:
        offen = dict(_offen)
        _offen.clear()
        if not ist_bestaetigung(satz):
            return None
        if not offen or offen.get("bis", 0) < jetzt:
            return {"satz": OHNE_FRAGE}
        schluessel = secrets.token_hex(8)
        for alt in [s for s, eintrag in _schluessel.items() if eintrag["bis"] < jetzt]:
            _schluessel.pop(alt, None)
        _schluessel[schluessel] = {"ereignis": offen["ereignis"], "id": offen["argumente"].get("id"),
                                   "bis": jetzt + SCHLUESSEL_GILT_S}
    return {"ereignis": offen["ereignis"], "antwort": offen["antwort"], "bubble_id": offen["bubble_id"],
            "argumente": {**offen["argumente"], "bestaetigung": schluessel}}


def einloesen(schluessel: Any, ereignis: str, kennung: Any) -> bool:
    """Genau einmal wahr, und nur für das Ereignis und die Kennung der Rückfrage."""
    if not isinstance(schluessel, str):
        return False
    with _sperre:
        eintrag = _schluessel.pop(schluessel, None)
    return (eintrag is not None and eintrag["bis"] >= _jetzt()
            and eintrag["ereignis"] == ereignis and eintrag["id"] == kennung)
