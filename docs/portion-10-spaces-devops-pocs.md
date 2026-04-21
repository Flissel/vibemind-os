# Portion 10 — Domain Spaces, DevOps & System-PoCs

> **Teil 10 von 10** der VibeMind-OS-Systemdokumentation.
> Dependencies: Portion 1 (SpaceType-Enum), Portion 2 (LLM-Config),
> Portion 3 (Datenbank — flowzen_*, video_*, scheduled_tasks).

---

## TL;DR

VibeMind-OS hat **14 Domain-Spaces**, von denen 5 bereits in Portionen 6–8
tiefgehend dokumentiert sind (coding, desktop, autogen, minibook, rowboat).
Diese Portion deckt die **restlichen 9 Spaces** ab, plus **8 System-PoC
MCP-Server** (2.481 Zeilen), den **Issue-Detector** (1.785 Zeilen),
**DevOps-Tooling** (1.431 Zeilen), das (noch leere) **Security**-Verzeichnis
und den **Business/Pitch-Deck-PoC**.

---

## 1. Domain-Space-Katalog (alle 14)

| Space      | Dateien | Typ          | Status           | Portion |
|------------|--------:|--------------|------------------|---------|
| coding     | 2.769 Py + 324 TS | Core Engine  | ✅ Portion 6    | 6       |
| desktop    | 287 Py + 169 TS   | Automation   | ✅ Portion 7    | 7       |
| autogen    | 71 Py + 41 TS     | Agent-Swarm  | ✅ Portion 8    | 8       |
| minibook   | 20 Py             | Integration  | ✅ Portion 8    | 8       |
| rowboat    | 29 Py + 818 TS    | Chat-SaaS    | ✅ Portion 9    | 9       |
| **ideas**  | 32 Py             | Exploration  | 📄 hier        | 10      |
| **flowzen**| 8 Py              | Well-Being   | 📄 hier        | 10      |
| **mirofish**| 9 Py             | Prediction   | 📄 hier        | 10      |
| **n8n**    | 14 Py             | Workflows    | 📄 hier        | 10      |
| **schedule**| 10 Py            | Kalender     | 📄 hier        | 10      |
| **research**| 5 Py             | Recherche    | 📄 hier        | 10      |
| **video**  | 6 Py              | Video-Gen    | 📄 hier        | 10      |
| **shuttles**| 1 Py             | Design       | 📄 hier        | 10      |
| **brain**  | 1 Py              | Knowledge    | 📄 hier        | 10      |

---

## 2. Ideas Space

**Verzeichnis:** `spaces/ideas/` (32 Python-Dateien).

Der Ideas-Explorer ist das Frontend für die `ideas`-Tabelle (Portion 3). Er
ermöglicht:
- **Bubble-Management** — Ideen als "Bubbles" visualisieren, erstellen,
  verknüpfen, promoten
- **Exploration-Sessions** — Agent-gesteuerte Ideen-Erweiterung (Tiefe/Breite/
  Beam-Strategien, `exploration_sessions` + `exploration_nodes` Tabellen)
- **Semantic Search** — über `search_ideas_by_embedding()` (Portion 3 §9)
- **Canvas-Layout** — 2D-Positionierung via `canvas_nodes` / `canvas_edges`
- **Shuttle-Pipeline** — Ideen zur Evaluation schicken (`shuttles`-Tabelle)

**Bezug zu Portion 3:** Direkter DB-Consumer für 8 Tabellen (ideas, projects,
canvas_*, exploration_*, discovered_edges, shuttles, mermaid_diagrams).

---

## 3. Flowzen Space

**Verzeichnis:** `spaces/flowzen/` (8 Python-Dateien).

Well-Being- und Produktivitäts-Tracking:
- **Check-Ins** — Mood, Energy, Focus erfassen (`flowzen_checkins`)
- **Activity-Tracking** — Typ, Dauer, Intensität (`flowzen_activity`)
- **Tagebuch** — Tägliche Einträge mit Tags (`flowzen_diary`)

