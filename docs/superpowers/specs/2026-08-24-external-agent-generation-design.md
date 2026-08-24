# External Runtime Agent Generation Contract

**Date:** 2026-08-24
**Status:** Approved design; implementation pending

## Problem

`scripts/sync_openfang_agents.py` already emits OpenFang's effective top-level
`mcp_servers` field and has focused tests for that contract. The older claim
that it emits `mcp_allowed` is stale.

The real registry check currently crashes for the enabled `agentfarm` space.
That space deliberately uses `agent: null` because Captain Cook is the runtime
and no OpenFang chat-agent manifest should be generated. `_skip_reason()`
recognizes the missing agent, but `sync()` subsequently formats the null value
with a string-width specifier and raises `TypeError`.

An implicit `agent: null` skip is too permissive: it cannot distinguish an
intentional external runtime from a missing agent caused by configuration
drift.

## Decision

Add an explicit per-space registry field:

```yaml
generate_agent: false
```

The field controls only OpenFang agent-manifest generation. It does not disable
the space, its event routing, its runtime declaration, or other authority
validation.

The field defaults to `true` when omitted, preserving all existing generated
agents.

## Registry Contract

For every space:

- `generate_agent` must be a boolean when present.
- If `generate_agent` is omitted or `true`, an enabled space must declare a
  non-empty string in `agent` unless it is already covered by an existing,
  explicit protected-agent rule.
- If `generate_agent` is `false`, `agent` must be null or omitted. A simultaneous
  agent name is contradictory and fails validation.
- `enabled: false` continues to suppress generation independently of this field.
- `agentfarm` declares `generate_agent: false` and retains `agent: null` plus its
  Captain Cook runtime and event definitions.

## Generator Behavior

Generation validation runs before any output files are compared or written.
Invalid types and contradictory combinations return exit code 1 with a concise
validation message; they never produce a traceback.

For a valid external runtime, `_skip_reason()` returns a stable reason such as
`external runtime (generation disabled)`. The reporting path converts display
values safely and prints the skip without attempting to create or compare an
`agent.toml` file.

Existing MCP authority, tool-scope, provenance, model, and event checks remain
active. Only validations that specifically require a generated OpenFang agent
or generated manifest scope are skipped for `generate_agent: false`.

## Data Flow

1. Load `config/space_agent_registry.yml`.
2. Validate the `generate_agent` type and its relationship to `agent`.
3. Run the existing registry authority validations.
4. For each space, skip disabled, protected, or explicitly external entries
   with a deterministic reason.
5. Render and compare manifests only for entries whose generation flag resolves
   to `true`.
6. Return normal check/drift status without uncaught exceptions.

## Tests

Focused tests will cover:

- an enabled external runtime with `generate_agent: false` and `agent: null`
  exits successfully and creates no manifest;
- an enabled space with `agent: null` and no explicit marker fails validation;
- `generate_agent: false` combined with a non-empty agent name fails validation;
- a non-boolean `generate_agent` value fails validation;
- existing generated-agent output remains byte-for-byte unchanged;
- the real registry identifies `agentfarm` as an external-runtime skip and does
  not raise a traceback.

The implementation follows RED/GREEN: add the failing contract tests first,
then make the smallest generator and registry changes needed to pass them.

## Verification Gate

After initializing the exact OpenFang gitlink in the clean worktree:

```text
python -m pytest scripts/tests/test_sync_openfang_agents.py -q
python scripts/sync_openfang_agents.py --check
git diff --check
```

Success requires all focused tests to pass, `--check` to exit 0 without drift or
traceback, and a clean whitespace check.

## Non-goals

- No changes to OpenFang's `AgentManifest` schema.
- No changes to MCP server or tool authorization semantics.
- No generated OpenFang agent for Captain Cook.
- No changes to the separate video capability or voice/chat-to-event path.
- No deployment, daemon restart, or live runtime mutation.
