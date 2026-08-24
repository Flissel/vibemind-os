# Space Definitions — VibeMind OS

Reference spec for all 14 domain spaces. Each space is a vertical slice of
functionality with its own backend agent, routed through OpenFang.

**Companion docs:**
- [Space-Agent Routing](space-agent-routing.md) — call flow, API contract, agent base pattern
- [Space Update Review](space-update-review.md) — per-space update tiers, files, priority

---

## Quick Reference

| # | Space ID | Name | Tier | Delegates To | One-liner |
|---|----------|------|------|-------------|-----------|
| 1 | `coding` | Coding | 2-Glue | `coding-engine/` | Code generation, review, refactoring via 10-agent AutoGen system |
| 2 | `security` | Security | 1-Wrapper | `security/` | Red/blue team, vulnerability scanning, forensics via 30+ PoCs |
| 3 | `search` | Search | 1-Wrapper | `la-fungus-search/` | Semantic search and knowledge retrieval via Qdrant |
| 4 | `email` | Email | 2-Glue | `ops/` | Email drafting, templates, campaigns, SMTP delivery |
| 5 | `pitch` | Pitch | 2-Glue | `ops/` | Pitch deck generation, slide refinement, investor research |
| 6 | `voice` | Voice | 2-Glue | `voice/` | TTS, STT, and 3D UI navigation commands |
| 7 | `social` | Social | 1-Wrapper | `x-pathfinder/` | X/Twitter discovery, profile scoring, trend analysis |
| 8 | `chat` | Chat | 2-Glue | `openclaw/` | Multi-channel messaging across 40+ platforms |
| 9 | `ops` | Ops | 2-Glue | `ops/` | System monitoring, PC health, service management |
| 10 | `research` | Research | 3-New | `shared/` | Deep web research, multi-source synthesis, citations |
| 11 | `data` | Data | 3-New | `shared/` | Data analysis, visualization, ETL pipelines |
| 12 | `creative` | Creative | 3-New | `shared/` | Image generation, creative writing, multi-modal content |
| 13 | `planning` | Planning | 3-New | `shared/` | Task management, project planning, scheduling |
| 14 | `enterprise` | Enterprise | 1-Wrapper | `langdock-mcp/` | Langdock API integration via 35 MCP tools |

---

## Definition Template

Every space definition below follows this structure:

```
### {#}. `{space_id}` — {Name}

> {One-line description}

| Field | Value |
|-------|-------|
| Agent | `{AgentClass}` |
| Tier | {1-Wrapper / 2-Glue / 3-New} |
| Delegates to | `{submodule/}` |

**Owns:** {what this space is responsible for}
**Does NOT own:** {what belongs to other spaces}

**Intents:**
| Intent | Description | Required Payload | Response Type |
|--------|-------------|-----------------|---------------|

**I/O:** {input types} → {output types}
**Dependencies:** {submodules + shared components}
**Extension:** {how to add a new intent}
```

---

## Space Definitions

### 1. `coding` — Coding

> Code generation, review, refactoring, and debugging via a 10-agent AutoGen system.

| Field | Value |
|-------|-------|
| Agent | `CodingAgent` |
| Tier | 2-Glue |
| Delegates to | `coding-engine/` |

**Owns:** All source code manipulation — generating code from descriptions, reviewing for quality/bugs, refactoring, explaining behavior, debugging errors, and multi-file project generation.

**Does NOT own:** Creative writing (`creative`). Data analysis scripts when user wants results not code (`data`). Deployment/infra scripts as operational tasks (`ops`). Vulnerability scanning (`security`).

**Intents:**

| Intent | Description | Required Payload | Response Type |
|--------|-------------|-----------------|---------------|
| `generate` | Generate code from description | `language`, `description` | `code` |
| `review` | Review code for quality and bugs | `code`, `language` | `structured_report` |
| `refactor` | Refactor existing code | `code`, `language`, `goal` | `code` |
| `explain` | Explain what code does | `code` | `text` |
| `debug` | Diagnose and fix a bug | `code`, `error_message` | `code` + `text` |
| `full_project` | Multi-file project generation (multi-agent) | `description`, `language` | `file_tree` |

**I/O:** Text (descriptions, code snippets, errors) → code, structured reports, text explanations, file trees.

**Dependencies:** `coding-engine/` (10 AutoGen agents), `vibemind-shared` (LLM client).

