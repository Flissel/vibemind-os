"""Augen des Desktop-Space: "Bestätige: Fenster X ist vorn und zeigt Y".

Ein fester Aufruf, den jeder Space nach einer Handlung rufen kann (Ziel
``direct:core.desktop_augen:bestaetigen``, Faehigkeit ``desktop_bestaetigen``). Nur lesen:
Gelesen wird ueber den Blick (``desktop_blick``, Ereignis ``desktop.blick``) mit eigenem
Laufzeit-Kontext, verglichen wie in der Nachpruefung ``truth:desktop_sicht``
(core/desktop_sicht.py, dieselbe Funktion ``sicht_pruefen``).

* X: Teil des Fenstertitels des aktiven Fensters (vorn), Y: Teil des Namens eines sichtbaren
  Bedienelements. Optional ``programm`` (Muster auf die Programmdatei).
* Passwortfelder werden nie ausgelesen: Der Blick liefert nur Namen von Bedienelementen, nie
  ihren Inhalt, und ein Fenster mit Kennwortfeld (Sperrliste, UIA IsPassword) liefert nur
  "gesperrt". Dann ist die Bestaetigung "nicht prüfbar", nie "ja".
* Im Schritt-Ergebnis (HopResult, Verlauf) stehen nur Wahrheitswerte, eine Zaehlzeile und
  eine Kennung. Der Satz mit dem gesehenen Fenstertitel liegt fluechtig in
  core/fluechtige_saetze.py und geht nur in die Antwort an die App.

``bestaetigt``: True = gesehen wie erwartet, False = Lesbares passt nicht, None = nicht
pruefbar (gesperrt, Elemente nicht lesbar, Blick nicht moeglich). Der Lesevorgang selbst ist
ein Erfolg; was daraus folgt, entscheidet der Aufrufer. Als Nachpruefung eines eigenen
Schritts bleibt ``validator: {kind: truth:desktop_sicht}`` der Weg.
"""
from __future__ import annotations

import itertools
import re
import time
from typing import Any, Mapping, Optional, Tuple

from .fluechtige_saetze import abholen, merken

CAPABILITY = "desktop_bestaetigen"
_ZAEHLER = itertools.count(1)

_SATZ = re.compile(
    r"^\s*(?:(?:bitte\s+)?(?:bestätige|bestaetige|prüfe|pruefe)\s*:?\s*(?:,?\s*dass\s+)?)?"
    r"(?:das\s+)?fenster\s+[„\"'“]?(?P<fenster>[^„“”\"']{1,200}?)[“”\"']?\s+ist\s+vorn[e]?"
    r"(?:\s+und\s+zeigt\s+[„\"'“]?(?P<zeigt>[^„“”\"']{1,200}?)[“”\"']?)?\s*[.!?]?\s*$",
    re.IGNORECASE)


def auftrag_aus_satz(satz: Any) -> Optional[Tuple[str, Optional[str]]]:
    """("Editor", "Speichern") aus "Bestätige: Fenster Editor ist vorn und zeigt Speichern"."""
    if not isinstance(satz, str):
        return None
    m = _SATZ.match(" ".join(satz.split()))
    if not m:
        return None
    zeigt = (m.group("zeigt") or "").strip() or None
    return m.group("fenster").strip(), zeigt


def _text(wert: Any) -> Optional[str]:
    return " ".join(wert.split()) if isinstance(wert, str) and wert.strip() else None


def auftrag(value: Any) -> Optional[dict]:
    """Die Erwartung aus den Argumenten: Felder ``fenster``/``zeigt``/``programm`` oder ein Satz.

    Reihenfolge: ausdrueckliche Felder, dann der Satz im Argument, dann die Absicht des Plans
    (``_intent``). ``None``: kein Fenster genannt.
    """
    daten = value if isinstance(value, Mapping) else {"value": value}
    fenster, zeigt = _text(daten.get("fenster")), _text(daten.get("zeigt"))
    if fenster is None:
        for quelle in (daten.get("value"), daten.get("_intent")):
            gefunden = auftrag_aus_satz(quelle)
            if gefunden:
                fenster, zeigt = gefunden
                break
    if fenster is None:
        return None
    postcondition = {"fenstertitel_enthaelt": fenster}
    if zeigt:
        postcondition["element_enthaelt"] = zeigt
    programm = _text(daten.get("programm"))
    if programm:
        # Woertlich, nie als Muster: das Argument kommt aus Plaenen anderer Spaces (kein ReDoS).
        name = programm.lower().removesuffix(".exe")
        postcondition["programm"] = "^" + re.escape(name) + "$"
    return postcondition


def _laufzeit() -> dict:
    nummer = next(_ZAEHLER)
    plan_id = f"augen-{int(time.time() * 1000)}-{nummer:04d}"
    return {"plan_id": plan_id, "plan_revision": 1, "correlation_id": plan_id, "step_id": "s1"}


def bestaetigen(value: Any = None, *, lesen=None, **_: Any) -> dict:
    """Ziel ``direct:core.desktop_augen:bestaetigen``. Nur Wahrheitswerte und eine Kennung zurueck."""
    from .desktop_sicht import erwartung_aus, saetze_fuer_plan, sicht_pruefen

    postcondition = auftrag(value)
    if postcondition is None:
        return {"success": False, "error": "auftrag_unklar"}
    erwartet, fehler = erwartung_aus(postcondition, "")
    if erwartet is None:
        return {"success": False, "error": "auftrag_unklar", "grund": fehler}
    laufzeit = _laufzeit()
    ok, signal, grund = sicht_pruefen(erwartet, laufzeit, lesen=lesen)
    gesehen = " ".join(saetze_fuer_plan(laufzeit["plan_id"]))
    if ok is True:
        satz = "Bestätigt. " + gesehen
    elif ok is False:
        satz = "Nicht bestätigt. " + gesehen
    else:
        satz = "Nicht prüfbar. " + (gesehen or "Der Blick auf den Bildschirm ist gerade nicht möglich.")
    # Nur Wahrheitswerte ins Ergebnis; der Grund ist eine Zaehlzeile ohne Bildschirminhalt.
    wahrheit = {k: v for k, v in (signal or {}).items() if v is None or isinstance(v, bool)}
    return {"success": True, "bestaetigt": ok, "signal": wahrheit, "grund": str(grund)[:120],
            "satz_id": merken(satz.strip())}


def _ergebnis(ergebnis: Any) -> Any:
    if isinstance(ergebnis, Mapping) and isinstance(ergebnis.get("result"), Mapping) and "satz_id" not in ergebnis:
        return ergebnis["result"]
    return ergebnis


def antwort(ergebnis: Any) -> Optional[str]:
    """Der Satz fuer die App (mit gesehenem Fenstertitel); einmal abgeholt, dann weg."""
    try:
        ergebnis = _ergebnis(ergebnis)
        if not isinstance(ergebnis, Mapping) or ergebnis.get("success") is not True:
            return None
        return abholen(ergebnis.get("satz_id"))
    except Exception:  # noqa: BLE001
        return None


def protokoll(ergebnis: Any) -> Optional[str]:
    """Satz fuer Plan-Verlauf und Protokolle: nur das Urteil."""
    try:
        ergebnis = _ergebnis(ergebnis)
        if not isinstance(ergebnis, Mapping) or ergebnis.get("success") is not True:
            return None
        urteil = {True: "ja", False: "nein"}.get(ergebnis.get("bestaetigt"), "nicht prüfbar")
        return f"Bestätigung am Bildschirm: {urteil} (Titel nur in der Antwort an die App)."
    except Exception:  # noqa: BLE001
        return None
