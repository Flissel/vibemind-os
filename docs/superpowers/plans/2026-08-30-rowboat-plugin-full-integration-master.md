# Rowboat OpenAI Plugin Full Integration Master Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an installed OpenAI plugin actually usable from Rowboat: a call that reaches its provider, is released by OpenFang, authenticates with an OpenFang-held credential, and returns a result.

**Architecture:** The plugin runtime is complete and wired up to the provider boundary. What remains are release decisions and coverage. OpenFang owns both decisions — whether a write may run, and which credential authenticates it — and Rowboat pulls them through ports it owns, so a revoked decision takes effect immediately and no secret is ever stored in Rowboat.

**Tech Stack:** TypeScript strict mode, Zod, Vitest, Next.js 15, MongoDB (replica set), the pinned `@rowboat/openai-plugin-runtime` kernel, OpenFang HTTP API.

**Spec:** `spaces/rowboat/rowboat/docs/openai-plugin-runtime-operations.md` (operational contract and non-claims) and `docs/superpowers/specs/2026-08-24-rowboat-openai-plugin-runtime-design.md` (R1–R15).

## Global Constraints

- Plugin source pin: `openai/plugins@11c74d6ba24d3a6d48f54a194cd00ef3beea18f9`, 180 plugins.
- Catalog digest pin: whatever `PINNED_PLUGIN_CATALOG_DIGEST` in `packages/openai-plugin-runtime/src/domain/catalog.ts` currently holds — W1 Task 1 moves it, so read the constant rather than copying a literal from this plan. Policy `rowboat-plugin-policy-v1`; schema `rowboat-plugin-schema-v1`.
- Changing any pin means re-running `catalog:sync`, updating the constant in `packages/openai-plugin-runtime/src/domain/catalog.ts`, the Desktop copy in `apps/x/apps/renderer/src/lib/rowboat-plugin-api.ts`, the committed lock, the byte-size assertion in `test/plugins/plugin-migration-keyset-snapshot.test.ts`, and the operations doc.
- New TypeScript uses `unknown` plus narrowing, never `any`.
- Plugin source content never supplies a secret value; a credential is always a reference resolved at call time.
- An unknown classification, an unreadable decision, and an absent credential all fail closed. Nothing is approximated.
- The kernel is consumed as a build artifact: after touching `packages/openai-plugin-runtime`, run `npm --prefix packages/openai-plugin-runtime run build` before the app sees it.
- Commit each numbered task separately with a conventional commit; run `git diff --check` before each commit.
- MongoDB must be a replica set for any test that writes through the repositories.

## Verified current state

Established by live runs against a real MongoDB and the running UI, not by reading code:

- Catalog loads (180 entries), UI installs a plugin, UI adds a component as a workflow tool, the tool appears in the workflow editor with its real provenance.
- Migration, cutover, rollback, the runtime mode gate and the removal gate all work and are covered by 605 app tests plus 406 kernel tests.
- An invocation now reaches its provider: it fails with `write_review_required`, no longer with `provider_unavailable`.
- Behind that gate the credential resolver releases nothing, so the next failure would be `credential_missing`.

## Workstreams

Ordered by what unblocks the next thing. W1 is the critical path and has its own detailed plan; the others get theirs when they are scheduled, because each depends on decisions W1 forces into the open.

| # | Workstream | Delivers | Detailed plan |
| --- | --- | --- | --- |
| W1 | OpenFang release path | One GitHub MCP call that runs end to end | `2026-08-30-rowboat-plugin-openfang-release-phase-1.md` |
| W2 | Component coverage | App components (156 of 1093) and process MCP become executable | written when scheduled |
| W3 | UI completion | Component-level install; plugin tools locked against renaming (credential binding cut) | `2026-08-31-rowboat-plugins-w3-ui-completion.md` |
| W4 | MCP access | Catalog, install, add-tool and runtime-mode as MCP tools | `2026-08-31-rowboat-plugins-w4-mcp-access.md` |
| W5 | Deployment | The stack can actually run all of the above | written when scheduled |

### W1 — OpenFang release path (critical path)

Acceptance: a GitHub MCP tool call from a Rowboat agent returns a real result, released by an OpenFang approval and authenticated by an OpenFang-held credential, with a redacted receipt naming the approval.

Contains: credential slots derived from the MCP declaration; an approval port plus its OpenFang adapter; the write gate wired into the tool runtime; an operation classifier so reads stop asking for approval; the credential transport decision.

### W2 — Component coverage

Acceptance: an `app` component of an installed plugin executes, and a `process` MCP server executes.

