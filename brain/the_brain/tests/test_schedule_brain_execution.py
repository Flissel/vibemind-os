from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml

from brain.the_brain.core.plan_executor import PlanExecutor, PlanRecorder
from brain.the_brain.core.plan_schema import HopSpec, Plan


class _FakePostgrest:
    """Ein kleines Double fuer die eine Tabelle, die schedule benutzt.

    Es bildet NUR nach, was das Repository wirklich aufruft: `eq.`-Filter,
    select/order/limit, POST und PATCH. Wichtig ist, wo es sich UNBEQUEM
    verhaelt wie das Original:

      * Ein PATCH ohne Treffer ist KEIN Fehler - PostgREST meldet ihn nicht.
        Genau deshalb liest `update()` danach zurueck; ein gefaelliges Double
        wuerde diese Zusage unpruefbar machen.
      * `trigger_config` wird als Objekt abgelegt, nicht als Text. Schickte
        der Code es serialisiert, laege ein String im jsonb und jeder Filter
        darauf ginge ins Leere - der Test unten haelt das fest.
    """

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def __call__(self, method, query, *, body=None, prefer=None):
        import urllib.parse
        params = dict(urllib.parse.parse_qsl(query))
        if method == "POST":
            self.rows.append(dict(body))
            return None
        selected = [row for row in self.rows if self._matches(row, params)]
        if method == "PATCH":
            for row in selected:
                row.update(body)
            return None            # auch ohne Treffer: kein Fehler
        if params.get("order", "").startswith("created_at"):
            selected.sort(key=lambda row: str(row.get("created_at")), reverse=True)
        limit = params.get("limit")
        if limit:
            selected = selected[: int(limit)]
        return [dict(row) for row in selected]

    @staticmethod
    def _matches(row, params):
        for key, value in params.items():
            if key in {"select", "order", "limit"}:
                continue
            if not value.startswith("eq."):
                raise AssertionError(f"unerwarteter Filter: {key}={value}")
            if str(row.get(key)) != value[3:]:
                return False
        return True


@pytest.fixture()
def schedule_execution(monkeypatch: pytest.MonkeyPatch):
    """schedule schreibt seit 2026-09-12 nach Supabase, nicht mehr nach SQLite.

    Der Test bleibt hermetisch: statt gegen die geteilte Datenbank zu
    schreiben, steht das Double hinter `_request`. Die Schicht darueber -
    Wiederholungsschutz, Ausloeser-Pruefung, Rueckfrage nach dem Schreiben -
    ist damit unveraendert unter Test.
    """
    monkeypatch.setenv("SUPABASE_URL", "http://supabase.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-key")
    module = importlib.import_module("spaces.schedule.execution")
    module = importlib.reload(module)
    store = _FakePostgrest()
    monkeypatch.setattr(module.ScheduleRepository, "_request",
                        lambda self, method, query, **kw: store(method, query, **kw))
    module._fake_store = store
    return module


def test_registry_routes_every_schedule_operation_to_real_targets():
    registry_path = Path(__file__).resolve().parents[3] / "config" / "space_agent_registry.yml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    events = registry["spaces"]["schedule"]["events"]

    expected = {
        "schedule.create",
        "schedule.list",
        "schedule.cancel",
        "schedule.modify",
        "schedule.status",
        "schedule.snooze",
        "openclaw.cron",
    }
    assert set(events) == expected
    assert all(event["tool"].startswith("schedule_") for event in events.values())


def test_brain_capabilities_have_resolvable_schedule_execution_targets():
    capabilities_path = Path(__file__).resolve().parents[1] / "data" / "capabilities.yaml"
    capabilities = {item["capability"]: item for item in yaml.safe_load(capabilities_path.read_text(encoding="utf-8"))}
    expected = {
        "schedule_create": "create",
        "schedule_list": "list_tasks",
        "schedule_cancel": "cancel",
        "schedule_modify": "modify",
        "schedule_status": "status",
        "schedule_snooze": "snooze",
        "schedule_openclaw_cron": "openclaw_cron",
    }
    for capability, function in expected.items():
        assert capabilities[capability]["execution_target"] == f"direct:spaces.schedule.execution:{function}"

    agent_path = Path(__file__).resolve().parents[1] / "configs" / "agents" / "brain-scheduler.yaml"
    agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    assert "openclaw.cron" in agent["events"]