**Extension:** Add `handle_{intent}` method to `CodingAgent`. Register in `intents.py`. No OpenFang routing changes.

---

### 2. `security` — Security

> Red/blue team operations, vulnerability scanning, and forensics via 30+ PoC scripts.

| Field | Value |
|-------|-------|
| Agent | `SecurityAgent` |
| Tier | 1-Wrapper |
| Delegates to | `security/` |

**Owns:** All security research and operations — vulnerability scanning, penetration testing, threat analysis, forensic investigation, security auditing, and defensive monitoring.

**Does NOT own:** Writing security-related application code (`coding`). System uptime monitoring (`ops`). General threat research without active scanning (`research`).

**Intents:**

| Intent | Description | Required Payload | Response Type |
|--------|-------------|-----------------|---------------|
| `scan_vulnerability` | Run vulnerability scan against a target | `target`, `scan_type` | `structured_report` |
| `red_team` | Execute an offensive security PoC | `poc_id`, `target` | `structured_report` |
| `blue_team` | Analyze or simulate defenses | `scenario` | `structured_report` |
| `forensic_analyze` | Investigate a security incident | `evidence` | `structured_report` |
| `audit` | Security audit of code or config | `artifact`, `artifact_type` | `structured_report` |
| `list_pocs` | List available PoC scripts | (none) | `structured_data` |

**I/O:** Text (targets, scenarios), files (logs, configs) → structured reports, scan results, risk scores.

**Dependencies:** `security/` (30+ PoC scripts), `vibemind-shared` (LLM client).

**Extension:** Add PoC script to `security/`, register in `poc_registry.py`. Agent dispatches automatically. `red_team` and `scan_vulnerability` require elevated authorization.

---

### 3. `search` — Search

> Semantic search and knowledge retrieval over indexed content via Qdrant vector database.

| Field | Value |
|-------|-------|
| Agent | `SearchAgent` |
| Tier | 1-Wrapper |
| Delegates to | `la-fungus-search/` |

**Owns:** All internal knowledge retrieval — semantic search over indexed documents, embedding-based similarity, and knowledge base queries.

**Does NOT own:** Live web search (`research`). Enterprise system search via Langdock (`enterprise`). Keyword/regex code search (`coding`). Synthesis/summarization of results (`research`).

**Intents:**

| Intent | Description | Required Payload | Response Type |
|--------|-------------|-----------------|---------------|
| `semantic_search` | Search indexed content by meaning | `query` | `search_results` |
| `similar` | Find documents similar to a given one | `document_id` or `text` | `search_results` |
| `index` | Add new content to search index | `content`, `metadata` | `confirmation` |
| `collections` | List available search collections | (none) | `structured_data` |

**I/O:** Text (queries), document references → ranked results with scores, snippets, source metadata.

**Dependencies:** `la-fungus-search/` (Qdrant + embeddings), `vibemind-shared` (embedding models).

**Extension:** Add `handle_{intent}` to `SearchAgent`. New collections configured in Qdrant via `la-fungus-search/`.

---

### 4. `email` — Email

> Email drafting, template management, campaign execution, and SMTP delivery.

| Field | Value |
|-------|-------|
| Agent | `EmailAgent` |
| Tier | 2-Glue |
| Delegates to | `ops/` |

**Owns:** All email tasks — drafting emails, managing templates, sending individual or bulk via SMTP, and tracking sent messages.

**Does NOT own:** Chat/messaging across platforms (`chat`). Investor outreach strategy (`pitch`). Social media posting (`social`).

**Intents:**

| Intent | Description | Required Payload | Response Type |
|--------|-------------|-----------------|---------------|
| `draft` | Draft an email from instructions | `to`, `subject`, `body_instructions` | `email_draft` |
| `send` | Send a previously drafted email | `draft_id` or (`to`, `subject`, `body`) | `confirmation` |
| `template_create` | Create a reusable email template | `name`, `body_template`, `variables` | `confirmation` |
| `template_list` | List available templates | (none) | `structured_data` |
| `campaign` | Send a bulk email campaign | `template_id`, `recipients` | `campaign_report` |
| `list_sent` | View sent email history | (none) | `structured_data` |

**I/O:** Text (instructions, addresses), structured data (recipient lists) → email drafts, confirmations, campaign reports.

