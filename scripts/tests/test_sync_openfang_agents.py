from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "sync_openfang_agents.py"


def _load_sync_module():
    spec = importlib.util.spec_from_file_location("sync_openfang_agents", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_registry_mcp_authority_requires_allowed_server_and_opaque_provenance(tmp_path, monkeypatch):
    module = _load_sync_module()
    registry = tmp_path / "space_agent_registry.yml"
    registry.write_text(
        """version: 1
spaces:
  ideas:
    agent: brain-ideas
    enabled: true
    mcp_servers: [vibemind-db]
    events:
      idea.create:
        tool: db_ideas_create
        execution:
          kind: mcp
          server: spaces-ideas
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "REGISTRY", registry)

    errors = module.validate_mcp_authority()

    assert any("mcp_servers" in error for error in errors)
    assert any("mcp_tools" in error for error in errors)
    assert any("approval_ref" in error for error in errors)
    assert any("cost_ref" in error for error in errors)
