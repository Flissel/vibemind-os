from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import yaml

from spaces.learning.contracts.events import LearningToolName


REPO_ROOT = Path(__file__).resolve().parents[4]
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_openfang_agents.py"
MANIFEST = REPO_ROOT / "openfang" / "agents" / "brain-learning" / "agent.toml"


def _sync_module():
    spec = importlib.util.spec_from_file_location("learning_sync_openfang", SYNC_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_brain_learning_manifest_is_registry_derived_and_exact() -> None:
    module = _sync_module()
    registry = yaml.safe_load(module.REGISTRY.read_text(encoding="utf-8"))
    learning = registry["spaces"]["learning"]
    rendered = module._render_agent_toml("learning", learning)
    committed = MANIFEST.read_text(encoding="utf-8")
    manifest = tomllib.loads(committed)

    expected_tools = {
        module.format_mcp_tool_name("spaces-learning", tool.value)
        for tool in LearningToolName
    }
    assert committed == rendered
    assert manifest["name"] == "brain-learning"
    assert manifest["mcp_servers"] == ["spaces-learning"]
    assert set(manifest["capabilities"]["tools"]) == {
        "memory_store",
        "memory_recall",
        *expected_tools,
    }


def test_learning_manifest_has_no_foreign_server_or_provider_authority() -> None:
    manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    tools = manifest["capabilities"]["tools"]

    assert manifest["mcp_servers"] == ["spaces-learning"]
    assert not any("filesystem" in tool or "vibemind_db" in tool for tool in tools)
    assert "OPENAI_API_KEY" not in MANIFEST.read_text(encoding="utf-8")
