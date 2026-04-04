# Space-by-Space Update Review

What each space needs to become a maintainable OpenFang-routed agent.

## Shared Foundation (applies to ALL 14 spaces)

Every space agent needs these same 4 things. Build them once, reuse everywhere.

| Component | What | Where |
|-----------|------|-------|
| `BaseSpaceAgent` | Abstract base class with `execute()`, intent dispatch, error handling, logging | `vibemind-shared` or new `vibemind-agents` package |
| `SpaceConfig` | Standardized config loader (LLM provider, timeouts, sub-agent definitions) | Same shared package |
| `EventEnvelope` | Pydantic model for request/response — validated at OpenFang boundary | Same shared package |
| `AgentHealthCheck` | `/spaces/{space_id}/health` endpoint — agent alive + dependencies reachable | Built into BaseSpaceAgent |

With this base, each space agent is ~50-100 lines of space-specific logic on top
of the shared foundation. **That's what makes it easy to maintain.**

---

## Per-Space Update Review

### Complexity Tiers

- **Tier 1 — Wrapper** : Agent mostly wraps an existing submodule. Low effort.
- **Tier 2 — Glue** : Agent needs adapter logic between OpenFang and submodule APIs. Medium effort.
- **Tier 3 — New Logic** : Agent has no dedicated submodule — needs its own implementation. Higher effort.

---

### 1. `coding` — CodingAgent

**Tier 2 (Glue)** | Delegates to: `coding-engine/`

| Update needed | Details |
|---------------|---------|
| Intent mapping | Map intents (`generate`, `review`, `refactor`, `explain`, `debug`) to coding-engine's 10-agent system |
| Bridge adapter | coding-engine uses AutoGen internally — agent needs to translate event envelope → AutoGen task → result |
| Output normalization | coding-engine returns multi-agent conversation — extract final code + metadata into response envelope |
| Config | Which LLM per sub-agent, max tokens, language preferences |

**Files to create/update:**
- `spaces/coding/agent.py` — CodingAgent class
- `spaces/coding/intents.py` — intent→handler map
- `coding-engine/` — expose a callable entry point (if not already present)

---

### 2. `security` — SecurityAgent

**Tier 1 (Wrapper)** | Delegates to: `security/`

| Update needed | Details |
|---------------|---------|
| PoC registry | Map intents to the 30+ existing PoCs — they're already standalone scripts |
| Sandboxing | Security PoCs run untrusted patterns — ensure OpenFang WASM sandbox or container isolation |
| Result parsing | PoCs output varies (logs, scan results, reports) — normalize to envelope |
| Auth gate | Add authorization check — only permitted users can trigger offensive tools |

**Files to create/update:**
- `spaces/security/agent.py` — SecurityAgent class
- `spaces/security/poc_registry.py` — intent→PoC script map
- `security/` — no changes needed if PoCs are already callable

**Easiest space to implement** — PoCs already follow the pattern we want.

---

### 3. `search` — SearchAgent

**Tier 1 (Wrapper)** | Delegates to: `la-fungus-search/`

| Update needed | Details |
|---------------|---------|
| Query adapter | Translate intent payload → Qdrant query (vector search, filters, top-k) |
| Embedding bridge | Ensure embedding model matches what's indexed in Qdrant |
| Result format | Return search hits with scores, snippets, source references |
| Index health | Health check should verify Qdrant connection + index exists |

**Files to create/update:**
- `spaces/search/agent.py` — SearchAgent class
- `la-fungus-search/` — expose search function if only CLI exists today

---

### 4. `email` — EmailAgent

**Tier 2 (Glue)** | Delegates to: `ops/`

| Update needed | Details |
|---------------|---------|
| Intent split | Separate intents: `draft`, `send`, `template`, `campaign`, `list_sent` |
| SMTP config bridge | Agent loads SMTP credentials from env, passes to ops/ pipeline |
| Template engine | If ops/ has templates, map intent payload → template variables |
| Safety gate | Confirm before sending — return draft for approval, require explicit `send` intent |
| Rate limiting | Prevent accidental mass sends |

**Files to create/update:**
- `spaces/email/agent.py` — EmailAgent class
- `spaces/email/intents.py` — intent handlers
- `ops/` — expose email pipeline as importable function (not just script)

---

### 5. `pitch` — PitchAgent

**Tier 2 (Glue)** | Delegates to: `ops/`

