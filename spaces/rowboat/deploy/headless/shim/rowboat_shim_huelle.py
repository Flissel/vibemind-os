"""Startet den Claude-Shim fuer Rowboat auf dem Mini-PC - mit Budget-Waechter.

Nach dem Muster von Vibemind_V1/scripts/brain_devops_shim.py: der Shim
(claude_code_openai_shim.py, am PC ~/.local/bin) bleibt unveraendert; diese Huelle laedt ihn
und umhuellt nur `run_claude()`: davor `claude_budget.pruefen`, danach `buchen`. Jede
Werkzeugrunde ist ein eigener CLI-Aufruf und wird einzeln gebucht.

Shim, Werkzeugmodul, claude_budget.py und agent_budget.yaml sind KEINE Repo-Dateien von
vibemind-os: bauen.ps1 kopiert sie beim Uebertragen neben diese Datei (Bau-Kontext shim/).

Pflicht-Umgebung (compose.yml):
    VIBEMIND_AGENT=rowboat-server   ohne Agentennamen kein Budget, kein Start
    VIBEMIND_BUDGET_DIR=/budget     Zaehldateien im Volume; sonst laege das Konto im Container
                                    und waere nach jedem Neubau leer
    VIBEMIND_BUDGET_CONFIG          Pfad zu agent_budget.yaml (Dockerfile setzt /opt/shim/...)
Aufruf:
    python3 rowboat_shim_huelle.py --host 127.0.0.1 --port 8121
"""
from __future__ import annotations

import functools
import importlib.util
import os
import re
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

HIER = Path(__file__).resolve().parent
STANDARD_SHIM = HIER / "claude_code_openai_shim.py"


def agent_aus_umgebung(umgebung: Mapping[str, str]) -> str:
    agent = (umgebung.get("VIBEMIND_AGENT") or "").strip()
    if not agent:
        raise SystemExit("VIBEMIND_AGENT fehlt - ohne Agentennamen kein Budget, kein Start")
    return agent


def budget_ordner_pruefen(umgebung: Mapping[str, str]) -> Path:
    """claude_budget faellt ohne VIBEMIND_BUDGET_DIR auf ~ im Container zurueck - das waere ein
    fluechtiges zweites Konto. Darum hier Pflicht, und der Ordner muss schon existieren (Volume)."""
    roh = (umgebung.get("VIBEMIND_BUDGET_DIR") or "").strip()
    if not roh:
        raise SystemExit("VIBEMIND_BUDGET_DIR fehlt - Budget-Zaehler muss im Volume liegen, kein Start")
    ordner = Path(roh)
    if not ordner.is_dir():
        raise SystemExit(f"VIBEMIND_BUDGET_DIR existiert nicht: {ordner} - Volume fehlt, kein Start")
    return ordner


def umhuellen(shim: ModuleType, budget: Any, agent: str) -> None:
    """Ersetzt shim.run_claude durch eine Fassung mit Pruefen und Buchen (fail-closed)."""
    original = shim.run_claude

    @functools.wraps(original)
    def run_claude(**kwargs: Any) -> Any:
        try:
            entscheidung = budget.pruefen(agent)
            erlaubt, grund = bool(entscheidung.erlaubt), str(entscheidung.grund)
        except Exception:  # noqa: BLE001 - fail-closed wie im Abo-Wrapper
            erlaubt, grund = False, "waechter_fehler"
        if not erlaubt:
            raise shim.ShimError(f"budget: abgelehnt ({grund}) fuer {agent}")
        start = time.monotonic()
        ergebnis = "fehler"
        try:
            antwort = original(**kwargs)
            ergebnis = "ok"
            return antwort
        finally:
            try:
                budget.buchen(agent, ergebnis, time.monotonic() - start)
            except Exception as exc:  # noqa: BLE001 - Buchung darf das Ergebnis nie veraendern
                sys.stderr.write(f"budget: buchung fehlgeschlagen: {type(exc).__name__}\n")

    shim.run_claude = run_claude


def textaufrufe_abfangen(werkzeuge: ModuleType) -> None:
    """Macht einen als Text geschriebenen Aufruf ('name({...})') zum Fehler, damit der Shim in
    Runde 1 einmal nach einem gueltigen <tool_calls>-Block fragt, statt ihn als Endantwort
    durchzureichen (wie brain_devops_shim.py, 24.10.2026)."""
    original = werkzeuge.parse_reply

    @functools.wraps(original)
    def parse_reply(text: str, tools: list) -> Any:
        ergebnis = original(text, tools)
        if ergebnis.calls or ergebnis.errors or not isinstance(text, str):
            return ergebnis
        namen = [n for n in ((t.get("name") or (t.get("function") or {}).get("name")) for t in (tools or [])
                             if isinstance(t, dict)) if n]
        if not namen:
            return ergebnis
        muster = re.compile(r"(?<![\w.])(" + "|".join(map(re.escape, namen)) + r")\s*\(\s*[\[{]")
        treffer = muster.search(text)
        if treffer is None:
            return ergebnis
        return werkzeuge.ParsedReply(calls=[], text="", had_block=True, errors=[
            f"Werkzeugaufruf als Text geschrieben ({treffer.group(1)}(...)). Rufe Werkzeuge ausschliesslich "
            "ueber den <tool_calls>-Block auf, nie als Funktionsschreibweise im Antworttext."])

    werkzeuge.parse_reply = parse_reply


def _laden(pfad: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, pfad)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Modul nicht ladbar: {pfad}")
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul  # dataclasses brauchen den Eintrag
    spec.loader.exec_module(modul)
    return modul


def main() -> None:
    agent = agent_aus_umgebung(os.environ)
    budget_ordner_pruefen(os.environ)
    shim_pfad = Path(os.environ.get("SHIM_MODUL", "").strip() or STANDARD_SHIM)
    shim = _laden(shim_pfad, "claude_code_openai_shim")
    sys.path.insert(0, str(HIER))
    import claude_budget

    umhuellen(shim, claude_budget, agent)
    if shim.tool_calls_enabled():
        textaufrufe_abfangen(shim.load_tools_module())
    sys.stderr.write(f"budget-waechter aktiv fuer {agent} (shim: {shim_pfad})\n")
    shim.main()


if __name__ == "__main__":
    main()
