# Brain OpenFang MCP server consumer alignment

## Context

OpenFang now defines an agent's MCP server scope with the top-level `mcp_servers` field. Brain still has four inventory and introspection readers that either inspect older sections or duplicate regular-expression parsing. They can therefore report an empty tool scope for a correctly generated Space agent.

This task changes static manifest consumption only. It does not dispatch an agent, connect an MCP server, widen an approval, change costs, or make a runtime or deployment claim.

## Considered approaches

1. Add one dependency-free Brain-core TOML helper and reuse it in every consumer. This is the selected approach because it gives one precedence and validation contract with a small diff.
2. Patch each consumer independently. This is smaller per call site but preserves duplicated parsing and makes future drift likely.
3. Introduce a full Agent Manifest domain model. This would also normalize tags, models, paths, and capabilities, but it is outside the bounded authority-alignment task.

## Design

`core.openfang_agent_manifest` loads an `agent.toml` with the standard-library TOML parser and exposes a focused MCP-server extraction function. Precedence is explicit:

1. top-level `mcp_servers`, including an explicitly empty list;
2. transitional `[capabilities].mcp_servers` only when the canonical key is absent;
3. legacy `[mcp_allowed].servers` only when both newer keys are absent.

Only lists containing non-empty strings are accepted. A missing, malformed, or type-invalid value returns an empty list. This is fail-closed for Brain discovery: malformed metadata never grants or displays additional server tools.

`capability_discovery.discover_agents` delegates MCP extraction to the helper while keeping its current tolerant per-file behavior. The JSON event mapping, per-agent tools endpoint, and XLSX event mapping in `introspection.py` use the same helper instead of their legacy-only regular expressions. Other manifest fields and endpoint response shapes remain unchanged.

## Tests

Focused tests first establish RED on the current code, then cover:

- canonical top-level parsing;
- canonical empty-list precedence over populated transitional or legacy sections;
- transitional and legacy compatibility when newer fields are absent;
- malformed and type-invalid values returning no servers;
- capability discovery returning canonical server scope;
- all three introspection consumers delegating to the shared parser.

The task remains hermetic: fixtures use temporary TOML files and mocks. No OpenFang process, MCP connection, Fungus service, provider request, database, or deployment is started.

## Non-claims

Passing tests prove only that committed Brain readers interpret the manifest contract consistently. They do not prove that any MCP server is configured, connected, reachable, approved, or successfully invoked.
