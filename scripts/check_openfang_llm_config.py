#!/usr/bin/env python3
"""Validate the parent LLM routing contract without making network calls."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "llm_config.yml.example"
REGISTRY_PATH = ROOT / "config" / "space_agent_registry.yml"

OPENFANG_PROVIDER = {
    "type": "openai",
    "base_url": "http://127.0.0.1:4200/v1",
    "key_ref": "openfang",
    "fail_closed": True,
    "max_retries": 3,
    "timeout_seconds": 30,
}
DIRECT_EXCEPTIONS = {
    "voice_realtime": ("openai", "gpt-4o-realtime-preview"),
}
REQUIRED_SPACE_ROLES = {
    "bubbles": "space_bubbles",
    "ideas": "space_ideas",
    "coding": "space_coding",
    "desktop": "space_desktop",
    "research": "space_research",
    "roarboot": "space_rowboat",
    "minibook": "space_minibook",
    "schedule": "space_schedule",
    "n8n": "space_n8n",
    "agentfarm": "space_agentfarm",
    "video": "space_video",
    "flowzen": "space_flowzen",
    "mirofish": "space_mirofish",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    if not isinstance(document, dict):
        raise ValueError(f"{path.name} must contain a mapping")
    return document


def _validate_role(
    role: str,
    config: Any,
    known_agents: set[str],
) -> list[str]:
    if not isinstance(config, dict):
        return [f"roles.{role} must be a mapping"]

    if role in DIRECT_EXCEPTIONS:
        expected_provider, expected_model = DIRECT_EXCEPTIONS[role]
        errors = []
        if config.get("provider") != expected_provider:
            errors.append(
                f"roles.{role}.provider must be declared exception {expected_provider!r}"
            )
        if config.get("model") != expected_model:
            errors.append(
                f"roles.{role}.model must be declared exception {expected_model!r}"
            )
        return errors

    errors = []
    if config.get("provider") != "openfang":
        errors.append(f"roles.{role}.provider must be 'openfang'")
    model = config.get("model")
    if not isinstance(model, str) or not model.startswith("openfang:"):
        errors.append(f"roles.{role}.model must use the openfang:<agent> namespace")
    elif model.removeprefix("openfang:") not in known_agents:
        errors.append(f"roles.{role}.model references unknown agent {model!r}")
    return errors


def main() -> int:
    config = _load_yaml(CONFIG_PATH)
    registry = _load_yaml(REGISTRY_PATH)
    spaces = registry.get("spaces", {})
    known_agents = {
        spec.get("agent")
        for spec in spaces.values()
        if isinstance(spec, dict) and isinstance(spec.get("agent"), str)
    }
    known_agents.update(
        {
            "assistant",
            "brain-fallback",
            "brain-knowledge",
            "brain-orchestrator",
            "brain-planner",
            "brain-researcher",
            "brain-security",
            "brain-writer",
            "email-assistant",
            "writer",
        }
    )

    errors: list[str] = []
    if set(config.get("keys", {})) != {"openfang", "openai"}:
        errors.append("keys must contain only openfang and the voice-realtime OpenAI exception")
    providers = config.get("providers", {})
    if set(providers) != {"openfang", "openai"}:
        errors.append(
            "providers must contain only openfang and the voice-realtime OpenAI exception"
        )
    if providers.get("openfang") != OPENFANG_PROVIDER:
        errors.append("providers.openfang does not match the fail-closed gateway contract")

    default = config.get("default", {})
    errors.extend(_validate_role("default", default, known_agents))

    roles = config.get("roles", {})
    if not isinstance(roles, dict):
        errors.append("roles must be a mapping")
        roles = {}
    for role, role_config in roles.items():
        errors.extend(_validate_role(str(role), role_config, known_agents))

    for space, role in REQUIRED_SPACE_ROLES.items():
        spec = spaces.get(space, {})
        expected_agent = spec.get("agent") if isinstance(spec, dict) else None
        actual = roles.get(role, {})
        if actual.get("model") != f"openfang:{expected_agent}":
            errors.append(
                f"roles.{role}.model must follow registry space {space!r}"
            )

    overrides = config.get("overrides", {})
    if overrides not in ({}, None):
        errors.append("overrides must be empty; role-to-agent routing is canonical")

    if errors:
        print("openfang-llm-config-check: FAIL", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(
        "openfang-llm-config-check: PASS "
        f"({len(roles)} roles, {len(REQUIRED_SPACE_ROLES)} Space routes, "
        f"{len(DIRECT_EXCEPTIONS)} declared direct exception)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
