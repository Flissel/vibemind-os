"""Event-Huelle fuer die Claude-Shims des Event-Claws (Gruppen und Empfang).

Laedt die Budget-Huelle des Shim-Images (`shim_huelle.py`, aus dem Repo bewerbung, im Image
unter /opt/shim, nicht in diesem oeffentlichen Repo) aus dem eigenen Ordner und aendert
genau eine Stelle: Lehnt der Budget-Waechter wegen der Tagesgrenze ab, antwortet der Shim
mit dem Text aus `VIBEMIND_BUDGET_ANTWORT` statt mit einem Fehler (OpenClaw zeigte sonst
"LLM request failed."). Alle anderen Ablehnungen und Fehler bleiben Fehler. Die
Schutzfunktionen der Basis (eingebaute CLI-Werkzeuge aus, Transkripte aufraeumen,
Werkzeugaufrufe als Block) bleiben unveraendert, denn ihr main() laeuft wie bisher.

Technisch: Vor `main()` der Basis wird deren Modulfunktion `umhuellen` ersetzt. Die neue
Fassung ruft die alte (Waechter um run_claude) und legt direkt darueber eine Schicht, die
nur `ShimError("budget: abgelehnt (tagesgrenze) ...")` in eine Antwort verwandelt. Das
Aufraeumen der Transkripte haengt die Basis danach aussen herum; es laeuft also weiter nach
jedem Aufruf.

Umgebung (compose):
    VIBEMIND_BUDGET_ANTWORT   Text bei erschoepfter Tagesgrenze; leer/fehlend = wie die Basis
    (alles andere wie shim_huelle.py: SHIM_TOOL_CALLS, VIBEMIND_AGENT, VIBEMIND_BUDGET_DIR, ...)
Aufruf:
    python3 /opt/shim/event_shim_huelle.py --host 0.0.0.0 --port 8125
"""
from __future__ import annotations

import functools
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

HIER = Path(__file__).resolve().parent
BASIS = HIER / "shim_huelle.py"
# So beginnt die Ablehnung der Basis bei erschoepfter Tagesgrenze (claude_budget: Grund
# "tagesgrenze"); die Klammer schliesst andere Gruende mit gleichem Anfang aus.
TAGESGRENZE = "budget: abgelehnt (tagesgrenze)"


def antwort_aus_umgebung(umgebung: Mapping[str, str]) -> str | None:
    return (umgebung.get("VIBEMIND_BUDGET_ANTWORT") or "").strip() or None


def antwort_einbauen(basis: ModuleType, antwort: str | None) -> None:
    """Ersetzt `basis.umhuellen`; ohne Antwort bleibt die Basis unberuehrt."""
    if not antwort:
        return
    original = basis.umhuellen

    def umhuellen(shim: ModuleType, budget: Any, agent: str) -> None:
        original(shim, budget, agent)
        innen = shim.run_claude

        @functools.wraps(innen)
        def run_claude(**kwargs: Any) -> Any:
            try:
                return innen(**kwargs)
            except shim.ShimError as exc:
                if not str(exc).startswith(TAGESGRENZE):
                    raise
                return {"result": antwort, "is_error": False, "usage": {},
                        "stop_reason": "end_turn"}

        shim.run_claude = run_claude

    basis.umhuellen = umhuellen


def _laden(pfad: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, pfad)
    if not pfad.is_file() or spec is None or spec.loader is None:
        raise SystemExit(f"event-huelle: {pfad} fehlt (Basis-Huelle des Shim-Images)")
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    spec.loader.exec_module(modul)
    return modul


def main() -> None:
    antwort = antwort_aus_umgebung(os.environ)
    basis = _laden(BASIS, "shim_huelle")
    antwort_einbauen(basis, antwort)
    sys.stderr.write(f"event-huelle: budget-antwort={'ja' if antwort else 'nein'}\n")
    basis.main()


if __name__ == "__main__":
    main()
