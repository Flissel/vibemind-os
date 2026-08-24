# Space-Agent Routing Architecture

## Overview

VoiceDialog (the "Brain") acts as a standalone intent router that forwards
space events to **OpenFang**, which executes them via dedicated per-space
backend agents. Each of the 14 domain spaces has **one main route** pointing
to a backend agent that owns all tasks within that space. Agents follow the
PoC pattern from `security/` and support multi-agent execution when a task
requires it.

## Call Flow

```
┌──────────────────────────────────┐
│  VoiceDialog (Brain)             │
│  Electron + Python Backend       │
│                                  │
│  1. User input (voice/text/UI)   │
│  2. Intent classification        │
│  3. Space resolution             │
│  4. Event dispatch ──────────────┼──► POST /spaces/{space_id}/execute
└──────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────┐
│  OpenFang (Execution Layer)      │
│  Rust · 27 LLMs · 53 Tools      │
│                                  │
│  /spaces/{space_id}/execute      │
│    → resolve agent for space_id  │
│    → execute event payload       │
│    → return result to Brain      │
│                                  │
│  Consistent API contract for     │
│  ALL spaces — same envelope,     │
│  same auth, same error model     │
└──────────┬───────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│  Space Backend Agents            │
│  AutoGen / PoC-style (Python)    │
│                                  │
│  One agent per space             │
│  Can spawn sub-agents for        │
│  multi-step execution            │
│  Uses vibemind-shared for LLMs   │
└──────────────────────────────────┘
```

## The 14 Space Routes

| # | Space ID | Route | Backend Agent | Description |
|---|----------|-------|---------------|-------------|
| 1 | `coding` | `/spaces/coding/execute` | `CodingAgent` | Code generation, review, refactoring (delegates to coding-engine/) |
| 2 | `security` | `/spaces/security/execute` | `SecurityAgent` | Red/blue team, vulnerability scanning, forensics (delegates to security/) |
| 3 | `search` | `/spaces/search/execute` | `SearchAgent` | Semantic search, knowledge retrieval (delegates to la-fungus-search/) |
| 4 | `email` | `/spaces/email/execute` | `EmailAgent` | Email campaigns, templates, SMTP (delegates to ops/) |
| 5 | `pitch` | `/spaces/pitch/execute` | `PitchAgent` | Pitch deck generation, investor outreach (delegates to ops/) |
| 6 | `voice` | `/spaces/voice/execute` | `VoiceAgent` | Voice processing, TTS/STT, 3D UI control |
| 7 | `social` | `/spaces/social/execute` | `SocialAgent` | X/Twitter, social media (delegates to x-pathfinder/) |
| 8 | `chat` | `/spaces/chat/execute` | `ChatAgent` | Multi-channel messaging (delegates to openclaw/) |
| 9 | `ops` | `/spaces/ops/execute` | `OpsAgent` | System monitoring, PC health, DevOps tasks |
| 10 | `research` | `/spaces/research/execute` | `ResearchAgent` | Deep research, web browsing, summarization |
| 11 | `data` | `/spaces/data/execute` | `DataAgent` | Data analysis, visualization, ETL pipelines |
| 12 | `creative` | `/spaces/creative/execute` | `CreativeAgent` | Image generation, creative writing, design |
| 13 | `planning` | `/spaces/planning/execute` | `PlanningAgent` | Project planning, task management, scheduling |
| 14 | `enterprise` | `/spaces/enterprise/execute` | `EnterpriseAgent` | Langdock API, enterprise integrations (delegates to langdock-mcp/) |

## API Contract

Every space route uses the same request/response envelope for consistent calls.

### Request — `POST /spaces/{space_id}/execute`

```json
{
  "event_id": "uuid-v4",
  "space_id": "coding",
  "intent": "generate_function",
  "payload": {
    "description": "Create a Python function that ...",
    "context": { ... }
  },
  "session_id": "brain-session-uuid",
  "user_id": "user-uuid",
  "priority": "normal"
}
```

### Response

```json
{
  "event_id": "uuid-v4",
  "space_id": "coding",
  "status": "completed",
  "result": {
    "type": "code",
    "content": "def my_function(): ...",
    "metadata": { ... }
  },
  "agent_trace": [
    { "agent": "CodingAgent", "action": "generate", "duration_ms": 1200 },
    { "agent": "CodingAgent.ReviewSubAgent", "action": "review", "duration_ms": 800 }
  ],
  "error": null
}
```

### Error Response

```json
{
  "event_id": "uuid-v4",
  "space_id": "coding",
  "status": "failed",
  "result": null,
  "agent_trace": [...],
  "error": {
    "code": "AGENT_TIMEOUT",
    "message": "CodingAgent did not respond within 30s"
  }
}
```

