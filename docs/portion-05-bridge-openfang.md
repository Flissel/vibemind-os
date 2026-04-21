# Portion 5 — Bridge & OpenFang: Routing und Agent-Ausführung

> **Teil 5 von 10** der VibeMind-OS-Systemdokumentation.
> Dependencies: Portion 4 (Brain — liefert `brain_gates` via `/api/cortex/route`).

---

## TL;DR

Die **Bridge** (FastAPI, Port 5100/5150) ist das Bindeglied zwischen Brain-
Entscheidungen und Agent-Ausführung. Sie implementiert einen **5-Schritt-Flow**:
Route via Brain → Map Space auf Agent-Template → Ensure Agent in OpenFang →
Enriched Message senden → Reward-Signal zurückfeuern. **OpenFang** (Rust,
Port 50051/4200) ist das Agent-OS, das die eigentlichen Agents spawnt und
ausführt — 57 Tools, 27 LLM-Modelle, gRPC-basiert. Die Bridge ist bewusst
**dünn** (837 Zeilen total) und delegiert schwere Arbeit an Brain und OpenFang.

---

## 1. Architektur-Überblick

```
User-Request
     │
     ▼
┌─────────────┐   POST /api/cortex/route   ┌──────────────┐
│   Bridge    │ ──────────────────────────► │    Brain     │
│  Port 5100  │ ◄────────────────────────── │  Port 5000   │
│             │    {brain_gates, confidence} │              │
│             │                              └──────────────┘
│             │   POST /api/agents/{id}/message
│             │ ──────────────────────────► ┌──────────────┐
│             │ ◄────────────────────────── │   OpenFang   │
│             │     {response, status}      │ Port 50051   │
│             │                              └──────────────┘
│             │   POST /api/cortex/route/reward
│             │ ──────────────────────────► Brain
└─────────────┘     (fire-and-forget)
```

---

## 2. Bridge-Dateien

```
bridge/
├── src/bridge/
│   ├── main.py                (62 Z.)  — FastAPI + Lifespan
│   ├── router.py              (185 Z.) — 5-Schritt-Flow + Endpoints
│   ├── brain_client.py        (62 Z.)  — HTTP → Brain
│   ├── openfang_client.py     (119 Z.) — HTTP → OpenFang
│   ├── space_agent_mapper.py  (94 Z.)  — YAML Space→Agent
│   ├── task_store.py          (61 Z.)  — In-Memory Task-State
│   ├── models.py              (37 Z.)  — Pydantic-Modelle
│   └── config.py              (18 Z.)  — Settings aus ENV
├── config/
│   └── space_agent_map.yaml   (22 Z.)  — Mappings
├── requirements.txt           — fastapi, httpx, pydantic, pyyaml
└── TESTPLAN.md                — Multi-Level-Testguide
```

**Gesamt: 837 Zeilen Code.** Bewusst minimal — die Bridge soll *routen*,
nicht *denken*.

---

## 3. Konfiguration (`config.py`)

```python
class Settings(BaseSettings):
    brain_url:          str   = "http://localhost:5000"
    openfang_url:       str   = "http://localhost:50051"
    bridge_port:        int   = 5150
    space_map_path:     str   = "config/space_agent_map.yaml"
    default_timeout_secs: int = 300
    brain_timeout_secs:  int  = 2       # Brain muss schnell antworten
    min_confidence:     float = 0.3     # darunter → Fallback-Agent
```

---

## 4. API-Endpoints

| Methode | Pfad                          | Zweck                                   |
|---------|-------------------------------|------------------------------------------|
| GET     | `/`                           | Service-Info + Endpoint-Listing         |
| POST    | `/bridge/route`               | **Haupt-Endpoint** (sync/async)         |
| GET     | `/bridge/tasks/{id}/status`   | Async-Task pollen                       |
| GET     | `/bridge/health`              | Health-Check (Brain + OpenFang)         |
| GET     | `/bridge/mapping`             | Aktuelles Space→Agent Mapping           |
| PUT     | `/bridge/mapping/reload`      | YAML Hot-Reload                         |

---

## 5. Der 5-Schritt-Flow (`router.py`)

### 5.1 Schritt 1: Route via Brain

```python
routing = await brain_client.route(request.task[:200], request.event_type or "")
# → RoutingInfo(primary_space="coding", confidence=0.83, routing_id="rt_abc123")
```