**Dependencies:** `ops/` (SMTP pipeline), `vibemind-shared` (LLM for drafting).

**Extension:** Add handler to `EmailAgent`. New template types added to ops/ template registry. `send` and `campaign` require explicit user confirmation (safety gate).

---

### 5. `pitch` — Pitch

> Pitch deck generation, slide refinement, investor research, and PDF export.

| Field | Value |
|-------|-------|
| Agent | `PitchAgent` |
| Tier | 2-Glue |
| Delegates to | `ops/` |

**Owns:** All investor pitch tasks — generating decks from company descriptions, refining slides, researching investors, and exporting to presentation formats.

**Does NOT own:** General creative writing or design (`creative`). General company research (`research`). Sending investor emails (`email`).

**Intents:**

| Intent | Description | Required Payload | Response Type |
|--------|-------------|-----------------|---------------|
| `generate_deck` | Generate a full pitch deck | `company_description`, `stage` | `slide_deck` |
| `refine_slide` | Improve a specific slide | `slide_data`, `feedback` | `slide_data` |
| `investor_research` | Research potential investors | `industry`, `stage` | `structured_data` |
| `export_pdf` | Export deck to PDF | `deck_id` or `slide_data` | `file` |
| `list_templates` | List available pitch templates | (none) | `structured_data` |

**I/O:** Text (descriptions, feedback), structured data (slide data) → slide decks (title, bullets, notes per slide), PDFs, investor lists.

**Dependencies:** `ops/` (pitch deck generator), `vibemind-shared` (LLM for content).

**Extension:** Add handler to `PitchAgent`. New industry/stage templates added to ops/ template directory.

---

### 6. `voice` — Voice

> Text-to-speech, speech-to-text, and 3D UI navigation commands.

| Field | Value |
|-------|-------|
| Agent | `VoiceAgent` |
| Tier | 2-Glue |
| Delegates to | `voice/` (self-referential) |

**Owns:** All voice I/O and 3D workspace control — speech recognition, speech synthesis, audio pipeline management, and spatial navigation of the 3D interface.

**Does NOT own:** Natural language understanding beyond transcription (that's the Brain's intent router). Content displayed in a space (each space owns its own output). Chat messaging (`chat`).

**Intents:**

| Intent | Description | Required Payload | Response Type |
|--------|-------------|-----------------|---------------|
| `speak` | Convert text to speech (TTS) | `text` | `audio` |
| `transcribe` | Convert speech to text (STT) | `audio_data` | `text` |
| `navigate_space` | Navigate 3D UI to a different space | `target_space_id` | `ui_command` |
| `toggle_view` | Toggle a UI view mode | `view_name` | `ui_command` |
| `zoom` | Zoom in/out in the 3D UI | `direction` | `ui_command` |
| `set_voice` | Change the active TTS voice | `voice_id` | `confirmation` |

**I/O:** Text, audio streams → audio streams, text transcriptions, UI commands (Electron IPC).

**Dependencies:** `voice/` (TTS/STT engines, Electron IPC), `vibemind-shared`.

**Extension:** Add handler to `VoiceAgent`. UI commands need corresponding IPC handlers in Electron. Agent lives in `voice/python/spaces/voice/` to avoid circular deps.

---

### 7. `social` — Social

> X/Twitter discovery, profile scoring, trend analysis, and social media intelligence.

| Field | Value |
|-------|-------|
| Agent | `SocialAgent` |
| Tier | 1-Wrapper |
| Delegates to | `x-pathfinder/` |

**Owns:** All social media intelligence — discovering backers/supporters on X/Twitter, scoring profiles, analyzing trends, and evaluating post engagement.

**Does NOT own:** Posting or content creation (`creative` for content, `chat` for sending). General person/company research (`research`). Other platform messaging (`chat`).

**Intents:**

| Intent | Description | Required Payload | Response Type |
|--------|-------------|-----------------|---------------|
| `discover_backers` | Find potential backers on X | `criteria` | `structured_data` |
| `score_profiles` | Score social profiles | `handles` or `profile_ids` | `structured_data` |
| `trending_topics` | Get trending topics with analysis | (none) | `structured_data` |
| `post_analysis` | Analyze engagement of posts | `post_urls` or `post_ids` | `structured_data` |