def test_create_is_idempotent_and_survives_repository_reopen(schedule_execution):
    payload = {
        "title": "Daily review",
        "action_text": "OpenClaw: summarize notifications",
        "trigger_config": {"cron": "0 9 * * *"},
        "idempotency_key": "voice-42",
    }

    first = schedule_execution.create(payload)
    second = schedule_execution.create(payload)
    reopened = schedule_execution.ScheduleRepository()

    assert first["schedule_id"] == second["schedule_id"]
    assert second["idempotent_replay"] is True
    assert reopened.get(first["schedule_id"])["timezone"] == "Europe/Berlin"
    assert len(reopened.list()) == 1


def test_openclaw_cron_validates_trigger_and_persists(schedule_execution):
    result = schedule_execution.openclaw_cron({
        "cron_expr": "*/15 * * * *",
        "prompt": "Check inbox",
        "idempotency_key": "api-cron-7",
    })

    assert result["success"] is True
    task = schedule_execution.ScheduleRepository().get(result["schedule_id"])
    assert task["event_type"] == "openclaw.cron"
    assert task["trigger_config"] == {"cron": "*/15 * * * *"}

    with pytest.raises(schedule_execution.ScheduleContractError, match="five fields"):
        schedule_execution.openclaw_cron({"cron_expr": "bad cron", "prompt": "x"})


def test_modify_snooze_cancel_and_status(schedule_execution):
    created = schedule_execution.create({
        "title": "Call dentist",
        "action_text": "Remind me to call dentist",
        "trigger_config": {"run_date": "2030-01-02T10:00:00+01:00"},
    })
    task_id = created["schedule_id"]

    modified = schedule_execution.modify({
        "task_id": task_id,
        "trigger_config": {"cron": "30 8 * * 1-5"},
        "action_text": "Call dentist office",
    })
    snoozed = schedule_execution.snooze({"task_id": task_id, "minutes": 10})
    status = schedule_execution.status({"task_id": task_id})
    cancelled = schedule_execution.cancel({"task_id": task_id})

    assert modified["trigger_type"] == "cron"
    assert snoozed["trigger_type"] == "date"
    assert status["task"]["status"] == "active"
    assert cancelled["status"] == "cancelled"


def test_invalid_or_ambiguous_triggers_fail_closed(schedule_execution):
    with pytest.raises(schedule_execution.ScheduleContractError, match="exactly one"):
        schedule_execution.create({
            "title": "Ambiguous",
            "action_text": "do it",
            "trigger_config": {"cron": "0 9 * * *", "run_date": "2030-01-01T09:00:00+01:00"},
        })

    with pytest.raises(schedule_execution.ScheduleContractError, match="timezone-aware"):
        schedule_execution.create({
            "title": "Naive",
            "action_text": "do it",
            "trigger_config": {"run_date": "2030-01-01T09:00:00"},
        })


def test_plan_executor_runs_structured_schedule_target(schedule_execution, tmp_path: Path):
    plan = Plan(
        plan_id="schedule-plan-1",
        intent="Create a reminder",
        rationale="canonical schedule event",
        hops=[HopSpec(
            step_id="create",
            description="persist reminder",
            capability="schedule_create",
            execution_target="direct:spaces.schedule.execution:create",
            arg_template='{"title":"Review","action_text":"Review alerts","trigger_config":{"cron":"0 9 * * *"}}',
        )],
    )
    executor = PlanExecutor(recorder=PlanRecorder(path=tmp_path / "plans.jsonl"))

    result = executor.execute(plan)

    assert result["ok"] is True
    assert result["executed"]["create"]["result"]["success"] is True


@pytest.mark.asyncio
async def test_worker_reloads_persisted_active_tasks_after_reboot(schedule_execution):
    created = schedule_execution.create({
        "title": "Persistent",
        "action_text": "Run persisted action",
        "trigger_config": {"cron": "0 7 * * *"},
    })
    registered = []

    class FakeScheduler:
        def start(self):
            return None

        def add_job(self, function, **kwargs):
            registered.append(kwargs)

    from spaces.schedule.workers.durable_worker import ScheduleWorker

    class FakeTrigger:
        timezone = "Europe/Berlin"

    worker = ScheduleWorker(
        scheduler_factory=lambda: FakeScheduler(),
        trigger_factory=lambda task: FakeTrigger(),
        execution_target=lambda task: {"ok": True},
    )
    await worker.start()

    assert worker.is_running is True
    assert registered[0]["id"] == created["schedule_id"]
    assert registered[0]["replace_existing"] is True
    assert str(registered[0]["trigger"].timezone) == "Europe/Berlin"

