"""SoM Planner — lokale State-Schicht (Quelle der Wahrheit).

Pro Run ein Verzeichnis state/runs/<run_id>/ mit plan.yaml, exec.yaml,
verdict.yaml, matrix.yaml, run_meta.yaml. Atomic write + Backup, Versions-Bump
in run_meta. Pattern von skills/buergergeld/_lib/timeline_helper.py.

Eigenständig importierbar (kein AutoGen-Import) — einzeln testbar.
"""

from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

_PLANNER_ROOT = Path(__file__).resolve().parents[1]   # spaces/autogen/planner/


# ── Per-Run Datei-Lock gegen read-modify-write Race in plan_write/run_meta_update ──
# (mehrere parallele Agents schreiben in denselben Run — Phase 2/3). filelock
# falls verfügbar, sonst no-op (Einzelschreiber-Fall bleibt korrekt).
try:
    from filelock import FileLock as _FileLock
    _HAS_FILELOCK = True
except Exception:  # noqa: BLE001
    _HAS_FILELOCK = False


@contextmanager
def _run_lock(run_id: str):
    """Serialisiert run_meta-Zugriffe eines Runs. Verhindert dass parallele
    plan_write den Versions-Bump überschreiben."""
    if not _HAS_FILELOCK:
        yield
        return
    lock_path = run_dir(run_id) / ".run.lock"
    lock = _FileLock(str(lock_path), timeout=30)
    with lock:
        yield


def state_root() -> Path:
    return Path(os.environ.get("SOM_STATE_ROOT", str(_PLANNER_ROOT / "state")))


def runs_root() -> Path:
    r = state_root() / "runs"
    r.mkdir(parents=True, exist_ok=True)
    return r


def run_dir(run_id: str) -> Path:
    d = runs_root() / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


VALID_KINDS = {"plan", "exec", "verdict", "matrix"}


def _ts() -> str:
    # Date.now ist im Workflow-Kontext tabu, hier normaler Prozess → ok
    return datetime.now().isoformat(timespec="seconds")


def _atomic_dump(path: Path, data: dict[str, Any]) -> None:
    backup_dir = path.parent / ".backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(path, backup_dir / f"{path.name}.{stamp}.bak")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120)
    tmp.replace(path)


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def run_create(run_id: str, intent: str) -> dict[str, Any]:
    """Legt einen neuen Run an — ODER setzt einen bestehenden für einen Re-Run
    (Resume, Phase 5) fort, OHNE die Versions-Historie zu verlieren.

    Idempotent: existiert run_meta.yaml bereits, bleiben versions, created_at +
    feedback_rounds erhalten (die Artefakte plan/exec/... bumpen weiter); nur
    status→planning + updated_at werden frisch gesetzt. So überschreibt ein
    Resume nicht den Versions-Stand des vorigen Laufs."""
    d = run_dir(run_id)
    existing = _load(d / "run_meta.yaml")
    if existing.get("run_id") == run_id:
        existing["status"] = "planning"
        existing["updated_at"] = _ts()
        existing.setdefault("versions", {"plan": 0, "exec": 0, "verdict": 0, "matrix": 0})
        _atomic_dump(d / "run_meta.yaml", existing)
        return existing
    meta = {
        "run_id": run_id,
        "intent": intent,
        "status": "planning",   # planning | validating | needs_human | ready | failed | cancelled
        "created_at": _ts(),
        "updated_at": _ts(),
        "versions": {"plan": 0, "exec": 0, "verdict": 0, "matrix": 0},
        "feedback_rounds": 0,
    }
    _atomic_dump(d / "run_meta.yaml", meta)
    return meta


def run_meta(run_id: str) -> dict[str, Any]:
    return _load(run_dir(run_id) / "run_meta.yaml")


def _run_meta_update_unlocked(run_id: str, **fields: Any) -> dict[str, Any]:
    meta = run_meta(run_id)
    meta.update(fields)
    meta["updated_at"] = _ts()
    _atomic_dump(run_dir(run_id) / "run_meta.yaml", meta)
    return meta


def run_meta_update(run_id: str, **fields: Any) -> dict[str, Any]:
    with _run_lock(run_id):
        return _run_meta_update_unlocked(run_id, **fields)


def plan_read(run_id: str, kind: str) -> dict[str, Any]:
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}, got {kind!r}")
    return _load(run_dir(run_id) / f"{kind}.yaml")


def plan_write(run_id: str, kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """Schreibt plan/exec/verdict/matrix.yaml + bumpt die Version in run_meta.
    Der read-modify-write-Block (run_meta lesen → version+1 → run_meta schreiben)
    ist per Run-Lock serialisiert, damit parallele plan_write sich nicht den
    Versions-Bump überschreiben (Race-Fix 2026-06-02)."""
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}, got {kind!r}")
    d = run_dir(run_id)
    payload = dict(data)
    payload.setdefault("_meta", {})
    with _run_lock(run_id):
        meta = run_meta(run_id)
        new_version = (meta.get("versions", {}).get(kind, 0)) + 1
        payload["_meta"].update({"kind": kind, "version": new_version, "written_at": _ts()})
        _atomic_dump(d / f"{kind}.yaml", payload)
        versions = meta.setdefault("versions", {})
        versions[kind] = new_version
        _run_meta_update_unlocked(run_id, versions=versions)  # bereits im Lock
    return payload


def list_runs() -> list[str]:
    return [p.name for p in runs_root().iterdir() if p.is_dir()]


# ── Answers / Resume-Loop (Phase 5) ──────────────────────────────────────────
# answers_needed.yaml ist die QUELLE DER WAHRHEIT für offene Rückfragen eines
# Runs. Telegram-Reply ODER manuelles Ausfüllen ODER eine UI schreiben hier rein;
# som_core.resume liest sie und nimmt den Run wieder auf. NICHT in VALID_KINDS
# (separates Artefakt, kein versioniertes plan/exec/verdict/matrix).
def answers_path(run_id: str) -> Path:
    return run_dir(run_id) / "answers_needed.yaml"


