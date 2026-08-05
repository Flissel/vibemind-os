"""Fail-closed MCP server scope reads for OpenFang agent manifests."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _validated_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    servers: list[str] = []
    seen: set[str] = set()
    for server in value:
        if not isinstance(server, str) or not server.strip():
            return []
        if server not in seen:
            seen.add(server)
            servers.append(server)
    return servers


def extract_mcp_servers(manifest: Mapping[str, object]) -> list[str]:
    """Return the declared MCP server scope with canonical precedence."""
    for container, key in (
        (manifest, "mcp_servers"),
        (_mapping(manifest.get("capabilities")), "mcp_servers"),
        (_mapping(manifest.get("mcp_allowed")), "servers"),
    ):
        if key in container:
            return _validated_string_list(container[key])
    return []


def load_mcp_servers(path: str | os.PathLike[str]) -> list[str]:
    """Load an agent manifest's MCP server scope without raising."""
    try:
        with open(path, "rb") as handle:
            manifest = tomllib.load(handle)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return []
    return extract_mcp_servers(manifest)