**I/O:** Text (criteria, handles, URLs) → structured data (scored profiles, trend rankings, engagement metrics).

**Dependencies:** `x-pathfinder/` (evolutionary discovery + scoring), X/Twitter API credentials.

**Extension:** Add handler to `SocialAgent`. New discovery algorithms go into `x-pathfinder/`. X API rate limits apply — agent queues and retries automatically.

---

### 8. `chat` — Chat

> Multi-channel messaging across 40+ platforms (Telegram, WhatsApp, Slack, Discord, etc.).

| Field | Value |
|-------|-------|
| Agent | `ChatAgent` |
| Tier | 2-Glue |
| Delegates to | `openclaw/` |

**Owns:** All cross-platform messaging — sending and receiving messages across 40+ channels, managing conversation context, and normalizing message formats.

**Does NOT own:** Email (`email`). Social media analytics (`social`). Message content generation (the requesting space generates content).

**Intents:**

| Intent | Description | Required Payload | Response Type |
|--------|-------------|-----------------|---------------|
| `send_message` | Send a message on a channel | `channel`, `recipient`, `message` | `confirmation` |
| `read_messages` | Read recent messages from a channel | `channel` | `structured_data` |
| `list_channels` | List available/configured channels | (none) | `structured_data` |
| `set_webhook` | Register webhook for incoming messages | `channel`, `callback_url` | `confirmation` |
| `channel_status` | Check connection status of a channel | `channel` | `structured_data` |

**I/O:** Text (messages), structured data (channel config), files (attachments) → confirmations, message lists, channel metadata.

**Dependencies:** `openclaw/` (40+ channel adapters), per-channel API credentials.

**Extension:** Add handler to `ChatAgent`. New channels require an adapter in `openclaw/` and credential registration.

---

### 9. `ops` — Ops

> System monitoring, PC health checks, DevOps tasks, and service management.

| Field | Value |
|-------|-------|
| Agent | `OpsAgent` |
| Tier | 2-Glue |
| Delegates to | `ops/` |

**Owns:** All system operations — monitoring health (CPU, memory, disk, network), managing services (start, stop, restart), running deployments and health checks, and reporting system metrics.

**Does NOT own:** Security scanning or forensics (`security`). Writing deployment scripts as code (`coding`). Deep statistical analysis of monitoring data (`data`).

**Intents:**

| Intent | Description | Required Payload | Response Type |
|--------|-------------|-----------------|---------------|
| `system_status` | Get current system health metrics | (none) | `metrics` |
| `monitor_pc` | Start/check PC health monitoring | (none) | `metrics` |
| `deploy` | Deploy a service or application | `service_name`, `target` | `structured_report` |
| `health_check` | Run health check on a service | `service_name` | `structured_report` |
| `restart_service` | Restart a service | `service_name` | `confirmation` |
| `list_services` | List managed services and status | (none) | `structured_data` |

**I/O:** Text (service names, targets), config → system metrics, structured reports, confirmations.

**Dependencies:** `ops/` (MCP tools for PC monitoring), `vibemind-shared`.

**Extension:** Add handler to `OpsAgent`. New MCP tools go into `ops/`. `deploy` and `restart_service` require elevated authorization.

---

### 10. `research` — Research

> Deep web research, multi-source synthesis, and cited summarization.

| Field | Value |
|-------|-------|
| Agent | `ResearchAgent` |
| Tier | 3-New |
| Delegates to | `shared/` (new logic) |

**Owns:** All live web research — searching the internet, fetching web pages, synthesizing information from multiple sources, and producing cited summaries.

**Does NOT own:** Internal/indexed knowledge search (`search`). Investor-specific research (`pitch`). Data analysis or visualization (`data`). Active vulnerability scanning (`security`).

**Intents:**

| Intent | Description | Required Payload | Response Type |
|--------|-------------|-----------------|---------------|
| `research` | Deep research on a topic with citations | `query` | `research_report` |
| `web_search` | Quick web search returning raw results | `query` | `search_results` |
| `fetch_page` | Fetch and extract content from a URL | `url` | `text` |
| `summarize_sources` | Summarize multiple sources | `sources` (list of URLs/texts) | `text` |
| `fact_check` | Verify a claim against web sources | `claim` | `structured_report` |

**I/O:** Text (queries, claims, URLs) → research reports with citations, search results, extracted content, summaries.