## Agent Structure (PoC Pattern)

Each space agent follows the same base pattern used in `security/` PoC agents,
with multi-agent execution capability.

```python
# Example: spaces/coding/agent.py

from vibemind_shared import create_llm_client
from autogen import AssistantAgent, UserProxyAgent

class CodingAgent:
    """Backend agent for the Coding space.
    
    Receives events from OpenFang, executes coding tasks,
    and optionally spawns sub-agents for multi-step work.
    """

    SPACE_ID = "coding"

    def __init__(self, config: dict):
        self.llm = create_llm_client(config["llm_provider"])
        self.sub_agents = self._init_sub_agents(config)

    def execute(self, event: dict) -> dict:
        """Main entry point — called by OpenFang route handler."""
        intent = event["intent"]
        handler = getattr(self, f"handle_{intent}", self.handle_default)
        return handler(event)

    def handle_generate_function(self, event: dict) -> dict:
        """Single-agent execution."""
        result = self.llm.complete(event["payload"]["description"])
        return {"type": "code", "content": result}

    def handle_full_project(self, event: dict) -> dict:
        """Multi-agent execution — spawns sub-agents."""
        architect = self.sub_agents["architect"]
        coder = self.sub_agents["coder"]
        reviewer = self.sub_agents["reviewer"]
        # AutoGen orchestration for complex tasks
        ...

    def handle_default(self, event: dict) -> dict:
        return self.llm.complete(event["payload"])

    def _init_sub_agents(self, config):
        return {
            "architect": AssistantAgent("architect", ...),
            "coder": AssistantAgent("coder", ...),
            "reviewer": AssistantAgent("reviewer", ...),
        }
```

## OpenFang Integration

OpenFang acts as the execution gateway. It needs a **space router** that:

1. Receives the event from VoiceDialog (Brain)
2. Looks up the registered agent for `space_id`
3. Calls `agent.execute(event)`
4. Returns the standardized response

```
openfang/
└── src/
    └── spaces/
        ├── mod.rs              # Space router — dispatches to agents
        ├── registry.rs         # Agent registry (space_id → agent endpoint)
        └── bridge.rs           # Python↔Rust bridge (PyO3 or HTTP to agent process)
```

Since agents are Python (AutoGen/PoC-style) and OpenFang is Rust, the bridge
layer connects them via one of:

- **HTTP** — each agent runs as a sidecar microservice, OpenFang calls it
- **PyO3** — embed Python agents directly in the Rust runtime
- **WASM** — agent logic compiled to WASM and run in OpenFang's sandbox

**Recommended: HTTP sidecar** for simplicity and independence. Each agent
process can scale, restart, and deploy independently.

## VoiceDialog (Brain) Integration

The Brain needs a thin dispatch layer that:

1. Classifies user intent → determines `space_id`
2. Builds the event envelope
3. Sends `POST /spaces/{space_id}/execute` to OpenFang
4. Receives result and updates the Electron 3D UI

```
voice/python/
└── brain/
    ├── intent_router.py        # Maps user input → space_id + intent
    ├── event_builder.py        # Constructs standardized event envelope
    ├── openfang_client.py      # HTTP client to OpenFang execution layer
    └── result_handler.py       # Processes agent results → UI updates
```

## Submodule Delegation Map

Agents delegate heavy work to existing submodules:

```
CodingAgent      → coding-engine/    (DaveFelix 10-agent system)
SecurityAgent    → security/         (30+ PoCs, red/blue team)
SearchAgent      → la-fungus-search/ (Qdrant + embeddings)
EmailAgent       → ops/              (SMTP pipeline)
PitchAgent       → ops/              (pitch deck generation)
SocialAgent      → x-pathfinder/     (evolutionary X discovery)
ChatAgent        → openclaw/         (40+ messaging channels)
EnterpriseAgent  → langdock-mcp/     (35 MCP tools)
VoiceAgent       → voice/            (internal — TTS/STT/3D)
OpsAgent         → ops/              (PC monitoring MCPs)
ResearchAgent    → shared/           (LLM client direct)
DataAgent        → shared/           (LLM client direct)
CreativeAgent    → shared/           (LLM client direct)
PlanningAgent    → shared/           (LLM client direct)
```

## Next Steps

1. **In VoiceDialog repo**: Implement `brain/` dispatch layer (intent_router, openfang_client)
2. **In OpenFang repo**: Add space router + agent registry + HTTP bridge
3. **New repo or in voice/**: Create agent implementations for all 14 spaces
4. **Define intent taxonomy**: Map all possible user intents → space_id + intent pairs
5. **Shared event schema**: Publish event envelope as JSON Schema in vibemind-shared
