# Migration: N:M Tools -> 1 MCP pro Space

> **Status:** ARCHITEKTUR-ZIEL / TODO. Noch nicht umgesetzt.
> **Entscheidung:** Vollstaendige Migration (Big-Bang im Zeitverlauf, aber Space fuer Space).
> **Owner:** Felix

---

## Kontext

Aktuell gilt im Repo ein **N:M Mapping** zwischen Spaces und MCP-Servern:

- Mehrere Spaces teilen sich wenige Shared-MCPs (`vibemind-db`, `vibemind`, `memory`, ...)
- Tools heissen generisch (`vibemind_bubble_create`, `db_ideas_list`) und leben nicht beim Space
- `config/space_agent_registry.yml` pflegt 272 Zeilen Event->Tool Mapping haendisch
- Die Bridge braucht einen `brain-fallback`-Agent, weil Routing auf nicht-existente Agents passieren kann
- Brain routet **blind** — kennt weder Agent-Verfuegbarkeit noch Tool-Liste

## Zielzustand

**1 MCP pro Space** + kleines Set geteilter Core-MCPs.

```
Space-MCPs (1:1):
  mcp-bubbles    -> tools: bubble.create, bubble.list, bubble.enter, ...
  mcp-ideas      -> tools: idea.create, idea.link, idea.format_table, ...
  mcp-coding     -> tools: code.generate, code.review, code.test, ...
  mcp-desktop    -> tools: desktop.click, desktop.type, desktop.ocr, ...
  mcp-video      -> tools: video.generate, video.deepfake, ...
  mcp-research   -> tools: research.search, research.summarize, ...
  mcp-schedule   -> tools: schedule.create, schedule.list, ...
  mcp-flowzen    -> tools: wellness.log, wellness.query, ...
  mcp-mirofish   -> tools: forecast.predict, ...
  mcp-n8n        -> tools: workflow.run, workflow.list, ...
  mcp-rowboat    -> tools: knowledge.search, knowledge.graph, ...
  mcp-shuttles   -> tools: shuttle.evaluate, shuttle.promote, ...
  mcp-minibook   -> tools: minibook.post, minibook.discuss, ...
  mcp-transformer-> tools: transform.bubble_to_spec, ...

Core-MCPs (geteilt):
  mcp-memory     -> tools: memory.store, memory.recall (Supermemory + pgvector)
  mcp-db         -> tools: db.query, db.insert (generic DB access)
  mcp-time       -> tools: time.now, time.schedule
  mcp-filesystem -> tools: fs.read, fs.write, fs.list
  mcp-fetch      -> tools: fetch.url (HTTP requests)
  mcp-llm        -> tools: llm.call (via vibemind-shared)
```

## Warum das die richtige Architektur ist

1. **MCP-Protokoll ist selbst-beschreibend.** `tools/list` + JSON-Schema → Brain kann Capability live abfragen, keine YAML-Pflege.
2. **Fail-Fast gratis.** MCP antwortet nicht → Space offline → Brain routet woanders hin. Kein Fallback-Agent noetig.
3. **Explainability.** Jeder Space deklariert was er kann. Debugging wird trivial.
4. **Deployment-Units.** Space = eigenes MCP = eigenes Repo = eigene CI = unabhaengig skalierbar.
5. **Ehrliches Hebbian-Learning.** Brain lernt nur aus real-ausgefuehrten Tasks, nicht aus Fallback-Routings.
6. **Obsolete Code verschwindet:**
   - `scripts/sync_openfang_agents.py` → nicht mehr noetig (agent.toml aus MCP generiert)
   - `space_agent_mapper.py` → MCP-Registry ersetzt ihn
   - Fallback-Logik in `bridge/router.py` → entfaellt
   - Event->Tool Sektion in Registry YAML → entfaellt

## Migrations-Reihenfolge

Nach Isolationsgrad, nicht alphabetisch. Kleine/isolierte zuerst, grosse/vernetzte zuletzt.

| Prio | Space            | Warum jetzt                       | Aufwand  |
|------|------------------|-----------------------------------|----------|
| 1    | `bubbles`        | Kleine Tool-Liste, nur DB         | ~4h      |
| 2    | `ideas`          | Aehnlich wie bubbles, gleiche DB  | ~4h      |
| 3    | `schedule`       | Nur APScheduler, isoliert         | ~2h      |
| 4    | `research`       | Nur Web-Calls                     | ~2h      |
| 5    | `flowzen`        | Wellness, isoliert                | ~4h      |
| 6    | `mirofish`       | Forecasting, isoliert             | ~4h      |
| 7    | `n8n`            | Workflow-CRUD                     | ~4h      |
| 8    | `rowboat`        | Knowledge Graph                   | ~6h      |
| 9    | `video`          | Video-Tools + Deepfake Submodule  | ~6h      |
| 10   | `shuttles`       | Requirement-Pipeline              | ~4h      |
| 11   | `minibook`       | Inter-Space Collaboration         | ~6h      |
| 12   | `transformer`    | Bubble-to-Spec Pipeline           | ~6h      |
| 13   | `desktop`        | Gross, viele Tools                | ~2d      |
| 14   | `coding`         | Groesste Tool-Basis, 88 Agents    | ~3d      |

**Gesamt:** ca. 2-3 Wochen fokussierte Arbeit, aber jeder Space liefert nach Migration sofort eine testbare Einheit.

## Migrations-Template (pro Space, halber Tag)

