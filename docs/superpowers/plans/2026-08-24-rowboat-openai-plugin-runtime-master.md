# Rowboat OpenAI Plugin Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Rowboat's public legacy plugin layer with a pinned, policy-controlled OpenAI-plugin-compatible runtime while preserving behavior until migration parity permits cutover.

**Architecture:** A framework-independent TypeScript kernel imports and admits pinned OpenAI plugin bundles. The Rowboat Web app owns persistence, authorization, provider resolution, migration, and cutover APIs; Desktop consumes the same service state. Four independently testable phase plans deliver the kernel, component execution, product integration, and legacy migration in order.

**Tech Stack:** TypeScript strict mode, Zod, YAML, Vitest, Node crypto/fs/path, Next.js 15, MongoDB, Awilix, React 19, Vite/Electron renderer, Model Context Protocol SDK, Python pytest contract tests.

---

## Authoritative inputs

- Design: `docs/superpowers/specs/2026-08-24-rowboat-openai-plugin-runtime-design.md`
- Approved design baseline: `9a2c9fbf2477764c206f105867ce9df8366b6160`
- Implementation integration base: `origin/master@b1c03cf`; the intervening
  commits have no `spaces/rowboat` diff, but recheck this before Task 1.
- Plugin source pin: `11c74d6ba24d3a6d48f54a194cd00ef3beea18f9`
- Feature branch: `codex/rowboat/openai-plugin-runtime-v1`

## Baseline non-claims

- `apps/rowboat`: full TypeScript checking is already red because tracked source references missing `logo.png`, `logo-only.png`, and `mascot.png` assets.
- `apps/x`: root ESLint is already red because all input files are ignored.
- Every phase must add and pass focused gates. Do not use focused green gates to claim either full application baseline is green.
- Do not repair these unrelated baselines inside this feature unless the user separately expands scope.

## Execution partitions

Implement these plans in order. A later phase may not begin until the prior phase's focused tests, typecheck, diff check, and commit audit pass.

1. [Phase 1: Kernel and catalog](2026-08-24-rowboat-openai-plugin-runtime-phase-1-kernel.md)
2. [Phase 2: Components and providers](2026-08-24-rowboat-openai-plugin-runtime-phase-2-components.md)
3. [Phase 3: Web and Desktop integration](2026-08-24-rowboat-openai-plugin-runtime-phase-3-product.md)
4. [Phase 4: Migration, parity, and cutover](2026-08-24-rowboat-openai-plugin-runtime-phase-4-migration.md)

## Global invariants

- [ ] Work only in the isolated feature worktree.
- [ ] Begin implementation only after branch/base parity is re-established and
  `git diff --name-status 9a2c9fb..origin/master -- spaces/rowboat` is still empty.
- [ ] Use RED then GREEN for every behavior change; capture the failing assertion before implementation.
- [ ] New TypeScript uses `unknown` plus narrowing, never `any`.
- [ ] Plugin source content never supplies runtime secret values.
- [ ] A component without an admitted license, provider, credential reference, or execution policy is not invoked.
- [ ] No shadow/parity path duplicates a write-capable tool action.
- [ ] Legacy workflow fields remain unchanged until per-project cutover.
- [ ] Actual database migration and legacy-path removal require action-time confirmation after dry-run evidence.
- [ ] Commit each numbered task separately with a conventional commit.
- [ ] Run `git diff --check` before each commit.

## Requirement coverage

| Design requirement | Implemented by |
| --- | --- |
| R1 Format compatibility | Phase 1 Tasks 2–4 |
| R2 Provenance and reproducibility | Phase 1 Tasks 3 and 6 |
| R3 License admission | Phase 1 Task 5 |
| R4 Secret handling | Phase 1 Task 5; Phase 2 Tasks 3–5; Phase 3 Task 2 |
| R5 Skills | Phase 2 Task 1 |
| R6 Agents | Phase 2 Task 1 |
| R7 Commands | Phase 2 Task 1 |
| R8 MCP servers | Phase 2 Tasks 2–3 |
| R9 Apps | Phase 2 Task 2 |
| R10 Hooks | Phase 2 Task 4 |
| R11 Assets | Phase 2 Task 1 |
| R12 Shared product semantics | Phase 3 Tasks 1–4 |
| R13 Legacy migration | Phase 4 Tasks 1–3 |
| R14 Space-level compatibility | Phase 4 Task 6 |
| R15 Cutover and rollback | Phase 4 Tasks 3–5 |

## Phase gate command

Run from the child worktree root after every phase:

```powershell
git diff --check
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime run typecheck
git status --short --branch
```

Expected after Phase 1 and Phase 2: plugin tests and package typecheck pass; status contains only the next task's intentional changes.

Phase 3 adds:

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins
pnpm --dir spaces/rowboat/rowboat/apps/x --filter @x/renderer test:plugins
```

Phase 4 adds:

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run plugins:verify
python -m pytest spaces/rowboat/tests/test_openai_plugin_runtime_contract.py -q
```

## Final completion audit

- [ ] Verify the catalog lock records exactly 180 plugins at the pinned commit.
- [ ] Verify every manifest and discovered component has a deterministic explicit status.
- [ ] Verify tests prove rejected/unavailable providers have zero calls.
- [ ] Scan tracked changes and generated receipts for secret-shaped values.
- [ ] Verify all ten legacy card recipes are deterministic and idempotent.
- [ ] Produce an all-project dry-run report from the current Rowboat database.
- [ ] Obtain action-time confirmation before executing the database migration or removing legacy public paths.
- [ ] Execute migration, parity, cutover, and rollback proof only within the confirmed scope.
- [ ] Rerun focused gates and report the two unrelated baseline failures separately.
- [ ] Verify the worktree is clean and list every feature commit.
- [ ] Do not mark the thread goal complete until each R1–R15 evidence item is current and direct.