def test_create_returns_the_stored_task_not_its_own_dict(schedule_execution):
    """`update` liest nach dem Schreiben frisch zurueck und wirft, wenn die
    Zeile fehlt (`schedule update was not persisted`). `create` tat das
    nicht: es gab das selbst gebaute Dict zurueck. Bei einem Terminplaner
    faellt ein nicht persistierter Eintrag erst auf, wenn er nicht feuert."""
    payload = {"title": "Rueckfrage", "action_text": "tu etwas",
               "trigger_config": {"cron": "0 9 * * 1"},
               "timezone": "Europe/Berlin"}
    result = schedule_execution.create(payload)
    stored = schedule_execution.ScheduleRepository().get(result["schedule_id"])
    assert stored is not None
    assert result["status"] == stored["status"]
    assert result["trigger_type"] == stored["trigger_type"]


def test_create_refuses_when_the_insert_did_not_land(schedule_execution,
                                                     monkeypatch):
    """Sabotage: das Einfuegen tut nichts. Ohne Rueckfrage meldet die
    Operation Erfolg ueber einen Termin, den es nicht gibt."""
    repo_cls = schedule_execution.ScheduleRepository
    monkeypatch.setattr(repo_cls, "_insert", lambda self, task: None)
    with pytest.raises(RuntimeError, match="not persisted"):
        schedule_execution.create({
            "title": "verschwindet", "action_text": "x",
            "trigger_config": {"cron": "0 9 * * 1"},
            "timezone": "Europe/Berlin"})


# ---------------------------------------------------------------------
# Zusagen, die erst durch den Umzug nach Supabase entstanden sind
# ---------------------------------------------------------------------

def test_trigger_config_is_stored_as_an_object_not_as_text(schedule_execution):
    """Die SQLite-Fassung musste `trigger_config` serialisieren. Bliebe das
    so, laege hier ein String IM jsonb - die Spalte waere formal gefuellt und
    jede Abfrage darauf trotzdem blind."""
    created = schedule_execution.create({
        "title": "Objekt statt Text", "action_text": "x",
        "trigger_config": {"cron": "0 9 * * *"}, "timezone": "Europe/Berlin",
    })
    gespeichert = schedule_execution._fake_store.rows[0]
    assert gespeichert["trigger_config"] == {"cron": "0 9 * * *"}
    assert not isinstance(gespeichert["trigger_config"], str)
    assert created["trigger_type"] == "cron"


def test_an_update_without_a_match_is_reported_not_swallowed(schedule_execution):
    """PostgREST meldet ein PATCH ohne Treffer NICHT als Fehler. Ohne die
    Rueckfrage danach wuerde ein Abbruch auf eine nicht existierende Aufgabe
    als Erfolg zurueckkommen."""
    with pytest.raises(schedule_execution.ScheduleContractError, match="not found"):
        schedule_execution.cancel({"task_id": "gibt-es-nicht"})


def test_a_store_that_does_not_persist_is_caught_at_create(schedule_execution, monkeypatch):
    """Der Fehler vom 11.09.: `create` gab sein selbst gebautes Dict weiter,
    ohne je nachzusehen. Bei einem Terminplaner faellt das erst auf, wenn der
    Eintrag nicht feuert."""
    monkeypatch.setattr(schedule_execution.ScheduleRepository, "_request",
                        lambda self, method, query, **kw: None if method == "POST" else [])
    with pytest.raises(RuntimeError, match="was not persisted"):
        schedule_execution.create({
            "title": "verschwindet", "action_text": "x",
            "trigger_config": {"cron": "0 9 * * *"}, "timezone": "Europe/Berlin",
        })


def test_the_key_may_come_from_the_file_convention(monkeypatch, tmp_path):
    """brain-core setzt NUR SUPABASE_ANON_KEY_FILE (Docker-Secret). Wer allein
    die Variable liest, steht dort ohne Schluessel da."""
    import importlib as _il
    secret = tmp_path / "anon.key"
    secret.write_text("  aus-der-datei  ", encoding="utf-8")
    monkeypatch.setenv("SUPABASE_URL", "http://supabase.test")
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_ANON_KEY_FILE", str(secret))
    module = _il.reload(_il.import_module("spaces.schedule.execution"))
    assert module.ScheduleRepository().key == "aus-der-datei"


def test_bearer_is_only_sent_for_a_real_jwt(monkeypatch):
    """Das lokale Supabase antwortet auf `Bearer anon` mit 401 PGRST301,
    waehrend der apikey-Kopf allein durchkommt."""
    import importlib as _il
    monkeypatch.setenv("SUPABASE_URL", "http://supabase.test")
    module = _il.reload(_il.import_module("spaces.schedule.execution"))
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    assert "Authorization" not in module.ScheduleRepository()._headers()
    monkeypatch.setenv("SUPABASE_ANON_KEY", "aaa.bbb.ccc")
    assert module.ScheduleRepository()._headers()["Authorization"] == "Bearer aaa.bbb.ccc"
