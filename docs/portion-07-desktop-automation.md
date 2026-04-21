# Portion 7 — Desktop Automation (TRAE)

> **Teil 7 von 10** der VibeMind-OS-Systemdokumentation.
> Dependencies: Portion 2 (LLM-Config), Portion 5 (Bridge kann hierher routen).

---

## TL;DR

**TRAE** (Desktop Automation UI) ist eine Plattform auf **Port 8007** (Backend,
FastAPI) + **Port 5173** (Frontend, Vite+React 18), die den Desktop
fernsteuert. Kern-Features: Multi-Monitor-Streaming via WebSocket,
**3 OCR-Engines** (Tesseract, EasyOCR, PaddleOCR), ein **LLM Intent Router**
(2.775 Zeilen) der natürliche Sprache in Automatisierungs-Aktionen übersetzt,
und ein **Node-basierter Workflow-Editor** (click, type, HTTP, conditions,
delays, OCR-Extraction). 13 Backend-Router, 165 Frontend-Komponenten, 32
MCP-Tools.

---

## 1. Architektur

```
┌────────────────────┐   WebSocket    ┌──────────────────────┐
│  React Frontend    │ ◄────────────► │  FastAPI Backend     │
│  Port 5173         │   Frames+OCR   │  Port 8007           │
│  165 TS-Files      │                │  13 Router           │
│  Zustand State     │                │  Services Layer      │
└────────────────────┘                │  PyAutoGUI + OCR     │
                                       │  Redis Pub/Sub       │
                                       │  Supabase DB         │
                                       └──────────────────────┘
```

---

## 2. Backend — 13 Router

**Hauptdatei:** `backend/app/main.py` (232 Zeilen).

| Router               | Zweck                                         |
|----------------------|-----------------------------------------------|
| `llm_intent.py`     | **LLM Intent Router** (2.775 Z.) — NL → Action|
| `automation.py`      | Desktop-Automation-Workflows                  |
| `workflows.py`       | Workflow-Ausführung/Management                |
| `desktop.py`         | Desktop-Control (Maus, Tastatur, Screen)      |
| `ocr.py`             | OCR-Text-Extraktion aus Screen-Regionen       |
| `websocket.py`       | Echtzeit-WebSocket-Streaming                  |
| `health.py`          | Health-Check-Endpoints                        |
| `shell.py`           | Shell-Befehle ausführen                       |
| `eyeterm.py`         | Terminal-Integration (EyeTerm)                |
| `mcp_bridge.py`      | MCP-Server-Bridge (32 Tools)                  |
| `clawdbot.py`        | Clawdbot-Integration (Skill-Marketplace)      |
| `clawhub.py`         | Clawhub-Management                            |
| `snapshots.py`       | Screen-Snapshots + Persistenz                 |

### 2.1 LLM Intent Router (Kern-Feature)

**Datei:** `backend/app/routers/llm_intent.py` (2.775 Zeilen).

Der größte Einzel-Router. Nimmt natürliche Sprache ("Öffne Chrome und geh auf
GitHub") und übersetzt sie via LLM in eine Sequenz von Desktop-Aktionen:

```
User: "Öffne Chrome und geh auf GitHub"
  ↓ LLM Instruction Prompting
  ↓
Action-Plan:
  1. keyboard.hotkey("win", "r")        # Run-Dialog
  2. keyboard.type("chrome")
  3. keyboard.press("enter")
  4. wait(2000)
  5. keyboard.type("https://github.com")
  6. keyboard.press("enter")
```

### 2.2 Services Layer

| Service               | Zweck                                    |
|-----------------------|------------------------------------------|
| `action_router.py`    | Routet Automatisierungs-Aktionen         |
| `manager.py`          | Service-Lifecycle-Management             |
| `redis_pubsub.py`     | Redis Pub/Sub für Task-Queue             |
| `clawdbot_bridge.py`  | Integration mit Clawdbot                 |

---

## 3. OCR-Integration (3 Engines)

| Engine       | Stärke                           | Nutzung                |
|--------------|----------------------------------|------------------------|
| Tesseract    | Schnell, bewährt, Latin-fokus    | Default für EN/DE Text |
| EasyOCR      | Multi-Language, gute Accuracy    | Asiatische Schriften   |
| PaddleOCR    | State-of-Art Accuracy            | Komplexe Layouts       |

**Workflow:**
1. Screen-Region per Maus auswählen (Frontend Canvas-Tool)
2. Screenshot der Region capten
3. An OCR-Engine senden
4. Extrahierter Text → kann als Variable in Workflows genutzt werden

---

## 4. WebSocket Desktop-Streaming

```
Frontend  ←─ WebSocket ─→  Backend
          Frame-Capture:
          1. PyAutoGUI screenshot
          2. Compress (JPEG quality)
          3. Base64 encode
          4. Send via WebSocket
          ~15 FPS bei 1080p
```

**Multi-Monitor:** Unterstützt mehrere Monitore — Frontend zeigt pro Monitor
einen eigenen Canvas, User kann zwischen Monitoren wechseln.

**WebSocket-Manager** (`core/websocket_manager.py`): Auto-Reconnection,
Client-Tracking, Broadcast an alle verbundenen Clients.

---

## 5. Workflow-System (Node-basiert)

Der Frontend-Workflow-Editor (`WorkflowCanvas.tsx`) erlaubt visuelles
Zusammenstecken von Automation-Nodes:

**Node-Typen:**
- `click` — Klick auf x,y-Koordinaten oder OCR-erkanntes Element
- `type` — Text eingeben
- `keyboard` — Tastatur-Shortcuts (Ctrl+C, Alt+Tab, ...)
- `http_request` — API-Call (GET/POST)
- `condition` — If/Else-Branching
- `delay` — Wartezeit
- `ocr_extract` — OCR-Text aus Region lesen
- `loop` — Wiederholung
- `variable` — Variable setzen/lesen

Workflows werden in Supabase persistiert und können per API getriggert werden.

---

## 6. Frontend — React 18 + Vite

**165 TypeScript-Dateien in `src/`.**

**Haupt-Bereiche:**
- **Live Desktop** — Multi-Monitor-Streaming, OCR-Region-Designer, Dual-Canvas
- **Automation** — Workflow-Builder, Node-Manager, Execution-History
- **Clawdbot** — Skill-Marketplace, installierte Skills, Status-Monitor
- **Moire** — MoireOrchestrator v2 für Pattern-Handling bei Streaming

**State-Management:** Zustand (project store).
**Styling:** Tailwind CSS, shadcn/ui Komponenten.

---

## 7. MCP-Tools (32 registriert)

Desktop-Automation, File-Operationen, Screen-Reading, OCR, Event-Queue-
Management — alles über den MCP-Bridge-Router (`mcp_bridge.py`) erreichbar.

---

## 8. Zusammenfassung

| Kennzahl                     | Wert                |
|------------------------------|---------------------|
| Backend-Port                 | 8007                |
| Frontend-Port                | 5173                |
| Backend-Router               | 13                  |
| LLM-Intent-Router            | 2.775 Zeilen        |
| Frontend-Dateien             | 165 TypeScript      |
| OCR-Engines                  | 3                   |
| MCP-Tools                    | 32                  |
| Workflow-Node-Typen          | 9+                  |
| WebSocket-Streaming          | ~15 FPS             |

**Nächste Portion:** *Portion 8 — Forge Agent System & Minibook.*