**DB-Tabellen:** 3 eigene (Portion 3 §8.1), Realtime für `activity` + `diary`.

---

## 4. MiroFish Space

**Verzeichnis:** `spaces/mirofish/` (9 Python-Dateien).

Offline-Prediction-Engine:
- **Stack:** Docker + Flask + Neo4j + Ollama
- **Zweck:** Graph-basierte Vorhersagen (Trends, Zusammenhänge)
- **Isolation:** Läuft komplett lokal, kein Cloud-API-Bedarf

---

## 5. N8N Space

**Verzeichnis:** `spaces/n8n/` (14 Python-Dateien).

Integration mit der **N8N-Workflow-Plattform**:
- Workflow-Templates erstellen/triggern
- N8N-API-Wrapper
- Bridge zwischen VibeMind-Events und N8N-Webhooks

---

## 6. Schedule Space

**Verzeichnis:** `spaces/schedule/` (10 Python-Dateien).

NLP-basiertes Scheduling:
- Natürliche Sprache → Kalender-Events ("Morgen um 10 Meeting mit Felix")
- Kalender-Integration
- Nutzung der `scheduled_tasks`-Tabelle (Portion 3 §7.2)

---

## 7. Research Space

**Verzeichnis:** `spaces/research/` (5 Python-Dateien).

Recherche-Agent-Tooling — Web-Suche, Quellen-Aggregation, Summary-Generierung.
Minimaler Space, wird primär als Tool-Provider für andere Spaces genutzt.

---

## 8. Video Space

**Verzeichnis:** `spaces/video/` (6 Python-Dateien).

Video-Frame-Processing und Deepfake-Integration.
**DB-Tabellen:** 4 eigene (videos, video_projects, video_project_persons,
video_pipeline_steps — Portion 3 §8.2).

---

## 9. Shuttles & Brain Spaces

**Shuttles** (`spaces/shuttles/`, 1 Datei): Software-Design-Patterns.
Minimaler Space.

**Brain** (`spaces/brain/`, 1 Datei): Knowledge-Base-Integration. Nicht zu
verwechseln mit dem Brain-System (Portion 4) — dieses ist nur ein Space-
Adapter.

---

## 10. System-PoC MCP-Server

**Verzeichnis:** `system/poc_*/` (8 PoCs, 2.481 Zeilen gesamt).

Alle basieren auf **FastMCP** und implementieren System-Management-Tools:

| PoC                    | Zeilen | Zweck                                    |
|------------------------|-------:|------------------------------------------|
| `poc_process_manager`  | 622    | Prozess-Monitoring & -Management          |
| `poc_driver_manager`   | 390    | Treiber-Installation & Lifecycle          |
| `poc_display_audio`    | 292    | Audio/Display-Konfiguration               |
| `poc_env_manager`      | 256    | Environment-Variable-Management           |
| `poc_scheduled_tasks`  | 250    | Scheduled-Task-Management                 |
| `poc_registry`         | 246    | Windows-Registry-Operationen              |
| `poc_power_manager`    | 242    | Power-State-Management                    |
| `poc_update_manager`   | 183    | System-Update-Orchestrierung              |

**Gemeinsames Pattern:**
```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("poc-process-manager")

@mcp.tool()
async def list_processes(filter: str = "") -> dict:
    ...

if __name__ == "__main__":
    mcp.run()
```

**Alle PoCs nutzen** `system/llm_client.py` (Portion 2 §12 — das
Parallelgleis). Nach der `TODO(llm-config-consolidation)` sollen sie auf
`vibemind_shared` umgestellt werden.

---

## 11. Issue-Detector

**Datei:** `issue-detector/mcp_server.py` (1.785 Zeilen).

Self-Healing-Loop:
1. **Scan** — alle 14 Spaces + Security-PoCs + System-PoCs durchleuchten
2. **Findings** → Pending-Queue (JSON-basiertes Dedup-Tracking)
3. **User-Approval** → Findings bestätigen oder ablehnen
4. **GitHub-Issues** → genehmigte Findings als Issues pushen
5. **Claude Code Fixes** → automatische Reparatur

