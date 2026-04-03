# CLAUDE.md — VibeMind OS

## What Is This Repository?

VibeMind OS is an open-source AI operating system — a **meta-repository** that ties together 12 specialized submodules via git submodules. It is built and maintained solo by Felix Baumann ([@Flissel](https://github.com/Flissel)).

This repo itself contains no application code. All implementation lives in the submodules. The meta-repo provides:
- Submodule references (`.gitmodules`)
- Architecture documentation (`README.md`)
- Licensing (`LICENSE` — MIT)

## Repository Structure

```
vibemind-os/                  # Meta-repo (this repo)
├── CLAUDE.md                 # AI assistant guide (this file)
├── README.md                 # Architecture overview & quick start
├── LICENSE                   # MIT
├── .gitmodules               # Submodule URL mappings
│
├── voice/                    # VibeMind-VoiceDialog — the main product
├── ops/                      # vibemind-os operations module
├── shared/                   # vibemind-shared — pip-installable LLM client
├── security/                 # vibemind-security — 30+ security PoCs
├── x-pathfinder/             # Evolutionary Twitter/X backer discovery
├── langdock-mcp/             # Langdock API MCP server (35 tools)
├── la-fungus-search/         # Semantic search (Qdrant + embeddings)
├── openclaude/               # OpenClaude HTTP service + Docker
├── coding-engine/            # DaveFelix-Coding-Engine (10 AI agents)
├── clawcode/                 # ClawCode — Docker Claude integration
├── openclaw/                 # Personal AI assistant (40+ channels)
└── openfang/                 # Agent OS (Rust, 53 tools, 27 LLMs)
```

## Submodule Details

| Directory | Upstream Repo | Language | Purpose |
|-----------|--------------|----------|---------|
| `voice/` | Flissel/VibeMind-VoiceDialog | Python, Electron, Three.js | Voice-controlled 3D AI workspace with 14 domain spaces |
| `ops/` | Flissel/vibemind-os | Python, SMTP | Email campaigns, pitch deck generation, PC monitoring MCPs |
| `shared/` | Flissel/vibemind-shared | Python (pip package) | Multi-provider LLM client factory (OpenAI, Anthropic, Gemini, Groq, Ollama) |
| `security/` | Flissel/vibemind-security | Python, AutoGen | Security research — injection chains, red/blue team, forensics |
| `x-pathfinder/` | Flissel/x-pathfinder | Python | Evolutionary X/Twitter discovery + backer scoring |
| `langdock-mcp/` | Flissel/langdock-mcp | Python, FastMCP | MCP server for Langdock API, AutoGen multi-agent team |
| `la-fungus-search/` | Flissel/la_fungus_search | Python | Semantic search engine (Qdrant vector DB + embeddings) |
| `openclaude/` | Flissel/openclaude | TypeScript/Node.js | OpenClaude HTTP service + Docker deployment |
| `coding-engine/` | Flissel/DaveFelix-Coding-Engine | Python, AutoGen | Autonomous code generation with 10 AI agents |
| `clawcode/` | Flissel/ClawCode | Docker, TypeScript | Docker-based Claude Code integration |
| `openclaw/` | Flissel/openclaw | Node.js | Personal AI assistant across 40+ messaging channels (fork) |
| `openfang/` | Flissel/openfang | Rust | Agent OS — 27 LLMs, 53 tools, WASM sandbox (fork) |

## Architecture

```
User speaks / types / messages
        │
  ┌─────┴───────────────────────────────┐
  │  Input Layer                        │
  │  voice/    → 3D UI + Voice Input    │
  │  openclaw/ → Telegram/WhatsApp/Slack│
  │  openfang/ → 40 channels + 27 LLMs │
  └─────┬───────────────────────────────┘
        │
  ┌─────┴───────────────────────────────┐
  │  Orchestration Layer                │
  │  Swarm Orchestrator + AutoGen       │
  │  14 Domain Spaces + Intent Router   │
  └─────┬───────────────────────────────┘
        │
  ┌─────┴───────────────────────────────┐
  │  Processing & Data Layer            │
  │  shared/         LLM client factory │
  │  langdock-mcp/   Enterprise AI API  │
  │  ops/            Email + Pitch MCPs │
  │  security/       Monitoring+Defense │
  │  la-fungus-search/ Semantic search  │
  │  coding-engine/  Code generation    │
  └─────────────────────────────────────┘
```

## Tech Stack Summary

- **Primary languages**: Python (majority), Rust (openfang), TypeScript/Node.js (clawcode, openclaw, openclaude)
- **AI/ML frameworks**: AutoGen (multi-agent orchestration), Swarm, FastMCP
- **LLM providers**: OpenAI, Anthropic, Gemini, Groq, Ollama — abstracted via `shared/vibemind-shared`
- **Frontend**: Electron + Three.js (voice module 3D UI)
- **Vector DB**: Qdrant (la-fungus-search)
- **Containerization**: Docker (clawcode, openclaude)
- **Messaging**: Telegram, WhatsApp, Slack, 40+ channels

## Development Workflow

### Initial Setup

```bash
git clone --recurse-submodules https://github.com/Flissel/VibeMind-OS.git
cd VibeMind-OS
git submodule update --init --recursive

# Install the shared Python package
cd shared && pip install -e . && cd ..
```

### Working With Submodules

Each submodule is an independent git repository. Key commands:

```bash
# Pull latest for all submodules
git submodule update --remote --merge

# Work inside a submodule (commits go to the submodule repo)
cd voice/
git checkout main
# ... make changes, commit, push ...
cd ..

# Update the meta-repo to point to new submodule commits
git add voice/
git commit -m "Update voice submodule to latest"
```

### Running the Main Product

```bash
cd voice && pip install -r requirements.txt
python python/electron_backend.py
```

## Conventions for AI Assistants

### General Rules

- **This is a submodule-based meta-repo.** Do not create application code at the root level. Code belongs in the appropriate submodule.
- **Submodules may be empty** if not initialized. Run `git submodule update --init --recursive` before exploring code.
- **Each submodule has its own git history, branches, and remotes.** Commits inside a submodule directory go to that submodule's repo, not this meta-repo.
- **Changes to submodule pointers** (which commit a submodule references) are tracked in this meta-repo.

### When Modifying Code

1. Identify which submodule the change belongs to.
2. Work inside that submodule's directory.
3. Follow the submodule's own conventions (check for its own CLAUDE.md, README, or config files).
4. After committing in the submodule, update the meta-repo's submodule pointer if needed.

### Language & Style Conventions

- **Python modules**: Follow each submodule's existing style. The shared package (`vibemind-shared`) is the canonical LLM abstraction — use it rather than calling providers directly.
- **Rust (openfang)**: Follow Rust idioms, use `cargo fmt` and `cargo clippy`.
- **TypeScript/Node.js**: Follow existing patterns in each submodule.

### Security Considerations

- **Never commit secrets** (API keys, SMTP credentials, tokens). Use environment variables.
- **The security/ submodule contains offensive security research.** Handle PoCs responsibly — they exist for authorized testing and education only.
- LLM provider keys (OpenAI, Anthropic, etc.) should be loaded from environment variables, not hardcoded.

### Key Dependency: vibemind-shared

The `shared/` submodule provides `vibemind-shared`, a pip-installable multi-provider LLM client factory. Other Python submodules depend on it. Always ensure it is installed (`pip install -e shared/`) before working on dependent modules.

## License

MIT — see `LICENSE` for full text. Each submodule may have its own license; check individually.