```
1. Space-MCP-Skelett anlegen
   spaces/<space>/mcp_server.py
   Minimaler MCP-Server mit leerer tools/list

2. Tool-Funktionen migrieren
   Aus dem alten Shared-MCP kopieren (Bubble-Server: vibemind_bubble_create -> bubble.create)
   Renaming: <space>.<verb> Convention

3. Smoke-Tests
   mcp-call mcp-<space> <space>.<verb> '{"...": ...}' -> verify

4. Parity-Test gegen alten Tool-Pfad
   Alter Pfad + Neuer Pfad -> gleiche Antwort -> OK

5. agent.toml umhaengen
   [mcp_allowed]
   own_space = "<space>"
   core_servers = ["memory", "time", ...]

6. End-to-End
   Bridge-Route -> Space -> neuer MCP -> Tool -> Response

7. Altes Tool aus Shared-MCP entfernen
```

## Naming-Convention

Vor Start der Migration einmal festzurren:

```
Tool-Name:      <space>.<verb>[.<modifier>]
                z.B. bubble.create, idea.format.table, desktop.click

Server-Name:    mcp-<space>
                z.B. mcp-bubbles, mcp-coding

Port-Bereich:   5200-5299 fuer Space-MCPs
                5300-5399 fuer Core-MCPs

Event-Type = Tool-Name
                Brain sendet event_type="bubble.create"
                Space-MCP hat Tool bubble.create -> direkt ausfuehrbar
```

## Brain-Capability-Introspection

Nach der Migration laeuft Routing so:

```
1. Brain startet
   -> Fragt alle registrierten MCPs: POST /tools/list
   -> Baut Capability-Map: {event_type: [capable_spaces]}
   -> Aktualisiert alle 60s (oder via Hot-Reload-Signal)

2. Task kommt rein
   -> Hebbian-Matrix: Top-K Kandidaten mit Confidence
   -> Filter: nur Kandidaten die event_type unterstuetzen (Capability-Map)
   -> Route auf ersten verfuegbaren
   -> Wenn keiner passt -> ehrlicher 404

3. Reward-Feedback
   -> Brain lernt nur aus ausgefuehrten Routen
   -> Matrix wird ehrlich (kein Fallback-Bias mehr)
```

## Stolpersteine

1. **State-Shared Tools** — `memory_store` wird von mehreren Spaces genutzt → bleibt in `mcp-memory`, NICHT in Space-MCP migrieren.
2. **Cross-Space-Referenzen** — z.B. `idea.to_project` ruft aktuell Tools in Coding-Space. Loesung: Orchestration ueber Brain, nicht Tool-to-Tool.
3. **Hand-gebaute Agents** — `brain-coder`, `rowboat-knowledge`, `brain-fallback` sind in `sync_openfang_agents.py:101` geschuetzt. Bei Migration manuell anfassen.
4. **Prozess-Proliferation** — 14 Space-MCPs + 6 Core-MCPs = 20 Prozesse. Loesung: Multi-Tenant Python-Launcher ODER Docker-Compose pro Space.
5. **Backwards-Compat waehrend der Migration** — Space-Agent-Registry bleibt waehrend der Uebergangsphase, schrumpft mit jedem migrierten Space.

## Ziel-Zustand der Konfigurations-Dateien

**Nachher (`config/space_agent_registry.yml`):**
```yaml
version: 2
spaces:
  bubbles:   { mcp_url: "http://localhost:5201" }
  ideas:     { mcp_url: "http://localhost:5202" }
  coding:    { mcp_url: "http://localhost:5214" }
  # ...
core_mcps:
  memory:    { mcp_url: "http://localhost:5301" }
  time:      { mcp_url: "http://localhost:5302" }
  # ...
```

Das war's. Aus 272 Zeilen werden ~30.

**Nachher (`agent.toml`):**
```toml
name = "brain-bubbles"
own_space = "bubbles"
core_servers = ["memory", "time"]
```

Keine `events:`-Sektion mehr, kein haendisches Tool-Whitelisting.

## Success-Kriterien

Migration ist **fertig** wenn:

- [ ] Alle 14 Spaces haben einen dedizierten MCP-Server
- [ ] `config/space_agent_registry.yml` schrumpft auf <50 Zeilen (nur noch MCP-URLs)
- [ ] `scripts/sync_openfang_agents.py` ist deleted
- [ ] `bridge/src/bridge/space_agent_mapper.py` ist deleted
- [ ] `brain-fallback` Agent ist deleted
- [ ] Bridge-Fallback-Logik in `router.py:42-47` ist deleted
- [ ] Brain hat `/api/cortex/capabilities` Endpoint
- [ ] Parity-Tests fuer alle Spaces gruen
- [ ] End-to-End-Test: User-Task -> Bridge -> Brain -> Space-MCP -> Response

## Verweise im Code

Die folgenden Dateien tragen eine **Migrations-Anekdote** als Kommentar-Header, die auf dieses Dokument verweist:

- `config/space_agent_registry.yml`
- `scripts/sync_openfang_agents.py`
- `bridge/src/bridge/router.py`
- `bridge/src/bridge/space_agent_mapper.py`

Alle tragen ein **`TODO(space-mcp-migration)`**-Tag. `grep -rn "space-mcp-migration"` findet alle Stellen, die nach der Migration verschwinden.

## Offene Fragen

- [ ] Multi-Tenant-Launcher oder Docker-Compose pro Space? (Betriebsaufwand vs. Isolation)
- [ ] Port-Vergabe statisch oder dynamisch (Service-Registry)?
- [ ] Authentifizierung zwischen Brain und Space-MCPs (shared secret? mTLS? nur lokal?)
- [ ] Logging: pro-MCP Logfiles oder zentraler Aggregator?
- [ ] Metriken: wie publishen MCPs ihre Last an Brain fuer Capacity-aware Routing?