**Dependencies:** `vibemind-shared` (LLM client), web search API, HTTP fetcher.

**Extension:** Add handler to `ResearchAgent`. New web tools (search providers, fetchers) register in `web_tools.py`.

---

### 11. `data` — Data

> Data analysis, visualization, ETL pipelines, and structured data processing.

| Field | Value |
|-------|-------|
| Agent | `DataAgent` |
| Tier | 3-New |
| Delegates to | `shared/` (new logic) |

**Owns:** All structured data tasks — loading from files/APIs, cleaning and transforming, statistical analysis, chart generation, and ETL pipelines.

**Does NOT own:** Writing standalone scripts (`coding`). Gathering unstructured web content (`research`). Artistic image generation (`creative`). Enterprise data via Langdock (`enterprise`).

**Intents:**

| Intent | Description | Required Payload | Response Type |
|--------|-------------|-----------------|---------------|
| `analyze` | Analyze a dataset and produce insights | `data_source` | `structured_report` + `image` |
| `visualize` | Generate a chart or graph | `data_source`, `chart_type` | `image` |
| `transform` | Clean or transform data (ETL) | `data_source`, `operations` | `structured_data` or `file` |
| `query` | Run SQL-like query on data | `data_source`, `query_string` | `structured_data` |
| `connect` | Test connection to a data source | `source_type`, `connection_config` | `confirmation` |

**I/O:** Files (CSV, JSON, Parquet), structured data, connection strings → reports, charts (base64/file), transformed datasets, tabular data.

**Dependencies:** `vibemind-shared` (LLM client), pandas, matplotlib, sandbox for generated code.

**Extension:** Add handler to `DataAgent`. New connectors go into `connectors.py`. New chart types in visualization module.

---

### 12. `creative` — Creative

> Image generation, creative writing, and multi-modal content creation.

| Field | Value |
|-------|-------|
| Agent | `CreativeAgent` |
| Tier | 3-New |
| Delegates to | `shared/` (new logic) |

**Owns:** All creative content generation — images from prompts, prose (stories, poems, blogs, scripts, social media copy), and multi-modal content combining text and images.

**Does NOT own:** Code generation (`coding`). Data visualization/charts (`data`). Social media posting or analytics (`social`/`chat`). Pitch deck structure (`pitch`).

**Intents:**

| Intent | Description | Required Payload | Response Type |
|--------|-------------|-----------------|---------------|
| `generate_image` | Generate image from text prompt | `prompt` | `image` |
| `write` | Generate creative text | `mode` (story/poem/blog/script/social_post), `prompt` | `text` |
| `refine` | Iterate on previous creative output | `previous_output`, `feedback` | `text` or `image` |
| `style_transfer` | Apply a style to content | `content`, `target_style` | `text` or `image` |
| `multimodal` | Generate combined text + image | `prompt` | `multimodal` |

**I/O:** Text (prompts, feedback) → text (prose, copy), images (base64/URL), multimodal bundles.

**Dependencies:** `vibemind-shared` (LLM client), image generation API (DALL-E, Stable Diffusion).

**Extension:** Add handler to `CreativeAgent`. New writing modes add a system prompt in `writing_styles.py`. New image providers register in `image_gen.py`.

---

### 13. `planning` — Planning

> Task management, project planning, scheduling, and progress tracking.

| Field | Value |
|-------|-------|
| Agent | `PlanningAgent` |
| Tier | 3-New |
| Delegates to | `shared/` (new logic) |

**Owns:** All project and task management — creating/managing tasks, organizing projects with templates, estimating timelines, tracking progress, and exporting plans.

**Does NOT own:** Executing planned tasks (routes to relevant space). Calendar/event management (no calendar integration). Deep analysis of project metrics (`data`). Code project scaffolding (`coding`).

**Intents:**

| Intent | Description | Required Payload | Response Type |
|--------|-------------|-----------------|---------------|
| `create_task` | Create a new task | `title` | `structured_data` |
| `create_project` | Create project from template | `name`, `template` (sprint/roadmap/launch) | `structured_data` |
| `list_tasks` | List tasks with filters | (none) | `structured_data` |
| `update_task` | Update task status or fields | `task_id`, `updates` | `confirmation` |
| `generate_timeline` | Auto-generate project timeline | `project_id` or `task_list` | `structured_data` |
| `export` | Export project/tasks | `project_id`, `format` (markdown/json/ics) | `file` or `text` |

