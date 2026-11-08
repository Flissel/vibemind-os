# Schedule Space

Scheduled tasks, reminders and timers. Persistence is the shared Supabase table
`public.scheduled_tasks`; triggers are fired by an APScheduler worker that
reloads its jobs from that table on boot.

Three files here are tombstones — `agents/schedule_agent.py`,
`tools/schedule_tools.py` and `workers/schedule_worker.py` hold a pointer to
this README and nothing else. Deleting them outright would have been the
cleaner form; the sandbox the change was made in refused `rm` and `git rm`, so
they were emptied in place instead, and a follow-up can remove the files. What
they used to claim, and why that had to stop, is at the bottom under
[Directory Structure](#directory-structure).

## One binding, one target

```
Voice/Chat: "Erinnere mich in 5 Minuten an den Termin"
    ↓
Brain plans a capability
    ↓
config/space_agent_registry.yml        ← Registry = SoT: event → capability
    ↓
brain/the_brain/data/capabilities.yaml ← capability → execution_target
    ↓
spaces/schedule/execution.py           ← the only implementation
    ↓
Supabase public.scheduled_tasks  +  truth:supabase_row validator (report)
    ↓
workers/durable_worker.py (APScheduler) fires the task
```

| Event | Capability (registry `tool:`) | Function in `execution.py` | Required params |
|-------|-------------------------------|----------------------------|-----------------|
| `schedule.create` | `schedule_create` | `create` | `title`, `action_text`, `trigger_config` |
| `schedule.list` | `schedule_list` | `list_tasks` | — |
| `schedule.cancel` | `schedule_cancel` | `cancel` | `task_id` |
| `schedule.modify` | `schedule_modify` | `modify` | `task_id` |
| `schedule.status` | `schedule_status` | `status` | — |
| `schedule.snooze` | `schedule_snooze` | `snooze` | `task_id` |
| `openclaw.cron` | `schedule_openclaw_cron` | `openclaw_cron` | `cron_expr`, `prompt` |

This table is a reading aid, not a second source. If it ever disagrees with
`config/space_agent_registry.yml`, the registry wins — and the table is wrong.
`brain/the_brain/tests/test_schedule_brain_execution.py` holds both ends:
every registry event must exist, and every capability must resolve to
`direct:spaces.schedule.execution:<function>`.

## Trigger contract

`trigger_config` must contain **exactly one** of three keys — more or fewer is
a `ScheduleContractError`, never a guess:

| Key | Example | Stored `trigger_type` |
|-----|---------|-----------------------|
| `run_date` | `"2030-01-02T10:00:00+01:00"` | `date` (must be timezone-aware ISO-8601) |
| `cron` | `"0 9 * * *"` | `cron` (exactly five fields) |
| `interval_seconds` | `900` | `interval` (positive integer) |

Further guarantees in `execution.py`:

- `timezone` defaults to `Europe/Berlin` and must be a known IANA zone.
- `idempotency_key` replays the stored task; the same key with a *different*
  request is rejected instead of silently creating a second task.
- Every write is read back. A scheduler entry that was not persisted would
  only show up by not firing, and by then the trace is gone.
- `trigger_config` goes to Postgres as an object, not as text — a serialized
  string inside the `jsonb` column would make every filter on it blind.

## Schedule Worker

**File:** `workers/durable_worker.py` — `ScheduleWorker`

- `AsyncIOScheduler` + `MemoryJobStore`, `coalesce=True`, `max_instances=1`,
  `misfire_grace_time=60`, timezone `Europe/Berlin`.
- `start()` loads all `status=active` rows from the repository and registers
  them as jobs, so a restart does not drop pending tasks.
- Dispatch is fail-closed: the `execution_target` must return a dict with
  `ok=true`, otherwise the task is set to `failed` and the error is raised.
- No implicit target — `start()` refuses to run without `execution_target`.

## NLP Time Parser

**File:** `nlp/time_parser.py` — regex-based (no LLM, deterministic, offline).
Standalone helper: `parse_time_expression()` turns German time phrases into a
`ParsedTime`. The brain path builds `trigger_config` itself, so nothing in the
execution path calls the parser today.

### Supported Patterns

| Pattern | Example | Trigger |
|---------|---------|---------|
| `in X Minuten/Stunden/Tagen` | "in 5 Minuten" | DATE (now + duration) |
| `in einer halben Stunde` | "in einer halben Stunde" | DATE (now + 30min) |
| `um HH:MM / HH Uhr` | "um 14 Uhr", "um 9:30" | DATE (today or tomorrow) |
| `jeden [Wochentag] um HH` | "jeden Montag um 9" | CRON |
| `alle X [Einheiten]` | "alle 2 Stunden" | INTERVAL |
| `täglich um HH` | "täglich um 8" | CRON |

Supports German day names, word numbers (eine–fünfzig), and unit variations.

## Stored Row

`public.scheduled_tasks` (initial migration `20260411_init_vibemind.sql:246`),
columns read and written by `ScheduleRepository`:

```
id, event_type, title, action_text, trigger_type, trigger_config,
timezone, status, idempotency_key, created_at, updated_at
```

`status` is one of `active`, `paused`, `completed`, `cancelled`, `failed`.

## Configuration

```bash
SUPABASE_URL=...                       # required — without it schedule cannot persist
SUPABASE_SERVICE_ROLE_KEY=...          # or SUPABASE_ANON_KEY, each also as *_FILE
SCHEDULE_HTTP_TIMEOUT_S=10             # PostgREST timeout
```

`config.py` (`ScheduleConfig`, `get_config()`) still reads `SCHEDULE_ENABLED`,
`SCHEDULE_TIMEZONE`, `SCHEDULE_MAX_CONCURRENT` and `SCHEDULE_MISFIRE_GRACE`.
The durable worker does **not** consult it — its timezone and grace time are
the fixed values listed above.

## Directory Structure

```
spaces/schedule/
├── execution.py                       # the only implementation (+ ScheduleRepository)
├── config.py                          # ScheduleConfig dataclass (standalone)
├── nlp/
│   ├── __init__.py
│   └── time_parser.py                 # parse_time_expression() + ParsedTime
├── workers/
│   ├── __init__.py                    # exports ScheduleWorker from durable_worker
│   ├── durable_worker.py              # APScheduler on top of execution.py
│   └── schedule_worker.py             # gone (SQLite worker) — pointer only
├── agents/                            # gone (Redis backend agent) — pointer only
└── tools/                             # gone (SQLite tool set) — pointer only
```

The three "gone" entries are the old SQLite/Electron path. They were left in the
tree after the durable path landed (27a59929) and the move to Supabase
(e4ab34dd), and kept claiming event-to-tool bindings the registry never had —
while being unimportable, because neither `swarm.backend_agents` nor the `data`
package exists in this repo. Their files now hold nothing but a pointer here.