| Update needed | Details |
|---------------|---------|
| Intent mapping | `generate_deck`, `refine_slide`, `export_pdf`, `investor_research` |
| LLM orchestration | Pitch generation likely needs multi-step: research → outline → slides → polish |
| Output format | Return structured slide data (title, bullets, notes per slide) + optional PDF |
| Template support | Map industry/stage to pitch templates |

**Files to create/update:**
- `spaces/pitch/agent.py` — PitchAgent class
- `ops/` — expose pitch deck generator as callable

---

### 6. `voice` — VoiceAgent

**Tier 2 (Glue)** | Delegates to: `voice/` (internal)

| Update needed | Details |
|---------------|---------|
| TTS/STT bridge | Route speech-to-text and text-to-speech through OpenFang for consistency |
| 3D UI commands | Intents like `navigate_space`, `toggle_view`, `zoom`, `rotate` → Electron IPC |
| Audio pipeline | Handle audio stream setup, codec negotiation, silence detection |
| Self-referential | This agent lives in voice/ but is called via OpenFang — avoid circular deps |

**Special consideration:** This is the only space where the agent and the Brain
are in the same repo. Keep the agent isolated in `voice/python/spaces/voice/`.

**Files to create/update:**
- `voice/python/spaces/voice/agent.py` — VoiceAgent class
- `voice/python/spaces/voice/tts.py` — TTS wrapper
- `voice/python/spaces/voice/stt.py` — STT wrapper

---

### 7. `social` — SocialAgent

**Tier 1 (Wrapper)** | Delegates to: `x-pathfinder/`

| Update needed | Details |
|---------------|---------|
| Intent mapping | `discover_backers`, `score_profiles`, `trending_topics`, `post_analysis` |
| API credentials | X/Twitter API keys from env → pass to x-pathfinder |
| Rate limit awareness | X API has strict rate limits — agent must queue and retry |
| Result format | Return scored profiles, trends as structured data |

**Files to create/update:**
- `spaces/social/agent.py` — SocialAgent class
- `x-pathfinder/` — expose discovery functions as importable (not just CLI)

---

### 8. `chat` — ChatAgent

**Tier 2 (Glue)** | Delegates to: `openclaw/`

| Update needed | Details |
|---------------|---------|
| Channel router | Intent must specify target channel (Telegram, WhatsApp, Slack, etc.) |
| Message adapter | Normalize message format across 40+ channels |
| Bidirectional | Agent needs to both send and receive — webhook registration for incoming |
| Session state | Track conversation context per channel/user |
| Credential management | Each channel has its own auth — agent manages credential lookup |

**Most complex Tier 2** — 40+ channels means many edge cases.

**Files to create/update:**
- `spaces/chat/agent.py` — ChatAgent class
- `spaces/chat/channel_adapter.py` — per-channel message normalization
- `openclaw/` — expose send/receive API (not just bot runtime)

---

### 9. `ops` — OpsAgent

**Tier 2 (Glue)** | Delegates to: `ops/`

| Update needed | Details |
|---------------|---------|
| Intent mapping | `system_status`, `monitor_pc`, `deploy`, `health_check`, `restart_service` |
| MCP tool bridge | ops/ has PC monitoring MCPs — expose them as intents |
| Permission model | Ops actions affect real systems — require elevated authorization |
| Status dashboard | Return structured system metrics for 3D UI rendering |

**Files to create/update:**
- `spaces/ops/agent.py` — OpsAgent class
- `ops/` — expose MCP tools as callable functions

---

### 10. `research` — ResearchAgent

**Tier 3 (New Logic)** | Delegates to: `shared/` (LLM direct)

| Update needed | Details |
|---------------|---------|
| Web browsing | Needs web search + page fetching capability — add as OpenFang tool |
| Multi-step research | Query → search → read sources → synthesize → cite |
| Source tracking | Return results with citations and confidence scores |
| Context window management | Long research needs chunking/summarization strategy |

**No existing submodule** — this is new logic built on vibemind-shared.

**Files to create/update:**
- `spaces/research/agent.py` — ResearchAgent class
- `spaces/research/web_tools.py` — search + fetch wrappers
- `spaces/research/synthesizer.py` — multi-source synthesis

---

### 11. `data` — DataAgent

**Tier 3 (New Logic)** | Delegates to: `shared/` (LLM direct)

| Update needed | Details |
|---------------|---------|
| Data connectors | CSV, JSON, SQL, API endpoints — need pluggable data source layer |
| Analysis pipeline | Load → clean → analyze → visualize |
| Code generation | Generate pandas/matplotlib code, execute in sandbox, return results |
| Visualization output | Return chart data (or base64 images) in response envelope |

