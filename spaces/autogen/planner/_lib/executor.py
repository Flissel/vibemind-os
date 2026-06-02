"""SoM Planner — Phase 6: Execution-Rückkanal (führt einen ready-Plan aus).

Ein `ready`-Plan ist bis Phase 5 nur ein Artefakt (exec.yaml). Dieses Modul führt
ihn SCHRITT FÜR SCHRITT aus:
- topologische Reihenfolge aus matrix.edges (depends_on),
- Output-Weitergabe zwischen Schritten (state-dict, input_X/{{state.X}}),
- Approval-Gate: vor Schritten mit approval_noetig PAUSIEREN (kein Auto-Versand),
- Ergebnis je Schritt in exec_state.yaml.

Ausführungs-Backend: `openfang:<agent>` (66x häufigster Target-Typ) wird DIREKT
via OpenFang /api/agents/{id}/message aufgerufen — der ganze SoM-Stack läuft eh
über OpenFang, kein Brain-Import nötig. Andere Target-Kinds (skill:/brain:/
supabase:) werden vorerst graceful übersprungen (status=unsupported) — kommen in
einem Folge-Schritt.

SICHERHEIT: nur `ready`-Pläne ausführbar; externe/irreversible Schritte NUR nach
Approval; sensible Werte nicht ins Log/Telegram (reuse questions/notify-Maskierung).
Reine Funktionen wo möglich (mock-bar) — Tests in tests/test_executor.py.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

import requests

_PLANNER = Path(__file__).resolve().parents[1]


def _load(name: str, file: Path):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_state = _load("som_state_exec", _PLANNER / "_lib" / "state.py")

_OPENFANG = os.environ.get("OPENFANG_URL", "http://127.0.0.1:4200").rstrip("/")


# ── Topologie: Schritte nach Abhängigkeit ordnen ─────────────────────────────
def topo_order(steps: list[dict], edges: list[dict]) -> list[str]:
    """Kahn-Topo-Sort über die exec-Schritte. steps: exec.yaml.steps
    (plan_step_id, reihenfolge). edges: matrix.edges ({from,to}). Tie-break nach
    `reihenfolge` dann id (deterministisch)."""
    ids = [s.get("plan_step_id") or s.get("id") for s in steps]
    ids = [i for i in ids if i]
    idset = set(ids)
    order_hint = {(s.get("plan_step_id") or s.get("id")): s.get("reihenfolge", 99)
                  for s in steps}

    indeg = {i: 0 for i in ids}
    adj: dict[str, list[str]] = {i: [] for i in ids}
    for e in edges or []:
        a, b = e.get("from"), e.get("to")
        if a in idset and b in idset:
            adj[a].append(b)
            indeg[b] += 1

    # Startmenge: indeg 0, sortiert nach reihenfolge dann id
    def _key(i): return (order_hint.get(i, 99), str(i))
    ready = sorted([i for i in ids if indeg[i] == 0], key=_key)
    out: list[str] = []
    while ready:
        n = ready.pop(0)
        out.append(n)
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
        ready.sort(key=_key)
    # Zyklus / nicht erfasste Reste hinten anhängen (defensiv; Validator/Topo-Check
    # in som_core fängt echte Zyklen schon vorher ab)
    for i in ids:
        if i not in out:
            out.append(i)
    return out


# ── Argument-Auflösung: input_X / {{state.X}} aus bisherigen Outputs ─────────
_PLACEHOLDER = re.compile(r"\{\{\s*state\.([\w.-]+)\s*\}\}")


def resolve_args(args: dict, state: dict) -> dict:
    """Ersetzt {{state.X}}-Platzhalter + input_*-Felder aus dem state-dict.

    - "{{state.cv_basis}}" → state['cv_basis']
    - konkrete_args-Felder die mit 'input_' beginnen + auf einen state-key zeigen
      werden mit dem Output verknüpft (Wert = state[<rest>] falls vorhanden,
      sonst der Originalwert als Hinweis).
    """
    out: dict[str, Any] = {}
    for k, v in (args or {}).items():
        if isinstance(v, str):
            m = _PLACEHOLDER.search(v)
            if m and m.group(1) in state:
                out[k] = state[m.group(1)]
                continue
        if k.startswith("input_"):
            ref = k[len("input_"):]
            # depends_on_output / input_cv_basis → versuche state[ref] ODER state[v]
            if ref in state:
                out[k] = state[ref]
                continue
            if isinstance(v, str) and v in state:
                out[k] = state[v]
                continue
        out[k] = v
    return out


# ── Ein Schritt ausführen (openfang: direkt) ─────────────────────────────────
def _resolve_openfang_id(agent_name: str) -> str | None:
    try:
        r = requests.get(f"{_OPENFANG}/api/agents", timeout=(3.05, 5))
        r.raise_for_status()
        data = r.json()
        agents = data if isinstance(data, list) else data.get("agents", [])
        for a in agents:
            if a.get("name") == agent_name:
                return a.get("id")
    except Exception:  # noqa: BLE001
        return None
    return None


def run_step(step: dict, state: dict, *, intent: str = "",
             caller: Callable[[str, str], dict] | None = None) -> dict:
    """Führt EINEN exec-Schritt aus. Rückgabe:
    {status: done|failed|unsupported, output, target}.

    caller: optionaler Hook (für Tests) statt echtem OpenFang-Call —
    signatur caller(target, message) -> dict.
    """
    target = step.get("execution_target") or ""
    sid = step.get("plan_step_id") or step.get("id") or "?"
    args = resolve_args(step.get("konkrete_args") or {}, state)
    # Nachricht an den Agent: konkrete_args als JSON + Intent-Kontext
    message = json.dumps({"task": args, "intent": intent, "step": sid}, ensure_ascii=False)

    if caller is not None:
        try:
            out = caller(target, message)
            return {"status": "done", "output": out, "target": target}
        except Exception as e:  # noqa: BLE001
            return {"status": "failed", "error": str(e)[:200], "target": target}

    if not target.startswith("openfang:"):
        # skill:/brain:/supabase: — noch nicht im SoM-Executor (Folge-Schritt)
        return {"status": "unsupported", "target": target,
                "note": f"Target-Typ '{target.split(':',1)[0]}' wird noch nicht direkt ausgeführt."}

    agent = target.split(":", 1)[1]
    aid = _resolve_openfang_id(agent)
    if not aid:
        return {"status": "failed", "target": target, "error": f"OpenFang-Agent '{agent}' nicht gefunden"}
    try:
        r = requests.post(f"{_OPENFANG}/api/agents/{aid}/message",
                          json={"message": message}, timeout=300)
        r.raise_for_status()
        resp = r.json()
        out = resp.get("response", resp)
        # Agent-Level-Fehler erkennen: viele Agents geben {"success": false, ...}
        # ODER eine response die mit Fehler-Markern beginnt → als failed werten,
        # nicht als done (sonst laufen Downstream-Schritte auf kaputten Daten).
        if _looks_failed(out):
            return {"status": "failed", "target": target,
                    "error": _err_text(out)[:200], "output": out}
        return {"status": "done", "output": out, "target": target}
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "target": target, "error": str(e)[:200]}


def _looks_failed(out: Any) -> bool:
    if isinstance(out, dict) and out.get("success") is False:
        return True
    s = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
    low = s.lower()
    return ('"success": false' in low or '"success":false' in low
            or low.lstrip().startswith(("fehler", "error", "traceback")))


def _err_text(out: Any) -> str:
    if isinstance(out, dict):
        return str(out.get("message") or out.get("error") or out)
    return str(out)


# ── Approval-Bestimmung ──────────────────────────────────────────────────────
# Targets/Stichworte mit irreversibler/externer Wirkung — Defense-in-depth:
# diese brauchen IMMER eine Freigabe, auch wenn Matrix/Validator das Gate vergessen
# haben. (Sicherheits-Constraint Phase 6: kein Auto-Schreiben/Versand/Löschen.)
_WRITE_TARGETS = ("openclaude-coder", "writer", "desktop", "skill-coordinator")
_WRITE_HINTS = ("schreib", "erstell", "speicher", "datei", "write", "create",
                "versand", "send", "mail", "lösch", "delete", "zahlung", "commit")


def needs_approval(step_id: str, matrix: dict, verdict: dict, step: dict | None = None) -> bool:
    """Approval wenn der Matrix-Node approval_noetig=true hat, der Validator ein
    approval_gate gesetzt hat, ODER (Defense-in-depth) der Schritt eine
    schreibende/externe Wirkung hat (Target/Beschreibung). Union = sicherste
    Variante; im Zweifel Gate."""
    for n in (matrix.get("nodes") or []):
        if (n.get("id") == step_id) and n.get("approval_noetig"):
            return True
    for g in (verdict.get("approval_gates") or []):
        gid = (g.get("plan_step_id") or g.get("step_id")) if isinstance(g, dict) else None
        if gid == step_id:
            return True
    # Defense-in-depth: schreibendes/externes Target ODER Beschreibung
    target = (step or {}).get("execution_target", "") if step else ""
    if any(w in target.lower() for w in _WRITE_TARGETS):
        return True
    node = next((n for n in (matrix.get("nodes") or []) if n.get("id") == step_id), {})
    blob = f"{node.get('beschreibung','')} {(step or {}).get('konkrete_args','')}".lower()
    if any(h in blob for h in _WRITE_HINTS):
        return True
    return False
