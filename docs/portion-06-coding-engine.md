# Portion 6 — Coding Engine: Autonome Code-Generierung

> **Teil 6 von 10** der VibeMind-OS-Systemdokumentation.
> Dependencies: Portion 2 (LLM-Config), Portion 5 (Bridge routet Jobs hierher).

---

## TL;DR

Die Coding Engine ist ein **Society-of-Mind**-System mit **37+ autonomen Agents**,
die über einen **EventBus** (510+ Event-Typen, Push-basiert, 0.5 s Batching)
kommunizieren. Code-Generierung läuft als **6-Phasen Hybrid-Pipeline**
(Architecture → Generation → Merge → Test → Deploy → Validate) mit einem
**Convergence-Loop**, der 100+ Metriken trackt und erst stoppt, wenn alle
Kriterien erfüllt sind. Dazu: **34 Skills**, **29 MCP-Server**, ein
**Electron+React Dashboard** für Live-Monitoring, und eine strikte
**NO-MOCKS-Policy** (echte DB, echte HTTP, echte Crypto in Tests).

---

## 1. Architektur — 3 Schichten

```
┌──────────────────────────────────────────────────────────────────┐
│ Layer 3: MCP Plugin Ecosystem                                    │
│   29 Server (filesystem, playwright, docker, redis, github, ...) │
│   ↕ tools/list + JSON-Schema                                     │
├──────────────────────────────────────────────────────────────────┤
│ Layer 2: Society of Mind (Orchestrator + 37+ Agents)             │
│   EventBus pub/sub  ←→  AutonomousAgent.should_act() / act()    │
│   SharedState + ConvergenceMetrics                               │
├──────────────────────────────────────────────────────────────────┤
│ Layer 1: Hybrid Pipeline Engine                                  │
│   6 Phasen: Architect → Generate → Merge → Test → Deploy → Valid │
│   Slicer → Parallel Workers → Merger → Checkpoint                │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Verzeichnis-Map

```
spaces/coding/Coding_engine/
├── CLAUDE.md                    ← 34 KB Leitfaden
├── src/
│   ├── mind/
│   │   ├── event_bus.py         (4.431 Z.) — Push-basierter EventBus
│   │   ├── orchestrator.py      (2.839 Z.) — Agent-Lifecycle + Convergence
│   │   ├── shared_state.py      (1.237 Z.) — SharedState Singleton
│   │   └── convergence.py       (413 Z.)   — Kriterien + Check
│   ├── agents/                  (88 Dateien) — Alle Agents
│   │   ├── autonomous_base.py   (2.427 Z.) — Base-Class + Contract
│   │   ├── generator_agent.py   — Code-Generator
│   │   ├── tester_team_agent.py — Test-Ausführung
│   │   ├── fixer_agent.py       — Error-Recovery
│   │   └── ...
│   ├── engine/
│   │   ├── hybrid_pipeline.py   (2.078 Z.) — 6-Phasen-Pipeline
│   │   ├── slicer.py            — Domain-basiertes Chunking
│   │   ├── merger.py            — Slice-Merging
│   │   ├── contracts.py         — TypeScript-Interfaces
│   │   └── checkpoint_manager.py— Resume-Fähigkeit
│   ├── api/main.py              — FastAPI REST + WebSocket
│   ├── tools/, validators/, monitoring/, security/
│   └── ...
├── mcp_plugins/servers/         (29 MCP-Server)
├── dashboard-app/               Electron + React Dashboard
├── .claude/skills/              (34 Skills)
├── config/                      Convergence-Defaults
├── tests/                       Test-Suite
├── run_generation.py            — Standalone Entry
├── run_engine.py                — Master-Orchestrator
├── run_society_hybrid.py        — Society-of-Mind Entry
└── docker-compose.yml           — Full-Stack
```

---

## 3. EventBus — Push-basierte Agent-Kommunikation

**Datei:** `src/mind/event_bus.py` (4.431 Zeilen).

### 3.1 Kern-Design

- **Push-basiert** (nicht Polling) — Events werden sofort an alle
  registrierten Handler delivered
- **Async Queues** pro Subscriber
- **Batching:** 0.5 s Fenster — Events innerhalb des Fensters werden als
  Batch an `should_act()` übergeben
- **Queue-Timeout:** 5.0 s

### 3.2 Event-Typen (510+ definiert)

```python
class EventType(str, Enum):
    FILE_CREATED       = "file.created"
    CODE_GENERATED     = "code.generated"
    BUILD_SUCCEEDED    = "build.succeeded"
    TEST_PASSED        = "test.passed"
    DEPLOY_SUCCEEDED   = "deploy.succeeded"
    CONVERGENCE_UPDATE = "convergence.update"
    # ... 500+ weitere
