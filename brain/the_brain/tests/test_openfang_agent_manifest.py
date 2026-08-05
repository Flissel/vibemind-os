"""Contract tests for static OpenFang MCP scope manifest consumption."""

from __future__ import annotations

import pytest

from core.openfang_agent_manifest import extract_mcp_servers, load_mcp_servers


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
