# Portion 1: System-Ueberblick & Architektur

> Foundational documentation for VibeMind-OS. Reading order: this document first, then
> Portion 2 (LLM-Configuration), then component deep-dives.

---

## 1. Was ist VibeMind-OS?

**VibeMind-OS** ist ein Open-Source AI-Operating-System, gebaut von **Felix Baumann**
([@Flissel](https://github.com/Flissel)) als Solo-Entwickler-Projekt. Die Kernidee:
ein sprachgesteuertes, multi-agentisches Betriebssystem, das Benutzereingaben ueber
40+ Kanaele (Voice, Telegram, WhatsApp, Slack, Discord, ...) entgegennimmt und an
eine neurowissenschaftlich inspirierte Routing-Schicht (Brain / Tahlamus) weitergibt,
welche die Anfrage an spezialisierte Domain-Spaces und Agent-Runtimes delegiert.

**Ziel des Systems:**
- Eine einheitliche Oberflaeche fuer Voice-first Interaktion mit LLMs
- Autonome Multi-Agent-Systeme fuer Coding, Desktop-Automatisierung, Research, Video, etc.
- Lokale-first Ausfuehrung moeglich (Ollama) - kein Cloud-Zwang
- Modularer Aufbau: jeder Teil laesst sich einzeln ausfuehren oder ersetzen

---

## 2. High-Level Architektur

```
User speaks / types / messages
        |
  +-----v------------------------------+
  |  voice/    3D UI + Voice Input     |
  |  openclaw/ Telegram/WhatsApp/Slack |
  |  openfang/ 40 Channels + 27 LLMs   |
  +-----+------------------------------+
        |
  +-----v------------------------------+
  |  Swarm Orchestrator + AutoGen      |
  |  14 Domain Spaces + Intent Router  |
  +-----+------------------------------+
        |
  +-----v------------------------------+
  |  shared/       LLM client factory  |
  |  langdock-mcp/ Enterprise AI API   |
  |  ops/          Email + Pitch + MCPs|
  |  security/     Monitoring + Defense|
  +------------------------------------+
```

**Routing-Kette (Kernpfad):**
```
User-Task
   -> Bridge (Port 5100, FastAPI)
   -> Brain / Tahlamus (Port 5000, Flask) - kognitive Routing-Entscheidung
   -> Space-Agent-Mapper - ordnet Space einem Agent-Template zu
   -> OpenFang (Port 50051, Rust) - fuehrt Agent mit Tools aus
   -> Reward-Feedback zurueck an Brain (Hebbian Learning)
```

---

## 3. Repository-Struktur

Top-Level Verzeichnisse und ihre Rolle:

| Verzeichnis           | Rolle                                                 |
| --------------------- | ----------------------------------------------------- |
| `brain/`              | Tahlamus kognitives Routing (Submodule `the_brain/`)  |
| `bridge/`             | FastAPI Routing-Schicht Brain <-> OpenFang            |
| `spaces/`             | 14 Domain-Spaces (ideas, coding, desktop, video, ...) |
| `voice/`              | VibeMind VoiceDialog (Electron 3D UI, Submodule)      |
| `ops/`                | Operations - Email, Pitch Deck, MCPs (Submodule)      |
| `shared/`             | `vibemind_shared` pip-Paket (Multi-Provider LLM)      |
| `security/`           | 30 Security-PoCs, red/blue team (Submodule)           |
| `openfang/`           | Agent OS, Rust, 57 Tools, 27 LLMs (Submodule)         |
| `openclaw/`           | Multi-Channel Agent Gateway (Submodule)               |
| `openclaude/`         | OpenClaude HTTP-Service (Submodule)                   |
| `clawcode/`           | Docker-basierte Claude-Integration (Submodule)        |
| `coding-engine/`      | Autonome Code-Generierung (Submodule)                 |
| `la-fungus-search/`   | Semantic Search (Qdrant + Embeddings, Submodule)      |
| `issue-detector/`     | MCP Server fuer Issue-Detection                       |
| `config/`             | Agent-Registry, Space-Konfiguration                   |
| `system/`             | 8 System-PoC MCP-Server + LLM Admin Helper            |
| `devops/`             | Git-Agents, Backup-Sync                               |
| `supabase/`           | PostgreSQL Migrations + Migration-Tools               |
| `scripts/`            | Utility Scripts (z.B. `sync_openfang_agents.py`)      |
| `business/`           | Pitch Deck Generation PoC                             |
| `.github/workflows/`  | CI: Config Validation + Secret Scanning               |

**Top-Level Dateien:**
- `README.md` - Projektvorstellung (112 Zeilen)
- `.env.example` - 58 Env-Variablen Template (5 Provider, Service-Ports, Optionen)
- `llm_config.yml.example` - Unified LLM-Konfiguration (213 Zeilen)
- `models_pricing.yml` - Preis-Referenz fuer Kosten-Schaetzung
- `start.sh` - Haupt-Startup-Skript (orchestriert Brain + OpenFang + Bridge)
- `.gitmodules` - 15 registrierte Submodule
- `migration_report.json` - Coding-Engine LLM-Migration Status
- `CONTRIBUTING.md` - Contribution Guidelines
- `OPENSOURCE_RELEASE_CHECKLIST.md` - Release-Checkliste

---

## 4. Submodul-Inventar (15 Submodule)

Aus `.gitmodules`:

| Pfad                            | Upstream                                       | Zweck                           |
| ------------------------------- | ---------------------------------------------- | ------------------------------- |
| `voice`                         | `Flissel/VibeMind-VoiceDialog`                 | Electron 3D UI + Voice Input    |
| `ops`                           | `Flissel/vibemind-os` (rekursiv!)              | Email/Pitch/MCPs Operations     |
| `shared`                        | `Flissel/vibemind-shared`                      | pip-installierbarer LLM Factory |
| `clawcode`                      | `Flissel/ClawCode`                             | Docker-Claude-Integration       |
| `openclaw`                      | `Flissel/openclaw`                             | 40+ Messaging Channels          |
| `openfang`                      | `Flissel/openfang`                             | Rust Agent-OS                   |
| `security`                      | `Flissel/vibemind-security`                    | Red/Blue Team PoCs              |
| `openclaude`                    | `Flissel/openclaude`                           | OpenClaude HTTP-Service         |
| `la-fungus-search`              | `Flissel/la_fungus_search`                     | Semantic Search Engine          |
| `coding-engine`                 | `Flissel/DaveFelix-Coding-Engine`              | 37+ Agent Code-Generierung      |
| `spaces/mirofish/mirofish`      | `Flissel/MiroFish` (backup-local-2026-04-12)   | Forecasting-Space               |
| `spaces/video/vibevideo`        | `Flissel/vibevideo`                            | Video-Generierung               |
| `spaces/video/vibevideo_deepfake` | `Flissel/vibevideo-deepfake`                 | Deepfake/Lipsync                |
| `spaces/flowzen/flowzen`        | `Flissel/flowzen`                              | Wellness/Workflow               |
| `spaces/shuttles/swe_desgine`   | `Flissel/swe_desgine`                          | SWE Design Factory              |
| `brain/the_brain/tribe`         | `Flissel/tribev2`                              | Agent-Tribe-System              |

> Hinweis: `ops/` zeigt rekursiv auf `vibemind-os` selbst - das ist beabsichtigt,
> um Ops-Artefakte separat vom Kern-OS verwalten zu koennen.

---

## 5. Service-Topologie

### Drei Kern-Services (gestartet durch `start.sh`)

| Service   | Port   | Framework    | Einstiegspunkt                                        |
| --------- | ------ | ------------ | ----------------------------------------------------- |
| Brain     | 5000   | Flask        | `brain/the_brain/production/api_server.py`            |
| OpenFang  | 50051  | Rust (gRPC)  | `openfang/target/release/openfang.exe start`          |
| Bridge    | 5100   | FastAPI      | `bridge/src/bridge/main.py` (uvicorn)                 |

### Weitere Services (ueber Spaces / separat gestartet)

| Service             | Port   | Beschreibung                                                  |
| ------------------- | ------ | ------------------------------------------------------------- |
| Coding Engine API   | 5173   | Autonome Code-Generierung (FastAPI)                           |
| Production API      | 5001   | Produktions-Endpunkt                                          |
| Swarm Server        | 5002   | 14 AutoGen-Agents                                             |
| Unified Brain       | 5003   | Unified Brain Service                                         |
| Memory API          | 8001   | Memory-System                                                 |
| Desktop Automation  | 8007   | TRAE / Automation_ui (FastAPI)                                |
| Ollama (optional)   | 11434  | Lokale LLMs                                                   |
| Rowboat (optional)  | 3000   | Knowledge Graph UI                                            |
| Minibook (optional) | 3480   | Inter-Space Collaboration UI                                  |
| Arch Team (optional)| 8087   | SWE Design Factory                                            |

### Health-Check Endpunkte

```bash
curl http://localhost:5000/api/health     # Brain    -> "alive"
curl http://localhost:50051/api/health    # OpenFang -> "ok"
curl http://localhost:5100/bridge/health  # Bridge   -> "ok"
```

---

## 6. Quick Start

```bash
# 1. Clone inkl. aller Submodule
git clone --recurse-submodules https://github.com/Flissel/VibeMind-OS.git
cd VibeMind-OS

# Falls ohne --recurse-submodules geklont:
git submodule update --init --recursive

# 2. API-Keys konfigurieren
cp .env.example .env
# Mindestens EINEN Key setzen: GROQ_API_KEY, OPENROUTER_API_KEY oder ANTHROPIC_API_KEY
# Groq ist kostenlos: https://console.groq.com/

# 3. OpenFang builden (einmalig)
cd openfang
cargo build --release -p openfang-cli
cd ..

# 4. Alle Services starten
./start.sh
```

**Routing-Chain Test nach Start:**
```bash
curl -X POST http://localhost:5100/bridge/route \
  -H "Content-Type: application/json" \
  -d '{"task": "Write a fibonacci function in Python"}'
```

**Voice 3D Workspace (optional):**
```bash
cd voice
pip install -r requirements.txt
python python/electron_backend.py
```

---

## 7. `.env.example` Walkthrough

Gruppierung der wichtigsten Variablen:

### LLM-Provider Keys (mind. einer erforderlich)
```ini
OPENAI_API_KEY=          # gpt-4o, gpt-4o-mini, gpt-4o-realtime (voice)
ANTHROPIC_API_KEY=       # Claude Sonnet/Opus/Haiku (beste Coding-Qualitaet)
OPENROUTER_API_KEY=      # Gateway zu 100+ Modellen, kostenlose Tier
GOOGLE_API_KEY=          # Gemini 2.0 Flash, Gemini 1.5 Pro
# Ollama benoetigt keinen Key - lokal: https://ollama.com/ + `ollama serve`
```

### Service-Ports
```ini
BRAIN_PORT=5000
OPENFANG_PORT=50051
BRIDGE_PORT=5100
```

### Brain (Tahlamus)
```ini
SUPERMEMORY_API_KEY=     # Optional: Long-term Memory
DEV_MODE=true
LOG_LEVEL=INFO
```

### OpenFang Defaults
```ini
OPENFANG_DEFAULT_PROVIDER=ollama                  # oder openai/anthropic/openrouter/google
OPENFANG_DEFAULT_MODEL=qwen2.5-coder:7b
```

### Bridge
```ini
BRAIN_URL=http://localhost:5000
OPENFANG_URL=http://localhost:50051
```

### Optional: Telegram (OpenFang Chat)
```ini
TELEGRAM_BOT_TOKEN=
```

> **Wichtig:** Die Zuordnung von LLM-Modellen zu **Rollen** passiert **nicht** hier,
> sondern in `llm_config.yml` (siehe Portion 2). `.env` haelt nur die API-Keys.

---

## 8. Multiverse-Spaces Konzept

Aus `spaces/__init__.py`:

VibeMind-OS organisiert seine AI-Faehigkeiten in **Spaces** - separate Universen
mit jeweils eigenem Zustaendigkeitsbereich. Jeder Space hat:

- **`type`** - SpaceType Enum
- **`name`** / **`description`** - Menschen-lesbare Beschreibung
- **`position`** - 3D-Koordinaten (x, y, z) fuer die Voice-3D-UI
- **`agent_slug`** - Zustaendiger Agent (z.B. "rachel", "minibook")
- **`color`** - Hex-Farbe fuer Visualisierung
- **`visualization`** - bubble, planet, nebula, portal, factory, light_planet
- **`metadata`** - Space-spezifische Konfiguration

### Die 8 Spaces in `SpaceType` Enum (Kern-Spaces)

| Space          | Position        | Agent     | Visual        | Zweck                                      |
| -------------- | --------------- | --------- | ------------- | ------------------------------------------ |
| `IDEAS`        | (0, 0, 0)       | rachel    | bubbles       | Ideen-Bubbles sammeln & organisieren       |
| `CODING`       | (-8, 2, -3)     | (auto)    | nebula        | Code-Werkstatt, Projekte                   |
| `DESKTOP_SPACE`| (10, 0, -5)     | (auto)    | light_planet  | Desktop-Kontrolle via Automation_ui        |
| `OPENCLAW`     | (12, 3, -8)     | openclaw  | planet        | AutoGen Society of Mind, Claude CLI        |
| `TRANSFORMER`  | (-4, 4, -6)     | (auto)    | portal        | Bubble-to-Coding Pipeline                  |
| `ROWBOAT`      | (-12, -2, -10)  | rowboat   | nebula        | Knowledge Graph (Emails, Meetings)         |
| `SWE_DESIGN`   | (8, 0, 5.5)     | (auto)    | factory       | SWE Design Factory (Shuttles-Pipeline)     |
| `MINIBOOK`     | (6, -3, -12)    | minibook  | nebula        | Inter-Space Collaboration / Multi-Agent    |

### Zusaetzliche Spaces im Repo (unter `spaces/`)

Neben den 8 Enum-Spaces existieren weitere Unter-Verzeichnisse, die de-facto als
Spaces fungieren: `autogen/`, `brain/`, `desktop/`, `flowzen/`, `mirofish/`, `n8n/`,
`research/`, `schedule/`, `shuttles/`, `video/` - insgesamt 14 Domain-Gruppen.

### API zum Spaces-Modul

```python
from spaces import SpaceType, get_space, get_all_spaces, get_space_by_agent

# Space per Typ holen
ideas = get_space(SpaceType.IDEAS)
print(ideas.position)  # {'x': 0, 'y': 0, 'z': 0}

# Alle Spaces listen
for space in get_all_spaces():
    print(f"{space.name}: {space.description}")

# Space ueber Agent-Slug finden
rachel_space = get_space_by_agent("rachel")
```

### `metadata`-Flags

- `entry_point: bool` - Kann direkt als Einstieg dienen (z.B. IDEAS, ROWBOAT)
- `allows_creation: bool` - Neue Objekte erstellen erlaubt
- `requires_hand_motion: bool` - Benoetigt Gesten-Input (DESKTOP_SPACE)
- `pipeline: bool` - Space ist eine Pipeline, keine persistente Welt
- `uses_autogen`, `uses_mcp`, `uses_docker` - Integrations-Flags

---

## 9. Tech-Stack Ueberblick

| Bereich                  | Technologie                                               |
| ------------------------ | --------------------------------------------------------- |
| Kern-Sprachen            | Python 3.10+, Rust (OpenFang), TypeScript/JavaScript      |
| Backend-Frameworks       | Flask (Brain), FastAPI (Bridge/Coding/Desktop), uvicorn   |
| Frontend                 | Next.js 16 (Minibook), React 18 (Dashboard), Electron 3D  |
| UI-Libraries             | Radix UI, Tailwind CSS 3/4, shadcn/ui, Three.js           |
| State Management         | Zustand (9 Stores im Coding-Dashboard), React Context     |
| Datenbank                | Supabase / PostgreSQL (pgvector), SQLite (Dev)            |
| ORM                      | SQLAlchemy (async) + Pydantic Validierung                 |
| Vector-Search            | Qdrant + SentenceTransformers Embeddings                  |
| LLM-Provider             | OpenAI, Anthropic, OpenRouter, Google, Ollama             |
| Agent-Framework          | AutoGen 0.4+, Society of Mind Pattern, MCP Protocol       |
| Real-time                | WebSocket, Server-Sent Events (SSE), Redis Pub/Sub        |
| Containerisierung        | Docker, docker-compose (pro Service)                      |
| CI/CD                    | GitHub Actions (Config-Validation + Secret-Scanning)      |
| Package-Manager          | pip (Python), Cargo (Rust), npm (JS/TS)                   |

---

## 10. Abhaengigkeiten zwischen Komponenten

```
Bridge  --depends-on-->  Brain + OpenFang
Brain   --depends-on-->  llm_config.yml (Rollen)
Spaces  --depends-on-->  Bridge + config/space_agent_registry.yml
Voice   --depends-on-->  Bridge (indirekt via Intent-Router)
Coding  --depends-on-->  MCP-Plugin-System + shared/ + Supabase
Desktop --depends-on-->  Redis + Supabase Edge Functions
```

**Harte Reihenfolge beim Start (start.sh):**
1. Brain (Port 5000) - muss zuerst laufen, da Bridge ihn braucht
2. OpenFang (Port 50051) - unabhaengig, aber Bridge braucht ihn
3. Bridge (Port 5100) - braucht beide oben

Wenn einer der drei fehlt, ist die End-to-End-Routing-Chain nicht verfuegbar -
einzelne Spaces oder die Coding-Engine koennen aber trotzdem standalone genutzt werden.

---

## 11. Git-Repository Info

- **Primaeres Repo:** `Flissel/vibemind-os`
- **Lizenz:** MIT
- **Aktueller Branch:** `master` (Feature-Branch: `claude/create-jodisweb-repo-mTlgF`)
- **Commits:** 27 (Stand April 2026)
- **Letzte Aktivitaet:** April 19, 2026 (chore: bump nested submodules)

---

## 12. Was NICHT hier dokumentiert ist

Dieser Ueberblick bewusst flach gehalten. Fuer Details siehe:

- **Portion 2** - LLM-Config, Shared-Infra, Role-System
- **Portion 3** - Brain / Tahlamus interne Architektur
- **Portion 4** - Bridge Routing-Details + OpenFang
- **Portion 5** - Coding Engine 37+ Agents, EventBus
- **Portion 6** - Desktop Automation TRAE
- **Portion 7** - Forge Agents + Minibook
- **Portion 8** - Frontends / Dashboards
- **Portion 9** - Datenbank-Schema
- **Portion 10** - Spezialisierte Spaces, DevOps, System-PoCs

---

## 13. Architektur-Roadmap: Aktive Migrationen

**Zwei Bewegungen sind entschieden aber noch nicht umgesetzt.** Beide sind
im Code mit `grep`-baren TODO-Tags markiert.

### 13.1 Space-MCP-Migration (`TODO(space-mcp-migration)`)

**Was:** N:M-Mapping (Spaces ↔ geteilte MCP-Server) → 1 MCP pro Space +
kleine Menge Core-MCPs (memory, time, db, filesystem).

**Warum:**
- Brain kennt live die Capabilities jedes Space (via MCP `tools/list`)
- Fallback-Logik in der Bridge entfaellt (Fail-Fast wird ehrlich)
- `scripts/sync_openfang_agents.py` wird obsolet
- `space_agent_mapper.py` wird obsolet
- `config/space_agent_registry.yml` schrumpft von 272 auf ~30 Zeilen

**Plan:** `docs/migration-to-space-mcps.md`

**Code-Anekdoten** (`grep -rn "space-mcp-migration" .`):
- `config/space_agent_registry.yml`
- `scripts/sync_openfang_agents.py`
- `bridge/src/bridge/router.py` (Fallback-Block)
- `bridge/src/bridge/space_agent_mapper.py`

---

### 13.2 LLM-Config-Konsolidierung (`TODO(llm-config-consolidation)`)

**Was:** Parallele `system/llm_*` Dateien → alles ueber die kanonische
`llm_config.yml` (Root) und `vibemind_shared` Factory. **Schlussendlich
brauchen wir nur den Root.**

**Warum:**
- Heute existieren zwei LLM-Config-Systeme: Root (CI-validiert, 33 moderne
  Rollen, Provider google) und `system/` (nicht CI-validiert, 10 alte Rollen,
  Provider gemini+groq)
- Drift-Gefahr: System-Config kann divergieren, ohne dass CI das meldet
- Alte Rollen-Namen (`red_team`, `blue_team`, ...) widersprechen der neuen
  `<subsystem>_<purpose>` Konvention
- Code-Duplikation: `system/llm_client.py` ist Reimplementierung von
  `vibemind_shared`

**Zielzustand:**
- `system/llm_config.yml` als `overrides.system:` in Root-Config migriert
- `system/llm_client.py` geloescht, alle PoCs importieren `from vibemind_shared`
- Alte Rollen umbenannt: `red_team` → `security_red_team`, etc.
- Entscheidung: `groq` als Provider behalten oder streichen?

**Plan:** `docs/portion-02-llm-config.md` Abschnitt 12 + 15

**Code-Anekdoten** (`grep -rn "llm-config-consolidation" .`):
- `system/llm_client.py`
- `system/llm_config.yml`

---

### Tracking aller aktiven Migrationen

```bash
grep -rn "TODO(" --include="*.py" --include="*.yml" --include="*.md" .
```

findet jede Stelle, die nach einer dieser Migrationen verschwinden oder sich
aendern muss. Faustregel: **keine Migration ohne TODO-Tag, kein TODO-Tag
ohne Migration-Doc in `docs/`.**

---

## 14. Offene Fragen / TODOs fuer Weiterentwicklung

- [ ] Architektur-Diagramm als SVG/PNG erstellen (derzeit nur ASCII)
- [ ] Health-Check-Endpunkte in Markdown-Tabelle mit erwarteten Antworten
- [ ] Docker-Compose auf Root-Ebene fuer One-Shot-Start aller Services
- [ ] `OPENSOURCE_RELEASE_CHECKLIST.md` verlinken und zusammenfassen
- [ ] Vergleichstabelle: lokal-only vs. Cloud-API Modus
- [ ] Sequence-Diagramm fuer eine typische Task (User-Input -> Bridge -> Brain -> OpenFang -> Response)