```

**Kategorien-Überblick:**

| Kategorie        | Anzahl | Beispiele                                          |
|------------------|-------:|----------------------------------------------------|
| File/Code        | 10     | FILE_CREATED, CODE_GENERATED, CODE_FIX_NEEDED     |
| Build/Deploy     | 8      | BUILD_STARTED/SUCCEEDED/FAILED, DEPLOY_*            |
| Test/E2E         | 15     | TEST_PASSED, E2E_TEST_STARTED, SANDBOX_TEST_*      |
| Validation       | 7      | VALIDATION_ERROR, TYPE_CHECK_PASSED                |
| Agent-Lifecycle  | 4      | AGENT_STARTED, AGENT_ACTING, AGENT_COMPLETED       |
| Database/API     | 10     | DATABASE_SCHEMA_GENERATED, API_ROUTES_GENERATED    |
| Review-Gate      | 5      | REVIEW_PAUSE_REQUESTED, REVIEW_FEEDBACK_SUBMITTED  |
| Fullstack        | 4      | FULLSTACK_CHECK_STARTED, FULLSTACK_VERIFIED        |
| Docker/Infra     | 7      | DOCKER_BUILD_REQUESTED/SUCCEEDED/FAILED            |
| UX/Browser       | 7      | UX_REVIEW_COMPLETE, BROWSER_CONSOLE_ERROR          |
| Messaging        | 20+    | GROUP_*, PRESENCE_*, E2EE_*                         |

### 3.3 Event-Struktur

```python
@dataclass
class Event:
    type: EventType
    payload: dict
    timestamp: float
    source: str              # Agent-Name
    correlation_id: str      # Request-Tracking
```

### 3.4 API

```python
bus = EventBus()
bus.subscribe(EventType.BUILD_FAILED, my_handler)
bus.subscribe_all(logging_handler)
await bus.publish(Event(type=EventType.CODE_GENERATED, payload={...}))
history = bus.get_history(EventType.BUILD_FAILED, limit=10)
```

---

## 4. Agent-Framework — should_act / act Contract

**Datei:** `src/agents/autonomous_base.py` (2.427 Zeilen).

### 4.1 Base-Class

```python
class AutonomousAgent(ABC):
    @property
    async def subscribed_events(self) -> list[EventType]:
        """Welche Events interessieren diesen Agent?"""

    async def should_act(self, events: list[Event]) -> bool:
        """Entscheiden: soll ich jetzt handeln?"""

    async def act(self, events: list[Event]) -> Optional[Event]:
        """Handeln und optional neues Event publishen."""
```

**Ablauf:**
1. EventBus sammelt Events im 0.5 s Batch-Window
2. Agent bekommt Batch → `should_act(batch)` entscheidet
3. Falls True → `act(batch)` → optionaler Return-Event → EventBus

### 4.2 Eingebaute Agent-Typen (in autonomous_base.py)

| Agent          | Zweck                                            |
|----------------|--------------------------------------------------|
| `BuilderAgent` | Build ausführen (npm build, etc.)                |
| `TesterAgent`  | Test-Suite ausführen                             |
| `ValidatorAgent` | TypeScript-Validation                          |
| `FixerAgent`   | Error-Recovery mit Multi-Agent-Fixing (FixerPool)|

### 4.3 FixerPool — Parallele Batch-Reparatur

```python
class FixerPool:
    max_concurrent: int = 3   # Semaphore-gesteuert
    # Groupiert Errors nach Typ → parallele Fixes
```

---

## 5. Orchestrator — Agent-Lifecycle + Convergence

**Datei:** `src/mind/orchestrator.py` (2.839 Zeilen).

### 5.1 Agent-Dependency-Graph

```
Builder → (keine Deps)
Validator → Builder
Tester → Builder
Fixer → Builder, Validator, Tester
Generator → Fixer
DatabaseAgent → Builder
APIAgent → DatabaseAgent
AuthAgent → APIAgent
InfrastructureAgent → AuthAgent
DeploymentTeam → Builder, Tester
E2E-Tests → DeploymentTeam
FullstackVerifier → (multiple Trigger)
ContinuousArchitect → Verification-Failures
```

### 5.2 Orchestrator-Result

```python
@dataclass
class OrchestratorResult:
    success: bool
    converged: bool
    convergence_reasons: list[str]
    final_metrics: Optional[ConvergenceMetrics]
    iterations: int
    duration_seconds: float
    errors: list[str]
