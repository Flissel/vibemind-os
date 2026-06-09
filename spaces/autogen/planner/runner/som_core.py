"""SoM Planner — Phase 1: RoundRobin-Kern (sequentieller Durchlauf).

Planner → Executor → Validator, feste Reihenfolge. Jede Rolle = ein
openfang_som_agent_wrapper-Aufruf (claude-code → JSON). Ergebnisse landen als
plan/exec/verdict.yaml im lokalen State.

Bewusst ein dünner Python-Loop (kein AutoGen) — der Kern ist deterministisch
sequentiell, AutoGen-Overhead bringt hier nichts. AutoGen/SelectorGroupChat
kommt in Phase 2, wo dynamisch gewählt werden muss.

Aufruf:
    python spaces/autogen/planner/runner/som_core.py --intent "..." [--run-id X]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

_PLANNER = Path(__file__).resolve().parents[1]            # spaces/autogen/planner/
_REPO = _PLANNER.parents[2]                               # vibemind-os/
_VIBEMIND_V1 = _REPO.parent                               # Vibemind_V1/
_WRAPPER = _VIBEMIND_V1 / "scripts" / "openfang_som_agent_wrapper.py"
_VENV_PY = _VIBEMIND_V1 / "vibemind-os" / "voice" / ".venv312" / "Scripts" / "python.exe"


def _load(name: str, file: Path):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_state = _load("som_state", _PLANNER / "_lib" / "state.py")
_tools = _load("som_tools", _PLANNER / "_lib" / "tools.py")
_matrix = _load("som_matrix", _PLANNER / "_lib" / "matrix.py")
_notify = _load("som_notify", _PLANNER / "_lib" / "notify.py")
_sources = _load("som_sources", _PLANNER / "_lib" / "sources.py")
_questions = _load("som_questions", _PLANNER / "_lib" / "questions.py")


# ── Phase C — Live-Fortschritt an den Brain pushen (Container-Boundary-sicher) ─
# Der Detached-Runner schreibt run_meta nur lokal; der Brain-Container sieht das
# nicht. Wir POSTen jede Status-Transition zusätzlich an POST /api/som/progress
# (best-effort, kurzes Timeout — Progress darf den Run NIE blockieren/brechen).
def _publish_progress(run_id: str, status: str, intent: str = "", source: str = "som") -> None:
    if os.environ.get("SOM_PROGRESS_PUSH", "1") in ("0", "false", "False"):
        return
    base = os.environ.get("BRAIN_URL", "http://localhost:5000").rstrip("/")
    try:
        import requests
        requests.post(base + "/api/som/progress", json={"run_id": run_id, "status": status,
                                 "intent": intent, "source": source}, timeout=2)
        # E2E-Trace (Phase 2b): wenn vom Brain eine trace_id mitkam, jede SoM-Stufe
        # auch als Stage-Event an den durchgaengigen Trace pushen — so erscheint die
        # HEAVY-Kette (planner/executor/validator/matrix) per-Schritt in /api/trace/{id}.
        _trace = os.environ.get("SOM_TRACE_ID", "").strip()
        if _trace:
            requests.post(f"{base}/api/trace/{_trace}/stage",
                          json={"stage": status, "component": source, "outcome": run_id},
                          timeout=2)
    except Exception:  # noqa: BLE001 — Push best-effort, nie blockierend
        pass


def _call_agent(role: str, user_input: str, run_id: str = "") -> dict:
    """Ruft den SoM-Wrapper für eine Rolle. Nutzt venv312-Python.
    Übergibt run_id, damit der aktive Agent via plan_write in den richtigen
    State schreibt."""
    py = str(_VENV_PY) if _VENV_PY.exists() else sys.executable
    env = dict(os.environ)
    env["SOM_ROLE"] = role
    if run_id:
        env["SOM_RUN_ID"] = run_id
    env.setdefault("SOM_MAX_TURNS", "12")
    # Timeout-Alignment (Fix 2026-06-02): innerer claude-Timeout + 60s Puffer =
    # äußerer subprocess-Timeout. So feuert der innere zuerst und der Wrapper kann
    # einen claude-Timeout sauber als JSON melden, statt vom äußeren als
    # "non-JSON" maskiert zu werden.
    claude_timeout = int(os.environ.get("SOM_CLAUDE_TIMEOUT_S", "180"))
    env["SOM_CLAUDE_TIMEOUT_S"] = str(claude_timeout)
    outer_timeout = claude_timeout + 60
    proc = subprocess.run(
        [py, str(_WRAPPER)],
        input=user_input,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=outer_timeout,
        env=env,
    )
    raw = (proc.stdout or "").strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {"error": "wrapper non-JSON", "raw": raw[:400], "stderr": (proc.stderr or "")[:300]}


def _capabilities_block() -> str:
    # kind zeigen (search/content/file/action/orchestration/plan), damit der
    # Planner Content-Erstellung nicht mit Search-Caps plant (run_0020-Mangel).
    caps = _tools.capability_list()
    lines = [f"- {c['name']} [{c.get('kind', '?')}]: {c['description'][:120]}" for c in caps]
    return "\n".join(lines)


def _load_context(intent: str, context_file: str | None) -> str:
    """Baut den Daten-Kontext für den Planner: echter Quellen-Index (welche
    Dateien EXISTIEREN — gegen Pfad-Raten) + nicht-sensible Inhalte (sensible
    Werte maskiert), plus optional eine explizite context_file.

    Generisch + sicherheitsbewusst: der Planner sieht WAS es gibt und rät keine
    Pfade/Caps mehr; sensible Werte (BG-Nr, IBAN, Beträge) erscheinen NIE im
    Prompt (sources.redact + sensitive-Klassifikation)."""
    parts: list[str] = []

    # 1. Automatischer Quellen-Index (der große Hebel gegen Halluzination)
    try:
        block = _sources.context_block(intent)
        if block:
            parts.append(block)
    except Exception:  # noqa: BLE001 — Index-Fehler darf Planung nie brechen
        pass

    # 2. Optionale explizite Kontext-Datei (was bereits vorliegt)
    if context_file:
        p = Path(context_file)
        if p.exists():
            try:
                # auch hier redaktieren (Defense-in-depth, falls sensibel)
                raw = p.read_text(encoding="utf-8", errors="ignore")[:6000]
                parts.append("ZUSÄTZLICHER KONTEXT (bereits vorliegende Daten):\n"
                             + _sources.redact(raw))
            except Exception:  # noqa: BLE001
                pass

    return "\n\n".join(parts)


def _derive_questions(exec_plan: dict | None, verdict: dict | None) -> list[dict]:
    """Leitet die offenen Fragen (Entscheidungsgrundlage) aus exec + verdict ab.

    Quellen:
    - exec.benoetigte_daten_fehlen → fehlende Pflicht-Daten (Frage an den Nutzer)
    - verdict.approval_gates → was freigegeben werden muss (grund)
    Rückgabe: [{id, frage, typ}] für Telegram-Anzeige + answers_needed.yaml.
    """
    questions: list[dict] = []
    exec_plan = exec_plan or {}
    verdict = verdict or {}

    for i, item in enumerate(exec_plan.get("benoetigte_daten_fehlen") or [], 1):
        frage = item if isinstance(item, str) else (item.get("frage") or str(item))
        questions.append({"id": f"daten_{i}", "frage": frage, "typ": "daten"})

    for i, gate in enumerate(verdict.get("approval_gates") or [], 1):
        if isinstance(gate, dict):
            grund = gate.get("grund") or gate.get("message") or ""
            step = gate.get("plan_step_id") or gate.get("step_id") or ""
            frage = f"Freigabe für '{step}': {grund}".strip(": ")
        else:
            frage = f"Freigabe: {gate}"
        questions.append({"id": f"gate_{i}", "frage": frage, "typ": "approval"})

    # Aufbereitung: interne Notizen raus, Tech/Pfade weg, menschlich formuliert,
    # dedupliziert (User-Feedback 2026-06-02 — Telegram zeigte Validator-Jargon).
    try:
        cleaned = _questions.clean_questions(questions)
        if cleaned:                       # nur ersetzen wenn was übrig bleibt
            return cleaned
    except Exception:  # noqa: BLE001 — Aufbereitung darf den Run nie brechen
        pass
    return questions


def _unwrap_envelope(text: str) -> str:
    """Packt das Brain-Argument-Envelope aus ({"value":...,"_intent":...}), falls
    es bis hierher durchgerutscht ist. Defense-in-depth: der Wrapper unwrapped
    schon, aber andere Pfade (build_executor direkt) liefern den rohen Envelope —
    sonst steht er im run_meta + Dashboard (kosmetischer Bug 2026-06-08)."""
    t = (text or "").strip()
    if not (t.startswith("{") and t.endswith("}")):
        return text
    try:
        obj = json.loads(t)
    except (json.JSONDecodeError, ValueError):
        return text
    if not isinstance(obj, dict):
        return text
    for key in ("value", "_intent", "intent", "message"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return text


def run(intent: str, run_id: str | None = None, context_file: str | None = None) -> dict:
    """Öffentlicher Entrypoint: führt die Pipeline aus + pusht das Ergebnis an
    Telegram (best-effort, ereignis-getrieben — hält den Nutzer pro Run aktuell).
    Notify abschaltbar via env SOM_NOTIFY=0."""
    intent = _unwrap_envelope(intent)   # Envelope nie in State/Dashboard/Telegram
    # ── Phase D (Kill-Switch SOM_LANGGRAPH): Erststart über den Graph, damit ein
    #    needs_input-Lauf einen durable Checkpoint anlegt, den resume() lädt.
    #    context_file wird hier nicht über den Graph gereicht (nur der Resume-Pfad
    #    braucht Checkpointing) → bei gesetztem context_file imperativer Pfad.
    if os.environ.get("SOM_LANGGRAPH") in ("1", "true", "True") and not context_file:
        try:
            somg = _load("som_graph_run", _PLANNER / "runner" / "som_graph.py")
            return somg.run(intent, run_id=run_id)
        except Exception as e:  # noqa: BLE001 — Graph-Fehler darf den Erststart nicht killen
            print(f"[som_core.run] LangGraph-Pfad fiel zurück: {e}", file=sys.stderr)

    result = _run_inner(intent, run_id=run_id, context_file=context_file)
    try:
        mid = _notify.notify_run(result)
        # Frage-Nachricht-ID für die Reply-Korrelation persistieren (Schritt 4)
        if mid and result.get("offene_fragen"):
            try:
                _state.answers_message_id(result["run_id"], mid)
                _state.answers_map_set(mid, result["run_id"])
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 — Notify bricht den Run nie ab
        pass
    return result


def resume(run_id: str, answers: dict[str, str] | None = None, cancel: bool = False) -> dict:
    """Nimmt einen bei needs_input/awaiting_approval pausierten Run wieder auf.

    - cancel=True → Run als 'cancelled' markieren + Telegram-Push, KEIN Re-Run.
    - sonst: Antworten in answers_needed.yaml eintragen, als bekannte Fakten in
      den Planner-Kontext geben und die Pipeline (gleiche run_id) erneut laufen
      lassen. Re-plant MIT den Antworten + Daten-Kontext. run_id bleibt stabil
      (Session), Artefakt-Versionen bumpen.

    Zwei Modi (unterschieden an run_meta.phase):
    - phase == "executing" + awaiting_approval → EXEC-FREIGABE: die Antwort ist
      ja/nein für ein Approval-Gate → som_execute.continue_run (Phase 6), KEIN
      Re-Plan.
    - sonst → PLAN-FRAGE: re-plant mit den Antworten als Fakten (Phase 5).
    Pusht das Ergebnis wie run() an Telegram.
    """
    meta = _state.run_meta(run_id)
    intent = meta.get("intent") or ""
    if not intent:
        return {"run_id": run_id, "status": "failed", "error": "unbekannter run_id / kein Intent"}

    # ── EXEC-Freigabe (Phase 6): pausierter Ausführungs-Lauf am Approval-Gate ──
    if meta.get("phase") == "executing" and meta.get("status") == "awaiting_approval":
        approved = (not cancel)
        if answers:
            txt = " ".join(str(v) for v in answers.values()).lower()
            if any(w in txt for w in ("nein", "no", "stop", "abbruch")):
                approved = False
            elif any(w in txt for w in ("ja", "yes", "ok", "freigabe", "los")):
                approved = True
        try:
            somx = _load("som_execute_resume", _PLANNER / "runner" / "som_execute.py")
            return somx.continue_run(run_id, approved=approved)
        except Exception as e:  # noqa: BLE001
            return {"run_id": run_id, "status": "failed", "error": f"exec-resume: {e}"}

    if cancel:
        _state.run_meta_update(run_id, status="cancelled")
        result = {"run_id": run_id, "intent": intent, "status": "cancelled", "steps": {}}
        try:
            _notify.notify_run(result)
        except Exception:  # noqa: BLE001
            pass
        return result

    # ── Phase D (Kill-Switch SOM_LANGGRAPH): Plan-Frage-Resume über den
    #    LangGraph-StateGraph (durable interrupt/checkpoint). Default aus →
    #    imperativer Pfad unten bleibt. Nur für Plan-Fragen (Exec-Freigabe oben
    #    ist bereits an som_execute delegiert).
    if os.environ.get("SOM_LANGGRAPH") in ("1", "true", "True"):
        try:
            somg = _load("som_graph_resume", _PLANNER / "runner" / "som_graph.py")
            return somg.resume(run_id, answers=answers)
        except Exception as e:  # noqa: BLE001 — Graph-Fehler darf den Resume nicht killen
            # Fallback auf den imperativen Pfad (nie schlechter als heute)
            print(f"[som_core.resume] LangGraph-Pfad fiel zurück: {e}", file=sys.stderr)

    # Antworten eintragen + als Kontext-Block formatieren
    answers_context = ""
    if answers:
        _state.answers_set(run_id, answers)
    data = _state.answers_read(run_id)
    beantwortet = [q for q in (data.get("questions") or []) if (q.get("antwort") or "").strip()]
    if beantwortet:
        lines = ["BEANTWORTETE RÜCKFRAGEN (vom Nutzer bestätigt — als Fakten behandeln):"]
        for q in beantwortet:
            lines.append(f"- {q.get('frage', q.get('id'))}\n  → ANTWORT: {q['antwort']}")
        answers_context = "\n".join(lines)

    result = _run_inner(intent, run_id=run_id, answers_context=answers_context)
    try:
        mid = _notify.notify_run(result)
        if mid and result.get("offene_fragen"):
            try:
                _state.answers_message_id(result["run_id"], mid)
                _state.answers_map_set(mid, result["run_id"])
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    return result


def _run_inner(intent: str, run_id: str | None = None, context_file: str | None = None,
               answers_context: str = "") -> dict:
    if run_id is None:
        # deterministische, aber eindeutige id ohne Date.now-Tabu: hash über intent + run-count
        existing = len(_state.list_runs())
        run_id = f"run_{existing + 1:04d}"

    # Beim Resume existiert der Run schon — run_create legt run_meta neu an, aber
    # die versionierten Artefakte (plan/exec/...) bleiben + bumpen weiter. Intent
    # bleibt stabil (gleiche Session).
    _state.run_create(run_id, intent)
    _publish_progress(run_id, "planning", intent)   # Phase C — Dashboard sieht den Run ab Start
    caps_block = _capabilities_block()
    skills = _tools.skill_search(intent, limit=6)
    skills_block = "\n".join(f"- {s['name']}: {s['description'][:80]}" for s in skills) or "(keine)"
    context_block = _load_context(intent, context_file)

    result: dict = {"run_id": run_id, "intent": intent, "steps": {}}

    # answers_context (vom Resume) wird mit in den Daten-Kontext gegeben — der
    # Planner bekommt die beantworteten Rückfragen als bekannte Fakten.
    full_context = "\n\n".join(p for p in (answers_context, context_block) if p)
    context_section = (
        f"\n\nBEREITS VORLIEGENDE DATEN / aktueller Stand (nutze das, nimm nicht an dass alles fehlt):\n{full_context}"
        if full_context else ""
    )

    # ── Feedback-Loop (Phase 3): Planner→Executor→Validator, bei FAIL gezielte
    #    Korrektur-Runde (max SOM_MAX_FEEDBACK, default 2). Validator-feedback_
    #    fuer_planner geht als konkreter Mangel zurück an den Planner — NICHT der
    #    ganze Intent neu. Danach: PASS/WARN → weiter; FAIL nach Limit → needs_human.
    max_feedback = int(os.environ.get("SOM_MAX_FEEDBACK", "2"))
    plan = exec_plan = verdict = None
    correction = ""        # Mangel-Text aus vorigem Validator-FAIL
    feedback_round = 0

    while True:
        # ── 1. PLANNER ───────────────────────────────────────────────────────
        correction_section = (
            f"\n\nKORREKTUR-AUFTRAG (Runde {feedback_round}): Der vorige Plan hatte "
            f"diesen Mangel — behebe GEZIELT nur diesen, behalte den Rest:\n{correction}"
            if correction else ""
        )
        planner_input = (
            f'Intent: "{intent}"\n\n'
            f"Verfügbare Capabilities:\n{caps_block}\n\n"
            f"Relevante Skill-Hinweise (welche Daten typischerweise gebraucht werden):\n{skills_block}"
            f"{context_section}{correction_section}"
        )
        plan = _call_agent("planner", planner_input, run_id=run_id)
        if "error" in plan:
            _state.run_meta_update(run_id, status="failed")
            result["steps"]["planner"] = {"ok": False, "error": plan["error"]}
            return result
        _state.plan_write(run_id, "plan", plan)
        result["steps"]["planner"] = {"ok": True, "n_steps": len(plan.get("steps", [])),
                                       "feedback_round": feedback_round}

        # ── 2. EXECUTOR ──────────────────────────────────────────────────────
        _state.run_meta_update(run_id, status="executing")
        _publish_progress(run_id, "executing", intent)
        executor_input = (
            f"Plan:\n{json.dumps(plan, ensure_ascii=False, indent=2)}\n\n"
            f"Verfügbare Capabilities (mit execution_targets):\n{caps_block}"
            f"{context_section}"
        )
        exec_plan = _call_agent("executor", executor_input, run_id=run_id)
        if "error" in exec_plan:
            _state.run_meta_update(run_id, status="failed")
            result["steps"]["executor"] = {"ok": False, "error": exec_plan["error"]}
            return result
        _state.plan_write(run_id, "exec", exec_plan)
        result["steps"]["executor"] = {"ok": True, "n_steps": len(exec_plan.get("steps", []))}

        # ── 3. VALIDATOR ─────────────────────────────────────────────────────
        _state.run_meta_update(run_id, status="validating")
        _publish_progress(run_id, "validating", intent)
        validator_input = (
            f"Plan:\n{json.dumps(plan, ensure_ascii=False, indent=2)}\n\n"
            f"Executor-Plan:\n{json.dumps(exec_plan, ensure_ascii=False, indent=2)}"
            f"{context_section}"
        )
        verdict = _call_agent("validator", validator_input, run_id=run_id)
        if "error" in verdict:
            _state.run_meta_update(run_id, status="failed")
            result["steps"]["validator"] = {"ok": False, "error": verdict["error"]}
            return result
        _state.plan_write(run_id, "verdict", verdict)
        result["steps"]["validator"] = {
            "ok": True,
            "verdict": verdict.get("verdict"),
            "n_gates": len(verdict.get("approval_gates", [])),
            "feedback_round": feedback_round,
        }
        _state.run_meta_update(run_id, feedback_rounds=feedback_round)

        # ── Topologie-Gegencheck (Fix 2026-06-02): verwaiste Nodes / Zyklen
        #    entstehen erst in der Matrix, NACH dem Validator. Der Validator ist
        #    dafür blind. Wir bauen die Matrix probeweise + prüfen die Topologie;
        #    ein struktureller Mangel zählt wie ein Validator-FAIL und löst eine
        #    gezielte Korrektur-Runde aus (statt als WARN durchzurutschen).
        topo_mangel = ""
        if verdict.get("verdict") != "FAIL":
            try:
                probe_matrix = _matrix.build_and_write(run_id)
                if "error" not in probe_matrix:
                    a = _matrix.analyze_matrix(probe_matrix)
                    if a["isolated_nodes"]:
                        topo_mangel = (
                            f"Verwaiste Schritte ohne Verbindung zum Ablauf: "
                            f"{a['isolated_nodes']}. Jeder Schritt muss über depends_on "
                            f"mit dem Plan verbunden sein (oder entfernt werden)."
                        )
                    elif a["has_cycle"]:
                        topo_mangel = ("Der Plan hat einen Abhängigkeits-Zyklus "
                                       "(depends_on im Kreis) — nicht ausführbar.")
                    elif a["approval_gates_invalid"]:
                        topo_mangel = (f"Approval-Gates auf nicht-existente Schritte: "
                                       f"{a['approval_gates_invalid']}.")
            except Exception:  # noqa: BLE001
                pass  # Matrix-Probe-Fehler blockiert den Loop nicht

        # ── Feedback-Entscheidung ────────────────────────────────────────────
        is_fail = verdict.get("verdict") == "FAIL" or bool(topo_mangel)
        if not is_fail:
            break  # PASS/WARN ohne Topologie-Mangel → raus aus dem Loop
        fb = (verdict.get("feedback_fuer_planner") or "").strip() or topo_mangel
        if topo_mangel:
            fb = topo_mangel  # Topologie-Mangel hat Vorrang (konkret + objektiv)
            result["steps"]["validator"]["topo_mangel"] = topo_mangel[:120]
        if feedback_round >= max_feedback or not fb:
            # FAIL nach Limit ODER kein konkretes Feedback → Mensch muss ran
            _state.run_meta_update(run_id, status="needs_human")
            result["status"] = "needs_human"
            result["feedback_exhausted"] = True
            # Offene Fragen ableiten + persistieren (Entscheidungsgrundlage für
            # Telegram + answers_needed.yaml für den Resume-Loop).
            questions = _derive_questions(exec_plan, verdict)
            if not questions:
                questions = [{"id": "review", "frage": fb or "Plan manuell prüfen.",
                              "typ": "review"}]
            result["offene_fragen"] = questions
            try:
                _state.answers_write(run_id, questions)
            except Exception:  # noqa: BLE001
                pass
            result["run_dir"] = str(_state.run_dir(run_id))
            return result
        # Noch eine Korrektur-Runde
        correction = fb
        feedback_round += 1
        result["steps"][f"feedback_{feedback_round}"] = {"mangel": fb[:120]}

    # ── 4. MATRIX (Phase 2) ─────────────────────────────────────────────────
    # Bei FAIL keine Matrix (Run geht an needs_human, Matrix wäre verfrüht).
    if verdict.get("verdict", "WARN") != "FAIL":
        try:
            matrix = _matrix.build_and_write(run_id)
            if "error" not in matrix:
                analysis = _matrix.analyze_matrix(matrix)
                result["steps"]["matrix"] = {
                    "ok": True,
                    "n_nodes": analysis["n_nodes"],
                    "n_edges": analysis["n_edges"],
                    "critical_path": analysis["critical_path_length"],
                    "isolated": analysis["isolated_nodes"],
                    "has_cycle": analysis["has_cycle"],
                }
                # Optionaler AutoGen-SelectorGroupChat-Verify (env-Flag, OpenAI-Kosten)
                if os.environ.get("SOM_MATRIX_VERIFY") in ("1", "true", "True"):
                    try:
                        sel = _load("som_selector", _PLANNER / "runner" / "som_selector.py")
                        verify = sel.run(run_id)
                        result["steps"]["matrix"]["verify_ok"] = verify.get("reviewer_ok")
                    except Exception as e:  # noqa: BLE001
                        result["steps"]["matrix"]["verify_error"] = str(e)[:120]
            else:
                result["steps"]["matrix"] = {"ok": False, "error": matrix["error"]}
        except Exception as e:  # noqa: BLE001
            result["steps"]["matrix"] = {"ok": False, "error": str(e)[:160]}

    # Ehrlicher Status (Fix 2026-06-02): WARN heißt NICHT automatisch "ready".
    # Wenn der Executor Pflicht-Daten als fehlend meldet ODER der Validator ein
    # offenes Approval-Gate hat, ist der Plan NICHT versandfertig → needs_input.
    v = verdict.get("verdict", "WARN")
    daten_fehlen = bool(exec_plan.get("benoetigte_daten_fehlen"))
    hat_gates = bool(verdict.get("approval_gates"))
    if v == "FAIL":
        final_status = "needs_human"
    elif v == "PASS" and not daten_fehlen:
        final_status = "ready"
    elif daten_fehlen:
        final_status = "needs_input"      # Pflicht-Daten fehlen — NICHT ready
    elif hat_gates:
        final_status = "awaiting_approval"  # WARN ohne fehlende Daten, aber Gates offen
    else:
        final_status = "ready"
    _state.run_meta_update(run_id, status=final_status)
    _publish_progress(run_id, final_status, intent)
    result["status"] = final_status
    result["needs_input"] = daten_fehlen
    result["run_dir"] = str(_state.run_dir(run_id))

    # Offene Fragen ableiten + persistieren wenn der Plan auf den Nutzer wartet.
    # Bei 'ready' keine Fragen (alles klar). Quelle: exec + verdict.
    if final_status in ("needs_input", "awaiting_approval"):
        questions = _derive_questions(exec_plan, verdict)
        if questions:
            result["offene_fragen"] = questions
            try:
                _state.answers_write(run_id, questions)
            except Exception:  # noqa: BLE001 — Persistenz darf den Run nicht brechen
                pass
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--intent", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--context-file", default=None,
                        help="Datei mit echtem Daten-Stand (was bereits vorliegt)")
    args = parser.parse_args()
    try:
        sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    out = run(args.intent, run_id=args.run_id, context_file=args.context_file)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
