"""AgentFarm space contract must stay canonical and fail closed.

The historical ``spaces/autogen`` implementation was removed and its team/run
state lived only in memory.  Until a versioned runtime source is present, the
Brain may understand AgentFarm intents but must not advertise an executable
target or route them to generic database tools.
"""

import ast
import json
from pathlib import Path
import sys
import tomllib

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.capability_router import CapabilityRouter


ROOT = Path(__file__).resolve().parents[3]
SPACE_REGISTRY = ROOT / "config" / "space_agent_registry.yml"
LLM_CONFIG = ROOT / "llm_config.yml.example"
CAPABILITIES = Path(__file__).resolve().parents[1] / "data" / "capabilities.yaml"
AGENT_MANIFEST = (
    Path(__file__).resolve().parents[1] / "configs" / "agents" / "brain-orchestrator.yaml"
)
RUNTIME_SOURCE = ROOT / "spaces" / "agentfarm"
RUNTIME_MANIFEST = RUNTIME_SOURCE / "runtime-manifest-v1.json"

OPERATIONS = {
    "create_team",
    "run",
    "status",
    "list_teams",
    "stop",
    "results",
    "list_templates",
    "collaborate",
}

ORDERED_OPERATIONS = [
    "create_team",
    "run",
    "status",
    "list_teams",
    "stop",
    "results",
    "list_templates",
    "collaborate",
]

RUNTIME_MANIFEST_V1 = {
    "schema_version": 1,
    "space_id": "agentfarm",
    "source_status": "versioned",
    "runtime_status": "blocked",
    "enabled": False,
    "executable_entrypoint": None,
    "operations": ORDERED_OPERATIONS,
    "blocker": "missing_persistent_agentfarm_runtime",
    "nonclaims": ["no_execution", "no_activation", "no_mcp", "no_deployment"],
}


def _space_contract():
    registry = yaml.safe_load(SPACE_REGISTRY.read_text(encoding="utf-8"))
    return registry["spaces"]["agentfarm"]


def _agentfarm_capabilities():
    capabilities = yaml.safe_load(CAPABILITIES.read_text(encoding="utf-8"))
    return {
        item["capability"]: item
        for item in capabilities
        if item.get("space") == "agentfarm"
    }


def test_agentfarm_is_canonical_with_autogen_as_legacy_alias():
    contract = _space_contract()

    assert contract["aliases"] == ["autogen"]
    assert contract["prefixes"] == ["agentfarm.", "autogen."]


def test_agentfarm_reserves_its_future_chat_identity_without_execution_scope():
    contract = _space_contract()

    assert contract["reserved_chat_agent"] == "brain-agentfarm"
    assert contract["agent"] == "vibemind"
    assert contract["enabled"] is False
    assert contract["mcp_servers"] == []
    assert contract["runtime"] == {
        "status": "blocked",
        "source_path": "spaces/agentfarm",
        "manifest_path": "spaces/agentfarm/runtime-manifest-v1.json",
        "blocker": "missing_persistent_agentfarm_runtime",
    }


def test_agentfarm_llm_role_uses_the_reserved_chat_identity() -> None:
    """LLM configuration may reserve chat identity but not create execution scope."""
    contract = _space_contract()
    config = yaml.safe_load(LLM_CONFIG.read_text(encoding="utf-8"))

    assert contract["enabled"] is False
    assert config["roles"]["space_agentfarm"]["provider"] == "openfang"
    assert config["roles"]["space_agentfarm"]["model"] == (
        f"openfang:{contract['reserved_chat_agent']}"
    )


def test_agentfarm_registry_is_disabled_while_the_versioned_source_has_no_runtime():
    contract = _space_contract()

    assert contract["enabled"] is False
    assert contract["runtime"]["status"] == "blocked"
    source_path = ROOT / contract["runtime"]["source_path"]
    assert source_path == RUNTIME_SOURCE
    assert source_path.is_dir()
    assert (source_path / "pyproject.toml").is_file()
    assert (source_path / "agentfarm" / "__init__.py").is_file()


def test_agentfarm_runtime_manifest_v1_is_closed_and_non_executable():
    manifest = json.loads(RUNTIME_MANIFEST.read_text(encoding="utf-8"))

    assert manifest == RUNTIME_MANIFEST_V1


def test_agentfarm_source_has_minimal_dependency_free_python_metadata():
    metadata = tomllib.loads((RUNTIME_SOURCE / "pyproject.toml").read_text(encoding="utf-8"))
    package_init = (RUNTIME_SOURCE / "agentfarm" / "__init__.py").read_text(
        encoding="utf-8"
    )
    package_tree = ast.parse(package_init)

    assert metadata == {
        "project": {
            "name": "vibemind-agentfarm-runtime-source",
            "version": "0.1.0",
            "requires-python": ">=3.11",
            "dependencies": [],
        }
    }
    assert len(package_tree.body) == 1
    statement = package_tree.body[0]
    assert isinstance(statement, ast.Expr)
    assert isinstance(statement.value, ast.Constant)
    assert isinstance(statement.value.value, str)


def test_agentfarm_events_name_real_operations_not_generic_database_tools():
    events = _space_contract()["events"]

    assert set(events) == {f"agentfarm.{operation}" for operation in OPERATIONS}
    assert {event["operation"] for event in events.values()} == OPERATIONS
    assert all("tool" not in event for event in events.values())


def test_brain_contract_has_no_execution_target_without_runtime_artifacts():
    capabilities = _agentfarm_capabilities()

    assert set(capabilities) == {f"agentfarm_{operation}" for operation in OPERATIONS}
    for capability in capabilities.values():
        assert capability["enabled"] is False
        assert "execution_target" not in capability
        assert capability["runtime_blocker"] == "missing_persistent_agentfarm_runtime"
        assert capability["description"] == (
            "AgentFarm runtime source is versioned, but the persistent runtime is missing"
        )


def test_disabled_capabilities_are_not_routable(tmp_path):
    registry = tmp_path / "capabilities.yaml"
    registry.write_text(
        yaml.safe_dump(
            [
                {
                    "capability": "agentfarm_create_team",
                    "description": "blocked until runtime exists",
                    "enabled": False,
                    "match_patterns": ["create agent team"],
                    "agents": {"primary": ["vibemind"]},
                }
            ]
        ),
        encoding="utf-8",
    )

    router = CapabilityRouter(registry)

    assert router.route("create agent team alpha") is None


def test_removed_agentfarm_worker_does_not_claim_events():
    manifest = yaml.safe_load(AGENT_MANIFEST.read_text(encoding="utf-8"))

    assert manifest == {
        "agent": "brain-orchestrator",
        "description": "Dormant AgentFarm orchestrator manifest",
        "default_namespace": "",
        "events": [],
        "fallback_agent": "",
        "notes": (
            "AgentFarm versioned source exists; persistent runtime missing; "
            "do not claim events until a real worker is deployed."
        ),
    }


def test_removed_autogen_space_is_not_resurrected_by_the_versioned_source():
    assert not (ROOT / "spaces" / "autogen").exists()
    assert all(path.name != "autogen" for path in RUNTIME_SOURCE.iterdir())
