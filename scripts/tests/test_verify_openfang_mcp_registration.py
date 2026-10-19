"""Tests for the fail-closed OpenFang MCP registration readback verifier.

The verifier implements the live-proof plane of
``openfang-mcp-authority-v1`` (task-integration-0005b): a registration
claim is valid only with a connected ``GET /api/mcp/servers`` entry and a
correlated per-agent readback from ``GET /api/agents/:id``. These tests
cover the pure evaluation functions against fixture payloads shaped like
the real OpenFang API responses (``openfang-api/src/routes.rs``).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_openfang_mcp_registration.py"

IDEAS_TOOLS = [
    "bubble_list", "bubble_get", "bubble_create", "bubble_update",
    "bubble_delete", "bubble_promote", "idea_list", "idea_get",
    "idea_create", "idea_update", "idea_delete", "idea_connect",
    "idea_disconnect",
]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "verify_openfang_mcp_registration", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve their defining module via sys.modules at class
    # creation time, so the module must be registered before exec_module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_module()


@pytest.fixture()
def contract(tmp_path) -> dict:
    return {
        "contract_id": "openfang-mcp-authority-v1",
        "servers": {
            "spaces-ideas": {
                "registration": "registered",
                "transport": "stdio",
                "tools": list(IDEAS_TOOLS),
            },
            "vibemind-db": {
                "registration": "registered",
                "transport": "stdio",
                "tools": None,
            },
        },
        "spaces": {
            "ideas": {
                "agent_role": "brain-ideas",
                "enabled": True,
                "mcp_servers": ["vibemind-db", "spaces-ideas"],
            },
            "bubbles": {
                "agent_role": "brain-bubbles",
                "enabled": True,
                "mcp_servers": ["vibemind-db", "vibemind", "spaces-ideas"],
            },
        },
    }


@pytest.fixture()
def contract_file(tmp_path, contract) -> Path:
    path = tmp_path / "openfang-mcp-authority-v1.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def _mcp_servers_payload(
    *,
    connected_names=("spaces-ideas", "vibemind-db"),
    ideas_tools=None,
    namespaced=True,
) -> dict:
    if ideas_tools is None:
        ideas_tools = IDEAS_TOOLS
    configured = [
        {"name": "spaces-ideas", "transport": {"type": "stdio"}, "env": []},
        {"name": "vibemind-db", "transport": {"type": "stdio"}, "env": []},
    ]
    connected = []
    for name in connected_names:
        if name == "spaces-ideas":
            tools = [
                {
                    "name": (
                        f"mcp_spaces_ideas_{t}" if namespaced else t
                    ),
                    "description": "",
                }
                for t in ideas_tools
            ]
        else:
            tools = [{"name": "mcp_vibemind_db_db_ideas_get", "description": ""}]
        connected.append(
            {
                "name": name,
                "tools_count": len(tools),
                "tools": tools,
                "connected": True,
            }
        )
    return {
        "configured": configured,
        "connected": connected,
        "total_configured": len(configured),
        "total_connected": len(connected),
    }


def _agent_detail(name: str, scope, mode: str = "allowlist") -> dict:
    return {
        "id": "00000000-0000-0000-0000-000000000000",
        "name": name,
        "mcp_servers": list(scope),
        "mcp_servers_mode": mode,
    }


# ---------------------------------------------------------------------------
# Contract loading / expectations
# ---------------------------------------------------------------------------


def test_expectations_from_contract(mod, contract_file):
    exp = mod.load_expectations(contract_file)
    assert exp.ideas_server == "spaces-ideas"
    assert set(exp.required_connected) == {"spaces-ideas", "vibemind-db"}
    assert exp.ideas_tools == IDEAS_TOOLS
    assert exp.agent_scopes["brain-ideas"] == ["vibemind-db", "spaces-ideas"]
    assert exp.agent_scopes["brain-bubbles"] == [
        "vibemind-db", "vibemind", "spaces-ideas",
    ]


def test_expectations_reject_missing_ideas_tools(mod, tmp_path, contract):
    contract["servers"]["spaces-ideas"]["tools"] = None
    path = tmp_path / "c.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(mod.ContractError):
        mod.load_expectations(path)


# ---------------------------------------------------------------------------
# GET /api/mcp/servers evaluation
# ---------------------------------------------------------------------------


def test_mcp_servers_pass(mod, contract_file):
    exp = mod.load_expectations(contract_file)
    checks = mod.evaluate_mcp_servers(_mcp_servers_payload(), exp)
    assert all(c["ok"] for c in checks), checks


def test_mcp_servers_pass_with_raw_tool_names(mod, contract_file):
    exp = mod.load_expectations(contract_file)
    payload = _mcp_servers_payload(namespaced=False)
    checks = mod.evaluate_mcp_servers(payload, exp)
    assert all(c["ok"] for c in checks), checks


def test_mcp_servers_fail_when_ideas_not_connected(mod, contract_file):
    exp = mod.load_expectations(contract_file)
    payload = _mcp_servers_payload(connected_names=("vibemind-db",))
    checks = mod.evaluate_mcp_servers(payload, exp)
    failed = [c for c in checks if not c["ok"]]
    assert failed
    assert any("spaces-ideas" in c["check"] for c in failed)


def test_mcp_servers_fail_when_db_not_connected(mod, contract_file):
    exp = mod.load_expectations(contract_file)
    payload = _mcp_servers_payload(connected_names=("spaces-ideas",))
    checks = mod.evaluate_mcp_servers(payload, exp)
    assert any(not c["ok"] and "vibemind-db" in c["check"] for c in checks)


def test_mcp_servers_fail_on_missing_tool(mod, contract_file):
    exp = mod.load_expectations(contract_file)
    payload = _mcp_servers_payload(ideas_tools=IDEAS_TOOLS[:-1])
    checks = mod.evaluate_mcp_servers(payload, exp)
    failed = [c for c in checks if not c["ok"]]
    assert failed
    assert any("idea_disconnect" in json.dumps(c) for c in failed)


def test_mcp_servers_fail_on_unexpected_extra_tool(mod, contract_file):
    exp = mod.load_expectations(contract_file)
    payload = _mcp_servers_payload(ideas_tools=IDEAS_TOOLS + ["rogue_tool"])
    checks = mod.evaluate_mcp_servers(payload, exp)
    assert any(not c["ok"] for c in checks)


def test_mcp_servers_fail_closed_on_malformed_payload(mod, contract_file):
    exp = mod.load_expectations(contract_file)
    checks = mod.evaluate_mcp_servers({}, exp)
    assert checks and all(not c["ok"] for c in checks)


# ---------------------------------------------------------------------------
# GET /api/agents/:id evaluation
# ---------------------------------------------------------------------------


def test_agent_detail_pass(mod, contract_file):
    exp = mod.load_expectations(contract_file)
    detail = _agent_detail("brain-ideas", ["vibemind-db", "spaces-ideas"])
    checks = mod.evaluate_agent_detail(
        "brain-ideas", detail, exp.agent_scopes["brain-ideas"]
    )
    assert all(c["ok"] for c in checks), checks


def test_agent_detail_fail_on_mode_all(mod, contract_file):
    exp = mod.load_expectations(contract_file)
    detail = _agent_detail("brain-ideas", [], mode="all")
    checks = mod.evaluate_agent_detail(
        "brain-ideas", detail, exp.agent_scopes["brain-ideas"]
    )
    assert any(not c["ok"] and "allowlist" in c["check"] for c in checks)


def test_agent_detail_fail_on_scope_mismatch(mod, contract_file):
    exp = mod.load_expectations(contract_file)
    detail = _agent_detail("brain-ideas", ["vibemind-db"])
    checks = mod.evaluate_agent_detail(
        "brain-ideas", detail, exp.agent_scopes["brain-ideas"]
    )
    assert any(not c["ok"] and "scope" in c["check"] for c in checks)


def test_agent_detail_fail_on_extra_scope_entry(mod, contract_file):
    exp = mod.load_expectations(contract_file)
    detail = _agent_detail(
        "brain-ideas", ["vibemind-db", "spaces-ideas", "filesystem"]
    )
    checks = mod.evaluate_agent_detail(
        "brain-ideas", detail, exp.agent_scopes["brain-ideas"]
    )
    assert any(not c["ok"] for c in checks)


# ---------------------------------------------------------------------------
# Agent listing resolution
# ---------------------------------------------------------------------------


def test_resolve_agent_ids(mod):
    listing = [
        {"id": "aaa", "name": "brain-ideas"},
        {"id": "bbb", "name": "brain-bubbles"},
        {"id": "ccc", "name": "brain-coder"},
    ]
    resolved = mod.resolve_agent_ids(listing, ["brain-ideas", "brain-bubbles"])
    assert resolved == {"brain-ideas": "aaa", "brain-bubbles": "bbb"}


def test_resolve_agent_ids_missing_role(mod):
    listing = [{"id": "ccc", "name": "brain-coder"}]
    resolved = mod.resolve_agent_ids(listing, ["brain-ideas"])
    assert resolved == {}


# ---------------------------------------------------------------------------
# Evidence assembly
# ---------------------------------------------------------------------------


def test_evidence_overall_fails_if_any_check_fails(mod):
    checks = [
        {"check": "a", "ok": True, "detail": ""},
        {"check": "b", "ok": False, "detail": "boom"},
    ]
    evidence = mod.build_evidence(
        base_url="http://127.0.0.1:4200",
        checks=checks,
        raw={},
        generated_utc="2026-08-13T00:00:00Z",
    )
    assert evidence["overall"] == "fail"
    assert evidence["live_claim"] is False


def test_evidence_overall_pass(mod):
    checks = [{"check": "a", "ok": True, "detail": ""}]
    evidence = mod.build_evidence(
        base_url="http://127.0.0.1:4200",
        checks=checks,
        raw={"servers": {"x": 1}},
        generated_utc="2026-08-13T00:00:00Z",
    )
    assert evidence["overall"] == "pass"
    assert evidence["live_claim"] is True
    assert "sha256" in evidence["raw"]["servers"]


@pytest.mark.parametrize(
    ("configured", "reachable", "verified_live", "expected"),
    [
        (False, False, False, "unavailable"),
        (True, False, False, "configured"),
        (True, True, False, "reachable"),
        (True, True, True, "verified_live"),
    ],
)
def test_registration_evidence_levels_are_explicit(
    mod, configured, reachable, verified_live, expected
):
    assert mod.registration_evidence_level(
        configured=configured,
        reachable=reachable,
        verified_live=verified_live,
    ) == expected


def test_evidence_reports_reachable_without_claiming_live(mod):
    evidence = mod.build_evidence(
        base_url="http://127.0.0.1:4200",
        checks=[{"check": "scope", "ok": False, "detail": "mismatch"}],
        raw={"health": {"ok": True}},
        generated_utc="2026-08-24T00:00:00Z",
    )

    assert evidence["evidence_level"] == "reachable"
    assert evidence["live_claim"] is False