**Brain-Timeout:** 2 Sekunden. Bei Fehler: Fallback-Routing mit
`primary_space="ideas"`, `confidence=0.0`, `routing_id="rt_fallback"`.

### 5.2 Schritt 2: Map Space → Agent-Template

```python
agent_template = space_agent_mapper.map_space(routing.primary_space, routing.confidence)
# confidence < 0.3 → "vibemind" (Fallback)
# "coding" → "brain-coder"
```

**Mapping aus `space_agent_map.yaml`:**

| Space       | Agent-Template      |
|-------------|---------------------|
| coding      | brain-coder         |
| research    | brain-researcher    |
| desktop     | brain-devops        |
| ideas       | brain-planner       |
| bubbles     | brain-writer        |
| minibook    | brain-writer        |
| agentfarm   | brain-orchestrator  |
| n8n         | brain-orchestrator  |
| schedule    | vibemind (fallback) |
| video       | vibemind            |
| flowzen     | vibemind            |
| mirofish    | vibemind            |

### 5.3 Schritt 3: Ensure Agent in OpenFang

```python
agent_id = await openfang_client.ensure_agent(agent_template)
```

**Flow:**
1. In-Memory-Cache prüfen → Hit → return agent_id
2. `GET /api/agents` → laufende Agents durchsuchen
3. Gefunden → cache + return
4. Nicht gefunden → `POST /api/agents {template: "brain-coder"}`
5. agent_id aus Response → cache + return

**Bei Fehler (agent_id = None):**
- Falls Template ≠ "brain-fallback": Retry mit "brain-fallback"
- Falls auch Fallback scheitert: `HTTPException(503)`

### 5.4 Schritt 4: Enriched Message bauen

```python
message = f"[Brain Context: space={primary_space}, confidence={confidence}, "
          f"routing_id={routing_id}]\n"
if secondary_spaces:
    message += f"[Secondary candidates: {', '.join(secondary_spaces)}]\n"
message += request.task
```

### 5.5 Schritt 5: Execute + Reward

**Sync-Modus** (`fire_and_forget=False`, default):
```python
result = await openfang_client.send_message(agent_id, message, timeout)
success = result.status != "failed"
asyncio.create_task(brain_client.reward(routing_id, success))  # fire-and-forget
return BridgeResponse(task_id=result.task_id, status=result.status, ...)
```

**Async-Modus** (`fire_and_forget=True`):
- Sofort `BridgeResponse(status="pending", task_id=uuid)` zurück
- Background-Coroutine führt aus, updatet TaskStore
- Client pollt `GET /bridge/tasks/{id}/status`

### 5.6 Reward-Signal (Hebbian Learning)

```python
POST /api/cortex/route/reward
{ "routing_id": "rt_abc123", "success": true }
```

**Regeln:**
- `routing_id == "rt_fallback"` → kein Reward (kein Lernen aus Fallback)
- Timeout: 5 s, fire-and-forget
- Fehler werden nur auf DEBUG geloggt (nie raised)

---

## 6. Datenmodelle (`models.py`)

```python
class BridgeRequest(BaseModel):
    task: str                            # Pflicht
    context: Optional[dict] = None
    event_type: Optional[str] = None     # Pre-klassifizierter Event-Typ
    fire_and_forget: bool = False
    timeout_secs: Optional[int] = None

class RoutingInfo(BaseModel):
    primary_space: str
    secondary_spaces: list[str] = []
    confidence: float                    # 0.0 – 1.0
    routing_id: str                      # für Reward-Feedback

class BridgeResponse(BaseModel):
    task_id: str
    status: str                          # pending | working | completed | failed
    result: Optional[str] = None
    routing: RoutingInfo
    agent: str
    latency_ms: float = 0.0
```

---

## 7. TaskStore — Fire-and-Forget State

**Datei:** `task_store.py` (61 Zeilen).

In-Memory-Dict `{task_id: (TaskStatus, created_at)}`.

- `create()` — neuen Task anlegen (status=pending)
- `update()` — Status + Result setzen
- `get()` — Status abfragen
- **TTL:** 3.600 s (1 Stunde). Abgelaufene completed/failed Tasks werden bei
  jedem `create()`/`update()` evicted.

---

## 8. OpenFang (Rust Agent-OS)

**Verzeichnis:** `openfang/` (Git-Submodule, Upstream: `Flissel/openfang.git`).

