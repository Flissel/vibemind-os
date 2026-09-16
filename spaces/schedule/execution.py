"""Durable, fail-closed execution target for Schedule Brain capabilities."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Europe/Berlin"
_TRIGGER_KEYS = ("run_date", "cron", "interval_seconds")


class ScheduleContractError(ValueError):
    """The request cannot safely be translated into a scheduler trigger."""


def _payload(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ScheduleContractError("schedule payload must be a JSON object") from exc
        value = decoded
    if not isinstance(value, Mapping):
        raise ScheduleContractError("schedule payload must be an object")
    result = dict(value)
    embedded = result.pop("value", None)
    if isinstance(embedded, str) and embedded.lstrip().startswith("{"):
        try:
            decoded = json.loads(embedded)
        except json.JSONDecodeError as exc:
            raise ScheduleContractError("schedule payload value must be valid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise ScheduleContractError("schedule payload value must be an object")
        return {**decoded, **result}
    return result


def _now() -> datetime:
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))


def _validate_timezone(value: Any) -> str:
    timezone = str(value or DEFAULT_TIMEZONE)
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ScheduleContractError(f"unknown IANA timezone: {timezone}") from exc
    return timezone


def _validate_cron(expr: Any) -> str:
    cron = str(expr or "").strip()
    if len(cron.split()) != 5:
        raise ScheduleContractError("cron expression must contain exactly five fields")
    return cron


def _validate_trigger(value: Any, timezone: str) -> tuple[str, Dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise ScheduleContractError("trigger_config must be an object")
    supplied = [key for key in _TRIGGER_KEYS if value.get(key) not in (None, "")]
    if len(supplied) != 1:
        raise ScheduleContractError("trigger_config must contain exactly one trigger")
    key = supplied[0]
    if key == "cron":
        return "cron", {"cron": _validate_cron(value[key])}
    if key == "interval_seconds":
        try:
            seconds = int(value[key])
        except (TypeError, ValueError) as exc:
            raise ScheduleContractError("interval_seconds must be an integer") from exc
        if seconds < 1:
            raise ScheduleContractError("interval_seconds must be positive")
        return "interval", {"interval_seconds": seconds}
    try:
        run_date = datetime.fromisoformat(str(value[key]))
    except ValueError as exc:
        raise ScheduleContractError("run_date must be ISO-8601") from exc
    if run_date.tzinfo is None or run_date.utcoffset() is None:
        raise ScheduleContractError("run_date must be timezone-aware")
    return "date", {"run_date": run_date.astimezone(ZoneInfo(timezone)).isoformat()}


class ScheduleRepository:
    """PostgREST-Repository gegen die geteilte Supabase.

    Vorher lag hier ein eigener SQLite-Laden. Die Tabelle
    `public.scheduled_tasks` gab es in Supabase aber schon seit der
    Initial-Migration (20260411_init_vibemind.sql:246) - mit reicherem
    Schema, und leer. Es waren also zwei Speicher fuer dieselbe Sache, und
    nur einer war fuer den Rest des Systems sichtbar: ein `truth:`-Validator,
    das Dashboard, jede Abfrage von aussen sahen die SQLite-Datei nicht.
    Betreiberentscheid 2026-09-12: alles Supabase.

    Jeder Schreibvorgang wird danach frisch gelesen. Das ist keine Vorsicht
    auf Verdacht: bei einem Terminplaner faellt ein nicht persistierter
    Eintrag erst auf, wenn er nicht feuert - und dann fehlt die Spur.
    """

    TABLE = "scheduled_tasks"
    FIELDS = ("id,event_type,title,action_text,trigger_type,trigger_config,"
              "timezone,status,idempotency_key,created_at,updated_at")

    def __init__(self, base_url: str | None = None, key: str | None = None) -> None:
        self.base = (base_url or os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
        self.key = (key or self._resolve_key()).strip()
        self.timeout = float(os.environ.get("SCHEDULE_HTTP_TIMEOUT_S", "10"))
        if not self.base:
            raise RuntimeError("SUPABASE_URL is not configured - schedule cannot persist")
        if not self.key:
            raise RuntimeError("no Supabase key configured - schedule cannot persist")

    @staticmethod
    def _resolve_key() -> str:
        """Direkte Variable, sonst die *_FILE-Konvention aus dem Stack.

        brain-core bekommt den Schluessel als Docker-Secret gereicht und setzt
        NUR SUPABASE_ANON_KEY_FILE - wer allein die Variable liest, steht dort
        ohne Schluessel da. Gleiche Aufloesung wie ideas_client.py:217.
        """
        for name in ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"):
            value = os.environ.get(name, "").strip()
            if value:
                return value
            path = os.environ.get(name + "_FILE", "").strip()
            if path:
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        value = handle.read().strip()
                except OSError:
                    value = ""
                if value:
                    return value
        return ""

    def _headers(self) -> Dict[str, str]:
        headers = {"apikey": self.key, "Accept": "application/json",
                   "Content-Type": "application/json"}
        # Bearer NUR bei einem echten JWT: das lokale Supabase antwortet auf
        # "Bearer anon" mit 401 PGRST301, waehrend der apikey-Kopf allein
        # durchkommt. Gleiche Regel wie im Ideas-Client.
        if self.key.count(".") == 2:
            headers["Authorization"] = "Bearer " + self.key
        return headers

    def _request(self, method: str, query: str, *, body: Any = None,
                 prefer: str | None = None) -> Any:
        url = self.base + "/rest/v1/" + self.TABLE
        if query:
            url = url + "?" + query
        headers = self._headers()
        if prefer:
            headers["Prefer"] = prefer
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=payload, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(
                f"schedule store {method} failed: HTTP {exc.code} {detail}") from exc
        except Exception as exc:
            raise RuntimeError(f"schedule store unreachable: {exc}") from exc
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    @staticmethod
    def _row(rows: Any) -> Dict[str, Any] | None:
        if isinstance(rows, list) and rows and isinstance(rows[0], Mapping):
            return dict(rows[0])
        return None

    def get(self, task_id: str) -> Dict[str, Any] | None:
        query = urllib.parse.urlencode({
            "select": self.FIELDS, "id": "eq." + str(task_id), "limit": "1"})
        return self._row(self._request("GET", query))

    def get_by_idempotency_key(self, key: str) -> Dict[str, Any] | None:
        query = urllib.parse.urlencode({
            "select": self.FIELDS, "idempotency_key": "eq." + str(key), "limit": "1"})
        return self._row(self._request("GET", query))

    def list(self, status_filter: str = "") -> list[Dict[str, Any]]:
        params = {"select": self.FIELDS, "order": "created_at.desc"}
        if status_filter:
            params["status"] = "eq." + status_filter
        rows = self._request("GET", urllib.parse.urlencode(params))
        return [dict(row) for row in rows or [] if isinstance(row, Mapping)]

    def _insert(self, task: Mapping[str, Any]) -> None:
        # trigger_config ist jsonb - als Objekt senden, nicht als Text. Die
        # SQLite-Fassung musste serialisieren; hier waere derselbe json.dumps
        # ein String IM jsonb, und jede Abfrage darauf ginge ins Leere.
        row = {
            "id": task["id"], "event_type": task["event_type"], "title": task["title"],
            "action_text": task["action_text"], "trigger_type": task["trigger_type"],
            "trigger_config": task["trigger_config"], "timezone": task["timezone"],
            "status": task["status"], "idempotency_key": task.get("idempotency_key"),
            "created_at": task["created_at"], "updated_at": task["updated_at"],
        }
        self._request("POST", "", body=row, prefer="return=minimal")

    def create(self, task: Mapping[str, Any]) -> Dict[str, Any]:
        """Legt die Aufgabe an und gibt den GESPEICHERTEN Stand zurueck."""
        self._insert(task)
        stored = self.get(str(task["id"]))
        if stored is None:
            raise RuntimeError("schedule create was not persisted")
        return stored

    def update(self, task_id: str, fields: Mapping[str, Any]) -> Dict[str, Any]:
        allowed = {"title", "action_text", "trigger_type", "trigger_config",
                   "timezone", "status", "updated_at"}
        changes = {key: value for key, value in fields.items() if key in allowed}
        if not changes:
            raise ScheduleContractError("update requires at least one known field")
        query = urllib.parse.urlencode({"id": "eq." + str(task_id)})
        self._request("PATCH", query, body=changes, prefer="return=minimal")
        task = self.get(task_id)
        if task is None:
            # PostgREST meldet ein PATCH OHNE Treffer nicht als Fehler. Erst
            # die Rueckfrage unterscheidet "geaendert" von "es gab nichts".
            raise ScheduleContractError(f"scheduled task not found: {task_id}")
        return task


def _required(data: Mapping[str, Any], key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ScheduleContractError(f"{key} is required")
    return value


def _task_result(task: Mapping[str, Any], **extra: Any) -> Dict[str, Any]:
    return {"success": True, "schedule_id": task["id"], "status": task["status"],
            "trigger_type": task["trigger_type"], "timezone": task["timezone"], **extra}


def create(value: Any) -> Dict[str, Any]:
    data = _payload(value)
    timezone = _validate_timezone(data.get("timezone"))
    trigger_type, trigger_config = _validate_trigger(data.get("trigger_config"), timezone)
    title = _required(data, "title")
    action_text = _required(data, "action_text")
    key = str(data.get("idempotency_key") or "").strip()
    repo = ScheduleRepository()
    if key:
        existing = repo.get_by_idempotency_key(key)
        if existing:
            canonical = json.dumps([title, action_text, trigger_config, timezone], sort_keys=True)
            old = json.dumps([existing["title"], existing["action_text"], existing["trigger_config"], existing["timezone"]], sort_keys=True)
            if hashlib.sha256(canonical.encode()).digest() != hashlib.sha256(old.encode()).digest():
                raise ScheduleContractError("idempotency key was already used with a different request")
            return _task_result(existing, idempotent_replay=True)
    timestamp = _now().isoformat()
    task = {"id": str(uuid.uuid4()), "event_type": str(data.get("event_type") or "schedule.create"),
            "title": title, "action_text": action_text, "trigger_type": trigger_type,
            "trigger_config": trigger_config, "timezone": timezone, "status": "active",
            "idempotency_key": key or None, "created_at": timestamp, "updated_at": timestamp}
    stored = repo.create(task)
    return _task_result(stored, idempotent_replay=False)


def list_tasks(value: Any = None) -> Dict[str, Any]:
    data = {} if value is None else _payload(value)
    tasks = ScheduleRepository().list(str(data.get("status") or ""))
    return {"success": True, "tasks": tasks, "count": len(tasks)}


def _task_id(value: Any) -> tuple[Dict[str, Any], str]:
    data = _payload(value)
    return data, _required(data, "task_id")


def cancel(value: Any) -> Dict[str, Any]:
    _, task_id = _task_id(value)
    task = ScheduleRepository().update(task_id, {"status": "cancelled", "updated_at": _now().isoformat()})
    return _task_result(task)


def modify(value: Any) -> Dict[str, Any]:
    data, task_id = _task_id(value)
    current = ScheduleRepository().get(task_id)
    if current is None:
        raise ScheduleContractError(f"scheduled task not found: {task_id}")
    fields: Dict[str, Any] = {"updated_at": _now().isoformat()}
    if data.get("trigger_config") is not None:
        trigger_type, trigger_config = _validate_trigger(data["trigger_config"], current["timezone"])
        fields.update(trigger_type=trigger_type, trigger_config=trigger_config, status="active")
    for key in ("title", "action_text"):
        if data.get(key):
            fields[key] = str(data[key]).strip()
    if len(fields) == 1:
        raise ScheduleContractError("modify requires a trigger, title, or action_text")
    return _task_result(ScheduleRepository().update(task_id, fields))


def status(value: Any = None) -> Dict[str, Any]:
    data = {} if value is None else _payload(value)
    repo = ScheduleRepository()
    if data.get("task_id"):
        task = repo.get(str(data["task_id"]))
        if task is None:
            raise ScheduleContractError(f"scheduled task not found: {data['task_id']}")
        return {"success": True, "task": task}
    tasks = repo.list()
    counts = {name: sum(task["status"] == name for task in tasks) for name in ("active", "cancelled", "paused", "completed", "failed")}
    return {"success": True, "counts": counts, "total": len(tasks)}


def snooze(value: Any) -> Dict[str, Any]:
    data, task_id = _task_id(value)
    try:
        minutes = int(data.get("minutes", 5))
    except (TypeError, ValueError) as exc:
        raise ScheduleContractError("minutes must be an integer") from exc
    if not 1 <= minutes <= 10080:
        raise ScheduleContractError("minutes must be between 1 and 10080")
    run_date = (_now() + timedelta(minutes=minutes)).isoformat()
    task = ScheduleRepository().update(task_id, {"trigger_type": "date", "trigger_config": {"run_date": run_date},
                                                      "timezone": DEFAULT_TIMEZONE, "status": "active", "updated_at": _now().isoformat()})
    return _task_result(task, new_run_at=run_date, snooze_minutes=minutes)


def openclaw_cron(value: Any) -> Dict[str, Any]:
    data = _payload(value)
    data["event_type"] = "openclaw.cron"
    data["title"] = str(data.get("title") or "OpenClaw cron")
    data["action_text"] = _required(data, "prompt")
    data["trigger_config"] = {"cron": _validate_cron(data.get("cron_expr"))}
    return create(data)