Two independent pieces. The **connector bridge** is the larger prize: 156 of 1093 catalog components are `app`, and the provider registry refuses `openai-connector-bridge` by design, so today they cannot run at all. Building one means deciding what an OpenAI connector call is in Rowboat's terms and who holds the connector session — an architecture decision, not a wiring task. The **process MCP** piece is smaller: it needs the verified execution root from the content store mounted into the web runtime, which is a deployment change plus a resolution branch.

### W3 — UI completion

Acceptance, **narrowed 2026-08-31**: an admitted component of a partially-available plugin can be installed and used, and a plugin tool cannot be renamed or shown as mocked. Detailed plan: `2026-08-31-rowboat-plugins-w3-ui-completion.md`.

The install gate is one line — `assertAdmitted` requires that *no* component has a non-admitted admission, which is what blocks `github` (its MCP component is admitted; its skills, app and assets are not). Measured payoff: this unlocks exactly **three** executable components (`cloudflare-api`, `github`, `notion`); `linear` is already installable. The repository layer already accepts partial installs — the W1 end-to-end proof writes one installation and one admission row by hand — so this is a use-case, preview and UI change with no repository or runtime work.

**Credential-slot binding is cut.** The credential a call uses now comes from OpenFang's allowlist per call, keyed by a catalog constant, so binding a slot in the UI would change nothing about which value is used — it duplicates an existing mechanism. The only thing it could add is per-project credential indirection, which is a kernel change and its own design. What survives is a truthfulness fix to `requiredCredentialNames`, handled separately: today the dialog surfaces `CODEX_HOME` (a home-directory path) as a credential and tells `linear` — the one plugin both installable and executable — that it needs none, which is false.

The real exposure behind "cannot be silently mocked" turned out not to be mocking: execution already ignores the mock flag for plugin tools, and a test pins that. It is **renaming** — the tool name becomes the MCP operation name, the read/write classifier's input, and the `toolName` a human reads in the OpenFang approval.

### W4 — MCP access

Acceptance: an agent can list the catalog, install a plugin, add a tool and read the runtime mode over MCP.

The Space MCP server exposes exactly one tool today (`rowboat_status`). The REST API already covers catalog, install, migration and runtime-mode; **add-tool has no REST route** and needs one before it can be exposed.

### W5 — Deployment

Acceptance: a deployed Rowboat serves the plugins page and executes a plugin call.

Required: MongoDB as a single-node replica set in both `docker-compose.yml` and `infra/swarm/vibemind-stack.yml` (both standalone today, and every plugin write is transactional); the four `PLUGIN_*` variables in the swarm stack (zero occurrences today); the kernel `dist` build in the image; `plugins:catalog-load` as a deploy step after `mongodb-ensure-indexes`. Two known warts to fix while there: `npm run dev` is broken for plugin pages under Turbopack, and `mongodb-ensure-indexes` creates its indexes and then never exits.

## Open decision this plan forces

**How does a credential reach the provider?** OpenFang has no credential issuance API: secrets live in `~/.openfang/secrets.env`, written by the channel configure endpoint. Two options, both real:

1. **Deploy-time injection.** OpenFang's secret store fills the Rowboat process environment; the resolver maps a reference such as `GITHUB_PAT_TOKEN` to that value. Simple, no new OpenFang surface, but the secret sits in Rowboat's process for its lifetime and rotation needs a restart.
2. **Call-time issuance.** A new OpenFang endpoint hands out a short-lived value for a named reference; the resolver fetches per call. No standing secret in Rowboat and immediate rotation, but it requires a change in the `openfang` submodule.

Recommendation: option 2, because it is the only one that keeps the property this runtime already enforces everywhere else — that a revoked decision takes effect immediately. W1 Task 5 stops for this decision before implementing either.

## Requirement coverage

| Gap (from the integration review) | Workstream |
| --- | --- |
| Credential slots are never emitted | W1 Task 1 |
| Every operation classified `write` | W1 Task 6 |
| Write needs an OpenFang release | W1 Tasks 2–4 |
| Credential is never released | W1 Task 5 |
| App components cannot execute | W2 |
| Process MCP cannot execute | W2 |
| Plugin-level install gate blocks admitted components | W3 |
| No UI to bind a credential slot | cut — duplicates OpenFang's allowlist; see W3 |
| Plugin tools offer mocking and parameter editing | W3 |
| No MCP tools for plugins; add-tool has no REST route | W4 |
| Standalone MongoDB, missing envs, missing dist build, missing catalog load | W5 |
| `npm run dev` broken under Turbopack; ensure-indexes never exits | W5 |
| Legacy public-path removal still gated | out of scope; the gate is correctly red |
| `shadow -> legacy` not an admitted transition | out of scope; widening the table is a separate decision |
