# Portion 9 — Frontends & Dashboards

> **Teil 9 von 10** der VibeMind-OS-Systemdokumentation.
> Querschnitt über alle User-facing Interfaces.

---

## TL;DR

VibeMind-OS hat **6 separate Frontend-Anwendungen** mit unterschiedlichen
Tech-Stacks (React 18–19, Next.js 14–16, Electron 28, Vite, Three.js).
Dazu kommen **9 Brain-Dashboards** (reines HTML+JS mit SSE/WebSocket).
Insgesamt ~1.453 TypeScript/JavaScript-Dateien über alle Frontends.

---

## 1. Frontend-Matrix

| App                        | Framework              | React | Port  | Zweck                           |
|----------------------------|------------------------|-------|-------|----------------------------------|
| TRAE (Desktop Automation)  | Vite + React           | 18.3  | 5173  | Desktop-Streaming, OCR, Workflows|
| Coding Engine Dashboard    | Electron + Vite        | 18.2  | —     | Generation-Monitor, VNC-Preview  |
| Coding Engine Web-App      | Vite + React           | 18.2  | 3003  | Monaco-Editor, Projekt-Preview   |
| Minibook                   | Next.js 16             | 19.2  | 3457  | Swarm-Graph, Projekt-Editor      |
| Rowboat (Main)             | Next.js 14             | 18.3  | 3000  | Multi-Workspace Chat-SaaS        |
| Rowboat (Chat-Widget)      | Next.js                | 18.3  | —     | Embeddable Chat-Widget           |
| Brain Dashboards (9×)      | Vanilla HTML + JS      | —     | 5000  | SVG-Ringe, SSE-Streams, Chat     |

---

## 2. TRAE Frontend (Desktop Automation)

**Verzeichnis:** `spaces/desktop/Automation_ui/src/` (165 TS-Dateien).
**Stack:** Vite + React 18.3 + TypeScript + Zustand + Tailwind + shadcn/ui.

**Haupt-Bereiche:**
- **Live Desktop** — WebSocket-basiertes Multi-Monitor-Streaming, OCR-Region-
  Designer, Dual-Canvas
- **Workflow Builder** — `WorkflowCanvas.tsx`, Node-basierter visueller Editor
- **Clawdbot** — Skill-Marketplace, installierte Skills, Status-Monitor
- **Moire** — MoireOrchestrator v2 für Pattern-Handling beim Streaming

**Besonderheiten:**
- Auto-Reconnection Hooks für WebSocket
- Echtzeit-Frame-Streaming (~15 FPS)
- Dual-Monitor-Support

---

## 3. Coding Engine Dashboard (Electron)

**Verzeichnis:** `spaces/coding/Coding_engine/dashboard-app/`.
**Stack:** Electron 28.3 + Vite 5 + React 18.2 + Zustand + Tailwind CSS.

**Haupt-Komponenten:**

| Komponente          | Datei                           | Zweck                          |
|---------------------|---------------------------------|--------------------------------|
| Live Preview        | `VNCViewer.tsx`                 | VNC-Iframe (Port 6080)         |
| Generation Monitor  | `GenerationMonitor.tsx`         | Fortschritts-Tracking          |
| Review Chat         | `ReviewChat.tsx`                | Pause/Resume + User-Feedback   |
| Task Board          | `TaskBoardModal.tsx`            | Kanban-artige Task-Übersicht   |
| Project Space       | `ProjectSpace.tsx`              | Projekt-Workspace              |
| Clarification Panel | `ClarificationPanel.tsx`        | Ambiguity-Auflösung            |
| Cell Publication    | `PublishCellModal.tsx`          | Microservice-Publishing        |

**Main Process (Electron):**
- IPC-Handler für Docker-Management, Port-Allocation, VNC-Connection
- Service-Manager für Backend-Lifecycle
- Build: electron-vite, electron-builder (NSIS/DMG/AppImage)

**State Management:** Zustand mit separaten API-Clients (vibeAPI, visionAPI,
debugAPI, clarificationAPI, portalAPI).

---

## 4. Coding Engine Web-App

**Verzeichnis:** `spaces/coding/Coding_engine/web-app/` (75 TS-Dateien).
**Stack:** Vite + React 18.2.
**Port:** 3003.

