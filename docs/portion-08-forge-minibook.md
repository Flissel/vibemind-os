# Portion 8 — Forge Agent System & Minibook

> **Teil 8 von 10** der VibeMind-OS-Systemdokumentation.
> Dependencies: Portion 2 (LLM-Config), Portion 6 (Coding Engine — teilt Agent-Patterns).

---

## TL;DR

**Forge** ist ein AutoGen-basierter Multi-Agent-Swarm für die
**Minibook**-Kollaborationsplattform. Die Pipeline
(`SwarmManager → CatalogAgent → ArchitectAgent → CoderAgent →
ReviewerAgent → TesterAgent → ValidatorAgent`) generiert Code-Projekte aus
natürlicher Sprache. Minibook selbst hat zwei Facetten: ein **Backend-Swarm**
(11.856 Zeilen Python) unter `spaces/autogen/farm/minibook/` und ein
**Integration-Space** (`spaces/minibook/`) mit Enrichment-Pipeline und
Rachel-Interface. Das Frontend ist **Next.js 16 + React 19** mit
Graph-Visualisierung (Dagre + XYFlow).

---

## 1. Zwei Verzeichnisse, ein System

```
spaces/autogen/farm/minibook/    ← Backend-Swarm + Frontend
├── swarm/                       ← Pipeline + Agents (11.856 Z. Python)
│   ├── pipeline.py              — Haupt-Orchestrierung
│   ├── forge_agents.py          — Spezialisierte Agents
│   ├── knowledge.py             — Knowledge-Base-Integration
│   ├── company_builder.py       — Multi-Agent-Team-Bau
│   ├── docker_ops.py            — Container-Orchestrierung
│   ├── input_parser.py          — Request-Parsing
│   ├── input_designer.py        — Input-Schema-Design
│   ├── llm.py                   — LLM-Client-Wrapper
│   ├── code_processing.py       — Code-Analyse + Generierung
│   ├── api_client.py            — API-Kommunikation
│   ├── todo_implementer.py      — TODO-Task-Implementierung
│   └── constants.py             — Konfiguration
└── frontend/                    ← Next.js 16 + React 19
    ├── package.json             — next 16.1.6, react 19.2.3
    └── src/                     — Radix UI, Dagre, XYFlow

spaces/minibook/                 ← Integration Space
├── minibook_hub.py              — Zentraler Hub
├── rachel_interface.py          — Rachel-AI-Integration
├── result_aggregator.py         — Ergebnis-Koordination
├── minibook_agent.py            — Integration-Agent
├── enrichment/
│   ├── pipeline.py              — Enrichment-Orchestrierung
│   ├── brain_router.py          — Brain-Space-Routing
│   ├── context_gather.py        — Kontext-Sammlung
│   ├── space_router.py          — Multi-Space-Routing
│   └── task_enricher.py         — Task-Anreicherung
└── tools/                       — Collaboration-Tools
```

---

## 2. Forge-Pipeline (Swarm)

### 2.1 Agent-Sequenz

```
SwarmManager
    │
    ▼
CatalogAgent     → analysiert Requirements, erstellt Katalog
    │
    ▼
ArchitectAgent   → technischer Entwurf, Komponenten-Hierarchie
    │
    ▼
CoderAgent       → generiert Code (parallel, pro Komponente)
    │
    ▼
ReviewerAgent    → Code-Review, Qualitätsprüfung
    │
    ▼
TesterAgent      → Tests generieren + ausführen
    │
    ▼
ValidatorAgent   → Endvalidierung, Integration-Check
```

### 2.2 Knowledge-Base (`knowledge.py`)

Zentrale Wissensbasis, die allen Agents zur Verfügung steht — Patterns,
Best-Practices, vorherige Projekte. Dient als "Gedächtnis" des Swarms.

### 2.3 Docker Operations (`docker_ops.py`)

Container-Orchestrierung für isolierte Build/Test-Umgebungen. Jedes
generierte Projekt bekommt seinen eigenen Container.

### 2.4 Company Builder (`company_builder.py`)

Baut dynamisch ein "Team" aus spezialisierten Agents zusammen, basierend
auf der Komplexität des Projekts. Einfache Projekte → weniger Agents.
Komplexe Projekte → volle Pipeline.

---

## 3. Frontend (Next.js 16 + React 19)

**Port:** 3457.
**Stack:** Next.js 16.1.6, React 19.2.3 (latest), Radix UI, Tailwind.
**Visualisierung:** Dagre + XYFlow für Graph-basierte Workflow-Darstellung.

**Features:**
- Projekt-Erstellung aus natürlicher Sprache
- Live-Fortschritts-Graph (Agent-Zustände als Knoten)
- Dokumenten-Rendering (React Markdown)
- Scroll-Areas mit Radix für lange Outputs

---

## 4. Integration Space (`spaces/minibook/`)

Der Integration-Space verbindet Minibook mit dem Rest von VibeMind-OS:

### 4.1 Enrichment-Pipeline

```
User-Request
    │
    ▼
task_enricher.py    → Task mit Kontext anreichern
    │
    ▼
context_gather.py   → relevanten Kontext aus allen Spaces sammeln
    │
    ▼
brain_router.py     → via Brain routen (Portion 4)
    │
    ▼
space_router.py     → an richtigen Space weiterleiten
    │
    ▼
pipeline.py         → Orchestrierung
```

### 4.2 Rachel-Interface

`rachel_interface.py` — Integration mit dem Rachel-AI-System für
natürlichsprachliche Interaktion mit der Minibook-Plattform.

### 4.3 Result-Aggregator

`result_aggregator.py` — sammelt Ergebnisse aus allen beteiligten Agents
und konsolidiert sie zu einem einheitlichen Output.

---

## 5. Zusammenfassung

| Kennzahl                  | Wert                        |
|---------------------------|-----------------------------| 
| Swarm Python-Code         | 11.856 Zeilen               |
| Swarm-Files               | 12                          |
| Pipeline-Agents           | 7 (Catalog→Validator)       |
| Frontend-Framework        | Next.js 16 + React 19       |
| Frontend-Port             | 3457                        |
| Integration-Space-Files   | 20 Python                   |
| Enrichment-Pipeline-Steps | 5                           |

**Nächste Portion:** *Portion 9 — Frontends & Dashboards.*