**I/O:** Text (descriptions), structured data (task specs, filters) → task/project objects, timeline data, exported files (Markdown, JSON, ICS).

**Dependencies:** `vibemind-shared` (LLM client), persistence layer (DB or file-based store).

**Extension:** Add handler to `PlanningAgent`. New project templates in `templates.py`. New export formats via serializer module.

---

### 14. `enterprise` — Enterprise

> Enterprise integrations via Langdock API with 35 MCP tools and AutoGen multi-agent orchestration.

| Field | Value |
|-------|-------|
| Agent | `EnterpriseAgent` |
| Tier | 1-Wrapper |
| Delegates to | `langdock-mcp/` |

**Owns:** All enterprise/B2B integration tasks — invoking Langdock MCP tools, managing enterprise API auth, and orchestrating multi-agent teams for complex enterprise workflows.

**Does NOT own:** Internal semantic search (`search`). Web search (`research`). System ops (`ops`). General-purpose tool use (each space has its own tools).

**Intents:**

| Intent | Description | Required Payload | Response Type |
|--------|-------------|-----------------|---------------|
| `invoke_tool` | Call a specific Langdock MCP tool | `tool_name`, `parameters` | varies by tool |
| `list_tools` | List available MCP tools | (none) | `structured_data` |
| `multi_agent_task` | Execute complex task via AutoGen team | `task_description` | `structured_report` |
| `enterprise_search` | Search enterprise knowledge via Langdock | `query` | `search_results` |
| `workflow` | Execute predefined enterprise workflow | `workflow_id`, `inputs` | `structured_report` |

**I/O:** Text (queries, task descriptions), structured data (tool params) → tool-specific outputs (normalized), search results, workflow reports.

**Dependencies:** `langdock-mcp/` (35 MCP tools, FastMCP, AutoGen team), enterprise API keys.

**Extension:** New MCP tools added via `langdock-mcp/` are automatically available through `invoke_tool` — no agent code changes needed.

---

## Cross-Space Boundary Matrix

When a user request could belong to multiple spaces, use this table:

| User Request | Routes To | NOT To | Why |
|-------------|-----------|--------|-----|
| "Write a Python script to analyze this CSV" | `coding` | `data` | User asked for a script (code artifact) |
| "Analyze this CSV and show me trends" | `data` | `coding` | User wants results, not code |
| "Generate an image of a dashboard" | `creative` | `data` | Artistic image, not real data viz |
| "Create a chart from this sales data" | `data` | `creative` | Data-driven visualization |
| "Research competitors for my pitch" | `pitch` | `research` | Investor-context research |
| "Research the history of AI" | `research` | `pitch` | General topic research |
| "Write a blog post about security" | `creative` | `security` | Creative writing task |
| "Scan my app for vulnerabilities" | `security` | `coding` | Active security scanning |
| "Write authentication code" | `coding` | `security` | Code generation |
| "Send this on Telegram" | `chat` | `email` | Platform messaging |
| "Send an email to investors" | `email` | `chat` | SMTP email |
| "Find backers on Twitter" | `social` | `research` | X-specific discovery |
| "Research a person's background" | `research` | `social` | General web research |
| "Deploy the new version" | `ops` | `coding` | Operational action |
| "Write a Dockerfile" | `coding` | `ops` | Code generation |
| "Check if server is healthy" | `ops` | `security` | Uptime monitoring |
| "Check if server is compromised" | `security` | `ops` | Security investigation |
| "Search our knowledge base" | `search` | `research` | Internal indexed content |
| "Search the web for recent news" | `research` | `search` | Live web content |
| "Create a sprint plan" | `planning` | `coding` | Task management |
| "Scaffold a new React project" | `coding` | `planning` | Code project structure |
| "Summarize enterprise docs via Langdock" | `enterprise` | `research` | Enterprise API tool |

---

## Adding a New Space

To add a 15th space:

1. Add its definition to this document following the template above
2. Register the space in OpenFang's agent registry (`openfang/src/spaces/registry.rs`)
3. Add an intent classification rule to VoiceDialog's intent router (`voice/python/brain/intent_router.py`)
4. Create the agent class extending `BaseSpaceAgent`
5. Update the boundary matrix for any overlaps with existing spaces
