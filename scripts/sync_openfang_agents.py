"""Sync OpenFang agent.toml files from space_agent_registry.yml.

Reads `config/space_agent_registry.yml` and for each space writes
`openfang/agents/<agent_name>/agent.toml` with the correct top-level
`mcp_servers` scope from OpenFang's `AgentManifest`. Existing files are
updated in place; new files are created.

Usage:
  python scripts/sync_openfang_agents.py              # write + report
  python scripts/sync_openfang_agents.py --dry-run    # report only
  python scripts/sync_openfang_agents.py --check      # exit 1 if drift
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "space_agent_registry.yml"
AGENTS_DIR = ROOT / "openfang" / "agents"


# Per-space `model:` overrides start from these values. A space that declares no
# override renders exactly the historical block, so dimensioning one space never
# moves another.
MODEL_DEFAULTS: dict[str, Any] = {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "max_tokens": 4096,
    "temperature": 0.2,
}
MODEL_OVERRIDE_KEYS = frozenset(MODEL_DEFAULTS)


AGENT_TOML_TEMPLATE = """name = "{name}"
version = "0.1.0"
description = "{description}"
author = "vibemind"
module = "builtin:chat"
tags = ["vibemind", "brain-routed", "space:{space}"]
mcp_servers = [{mcp_list}]

[model]
provider = "{provider}"
model = "{model}"
max_tokens = {max_tokens}
temperature = {temperature}
system_prompt = \"\"\"You are {name}, the VibeMind agent for the "{space}" space.

You receive structured intent envelopes with schema "vibemind.intent.v1":
  {{
    "event_type": "...",
    "space": "{space}",
    "preferred_tool": "...",
    "required_params": [...],
    "params": {{...}},
    "context": {{...}},
    "user_text": "..."
  }}

METHODOLOGY:
1. If "preferred_tool" is set, try that tool first with the provided params.
2. Fill missing required_params from "context" or "user_text".
3. Return a concise result — the user expects voice-friendly replies.

{prompt_hint}
\"\"\"

[resources]
max_llm_tokens_per_hour = 100000
max_concurrent_tools = 5

