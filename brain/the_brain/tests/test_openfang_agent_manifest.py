"""Contract tests for static OpenFang MCP scope manifest consumption."""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from core.capability_discovery import discover_agents
from core.openfang_agent_manifest import extract_mcp_servers, load_mcp_servers
from web.routers import introspection


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        (
            {"mcp_servers": ["canonical"], "mcp_allowed": {"servers": ["legacy"]}},
            ["canonical"],
        ),
        (
            {
                "mcp_servers": [],
                "capabilities": {"mcp_servers": ["transitional"]},
            },
            [],
        ),
        ({"capabilities": {"mcp_servers": ["transitional"]}}, ["transitional"]),
        ({"mcp_allowed": {"servers": ["legacy"]}}, ["legacy"]),
        ({"mcp_servers": "all"}, []),
        ({"mcp_servers": ["valid", 3]}, []),
        ({"mcp_servers": ["valid", "   "]}, []),
        (
            {"mcp_servers": ["spaces-ideas", "spaces-ideas", "spaces-other"]},
            ["spaces-ideas", "spaces-other"],
        ),
    ],
)
def test_extract_mcp_servers_is_canonical_and_fail_closed(manifest, expected):
    assert extract_mcp_servers(manifest) == expected


def test_load_mcp_servers_reads_valid_toml_and_rejects_malformed_toml(tmp_path):
    valid = tmp_path / "valid.toml"
    valid.write_text('mcp_servers = ["canonical"]\n', encoding="utf-8")
    malformed = tmp_path / "malformed.toml"
    malformed.write_text('mcp_servers = ["unterminated"\n', encoding="utf-8")

    assert load_mcp_servers(valid) == ["canonical"]
    assert load_mcp_servers(malformed) == []


def test_extract_mcp_servers_returns_an_isolated_list():
    declared = ["spaces-ideas"]

    extracted = extract_mcp_servers({"mcp_servers": declared})
    extracted.append("spaces-other")

    assert declared == ["spaces-ideas"]


def test_load_mcp_servers_rejects_invalid_utf8(tmp_path):
    manifest = tmp_path / "agent.toml"
    manifest.write_bytes(b'mcp_servers = ["\xff"]\n')

    assert load_mcp_servers(manifest) == []


def _write_agent_manifest(
    agents_dir,
    name="brain-ideas",
    mcp_servers='["spaces-ideas"]',
):
    agent_dir = agents_dir / name
    agent_dir.mkdir()
    (agent_dir / "agent.toml").write_text(
        "\n".join(
            [
                f'name = "{name}"',
                'description = "Ideas agent"',
                'tags = ["space:ideas"]',
                f"mcp_servers = {mcp_servers}",
                '[model]',
                'provider = "test"',
                'model = "test-model"',
                '[capabilities]',
                'mcp_servers = ["transitional"]',
                '[mcp_allowed]',
                'servers = ["legacy"]',
            ]
        ),
        encoding="utf-8",
    )
    return agent_dir


def test_discover_agents_uses_canonical_mcp_servers(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent_manifest(agents_dir)

    assert discover_agents(str(agents_dir))[0]["mcp_servers"] == ["spaces-ideas"]


def test_openfang_agent_loader_uses_canonical_mcp_servers(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent_manifest(agents_dir)

    assert introspection._load_openfang_agents(str(agents_dir))[0]["mcp_servers"] == [
        "spaces-ideas"
    ]


def test_openfang_agent_loader_skips_invalid_utf8(tmp_path):
    agents_dir = tmp_path / "agents"
    agent_dir = agents_dir / "invalid-agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.toml").write_bytes(b'name = "\xff"\n')

    assert introspection._load_openfang_agents(str(agents_dir)) == []


def test_openfang_agent_loader_parses_each_manifest_once(monkeypatch, tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent_manifest(agents_dir)

    def reject_second_read(path):
        raise AssertionError(f"unexpected second manifest read: {path}")

    monkeypatch.setattr(introspection, "load_mcp_servers", reject_second_read)

    assert introspection._load_openfang_agents(str(agents_dir))[0]["mcp_servers"] == [
        "spaces-ideas"
    ]


def _install_introspection_dependencies(monkeypatch):
    registry = SimpleNamespace(
        reload_if_changed=lambda: None,
        stats_dict=lambda: {"events_total": 1},
        get_event_agent=lambda event_id: None,
        get_agent_events=lambda agent_name: [],
    )
    discovery = SimpleNamespace(
        list_servers=lambda: [],
        stats_dict=lambda: {},
        find_tool_for_event=lambda event_id, servers: None,
        all_tools_flat=lambda: [],
    )
    monkeypatch.setitem(
        sys.modules,
        "core.agent_yaml_registry",
        SimpleNamespace(get_registry=lambda: registry),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.mcp_discovery",
        SimpleNamespace(get_discovery=lambda: discovery),
    )
    return discovery


def _request_with_empty_events():
    client = SimpleNamespace(scroll=lambda **kwargs: ([], None))
    kg = SimpleNamespace(client=client)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(qdrant_kg=kg)))


def test_events_mapping_uses_shared_openfang_agent_loader(monkeypatch):
    _install_introspection_dependencies(monkeypatch)
    calls = []
    monkeypatch.setattr(
        introspection,
        "_load_openfang_agents",
        lambda: calls.append(True) or [],
        raising=False,
    )

    response = asyncio.run(introspection.events_mapping(_request_with_empty_events()))

    assert response.status_code == 200
    assert calls == [True]


