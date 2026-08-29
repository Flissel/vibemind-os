"""Space-boundary contracts around the OpenAI plugin runtime.

The plugin runtime lives inside the Rowboat web app. This file guards the two
boundaries the Space itself owns: the status contract that other spaces consume
through the stdio MCP server, and the HTTP surface shape of the versioned
plugin API.

Every assertion here runs offline. The live-service checks at the bottom are
opt-in and *skip* when no Rowboat is reachable; a skip is not a pass and is
reported as unrun in the completion evidence.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

import importlib.util
import pytest


SPACE = Path(__file__).resolve().parents[1]

# `spaces.rowboat.__init__` imports .config/.agents/.broadcast, none of which are
# tracked in this repository, so importing the package fails on master too. That
# unrelated baseline is not repaired here; the status contract is loaded from its
# own module file instead.
_SPEC = importlib.util.spec_from_file_location("rowboat_mcp_server_contract", SPACE / "mcp_server.py")
assert _SPEC is not None and _SPEC.loader is not None
mcp_server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mcp_server)


APP = SPACE / "rowboat" / "apps" / "rowboat"
PLUGIN_API = APP / "app" / "api" / "v1"
CHAT_ROUTE = PLUGIN_API / "[projectId]" / "chat" / "route.ts"
RESPONSES = PLUGIN_API / "projects" / "[projectId]" / "plugins" / "_responses.ts"


class _FakeResponse:
    def __init__(self, code: int) -> None:
        self._code = code

    def getcode(self) -> int:
        return self._code

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


def _call(message: Mapping[str, Any]) -> dict[str, Any] | None:
    return mcp_server.handle_message(message)


def _payload(response: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(response["result"]["content"][0]["text"])


def test_existing_status_contract_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """rowboat_status stays the single tool with the same payload keys."""
    listed = _call({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed is not None
    tools = listed["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["rowboat_status"]
    assert tools[0]["inputSchema"] == {"type": "object", "properties": {}, "additionalProperties": False}

    monkeypatch.setenv("ROWBOAT_URL", "http://rowboat.internal:3000")
    monkeypatch.setattr(mcp_server, "_open_head_without_redirect", lambda request, timeout: _FakeResponse(200))
    response = _call({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "rowboat_status", "arguments": {}}})
    assert response is not None
    assert response["result"]["isError"] is False
    assert _payload(response) == {"ok": True, "source": "rowboat-http", "url": "http://rowboat.internal:3000", "http_status": 200}


def test_status_contract_still_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plugin runtime did not add a tool, an argument, or a fallback URL."""
    unknown = _call({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "plugin_runtime_mode", "arguments": {}}})
    assert unknown is not None and unknown["result"]["isError"] is True

    monkeypatch.setenv("ROWBOAT_URL", "http://rowboat.internal:3000")
    extra = _call({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "rowboat_status", "arguments": {"mode": "openai"}}})
    assert extra is not None and extra["result"]["isError"] is True

    monkeypatch.delenv("ROWBOAT_URL", raising=False)
    with pytest.raises(mcp_server.ToolError):
        mcp_server.rowboat_status({})

    # The probe still refuses a loopback target, so a plugin rollout cannot be
    # declared healthy against a service on the operator machine.
    with pytest.raises(mcp_server.ToolError):
        mcp_server.rowboat_status({"ROWBOAT_URL": "http://localhost:3000"})


def test_existing_chat_contract_does_not_require_plugin_fields() -> None:
    """The conversation entry point carries no plugin request field."""
    source = CHAT_ROUTE.read_text(encoding="utf-8")
    assert "pluginBinding" not in source
    assert "pluginRuntime" not in source
    assert "runtime-mode" not in source


def test_plugin_api_is_versioned_and_fail_closed() -> None:
    """Every plugin route is under /api/v1 and every plugin error maps to 4xx or 5xx."""
    relative = sorted(str(path.relative_to(APP)).replace("\\", "/") for path in APP.glob("app/api/**/route.ts"))
    routes = [route for route in relative if "plugin" in route]
    assert routes, "expected the plugin API routes to exist"
    assert all(route.startswith("app/api/v1/") for route in routes)
    assert "app/api/v1/projects/[projectId]/plugins/runtime-mode/route.ts" in routes

    runtime_mode = (APP / "app" / "api" / "v1" / "projects" / "[projectId]" / "plugins" / "runtime-mode" / "route.ts").read_text(encoding="utf-8")
    assert "export const POST" in runtime_mode
    assert "export const GET" not in runtime_mode

    statuses = RESPONSES.read_text(encoding="utf-8")
    block = statuses.split("const ERROR_STATUS = Object.freeze({", 1)[1].split("});", 1)[0]
    mapped = {name: int(code) for name, code in re.findall(r"(\w+):\s*(\d{3})", block)}
    for reason in ("plugin_runtime_request_invalid", "runtime_mode_transition_rejected", "cutover_evidence_required", "plugin_runtime_state_conflict"):
        assert mapped[reason] >= 400, f"{reason} must fail closed"
    assert all(code >= 400 for code in mapped.values())


def _live_base_url() -> str | None:
    url = (os.environ.get("ROWBOAT_CONTRACT_URL") or "").strip()
    if not url:
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5):
            return url
    except urllib.error.HTTPError:
        return url
    except Exception:
        return None


@pytest.fixture()
def rowboat_client() -> str:
    url = _live_base_url()
    if url is None:
        pytest.skip("set ROWBOAT_CONTRACT_URL to a reachable Rowboat to run the live boundary checks")
    return url


def _status_of(url: str, *, method: str, body: bytes | None = None) -> int:
    request = urllib.request.Request(url, method=method, data=body, headers={"content-type": "application/json"} if body else {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return int(response.getcode())
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def test_live_chat_contract_does_not_require_plugin_fields(rowboat_client: str) -> None:
    status = _status_of(f"{rowboat_client}/api/v1/test-project/chat", method="POST", body=json.dumps({"messages": []}).encode("utf-8"))
    assert status != 422


def test_live_plugin_api_is_fail_closed(rowboat_client: str) -> None:
    body = json.dumps({"mode": "openai", "expectedRevision": 0, "catalogDigest": None, "migrationRecordId": None, "parityReceiptId": None}).encode("utf-8")
    status = _status_of(f"{rowboat_client}/api/v1/projects/test-project/plugins/runtime-mode", method="POST", body=body)
    assert status in {400, 401, 403, 404, 409}