**Files to create/update:**
- `spaces/data/agent.py` — DataAgent class
- `spaces/data/connectors.py` — pluggable data source adapters
- `spaces/data/sandbox.py` — safe code execution for generated analysis

---

### 12. `creative` — CreativeAgent

**Tier 3 (New Logic)** | Delegates to: `shared/` (LLM direct)

| Update needed | Details |
|---------------|---------|
| Image generation | Bridge to DALL-E / Stable Diffusion via LLM provider APIs |
| Writing modes | `story`, `poem`, `script`, `blog`, `social_post` — each needs style tuning |
| Iteration support | `refine` intent takes previous output + feedback → improved version |
| Multi-modal output | Text + images in same response |

**Files to create/update:**
- `spaces/creative/agent.py` — CreativeAgent class
- `spaces/creative/image_gen.py` — image generation bridge
- `spaces/creative/writing_styles.py` — per-mode system prompts

---

### 13. `planning` — PlanningAgent

**Tier 3 (New Logic)** | Delegates to: `shared/` (LLM direct)

| Update needed | Details |
|---------------|---------|
| Task model | Structured task objects (title, status, assignee, deadline, dependencies) |
| Project templates | Predefined project structures (sprint, roadmap, launch plan) |
| Timeline generation | Auto-estimate durations, detect conflicts, suggest schedules |
| Persistence | Tasks need storage — integrate with a simple DB or file-based store |
| Export | Markdown, JSON, or calendar format (ICS) |

**Files to create/update:**
- `spaces/planning/agent.py` — PlanningAgent class
- `spaces/planning/task_model.py` — task/project data structures
- `spaces/planning/templates.py` — project templates

---

### 14. `enterprise` — EnterpriseAgent

**Tier 1 (Wrapper)** | Delegates to: `langdock-mcp/`

| Update needed | Details |
|---------------|---------|
| MCP tool mapping | Map intents → langdock-mcp's 35 MCP tools |
| Auth forwarding | Enterprise API keys from Brain session → agent → langdock |
| Multi-agent team | langdock-mcp already has AutoGen multi-agent — reuse directly |
| Result normalization | MCP tool outputs → standard response envelope |

**Straightforward** — langdock-mcp already does the heavy lifting.

**Files to create/update:**
- `spaces/enterprise/agent.py` — EnterpriseAgent class
- `langdock-mcp/` — ensure MCP tools are callable (likely already are via FastMCP)

---

## Implementation Priority

Recommended order based on effort vs. value:

| Priority | Space | Tier | Why |
|----------|-------|------|-----|
| 1 | `security` | Wrapper | PoCs already match the pattern — prove the architecture works |
| 2 | `enterprise` | Wrapper | langdock-mcp already has AutoGen — fast integration |
| 3 | `search` | Wrapper | Qdrant is well-defined — clear input/output |
| 4 | `social` | Wrapper | x-pathfinder is standalone — easy wrap |
| 5 | `coding` | Glue | High value — coding-engine has 10 agents ready |
| 6 | `email` | Glue | ops/ pipeline exists — needs safety gates |
| 7 | `pitch` | Glue | Shares ops/ — do alongside email |
| 8 | `ops` | Glue | MCP tools exist — needs permission model |
| 9 | `voice` | Glue | Special (self-referential) — needs care |
| 10 | `chat` | Glue | 40+ channels = most complex glue |
| 11 | `research` | New | High value but needs web tools built |
| 12 | `planning` | New | Needs persistence layer |
| 13 | `data` | New | Needs sandbox + connectors |
| 14 | `creative` | New | Needs image gen integration |

## Maintainability Checklist

For each space to be "easy to maintain":

- [ ] Inherits from `BaseSpaceAgent` — no duplicated boilerplate
- [ ] Intent handlers are isolated functions — add/remove intents without touching core
- [ ] Config is external (env vars / YAML) — no hardcoded values in agent code
- [ ] Health check works — OpenFang can monitor all 14 agents
- [ ] Logging follows shared format — one log aggregation for all spaces
- [ ] Tests use shared fixtures — `FakeEvent`, `MockLLM`, `MockOpenFangClient`
- [ ] Agent is stateless — all state lives in session/DB, agent can restart cleanly
- [ ] Submodule changes don't break agent — use stable interfaces, not internals