def answers_write(run_id: str, questions: list[dict], message_id: int | None = None) -> dict[str, Any]:
    """Schreibt die offenen Fragen als answers_needed.yaml (status awaiting_answers).
    questions: [{id, frage, typ}]. Antwort-Felder bleiben leer bis beantwortet."""
    with _run_lock(run_id):
        payload = {
            "run_id": run_id,
            "status": "awaiting_answers",
            "message_id": message_id,          # für message_id->run_id-Reply-Map
            "questions": [
                {"id": q.get("id", f"q{i}"), "frage": q.get("frage", ""),
                 "typ": q.get("typ", "daten"), "antwort": ""}
                for i, q in enumerate(questions, 1)
            ],
        }
        _atomic_dump(answers_path(run_id), payload)
    return payload


def answers_read(run_id: str) -> dict[str, Any]:
    return _load(answers_path(run_id))


def latest_awaiting_run() -> str | None:
    """Jüngster Run der auf Antworten wartet (answers_needed.status ==
    awaiting_answers). Das ist der Kern der run-id-freien Telegram-Antwort:
    der Frage-Push hat den Run als wartend markiert, die nächste eingehende
    Nachricht beantwortet genau diesen. Sortiert nach run_meta.updated_at, sonst
    nach Ordner-Name (run_0021 > run_0020).
    """
    candidates: list[tuple[str, str]] = []
    for run_id in list_runs():
        a = answers_read(run_id)
        if a.get("status") == "awaiting_answers":
            updated = run_meta(run_id).get("updated_at") or ""
            candidates.append((f"{updated}|{run_id}", run_id))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def answers_set(run_id: str, antworten: dict[str, str]) -> dict[str, Any]:
    """Trägt Antworten ein (Mapping question-id ODER 1-basierter Index -> Text).
    Akzeptiert sowohl {"daten_1": "..."} als auch {"1": "..."}."""
    with _run_lock(run_id):
        data = answers_read(run_id)
        qs = data.get("questions") or []
        for i, q in enumerate(qs, 1):
            qid = q.get("id")
            if qid in antworten:
                q["antwort"] = str(antworten[qid])
            elif str(i) in antworten:
                q["antwort"] = str(antworten[str(i)])
        if all((q.get("antwort") or "").strip() for q in qs) and qs:
            data["status"] = "answered"
        _atomic_dump(answers_path(run_id), data)
    return data


def answers_message_id(run_id: str, message_id: int) -> dict[str, Any]:
    """Persistiert die Telegram-message_id der Frage-Nachricht (für Reply-Map)."""
    with _run_lock(run_id):
        data = answers_read(run_id)
        data["message_id"] = message_id
        _atomic_dump(answers_path(run_id), data)
    return data


# ── Execution-State (Phase 6: ready-Plan ausführen) ──────────────────────────
# exec_state.yaml hält pro Run den Ausführungs-Fortschritt: je Schritt
# {status, output, error}. Quelle der Wahrheit fürs Resume eines Exec-Laufs.
def exec_state_path(run_id: str) -> Path:
    return run_dir(run_id) / "exec_state.yaml"


def exec_state_read(run_id: str) -> dict[str, Any]:
    return _load(exec_state_path(run_id))


def exec_state_init(run_id: str, step_ids: list[str]) -> dict[str, Any]:
    """Legt exec_state.yaml an (alle Schritte pending) — idempotent: vorhandene
    done/failed-Stati bleiben erhalten (Resume eines abgebrochenen Exec-Laufs)."""
    with _run_lock(run_id):
        data = exec_state_read(run_id)
        steps = data.get("steps") or {}
        for sid in step_ids:
            steps.setdefault(sid, {"status": "pending", "output": None, "error": None})
        data["steps"] = steps
        data.setdefault("phase", "executing")
        _atomic_dump(exec_state_path(run_id), data)
    return data


def exec_step_update(run_id: str, step_id: str, **fields: Any) -> dict[str, Any]:
    with _run_lock(run_id):
        data = exec_state_read(run_id)
        steps = data.setdefault("steps", {})
        st = steps.setdefault(step_id, {"status": "pending"})
        st.update(fields)
        _atomic_dump(exec_state_path(run_id), data)
    return data


# ── message_id -> run_id Reply-Map (Schritt 4, global über alle Runs) ─────────
def _answers_map_path() -> Path:
    return state_root() / "answers_map.json"


def answers_map_set(message_id: int, run_id: str) -> None:
    """Merkt sich welche Telegram-Frage-Nachricht zu welchem Run gehört, damit
    eine Reply (trägt reply_to_message_id) dem richtigen Run zugeordnet wird."""
    import json
    p = _answers_map_path()
    try:
        m = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:  # noqa: BLE001
        m = {}
    m[str(message_id)] = run_id
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def answers_map_get(message_id: int) -> str | None:
    import json
    p = _answers_map_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get(str(message_id))
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    # Selbsttest
    import tempfile
    os.environ["SOM_STATE_ROOT"] = tempfile.mkdtemp()
    m = run_create("test1", "Bereite Antwort vor")
    assert m["status"] == "planning"
    plan_write("test1", "plan", {"steps": [{"id": "s1", "desc": "parse"}]})
    p = plan_read("test1", "plan")
    assert p["steps"][0]["id"] == "s1"
    assert p["_meta"]["version"] == 1
    plan_write("test1", "plan", {"steps": []})
    assert run_meta("test1")["versions"]["plan"] == 2
    print("state.py selftest OK:", state_root())