```

---

## 6. Hybrid-Pipeline — 6 Phasen

**Datei:** `src/engine/hybrid_pipeline.py` (2.078 Zeilen).

### Phase 1: Architecture Analysis
`ArchitectAgent` analysiert Requirements → TypeScript-Contracts (Interfaces,
API-Routen, DB-Schema).

### Phase 2: Parallel Code Generation
`Slicer` zerlegt Requirements in Domain-Chunks → parallele Workers generieren
Code pro Chunk → `CheckpointManager` sichert Zwischenstände.

### Phase 3: Merge & Validate
`Merger` kombiniert Slices → `ValidatorAgent` prüft TypeScript-Typen,
Imports, Konsistenz.

### Phase 4: Test & Debug
`TesterTeamAgent` führt Vitest/Jest aus → `FixerAgent` repariert Fehler →
Iteration bis Tests grün.

### Phase 5: Deploy & E2E
Docker-Sandbox starten → `PlaywrightE2EAgent` testet im Browser →
Screenshots → `UXDesignAgent` bewertet.

### Phase 6: Full Validation
`ValidationTeamAgent` nutzt **Multi-Agent-Debate** (3 Solver-Perspektiven:
Implementation, Testing, Deployment) mit Majority-Voting.

---

## 7. Convergence — 100+ Metriken

**Dateien:** `shared_state.py` (1.237 Z.), `convergence.py` (413 Z.).

### 7.1 Metriken-Kategorien (Auszug)

| Kategorie      | Metriken                                                |
|----------------|---------------------------------------------------------|
| Tests          | total, passed, failed, skipped, coverage               |
| Build          | attempted, success, errors                             |
| Validation     | errors, warnings, type_errors                          |
| E2E            | e2e_tested, e2e_success, playwright_visual_issues      |
| Fullstack      | frontend/backend/database/integration_verified         |
| Sandbox        | tested, success, errors, duration_ms                   |
| Deadlock       | consecutive_same_errors, is_stuck                      |
| Code           | files_generated, lines_of_code                         |

### 7.2 Convergence-Kriterien (konfigurierbar)

```python
@dataclass
class ConvergenceCriteria:
    min_tests_passing_rate:  float = 95.0     # %
    require_build_success:   bool  = True
    max_validation_errors:   int   = 0
    max_type_errors:         int   = 0
    min_confidence_score:    float = 0.85
    max_iterations:          int   = 50
    max_time_seconds:        int   = 600      # 10 min
    enable_deadlock_detection: bool = True
    stuck_threshold:          int   = 3
