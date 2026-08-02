#!/usr/bin/env python3
"""Validate the parent LLM routing contract without making network calls."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "llm_config.yml.example"
DEFAULT_BRAIN_CONFIG_PATH = ROOT / "brain" / "the_brain" / "llm_config.yml"
REGISTRY_PATH = ROOT / "config" / "space_agent_registry.yml"

OPENFANG_PROVIDER = {
    "type": "openai",
    "base_url": "${OPENFANG_URL}/v1",
    "key_ref": "openfang",
    "fail_closed": True,
    "max_retries": 3,
    "timeout_seconds": 30,
}
FUNGUS_SEARCH_EMBEDDING = {
    "driver": "openai",
    "provider": "openfang",
    "model": "text-embedding-3-large",
    "dim": 3072,
}
DIRECT_EXCEPTIONS = {
    "voice_realtime": ("openai", "gpt-4o-realtime-preview"),
}
BRAIN_RUNTIME_ROLES = {
    "fast_reasoning",
    "planning",
    "context_tracking",
    "communication",
    "long_term_memory",
    "supermemory",
    "format_generation",
    "brain_fast_reasoning",
    "brain_planning",
    "brain_context_tracking",
    "brain_communication",
    "brain_long_term_memory",
    "brain_supermemory",
    "brain_data_collector",
    "brain_data_collector_anthropic",
}
REQUIRED_SPACE_ROLES = {
    "bubbles": "space_bubbles",
    "ideas": "space_ideas",
    "coding": "space_coding",
    "desktop": "space_desktop",
    "research": "space_research",
    "rowboat": "space_rowboat",
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
    *,
    allow_direct_exception: bool,
) -> list[str]:
    if not isinstance(config, dict):
        return [f"roles.{role} must be a mapping"]

    if allow_direct_exception and role in DIRECT_EXCEPTIONS:
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


def _validate_provider_config(config: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    if config.get("keys") != {"openfang": "${OPENFANG_API_KEY}"}:
        errors.append(f"{label}.keys must contain only the OpenFang key reference")
    if config.get("providers") != {"openfang": OPENFANG_PROVIDER}:
        errors.append(
            f"{label}.providers.openfang must match the fail-closed gateway contract"
        )
    if config.get("overrides") not in ({}, None):
        errors.append(f"{label}.overrides must be empty; role-to-agent routing is canonical")
    return errors


def _validate_fungus_search_embedding(config: dict[str, Any]) -> list[str]:
    """Require the bounded Fungus embedding role to use OpenFang exactly."""
    embeddings = config.get("embeddings")
    if not isinstance(embeddings, dict):
        return ["embeddings must be a mapping"]

    fungus_search = embeddings.get("fungus_search")
    if not isinstance(fungus_search, dict):
        return ["embeddings.fungus_search must be a mapping"]

    errors: list[str] = []
    for field, expected in FUNGUS_SEARCH_EMBEDDING.items():
        if fungus_search.get(field) != expected:
            errors.append(
                f"embeddings.fungus_search.{field} must be {expected!r}"
            )
    unexpected = set(fungus_search) - set(FUNGUS_SEARCH_EMBEDDING)
    if unexpected:
        errors.append(
            "embeddings.fungus_search contains unsupported fields: "
            + ", ".join(sorted(unexpected))
        )
    return errors


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate fail-closed OpenFang LLM config without network calls."
    )
    parser.add_argument("--root-config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--brain-config", type=Path, default=DEFAULT_BRAIN_CONFIG_PATH)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    config = _load_yaml(args.root_config)
    brain_config = _load_yaml(args.brain_config)
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

    errors.extend(_validate_fungus_search_embedding(config))

    default = config.get("default", {})
    errors.extend(
        _validate_role("default", default, known_agents, allow_direct_exception=False)
    )

    roles = config.get("roles", {})
    if not isinstance(roles, dict):
        errors.append("roles must be a mapping")
        roles = {}
    for role, role_config in roles.items():
        errors.extend(
            _validate_role(
                str(role), role_config, known_agents, allow_direct_exception=True
            )
        )

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

    errors.extend(_validate_provider_config(brain_config, "brain runtime config"))
    brain_default = brain_config.get("default")
    if brain_default != default:
        errors.append("brain runtime config.default must match the central default")
    else:
        errors.extend(
            _validate_role(
                "default", brain_default, known_agents, allow_direct_exception=False
            )
        )

    brain_roles = brain_config.get("roles")
    if not isinstance(brain_roles, dict):
        errors.append("brain runtime config.roles must be a mapping")
        brain_roles = {}
    for role in sorted(BRAIN_RUNTIME_ROLES):
        central_role = roles.get(role)
        runtime_role = brain_roles.get(role)
        if central_role is None:
            errors.append(f"roles.{role} is missing from the central config")
            continue
        if runtime_role != central_role:
            errors.append(
                f"brain runtime config.roles.{role} must match the central config"
            )
            continue
        errors.extend(
            _validate_role(
                role, runtime_role, known_agents, allow_direct_exception=False
            )
        )
    unexpected_runtime_roles = set(brain_roles) - BRAIN_RUNTIME_ROLES
    if unexpected_runtime_roles:
        errors.append(
            "brain runtime config contains non-Brain roles: "
            + ", ".join(sorted(unexpected_runtime_roles))
        )

    if errors:
        print("openfang-llm-config-check: FAIL", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(
        "openfang-llm-config-check: PASS "
        f"({len(roles)} central roles, {len(BRAIN_RUNTIME_ROLES)} Brain roles, "
        f"{len(REQUIRED_SPACE_ROLES)} Space routes, "
        f"{len(DIRECT_EXCEPTIONS)} declared global direct exception)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