**Im Monorepo:** Submodule ist aktuell **nicht geklont** (leerer Ordner). Das
System kommuniziert via HTTP/gRPC.

**Capabilities (aus Dokumentation):**
- 57 registrierte Tools
- 27 LLM-Modelle (OpenRouter, Ollama, direkte APIs)
- Port 50051 (gRPC) bzw. 4200 (HTTP REST)
- Agent-Templates via `agent.toml` Files

### 8.1 Agent-Sync-Script

**Datei:** `scripts/sync_openfang_agents.py` (198 Zeilen).

Liest `config/space_agent_registry.yml` (312 Zeilen) und generiert
`openfang/agents/{name}/agent.toml` Dateien:

```toml
name = "brain-coder"
version = "0.1.0"
module = "builtin:chat"
[model]
provider = "openrouter"
model = "anthropic/claude-3.5-sonnet"
max_tokens = 4096
temperature = 0.2
[mcp_allowed]
servers = ["vibemind-db", "vibemind"]
```

**Protected Agents** (nie überschrieben): `brain-coder`, `rowboat-knowledge`,
`brain-fallback`.

**Usage:**
```bash
python scripts/sync_openfang_agents.py              # schreiben
python scripts/sync_openfang_agents.py --dry-run    # nur anzeigen
python scripts/sync_openfang_agents.py --check      # CI: exit 1 bei Drift
```

---

## 9. Health-Check

```bash
curl http://localhost:5100/bridge/health
# → {"bridge": "ok", "brain": "ok", "openfang": "ok"}
```

Pingt Brain (`POST /api/cortex/route "health check"`) und OpenFang
(`GET /api/health`). Wenn Brain `rt_fallback` zurückgibt, gilt Brain
als `unreachable`.

---

## 10. Error-Handling & Graceful Degradation

| Fehler                          | Reaktion                                    |
|---------------------------------|---------------------------------------------|
| Brain-Routing fehlschlägt       | Fallback-Routing (ideas, confidence=0.0)   |
| Space unbekannt                 | Fallback-Agent "vibemind"                   |
| Confidence < 0.3               | Fallback-Agent "vibemind"                   |
| Agent-Spawn scheitert           | Retry mit "brain-fallback"                  |
| Fallback-Spawn auch gescheitert | HTTP 503                                    |
| Agent-Timeout (300s default)    | AgentResult(status="failed")                |
| Reward-Fehler                   | Geloggt (DEBUG), nicht propagiert           |

---

## 11. Bezug zur Space-MCP-Migration

Die Bridge ist das Stück Code, das sich durch die Migration am meisten
verändert. Wenn jeder Space seinen eigenen MCP hat:

| Heutig                                | Nach Migration                              |
|---------------------------------------|---------------------------------------------|
| YAML-Mapping (`space_agent_map.yaml`) | Weg — MCP `tools/list` als Source-of-Truth |
| `space_agent_mapper.py`               | Gelöscht                                    |
| Fallback-Agent "brain-fallback"       | Weg — Fail-Fast statt Fallback             |
| `sync_openfang_agents.py`             | Weg — Agents werden automatisch registriert |
| Reward-Signal verfälscht durch Fallback| Reward wird ehrlich                        |

**`TODO(space-mcp-migration)`** markiert in: `router.py` (Fallback-Block),
`space_agent_mapper.py` (Kopf), `sync_openfang_agents.py` (Kopf),
`config/space_agent_registry.yml` (Kopf).

---

## 12. Zusammenfassung

| Kennzahl                        | Wert                        |
|---------------------------------|-----------------------------|
| Bridge-Code gesamt              | 837 Zeilen                  |
| Endpoints                       | 6                           |
| Routing-Schritte                | 5                           |
| Brain-Timeout                   | 2 s                         |
| Agent-Timeout                   | 300 s (default)             |
| Space→Agent-Mappings            | 13 Spaces                   |
| Min-Confidence für echtes Routing| 0.3                        |
| TaskStore-TTL                   | 3.600 s                     |
| OpenFang-Tools                  | 57                          |
| OpenFang-LLM-Modelle           | 27                          |

**Nächste Portion:** *Portion 6 — Coding Engine* — das Society-of-Mind System
mit 37+ Agents, EventBus (510+ Event-Typen) und 6-Phasen-Pipeline.
