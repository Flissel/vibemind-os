#!/usr/bin/env python
"""Fail-closed readback verifier for the OpenFang MCP live registration.

Implements the live-proof plane of the ``openfang-mcp-authority-v1``
contract (owned by ``task-integration-0005b``): a registration claim is
valid only when

1. ``GET /api/health`` answers (runtime reachable),
2. ``GET /api/mcp/servers`` lists the golden-path servers
   (``spaces-ideas``, ``vibemind-db``) as **connected**, with the
   ``spaces-ideas`` tool list exactly matching the contract's 13 tools,
3. ``GET /api/agents`` + ``GET /api/agents/:id`` show the golden-path
   agent roles (``brain-ideas``, ``brain-bubbles``) running with
   ``mcp_servers_mode == "allowlist"`` and exactly the contract scopes.

If the runtime is unreachable the verifier exits ``2`` and explicitly
reports fail-closed: **no live claim**. Any failed check exits ``1``.
Exit ``0`` means every check passed; the correlated evidence JSON
(timestamps, endpoint, raw-response SHA-256 digests, per-check verdicts)
is written to ``--evidence-out`` for the governance record.

The verifier performs read-only GETs. It never registers, mutates,
spawns, or writes anything into OpenFang or any space data plane.

Usage:
    python scripts/verify_openfang_mcp_registration.py \
        [--base-url http://127.0.0.1:4200] \
        [--contract PATH/to/openfang-mcp-authority-v1.json] \
        [--evidence-out PATH.json]

Without ``--contract`` the script walks up from its own location looking
for ``shared/contracts/openfang-mcp-authority-v1.json`` (the superproject
layout). An ``OPENFANG_API_KEY`` environment variable, when set, is sent
as a ``Authorization: Bearer`` header.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONTRACT_FILENAME = "openfang-mcp-authority-v1.json"
DEFAULT_BASE_URL = "http://127.0.0.1:4200"
REQUEST_TIMEOUT_SECONDS = 10


def _load_openfang_api_key_fallback() -> None:
    """Fill OPENFANG_API_KEY from the repo .env if the caller's shell never
    sourced it (D1 Stufe 2, 2026-09-22 live incident: this verifier hit a
    real 401 from a shell that had the key in .env but not exported --
    this script only ever read os.environ, never .env itself)."""
    if os.environ.get("OPENFANG_API_KEY"):
        return
    here = Path(__file__).resolve()
    repo_root = next((p for p in (here, *here.parents) if (p / "vibemind-os").is_dir()), None)
    if repo_root is None:
        return
    env_file = repo_root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("OPENFANG_API_KEY="):
            os.environ["OPENFANG_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
            break


_load_openfang_api_key_fallback()

GOLDEN_PATH_SERVER = "spaces-ideas"
GOLDEN_PATH_DB = "vibemind-db"
GOLDEN_PATH_SPACES = ("ideas", "bubbles")

EXIT_OK = 0
EXIT_CHECK_FAILED = 1
EXIT_UNREACHABLE = 2


class ContractError(RuntimeError):
    """The contract JSON does not carry the data the verifier needs."""


@dataclass
class Expectations:
    ideas_server: str
    ideas_tools: list[str]
    required_connected: list[str]
    agent_scopes: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Contract loading
# ---------------------------------------------------------------------------


def discover_contract(start: Path) -> Path | None:
    for parent in [start, *start.resolve().parents]:
        candidate = parent / "shared" / "contracts" / CONTRACT_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_expectations(contract_path: Path) -> Expectations:
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read contract {contract_path}: {exc}") from exc

    servers = contract.get("servers") or {}
    ideas = servers.get(GOLDEN_PATH_SERVER) or {}
    tools = ideas.get("tools")
    if not isinstance(tools, list) or not tools:
        raise ContractError(
            f"contract has no authoritative tool inventory for {GOLDEN_PATH_SERVER}"
        )

    spaces = contract.get("spaces") or {}
    agent_scopes: dict[str, list[str]] = {}
    for space_name in GOLDEN_PATH_SPACES:
        space = spaces.get(space_name) or {}
        role = space.get("agent_role")
        scope = space.get("mcp_servers")
        if not role or not isinstance(scope, list) or not scope:
            raise ContractError(
                f"contract space '{space_name}' lacks agent_role/mcp_servers"
            )
        agent_scopes[role] = list(scope)

    return Expectations(
        ideas_server=GOLDEN_PATH_SERVER,
        ideas_tools=list(tools),
        required_connected=[GOLDEN_PATH_SERVER, GOLDEN_PATH_DB],
        agent_scopes=agent_scopes,
    )


# ---------------------------------------------------------------------------
# Pure evaluation (unit-tested)
# ---------------------------------------------------------------------------


def _check(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"check": name, "ok": bool(ok), "detail": detail}


def _normalize(name: str) -> str:
    return name.replace("-", "_")


def _strip_namespace(tool_name: str, server: str) -> str:
    prefix = f"mcp_{_normalize(server)}_"
    if tool_name.startswith(prefix):
        return tool_name[len(prefix):]
    return tool_name


def evaluate_mcp_servers(payload: Any, exp: Expectations) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    configured = payload.get("configured") if isinstance(payload, dict) else None
    connected = payload.get("connected") if isinstance(payload, dict) else None

    if not isinstance(configured, list) or not isinstance(connected, list):
        return [
            _check(
                "mcp-servers-payload-shape",
                False,
                "GET /api/mcp/servers payload lacks configured/connected lists",
            )
        ]

    configured_names = {
        e.get("name") for e in configured if isinstance(e, dict)
    }
    connected_by_name = {
        e.get("name"): e for e in connected if isinstance(e, dict)
    }

    for server in exp.required_connected:
        checks.append(
            _check(
                f"configured:{server}",
                server in configured_names,
                f"{server} present in configured mcp_servers",
            )
        )
        entry = connected_by_name.get(server)
        checks.append(
            _check(
                f"connected:{server}",
                entry is not None and bool(entry.get("connected", True)),
                f"{server} present in connected list with live tools",
            )
        )

    ideas_entry = connected_by_name.get(exp.ideas_server)
    if ideas_entry is not None:
        raw_tools = ideas_entry.get("tools") or []
        seen = {
            _strip_namespace(t.get("name", ""), exp.ideas_server)
            for t in raw_tools
            if isinstance(t, dict)
        }
        expected = set(exp.ideas_tools)
        missing = sorted(expected - seen)
        unexpected = sorted(seen - expected)
        checks.append(
            _check(
                f"tools:{exp.ideas_server}",
                not missing and not unexpected,
                json.dumps(
                    {
                        "expected_count": len(expected),
                        "seen_count": len(seen),
                        "missing": missing,
                        "unexpected": unexpected,
                    },
                    sort_keys=True,
                ),
            )
        )
    return checks


def evaluate_agent_detail(
    role: str, detail: Any, expected_scope: list[str]
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if not isinstance(detail, dict):
        return [
            _check(
                f"agent-detail:{role}",
                False,
                "GET /api/agents/:id returned no object",
            )
        ]

    mode = detail.get("mcp_servers_mode")
    checks.append(
        _check(
            f"allowlist-mode:{role}",
            mode == "allowlist",
            f"mcp_servers_mode={mode!r} (contract requires 'allowlist'; "
            "'all' would grant every connected server)",
        )
    )

    scope = detail.get("mcp_servers")
    scope_set = set(scope) if isinstance(scope, list) else set()
    expected_set = set(expected_scope)
    missing = sorted(expected_set - scope_set)
    unexpected = sorted(scope_set - expected_set)
    checks.append(
        _check(
            f"scope:{role}",
            scope_set == expected_set and bool(scope_set),
            json.dumps(
                {
                    "expected": sorted(expected_set),
                    "actual": sorted(scope_set),
                    "missing": missing,
                    "unexpected": unexpected,
                },
                sort_keys=True,
            ),
        )
    )
    return checks


def resolve_agent_ids(listing: Any, roles: list[str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    if not isinstance(listing, list):
        return resolved
    by_name = {
        e.get("name"): e for e in listing if isinstance(e, dict)
    }
    for role in roles:
        entry = by_name.get(role)
        if entry and entry.get("id"):
            resolved[role] = str(entry["id"])
    return resolved


def build_evidence(
    *,
    base_url: str,
    checks: list[dict[str, Any]],
    raw: dict[str, Any],
    generated_utc: str,
) -> dict[str, Any]:
    overall_ok = bool(checks) and all(c["ok"] for c in checks)
    raw_digests = {
        key: {
            "sha256": hashlib.sha256(
                json.dumps(value, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "payload": value,
        }
        for key, value in raw.items()
    }
    return {
        "task": "task-integration-0005b-openfang-mcp-live-registration-v1",
        "contract": "openfang-mcp-authority-v1",
        "generated_utc": generated_utc,
        "base_url": base_url,
        "checks": checks,
        "raw": raw_digests,
        "overall": "pass" if overall_ok else "fail",
        "live_claim": overall_ok,
    }


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def _get_json(base_url: str, path: str) -> Any:
    request = urllib.request.Request(base_url.rstrip("/") + path)
    api_key = os.environ.get("OPENFANG_API_KEY", "").strip()
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--contract", type=Path, default=None)
    parser.add_argument("--evidence-out", type=Path, default=None)
    args = parser.parse_args(argv)

    contract_path = args.contract or discover_contract(Path(__file__).parent)
    if contract_path is None or not Path(contract_path).is_file():
        print(
            "ERROR: contract JSON not found — pass --contract "
            f"PATH/to/{CONTRACT_FILENAME}",
            file=sys.stderr,
        )
        return EXIT_CHECK_FAILED

    try:
        exp = load_expectations(Path(contract_path))
    except ContractError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_CHECK_FAILED

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []

    try:
        raw["health"] = _get_json(args.base_url, "/api/health")
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(
            f"UNREACHABLE: {args.base_url}/api/health -> {exc}\n"
            "fail-closed: the OpenFang runtime did not answer — "
            "NO live-registration claim can be made.",
            file=sys.stderr,
        )
        if args.evidence_out:
            evidence = build_evidence(
                base_url=args.base_url,
                checks=[
                    _check("health", False, f"unreachable: {exc}"),
                ],
                raw={},
                generated_utc=generated,
            )
            args.evidence_out.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return EXIT_UNREACHABLE

    checks.append(_check("health", True, f"{args.base_url}/api/health answered"))

    try:
        raw["mcp_servers"] = _get_json(args.base_url, "/api/mcp/servers")
        checks.extend(evaluate_mcp_servers(raw["mcp_servers"], exp))

        raw["agents"] = _get_json(args.base_url, "/api/agents")
        roles = sorted(exp.agent_scopes)
        resolved = resolve_agent_ids(raw["agents"], roles)
        for role in roles:
            agent_id = resolved.get(role)
            if agent_id is None:
                checks.append(
                    _check(
                        f"agent-present:{role}",
                        False,
                        f"{role} not present in GET /api/agents — the role is "
                        "not running in this OpenFang instance",
                    )
                )
                continue
            checks.append(
                _check(f"agent-present:{role}", True, f"id={agent_id}")
            )
            detail = _get_json(args.base_url, f"/api/agents/{agent_id}")
            raw[f"agent:{role}"] = detail
            checks.extend(
                evaluate_agent_detail(role, detail, exp.agent_scopes[role])
            )
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        checks.append(_check("readback", False, f"readback aborted: {exc}"))

    evidence = build_evidence(
        base_url=args.base_url, checks=checks, raw=raw, generated_utc=generated
    )
    if args.evidence_out:
        args.evidence_out.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    for check in checks:
        marker = "PASS" if check["ok"] else "FAIL"
        print(f"[{marker}] {check['check']}: {check['detail']}")
    print(f"overall: {evidence['overall']} (live_claim={evidence['live_claim']})")

    return EXIT_OK if evidence["overall"] == "pass" else EXIT_CHECK_FAILED


if __name__ == "__main__":
    sys.exit(main())