```

**3 Presets:** DEFAULT, STRICT (`require_all_tests_pass=True`),
RELAXED (`min_tests_passing_rate=80`).

---

## 8. 88 Agent-Dateien — Die Armee

**Verzeichnis:** `src/agents/` (88 Python-Dateien).

### 8.1 Aktive Agents (~29)

| Gruppe              | Agents                                                     |
|---------------------|------------------------------------------------------------|
| Core (4)            | Builder, Tester, Validator, Fixer                          |
| Code-Gen (2)        | Generator, BugFixer                                         |
| E2E-Testing (7)     | TesterTeam, PlaywrightE2E, ValidationTeam, CodeQuality,    |
|                     | ContinuousE2E, E2EIntegrationTeam, RequirementsPlaywright  |
| Deployment (3)      | DeploymentTeam, Deploy, RuntimeDebug                        |
| Backend-Chain (5)   | Database, API, Auth, Infrastructure, DatabaseSchema         |
| UX & Docs (2)       | UXDesign, Documentation                                     |
| Security (2)        | SecurityScanner, DependencyManager                          |
| UI-Integration (2)  | UIIntegration, BrowserConsole                               |
| Advanced (2)        | ContinuousDebug, FullstackVerifier                          |

### 8.2 Spezial-Agents (optional/Feature-Flagged)

EventInterpreter, FrontendValidator, DevContainer, ChunkPlanner,
ContinuousArchitect, ValidationRecovery.

### 8.3 Legacy (nicht instanziiert, aber vorhanden)

Backend, Frontend, Testing, Security, DevOps, Recovery, Preview, RuntimeTest
— historische Agent-Definitionen, die durch die aktiven Agents ersetzt wurden.

---

## 9. 29 MCP-Server

**Verzeichnis:** `mcp_plugins/servers/`.

| Server         | Zweck                                    |
|----------------|------------------------------------------|
| filesystem     | Datei-Operationen (read, write, search)  |
| playwright     | Browser-Automation, E2E                  |
| docker         | Container-Management                     |
| redis          | Pub/Sub, Caching                         |
| github         | GitHub API (Repos, PRs, Issues)          |
| git            | Git-Operationen (commit, push, branch)   |
| prisma         | ORM-Operationen, Migrations              |
| npm            | Package-Management                       |
| postgres       | PostgreSQL direkt                        |
| fetch          | HTTP-Client (curl-Wrapper)               |
| supabase       | Supabase-Backend                         |
| memory         | Agent-Memory-Persistence                 |
| context7       | Context-Manager (RAG)                    |
| tavily         | Web-Search-API                           |
| brave-search   | Brave-Search                             |
| n8n            | N8N-Workflow-Automation                  |
| claude-code    | Claude-CLI-Integration                   |
| time           | Zeit/Scheduling                          |
| windows-core   | Windows-spezifisch                       |
| desktop        | Desktop/GUI-Automation                   |
| qdrant         | Vector-DB (Embedding-Search)             |
| supermemory    | Batch-Context-Loading                    |
| taskmanager    | Task-Queue                               |
| grpc_host      | gRPC für verteilte Agents                |
| fungus_mcp     | Fungus RAG-Validation                    |
| dev            | Dev-Utilities                            |
| shared         | Shared Server-Utilities                  |
| tests          | Test-Utilities                           |

---

## 10. 34 Skills

**Verzeichnis:** `.claude/skills/`.

**Tier-basiertes Loading:**
- **Minimal** (~200 Token): Trigger-Events + Critical Rules
- **Standard** (~800 Token): + Workflow + Error-Patterns
- **Full** (~1.600 Token): + Code-Examples

**Wichtige Skills:**

| Skill                    | Trigger-Events                                    |
|--------------------------|---------------------------------------------------|
| code-generation          | BUILD_FAILED, CODE_FIX_NEEDED, E2E_TEST_FAILED  |
| test-generation          | GENERATION_COMPLETE, BUILD_SUCCEEDED              |
| database-schema-generation| CONTRACTS_GENERATED, SCHEMA_UPDATE_NEEDED        |
| api-generation           | CONTRACTS_GENERATED, DATABASE_SCHEMA_GENERATED    |
| docker-sandbox           | BUILD_SUCCEEDED, DEPLOY_REQUESTED                 |
| e2e-testing              | DEPLOY_SUCCEEDED, APP_LAUNCHED                    |
| validation               | TEST_PASSED, E2E_TEST_PASSED                      |
| debugging                | BUILD_FAILED, SANDBOX_TEST_FAILED                 |

**Kritische Policies:**
- **NO MOCKS** — echte DB, echte HTTP, echte Crypto in Tests
- **Admin Seeding** — muss Admin-User bei Startup generieren
- **Permission Checking** — direkte Berechtigungsprüfung
- **Production-Ready** — keine TODOs/FIXMEs im generierten Code

---

## 11. Dashboard-App (Electron + React)

**Verzeichnis:** `dashboard-app/`.
**Stack:** Electron 28 + Vite 5 + React 18 + Zustand + Tailwind CSS.

**Features:**
- VNC-Streaming (Live-Preview auf `localhost:6080/vnc.html`)
- Generation-Progress-Monitor (WebSocket-Updates)
- Review-Chat (Pause/Resume mit User-Feedback)
- Docker-Container-Management
- Task-Board (Kanban-artig)
- Multi-Projekt-Support

---

## 12. Entry-Points

| Datei                  | Zweck                                  | Kommando                                    |
|------------------------|----------------------------------------|---------------------------------------------|
| `run_generation.py`    | Standalone-Generation                  | `python run_generation.py --project-path ...`|
| `run_engine.py`        | Master-Orchestrator                    | `python run_engine.py --project Data/...`   |
| `run_society_hybrid.py`| Society-of-Mind + Pipeline             | `python run_society_hybrid.py reqs.json`    |
| `src/api/main.py`      | REST/WebSocket-API                     | `python -m src.api.main`                    |

**CLI-Flags:** `--fast`, `--strict`, `--autonomous`, `--no-preview`,
`--max-iterations N`, `--preview-port P`.

---

## 13. Zusammenfassung

| Kennzahl               | Wert                     |
|------------------------|--------------------------|
| Agent-Dateien          | 88                       |
| Aktive Agents          | ~29                      |
| Event-Typen            | 510+                     |
| MCP-Server             | 29                       |
| Skills                 | 34                       |
| Pipeline-Phasen        | 6                        |
| Convergence-Metriken   | 100+                     |
| Code gesamt            | ~35.000+ Zeilen          |
| EventBus-Batch-Window  | 0,5 s                    |
| Max-Iterations Default | 50                       |

**Nächste Portion:** *Portion 7 — Desktop Automation (TRAE)* — die
Desktop-Automatisierungsplattform auf Port 8007.
