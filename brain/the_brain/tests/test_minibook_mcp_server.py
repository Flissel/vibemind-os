"""Contract tests for the read-only Minibook status MCP server."""

from __future__ import annotations

import importlib
import json
import urllib.error

import pytest

from core.capability_targets import McpExecutor


@pytest.fixture
def mcp_server():
    return importlib.import_module("spaces.minibook.mcp_server")


def _tool_payload(response):
    return json.loads(response["result"]["content"][0]["text"])


def test_lists_exactly_one_closed_no_argument_status_tool(mcp_server):
    assert mcp_server.TOOLS == [{
        "name": "minibook_status",
        "description": "Read the configured Minibook HTTP status using HEAD.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    }]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8800/api/v1/status",
    "http://localhost:8800/api/v1/status",
    "http://[::1]/api/v1/status",
    "http://0x7f000001/api/v1/status",
    "http://2130706433/api/v1/status",
    "http://0177.0.0.1/api/v1/status",
    "http://[::ffff:127.0.0.1]/api/v1/status",
    "https://minibook.example/api/v1/status?token=secret",
    "https://user:secret@minibook.example/api/v1/status",
])
def test_rejects_unsafe_or_ambiguous_configured_urls(mcp_server, url):
    with pytest.raises(mcp_server.ToolError, match="MINIBOOK_STATUS_URL is invalid"):
        mcp_server.minibook_status({"MINIBOOK_STATUS_URL": url})


def test_requires_the_explicit_status_url_without_legacy_fallback(mcp_server):
    with pytest.raises(mcp_server.ToolError, match="MINIBOOK_STATUS_URL is required"):
        mcp_server.minibook_status({"MINIBOOK_URL": "https://minibook.example"})


def test_probes_configured_status_url_once_with_head_and_no_redirects(mcp_server, monkeypatch):
    calls = []

    class Response:
        def getcode(self):
            return 204

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def open_head(request, *, timeout):
        calls.append((request.full_url, request.get_method(), timeout))
        return Response()

    monkeypatch.setattr(mcp_server, "_open_head_without_redirect", open_head)
    result = mcp_server.minibook_status({"MINIBOOK_STATUS_URL": "https://minibook.example/api/v1/status"})

    assert result == {
        "ok": True,
        "source": "minibook-http",
        "http_status": 204,
    }
    assert calls == [("https://minibook.example/api/v1/status", "HEAD", mcp_server.REQUEST_TIMEOUT_SECONDS)]


def test_redirect_is_reported_without_following_it(mcp_server, monkeypatch):
    def open_head(_request, *, timeout):
        del timeout
        raise urllib.error.HTTPError("https://minibook.example/status", 302, "Found", {}, None)

    monkeypatch.setattr(mcp_server, "_open_head_without_redirect", open_head)
    result = mcp_server.minibook_status({"MINIBOOK_STATUS_URL": "https://minibook.example/status"})

    assert result == {
        "ok": False,
        "source": "minibook-http",
        "http_status": 302,
    }


def test_status_payloads_do_not_echo_configured_url_or_path_secrets(mcp_server, monkeypatch):
    secret_url = "https://minibook.example/internal/secret-status-path"

    class Response:
        def getcode(self):
            return 204

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(mcp_server, "_open_head_without_redirect", lambda *_args, **_kwargs: Response())
    success = mcp_server.minibook_status({"MINIBOOK_STATUS_URL": secret_url})
    assert "secret-status-path" not in repr(success)

    def unavailable(*_args, **_kwargs):
        raise OSError("connection failed")

    monkeypatch.setattr(mcp_server, "_open_head_without_redirect", unavailable)
    with pytest.raises(mcp_server.ToolError) as exc_info:
        mcp_server.minibook_status({"MINIBOOK_STATUS_URL": secret_url})
    assert exc_info.value.payload == {"ok": False, "source": "minibook-http", "error": "OSError"}
    assert "secret-status-path" not in repr(exc_info.value.payload)


def test_tool_call_rejects_arguments_and_exposes_no_secrets(mcp_server):
    response = mcp_server.handle_message({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "minibook_status", "arguments": {"url": "https://attacker.example/?token=secret"}},
    })

    assert response is not None
    assert response["result"]["isError"] is True
    assert _tool_payload(response) == {"error": "invalid_arguments: minibook_status accepts no arguments"}


@pytest.mark.parametrize("http_status", [302, 404, 503])
def test_non_2xx_status_payloads_are_mcp_errors(mcp_server, monkeypatch, http_status):
    monkeypatch.setattr(mcp_server, "minibook_status", lambda: {
        "ok": False,
        "source": "minibook-http",
        "http_status": http_status,
    })

    response = mcp_server.handle_message({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "minibook_status", "arguments": {}},
    })

    assert response is not None
    assert response["result"]["isError"] is True
    assert _tool_payload(response) == {
        "ok": False,
        "source": "minibook-http",
        "http_status": http_status,
    }


def test_mcp_executor_classifies_minibook_non_2xx_result_as_failure(mcp_server, monkeypatch):
    monkeypatch.setattr(mcp_server, "minibook_status", lambda: {
        "ok": False,
        "source": "minibook-http",
        "http_status": 302,
    })
    tool_response = mcp_server.handle_message({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "minibook_status", "arguments": {}},
    })
    assert tool_response is not None

    executor = McpExecutor("mcp:brain-knowledge:spaces-minibook:minibook_status")
    monkeypatch.setattr(executor, "_configuration", lambda: ("https://openfang.example", "token"))
    monkeypatch.setattr(executor, "_resolve_agent_id", lambda *_args: "agent-1")

    def request_json(_method, _url, *, payload, **_kwargs):
        return {
            "jsonrpc": "2.0",
            "id": payload["id"],
            "result": tool_response["result"],
        }

    monkeypatch.setattr(executor, "_request_json", request_json)
    result = executor.call()

    assert result["ok"] is False
    assert "iserror" in result["error"].lower()