Leichtere Alternative zum Electron-Dashboard — Browser-basiert, Monaco-Editor
für Code-Ansicht, Projekt-Preview.

---

## 5. Minibook Frontend

**Verzeichnis:** `spaces/autogen/farm/minibook/frontend/`.
**Stack:** Next.js 16.1.6 + React 19.2.3 + Radix UI + Tailwind.
**Port:** 3457.

**Besonderheiten:**
- **Dagre + XYFlow** für Graph-Visualisierung (Agent-Pipeline als DAG)
- React Markdown für Dokumenten-Rendering
- Radix UI Primitives (Avatar, Dialog, Dropdown, Scroll Area, Tabs)
- Neueste React-Version im gesamten Monorepo (19.2.3)

---

## 6. Rowboat

**Verzeichnis:** `spaces/rowboat/rowboat/`.
**Stack:** Next.js 14.2 + React 18.3.
**Port:** 3000.

**Zwei Apps:**
1. **Main App** (`apps/rowboat/`) — Multi-Workspace Chat-SaaS mit Agent-
   Integration, Qdrant-RAG, MongoDB-Backend
2. **Chat Widget** (`packages/chat_widget/`) — Embeddable Widget für externe
   Websites

**Features:**
- Agent-CLI für Terminal-basierte Interaktion
- Voice-Integration (core/src/voice)
- Multi-Workspace mit Projekt-Isolation

---

## 7. Brain Dashboards (9 HTML-Views)

**Verzeichnis:** `brain/the_brain/web/templates/` (9.881 Zeilen gesamt).
**Stack:** Vanilla HTML + JavaScript, kein Build-Tool.
**Port:** 5000 (vom Brain-Server geserved).

| Dashboard                              | Zeilen | Inhalt                              |
|----------------------------------------|-------:|-------------------------------------|
| `brain_dashboard.html`                 | 2.179  | Unified-Hauptansicht                |
| `moltbook_dashboard.html`              | 1.639  | Knowledge-Graph, Retrieval-Ranking  |
| `unified_brain_dashboard.html`         | 1.172  | **SVG-Ringe + Bridges**             |
| `klotski_dashboard.html`               |   993  | CTM-Puzzle-Visualisierung           |
| `klotski_3d_rings.html`                |   852  | 3D-Ring-View                        |
| `oscillator_dashboard.html`            |   815  | Temporal-Oszillator                 |
| `evolutionary_training_dashboard.html` |   670  | CTM-Training, Fitness               |
| `autonomous_swarm.html`                |   459  | 14 Micro-Agents                     |
| `cognitive_loop_viz.html`              |   412  | 9-Phasen-Loop                       |

**Alle nutzen SSE (2 Hz) oder WebSocket** — kein Polling.

---

## 8. Tech-Stack-Vergleich

| Aspekt               | TRAE          | CE Dashboard  | CE Web        | Minibook      | Rowboat       | Brain         |
|----------------------|---------------|---------------|---------------|---------------|---------------|---------------|
| Framework            | Vite          | Electron+Vite | Vite          | Next.js 16    | Next.js 14    | Vanilla       |
| React                | 18.3          | 18.2          | 18.2          | 19.2          | 18.3          | —             |
| State                | Zustand       | Zustand       | —             | —             | —             | —             |
| Styling              | Tailwind+shad | Tailwind      | —             | Tailwind+Radix| —             | Inline        |
| Realtime             | WebSocket     | WebSocket     | —             | —             | —             | SSE+WS        |
| Build Target         | Browser       | Desktop       | Browser       | Browser       | Browser       | Server-Rendered|

---

## 9. Zusammenfassung

| Kennzahl                        | Wert           |
|---------------------------------|----------------|
| Separate Frontend-Apps          | 6              |
| Brain-Dashboards                | 9              |
| Gesamt TypeScript/JS-Dateien    | ~1.453         |
| React-Versionen im Einsatz     | 18.2 – 19.2    |
| Electron-Apps                   | 1              |
| Next.js-Apps                    | 3              |
| Vite-Apps                       | 3              |
| SSE/WebSocket-Streams           | 5+             |

**Nächste Portion:** *Portion 10 — Domain Spaces, DevOps & System-PoCs.*