def test_events_mapping_xlsx_uses_shared_openfang_agent_loader(monkeypatch):
    pytest.importorskip("openpyxl")
    _install_introspection_dependencies(monkeypatch)
    calls = []
    monkeypatch.setattr(
        introspection,
        "_load_openfang_agents",
        lambda: calls.append(True) or [],
        raising=False,
    )

    response = asyncio.run(introspection.events_mapping_xlsx(_request_with_empty_events()))

    assert response.status_code == 200
    assert calls == [True]


def test_agent_tools_uses_canonical_mcp_servers(monkeypatch, tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent_manifest(agents_dir, name="test-canonical-agent")
    monkeypatch.setattr(
        introspection, "_OPENFANG_AGENTS_DIR", str(agents_dir), raising=False
    )
    calls = []
    discovery = SimpleNamespace(
        list_tools=lambda server: calls.append(server) or [{"name": "idea.create"}]
    )
    monkeypatch.setitem(
        sys.modules,
        "core.mcp_discovery",
        SimpleNamespace(get_discovery=lambda: discovery),
    )

    response = asyncio.run(introspection.agent_tools(None, "test-canonical-agent"))

    assert json.loads(response.body) == {
        "ok": True,
        "agent": "test-canonical-agent",
        "mcp_servers": ["spaces-ideas"],
        "tools_by_server": {"spaces-ideas": [{"name": "idea.create"}]},
    }
    assert calls == ["spaces-ideas"]


def test_agent_tools_rejects_invalid_utf8_without_500(monkeypatch, tmp_path):
    agents_dir = tmp_path / "agents"
    agent_dir = agents_dir / "invalid-agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.toml").write_bytes(b'mcp_servers = ["\xff"]\n')
    monkeypatch.setattr(introspection, "_OPENFANG_AGENTS_DIR", str(agents_dir))
    calls = []
    discovery = SimpleNamespace(list_tools=lambda server: calls.append(server) or [])
    monkeypatch.setitem(
        sys.modules,
        "core.mcp_discovery",
        SimpleNamespace(get_discovery=lambda: discovery),
    )

    response = asyncio.run(introspection.agent_tools(None, "invalid-agent"))

    assert response.status_code == 200
    assert json.loads(response.body) == {
        "ok": True,
        "agent": "invalid-agent",
        "mcp_servers": [],
        "tools_by_server": {},
    }
    assert calls == []


@pytest.mark.parametrize("path_kind", ["traversal", "nested", "absolute"])
def test_agent_tools_rejects_non_child_agent_paths(
    monkeypatch, tmp_path, path_kind
):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    if path_kind == "traversal":
        _write_agent_manifest(tmp_path, name="outside")
        agent_name = "..\\outside"
    elif path_kind == "nested":
        nested_dir = agents_dir / "nested"
        nested_dir.mkdir()
        _write_agent_manifest(nested_dir, name="agent")
        agent_name = "nested\\agent"
    else:
        absolute_dir = _write_agent_manifest(tmp_path, name="absolute")
        agent_name = str(absolute_dir.resolve())
    monkeypatch.setattr(introspection, "_OPENFANG_AGENTS_DIR", str(agents_dir))
    calls = []
    discovery = SimpleNamespace(
        list_tools=lambda server: calls.append(f"list:{server}") or []
    )
    monkeypatch.setitem(
        sys.modules,
        "core.mcp_discovery",
        SimpleNamespace(
            get_discovery=lambda: calls.append("get") or discovery,
        ),
    )

    response = asyncio.run(introspection.agent_tools(None, agent_name))

    assert response.status_code == 404
    assert json.loads(response.body) == {"ok": False, "error": "agent not found"}
    assert calls == []


def test_agent_tools_deduplicates_server_discovery_calls(monkeypatch, tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent_manifest(
        agents_dir,
        name="duplicate-agent",
        mcp_servers='["spaces-ideas", "spaces-ideas", "spaces-other"]',
    )
    monkeypatch.setattr(introspection, "_OPENFANG_AGENTS_DIR", str(agents_dir))
    calls = []
    discovery = SimpleNamespace(list_tools=lambda server: calls.append(server) or [])
    monkeypatch.setitem(
        sys.modules,
        "core.mcp_discovery",
        SimpleNamespace(get_discovery=lambda: discovery),
    )

    response = asyncio.run(introspection.agent_tools(None, "duplicate-agent"))

    assert response.status_code == 200
    assert json.loads(response.body)["mcp_servers"] == ["spaces-ideas", "spaces-other"]
    assert calls == ["spaces-ideas", "spaces-other"]


def test_agent_tools_rejects_blank_server_before_list_tools(monkeypatch, tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent_manifest(
        agents_dir,
        name="blank-agent",
        mcp_servers='["spaces-ideas", "   "]',
    )
    monkeypatch.setattr(introspection, "_OPENFANG_AGENTS_DIR", str(agents_dir))
    calls = []
    discovery = SimpleNamespace(list_tools=lambda server: calls.append(server) or [])
    monkeypatch.setitem(
        sys.modules,
        "core.mcp_discovery",
        SimpleNamespace(get_discovery=lambda: discovery),
    )

    response = asyncio.run(introspection.agent_tools(None, "blank-agent"))

    assert response.status_code == 200
    assert json.loads(response.body)["mcp_servers"] == []
    assert calls == []