[capabilities]
tools = [{tool_list}]
network = ["*"]
memory_read = ["*"]
memory_write = ["self.*"]
"""


def _mcp_list_literal(servers: list[str]) -> str:
    return ", ".join(json.dumps(server) for server in servers)


def format_mcp_tool_name(server: str, tool: str) -> str:
    """Return the canonical OpenFang capability name for one MCP tool."""
    normalized_server = server.lower().replace("-", "_")
    normalized_tool = tool.lower().replace("-", "_")
    return f"mcp_{normalized_server}_{normalized_tool}"


def _capability_tool_names(spec: dict) -> list[str]:
    """Return the closed, deterministic capability list for a generated agent."""
    mcp_tools = spec.get("mcp_tools", {})
    if not isinstance(mcp_tools, dict):
        return ["memory_store", "memory_recall"]
    generated = {
        format_mcp_tool_name(server.strip(), tool.strip())
        for server, tools in mcp_tools.items()
        if isinstance(server, str) and isinstance(tools, list)
        for tool in tools
        if isinstance(tool, str)
    }
    return ["memory_store", "memory_recall", *sorted(generated)]


def _tool_list_literal(tools: list[str]) -> str:
    return ", ".join(json.dumps(tool) for tool in tools)


def resolve_model_block(spec: dict) -> dict:
    """Return the model dimensioning for one space, defaults filled in."""
    override = spec.get("model", {})
    resolved = dict(MODEL_DEFAULTS)
    if isinstance(override, dict):
        resolved.update(
            {
                key: value
                for key, value in override.items()
                if key in MODEL_OVERRIDE_KEYS
            }
        )
    return resolved


def _render_agent_toml(space: str, spec: dict) -> str:
    agent = spec["agent"]
    description = spec.get(
        "description", f"VibeMind {space} agent, Brain-routed."
    )
    prompt_hint = spec.get("system_prompt_hint", "")
    mcp_servers = spec.get("mcp_servers", [])
    model_block = resolve_model_block(spec)
    return AGENT_TOML_TEMPLATE.format(
        name=agent,
        space=space,
        description=description,
        prompt_hint=prompt_hint,
        mcp_list=_mcp_list_literal(mcp_servers),
        tool_list=_tool_list_literal(_capability_tool_names(spec)),
        provider=model_block["provider"],
        model=model_block["model"],
        max_tokens=json.dumps(model_block["max_tokens"]),
        temperature=json.dumps(model_block["temperature"]),
    )


def _skip_reason(space: str, spec: dict) -> str | None:
    if not spec.get("enabled", True):
        return "disabled"
    if spec.get("generate_agent", True) is False:
        return "external runtime (generation disabled)"
    agent = spec.get("agent", "")
    if not isinstance(agent, str) or not agent.strip():
        return "invalid agent name"
    # Don't overwrite pre-existing hand-curated agents
    for protected in ("brain-coder", "rowboat-chat", "brain-fallback"):
        if agent == protected:
            return f"protected (pre-existing): {agent}"
    return None


def validate_agent_generation_contract() -> list[str]:
    """Validate whether each enabled space should generate an OpenFang agent."""
    with open(REGISTRY, "r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    spaces = data.get("spaces", {})
    if not isinstance(spaces, dict):
        return ["registry spaces must be a mapping"]

    errors: list[str] = []
    for space_name, spec in spaces.items():
        if not isinstance(spec, dict):
            continue
        generate_agent = spec.get("generate_agent", True)
        if not isinstance(generate_agent, bool):
            errors.append(f"{space_name} generate_agent must be a boolean")
            continue
        agent = spec.get("agent")
        if generate_agent is False:
            if agent is not None:
                errors.append(
                    f"{space_name} must omit agent or set it to null when "
                    "generate_agent is false"
                )
            continue
        if spec.get("enabled", True) and (
            not isinstance(agent, str) or not agent.strip()
        ):
            errors.append(
                f"{space_name} requires non-empty agent when generate_agent is true"
            )
    return errors


def validate_generated_agent_mcp_scopes() -> list[str]:
    """Require non-empty top-level AgentManifest MCP scopes for generated agents.

    OpenFang interprets an empty ``mcp_servers`` list as every connected MCP
    server. A no-MCP agent must therefore opt in explicitly with ``no_mcp:
    true`` and an empty list.
    """
    with open(REGISTRY, "r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    spaces = data.get("spaces", {})
    if not isinstance(spaces, dict):
        return ["registry spaces must be a mapping"]

    errors: list[str] = []
    for space_name, spec in spaces.items():
        if not isinstance(spec, dict) or _skip_reason(str(space_name), spec):
            continue
        servers = spec.get("mcp_servers")
        agent = spec.get("agent")
        if spec.get("no_mcp") is True:
            if servers != []:
                errors.append(
                    f"{space_name} declares no_mcp but must use an empty mcp_servers list"
                )
            continue
        if not isinstance(servers, list) or not all(
            isinstance(server, str) and server.strip() for server in servers
        ) or not servers:
            errors.append(
                f"{space_name} requires non-empty mcp_servers for generated agent {agent}"
            )
    return errors


def validate_mcp_tool_scopes() -> list[str]:
    """Require registry MCP tools to stay within an explicit server allowlist."""
    with open(REGISTRY, "r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    spaces = data.get("spaces", {})
    if not isinstance(spaces, dict):
        return ["registry spaces must be a mapping"]

    errors: list[str] = []
    for space_name, spec in spaces.items():
        if not isinstance(spec, dict):
            errors.append(f"space {space_name!r} must be a mapping")
            continue
        mcp_tools = spec.get("mcp_tools", {})
        if not isinstance(mcp_tools, dict):
            errors.append(f"{space_name} mcp_tools must be a mapping")
            continue
        mcp_servers = spec.get("mcp_servers", [])
        allowed_servers = {
            server.strip()
            for server in mcp_servers
            if isinstance(server, str) and server.strip()
        } if isinstance(mcp_servers, list) else set()
        capability_sources: dict[str, tuple[str, str]] = {}
        for server, tools in mcp_tools.items():
            if not isinstance(server, str) or not server.strip():
                errors.append(f"{space_name} mcp_tools server must be a non-empty string")
                continue
            normalized_server = server.strip()
            if normalized_server not in allowed_servers:
                errors.append(
                    f"{space_name} mcp_tools server {normalized_server!r} is not in mcp_servers"
                )
            if not isinstance(tools, list) or not tools:
                errors.append(
                    f"{space_name} mcp_tools for {normalized_server!r} must be a non-empty list"
                )
                continue
            if not all(isinstance(tool, str) and tool.strip() for tool in tools):
                errors.append(
                    f"{space_name} mcp_tools for {normalized_server!r} must contain only non-empty strings"
                )
                continue
            for tool in tools:
                raw_source = (server, tool)
                capability = format_mcp_tool_name(
                    normalized_server, tool.strip()
                )
                previous_source = capability_sources.get(capability)
                if previous_source is None:
                    capability_sources[capability] = raw_source
                elif previous_source != raw_source:
                    errors.append(
                        f"{space_name} mcp_tools normalization collision: "
                        f"{previous_source!r} and {raw_source!r} both map to "
                        f"{capability!r}"
                    )
    return errors


def validate_model_overrides() -> list[str]:
    """Require every registry `model:` override to stay closed and well-typed.

    An unknown key is rejected rather than ignored: silently dropping it would
    generate a manifest that does not carry the dimensioning it was asked for.
    """
    with open(REGISTRY, "r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    spaces = data.get("spaces", {})
    if not isinstance(spaces, dict):
        return ["registry spaces must be a mapping"]

    errors: list[str] = []
    for space_name, spec in spaces.items():
        if not isinstance(spec, dict) or "model" not in spec:
            continue
        override = spec.get("model")
        if not isinstance(override, dict):
            errors.append(f"{space_name} model must be a mapping")
            continue
        unknown = sorted(set(override) - MODEL_OVERRIDE_KEYS)
        if unknown:
            errors.append(
                f"{space_name} model has unsupported keys {unknown}; "
                f"allowed: {sorted(MODEL_OVERRIDE_KEYS)}"
            )
        for key in ("provider", "model"):
            if key not in override:
                continue
            value = override[key]
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{space_name} model {key} must be a non-empty string")
        if "max_tokens" in override:
            value = override["max_tokens"]
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                errors.append(f"{space_name} model max_tokens must be a positive integer")
        if "temperature" in override:
            value = override["temperature"]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"{space_name} model temperature must be a number")
            elif not 0 <= value <= 2:
                errors.append(f"{space_name} model temperature must be between 0 and 2")
    return errors


def validate_mcp_authority() -> list[str]:
    """Validate every registry-declared MCP event before generating agents.

    Top-level AgentManifest ``mcp_servers`` is generated exclusively from
    the registry allowlist.
    A deterministic event must therefore declare a canonical agent, an
    explicit tool, an allowed server, and opaque approval/cost provenance.
    """
    with open(REGISTRY, "r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    spaces = data.get("spaces", {})
    if not isinstance(spaces, dict):
        return ["registry spaces must be a mapping"]

    errors: list[str] = []
    for space_name, space in spaces.items():
        if not isinstance(space, dict):
            errors.append(f"space {space_name!r} must be a mapping")
            continue
        events = space.get("events", {})
        if not isinstance(events, dict):
            continue
        allowed_servers = space.get("mcp_servers", [])
        if not isinstance(allowed_servers, list):
            allowed_servers = []
        allowed = {
            value.strip() for value in allowed_servers
            if isinstance(value, str) and value.strip()
        }
        allowed_tools_by_server = space.get("mcp_tools", {})
        if not isinstance(allowed_tools_by_server, dict):
            allowed_tools_by_server = {}
        for event_name, event in events.items():
            if not isinstance(event, dict):
                continue
            execution = event.get("execution")
            if not isinstance(execution, dict) or execution.get("kind") != "mcp":
                continue
            required = {
                "agent": space.get("agent"),
                "server": execution.get("server"),
                "tool": event.get("tool"),
            }
            for name, value in required.items():
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{space_name}.{event_name} missing {name}")
            server = required["server"]
            if isinstance(server, str) and server.strip() and server.strip() not in allowed:
                errors.append(
                    f"{space_name}.{event_name} server {server.strip()!r} is not in mcp_servers"
                )
            tool = required["tool"]
            server_tools = allowed_tools_by_server.get(server.strip()) if isinstance(server, str) else None
            allowed_tools = {
                value.strip() for value in server_tools
                if isinstance(value, str) and value.strip()
            } if isinstance(server_tools, list) else set()
            if not isinstance(tool, str) or not tool.strip() or tool.strip() not in allowed_tools:
                errors.append(
                    f"{space_name}.{event_name} tool {tool!r} is not in mcp_tools for {server!r}"
                )
            if event.get("required_provenance") != ["approval_ref", "cost_ref"]:
                errors.append(
                    f"{space_name}.{event_name} requires closed required_provenance "
                    "[approval_ref, cost_ref]"
                )
    return errors


def sync(dry_run: bool = False, check: bool = False) -> int:
    validation_errors = (
        validate_agent_generation_contract()
        + validate_generated_agent_mcp_scopes()
        + validate_mcp_tool_scopes()
        + validate_model_overrides()
        + validate_mcp_authority()
    )
    if validation_errors:
        for error in validation_errors:
            print(f"  INVALID {error}")
        return 1
    with open(REGISTRY, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    spaces = data.get("spaces", {}) or {}

    written = 0
    skipped = 0
    drift = 0
    for space, spec in spaces.items():
        reason = _skip_reason(space, spec)
        agent = spec.get("agent")
        agent_label = agent if isinstance(agent, str) else "-"
        if reason:
            print(f"  skip  {space:<12} ({agent_label:<24}) — {reason}")
            skipped += 1
            continue
        target_dir = AGENTS_DIR / agent
        target_file = target_dir / "agent.toml"
        new_content = _render_agent_toml(space, spec)
        existing = target_file.read_text(encoding="utf-8") if target_file.exists() else ""
        if existing == new_content:
            print(f"  ok    {space:<12} -> {agent}")
            continue
        if check:
            print(f"  DRIFT {space:<12} -> {agent}")
            drift += 1
            continue
        if dry_run:
            print(f"  would {space:<12} -> {agent}")
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file.write_text(new_content, encoding="utf-8")
        action = "update" if existing else "create"
        print(f"  {action:<5} {space:<12} -> {agent}")
        written += 1

    print()
    print(f"Summary: {written} written, {skipped} skipped, {drift} drift")
    if check and drift > 0:
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true", help="exit 1 if drift")
    args = ap.parse_args()
    return sync(dry_run=args.dry_run, check=args.check)


if __name__ == "__main__":
    sys.exit(main())
