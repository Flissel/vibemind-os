"""Schritt-Waechter: Urteilsfunktion.

Beurteilt einen ausgefuehrten Plan-Schritt: auffaellig (True), unauffaellig
(False, nur wenn ein Modell es bestaetigt) oder nicht beurteilt (None).
Ein nicht beurteilter Schritt gilt nie als unauffaellig.

`beurteile` und `modus` sind reine Funktionen, kein Netz; das Modell kommt nur
als Callable `frage` herein. Die Klasse `Schatten` schreibt die Zeilen in die
Schatten-Datei (einziger Seiteneffekt des Moduls).

Schritte ohne Zeile (der Anschluss in plan_executor beurteilt sie nicht):
- wegen eines gescheiterten Vorgaengers uebersprungene Schritte
- vom start_when-Gate gesperrte Schritte
- Eltern-Sammelschritte von repeat-Hops
- Pending-Ergebnisse (Agenten-Auftraege auf dem Nutzerweg; das Ergebnis kommt
  spaeter ueber den Nachfasser und nie ueber _exec_hop)
- Abstuerze des Kerns, fuer die execute() selbst ein Ergebnis baut
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from .capability_targets import result_indicates_failure

MAX_PRUEFTEXT = 4000
MAX_GRUND = 500
_MAX_PARAMETER = 1000

_MODI = ("0", "schatten", "sichtbar")

# Platzhalter in Bezeichner-Form. Deckt die beiden Renderer-Formen aus
# plan_executor.py (Zeile 52: {{state.X}}; Zeilen 2276-2278: {{item}},
# {{loop.index}}, {{loop.index0}}, {{loop.value}}) und vom Planner erfundene
# Bezeichner wie {{output_var}}. Ausdruecke mit $, Leerzeichen oder Operatoren
# im Inhalt (etwa n8n {{ $json.x }}) zaehlen nicht.
_PLATZHALTER = re.compile(r"\{\{\s*[A-Za-z_][\w.\[\]]*\s*\}\}")

_FRAGE_PRAEFIXE = ("please enter", "which idea", "what would you like me to")


@dataclass(frozen=True)
class Urteil:
    auffaellig: Optional[bool]
    grund: str
    quelle: str  # "regel", "modell" oder "keine"


_KEIN_URTEIL = Urteil(None, "", "keine")


def _als_text(wert: object, grenze: int) -> str:
    """Text eines Werts, gekuerzt auf `grenze` Zeichen."""
    if wert is None:
        return ""
    if isinstance(wert, str):
        text = wert
    else:
        try:
            text = json.dumps(wert, ensure_ascii=False, default=str)
        except Exception:
            text = str(wert)
    return text[:grenze]


def _regel(ok: bool, ergebnis: object, fehler: Optional[str], parameter: object,
           vorgaenger_auffaellig: bool) -> Optional[str]:
    """Grund des ersten Regeltreffers oder None."""
    if not ok:
        return "Schritt meldet Fehler: " + (_als_text(fehler, 200) or "ohne Angabe")
    if result_indicates_failure(ergebnis):
        return "Ergebnis zeigt einen Fehlschlag"
    text = _als_text(ergebnis, MAX_PRUEFTEXT)
    if _PLATZHALTER.search(text):
        return "Ergebnis enthaelt einen unaufgeloesten Platzhalter"
    klein = text.strip().lower()
    if any(klein.startswith(p) for p in _FRAGE_PRAEFIXE):
        return "Ergebnis ist eine Rueckfrage statt einer Ausfuehrung"
    if _PLATZHALTER.search(_als_text(parameter, MAX_PRUEFTEXT)):
        return "Parameter enthaelt einen unaufgeloesten Platzhalter"
    if vorgaenger_auffaellig:
        return "Vorgaenger-Schritt war auffaellig"
    return None


def _modell_urteil(frage: Callable[[str], str], ergebnis: object,
                   parameter: object) -> Urteil:
    try:
        text = ("Parameter: " + _als_text(parameter, _MAX_PARAMETER)
                + "\nErgebnis: " + _als_text(ergebnis, MAX_PRUEFTEXT))
        antwort = json.loads(frage(text))
        folge = antwort["folge"]
        grund = antwort.get("grund", "")
        if folge not in ("nichts", "pruefen") or not isinstance(grund, str):
            return _KEIN_URTEIL
        return Urteil(folge == "pruefen", grund, "modell")
    except Exception:
        return _KEIN_URTEIL


def beurteile(*, ok: bool, ergebnis: object, fehler: Optional[str], parameter: object,
              vorgaenger_auffaellig: bool,
              frage: Optional[Callable[[str], str]] = None) -> Urteil:
    grund = _regel(ok, ergebnis, fehler, parameter, vorgaenger_auffaellig)
    if grund is not None:
        return Urteil(True, grund, "regel")
    if frage is None:
        return _KEIN_URTEIL
    return _modell_urteil(frage, ergebnis, parameter)


def modus(umgebung: Optional[Mapping[str, str]] = None) -> str:
    """Schalter SCHRITT_WAECHTER: "0", "schatten" oder "sichtbar"; sonst "0"."""
    env = os.environ if umgebung is None else umgebung
    wert = str(env.get("SCHRITT_WAECHTER", "0")).strip().lower()
    return wert if wert in _MODI else "0"


def vorgabe_pfad() -> Path:
    """<the_brain>/data/schritt_waechter.jsonl, vom Ort dieser Datei aus."""
    return Path(__file__).resolve().parent.parent / "data" / "schritt_waechter.jsonl"


class Schatten:
    """Schreibt je Schritt eine JSON-Zeile in die Schatten-Datei."""

    def __init__(self, pfad: Path) -> None:
        self._pfad = Path(pfad)
        self._sperre = threading.Lock()

    def schreibe(self, *, plan_id: str, step_id: str, capability: Optional[str],
                 urteil: Urteil, dauer_s: float, ts: Optional[float] = None) -> None:
        zeile = json.dumps({
            "ts": time.time() if ts is None else ts,
            "plan_id": plan_id,
            "step_id": step_id,
            "capability": capability,
            "auffaellig": urteil.auffaellig,
            "grund": urteil.grund[:MAX_GRUND],
            "quelle": urteil.quelle,
            "dauer_s": dauer_s,
        }, ensure_ascii=False) + "\n"
        with self._sperre:
            self._pfad.parent.mkdir(parents=True, exist_ok=True)
            with open(self._pfad, "a", encoding="utf-8", newline="") as f:
                f.write(zeile)