**32 Tools:** Detection (scan_*), Findings (list/approve/reject), GitHub
(push_to_github, full_scan_and_push), Notifications (notify_user, get_inbox).

**Integration:** OpenFang-Webhook-Support, Event-Drops-Directory für externe
Systeme.

---

## 12. DevOps

**Verzeichnis:** `devops/` (1.431 Zeilen gesamt).

### 12.1 Backup-Sync
`devops/poc_backup_sync/mcp_server.py` (251 Zeilen) — Backup-Synchronisierung.

### 12.2 Git-Agents
`devops/poc_git_agents/` (19 Python-Dateien, 1.180 Zeilen) — Git-Workflow-
Agents für automatisierte Commits, Branch-Management, Merge-Handling,
Issue-Tracking-Integration.

---

## 13. Security

**Verzeichnis:** `security/` — **aktuell leer** (Infrastruktur-Verzeichnis).
Geplant für Security-Scanning-Integration.

Der Issue-Detector (§11) übernimmt aktuell teilweise diese Rolle mit seinen
`scan_security`-Tools.

---

## 14. Business

**Verzeichnis:** `business/`.

### Pitch-Deck-PoC
`business/poc_pitch_deck/` — Business-Pitch-Generator (PoC-Stadium).
Eigene `llm_client.py` + `llm_config.yml` (nutzt OpenRouter
`arcee-ai/trinity-large-preview:free` als Override, Portion 2 §8).

---

## 15. Zusammenfassung

| Kennzahl                        | Wert                  |
|---------------------------------|-----------------------|
| Domain Spaces gesamt            | 14                    |
| Davon in früheren Portionen     | 5 (coding, desktop, autogen, minibook, rowboat) |
| Davon hier dokumentiert         | 9                     |
| System-PoC-MCP-Server           | 8 (2.481 Zeilen)      |
| Issue-Detector                  | 1.785 Zeilen, 32 Tools|
| DevOps Python-Dateien           | 20 (1.431 Zeilen)     |
| Security                        | leer (geplant)        |
| Business PoC                    | 1 (Pitch-Deck)        |

---

## 16. Gesamtbild — VibeMind-OS in Zahlen

| Metrik                             | Wert                |
|------------------------------------|---------------------|
| **Python-Dateien**                 | ~4.138              |
| **TypeScript/JS-Dateien**          | ~1.453              |
| **Gesamt-LoC (geschätzt)**        | ~500.000+           |
| **Git-Submodule**                  | 15                  |
| **Domain Spaces**                  | 14                  |
| **MCP-Server (Coding Engine)**     | 29                  |
| **MCP-Server (System PoCs)**       | 8                   |
| **MCP-Server (Desktop)**           | 32 Tools            |
| **MCP-Server (Issue-Detector)**    | 32 Tools            |
| **Autonome Agents (Coding)**       | 37+ (88 Dateien)    |
| **LLM-Rollen (Root Config)**       | 33                  |
| **Datenbank-Tabellen**             | 22                  |
| **Brain Core-Module**              | 268                 |
| **Brain Neuroscience-Module**      | 43                  |
| **Brain Bridges**                  | 10                  |
| **Brain Tests**                    | 1.981+              |
| **Event-Typen (Coding)**           | 510+                |
| **Convergence-Metriken**           | 100+                |
| **Frontend-Apps**                  | 6                   |
| **Brain-Dashboards**               | 9                   |
| **Dokumentations-Portionen**       | 10 (dieses Dokument)|

---

**Dokumentation abgeschlossen.** Alle 10 Portionen sind geschrieben. Die
Dokumentation kann jetzt als Grundlage für weiterführende Ausarbeitung
(z. B. im Jodi_goes_big Repo) oder als Onboarding-Material verwendet werden.
