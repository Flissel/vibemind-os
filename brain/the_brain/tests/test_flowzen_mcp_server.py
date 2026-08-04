"""Contract tests for Flowzen's closed, read-only Supabase status MCP tool."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from unittest import mock
import urllib.error
import urllib.parse
import urllib.request

import pytest

from core.capability_targets import McpExecutor


ROOT = Path(__file__).resolve().parents[3]
SERVER_PATH = ROOT / "spaces" / "flowzen" / "mcp_server.py"
ENV = {
    "SUPABASE_URL": "https://flowzen.supabase.example",
    "SUPABASE_SERVICE_ROLE_KEY": "service-role-secret",
}
CHECKIN = {
    "mood": "focused",
    "energy": 8,
    "time_window": "morning",
    "hour": 9,
    "created_at": "2026-08-04T09:00:00Z",
}
ACTIVITY = {
    "id": "activity-private-id",
    "event_type": "recommendation_accepted:fzr_private",
    "time_window": "morning",
    "hour": 9,
    "created_at": "2026-08-04T09:01:00Z",
}


def load_server():
    if not SERVER_PATH.is_file():
        raise AssertionError("spaces/flowzen/mcp_server.py must be versioned")
    spec = importlib.util.spec_from_file_location("spaces_flowzen_mcp_server", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("Flowzen MCP server must be importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mcp_server():
    if not SERVER_PATH.is_file():
        pytest.skip("Flowzen MCP server is not implemented yet")
    return load_server()


def call_status(server, *, request_id="flowzen-request", arguments=None, name="flowzen_status"):
    return server.handle_message({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": {} if arguments is None else arguments},
    })


def payload(response):
    return json.loads(response["result"]["content"][0]["text"])


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body.encode("utf-8")


def opener_for(*bodies, seen=None):
    responses = iter(bodies)

    def open_request(request, timeout):
        if seen is not None:
            seen.append((request, timeout))
        return Response(next(responses))

    return open_request


def test_flowzen_mcp_server_module_is_versioned():
    assert SERVER_PATH.is_file(), "spaces/flowzen/mcp_server.py must be versioned"


def test_lists_exactly_one_closed_no_argument_status_tool(mcp_server):
    response = mcp_server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"tools": [{
            "name": "flowzen_status",
            "description": "Read the latest independent Flowzen check-in and activity rows.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        }]},
    }


@pytest.mark.parametrize("arguments", [
    {"ignored": True},
    {"url": "https://attacker.example"},
    {"include": "raw"},
    ["not", "an", "object"],
])
def test_rejects_any_arguments_before_network(mcp_server, monkeypatch, arguments):
    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("must not request"))

    response = call_status(mcp_server, arguments=arguments, request_id="bad-arguments")

    assert response["id"] == "bad-arguments"
    assert response["result"]["isError"] is True
    assert payload(response) == {"error": "flowzen_status_unverified"}


@pytest.mark.parametrize("configured_env", [
    {},
    {"SUPABASE_URL": "   ", "SUPABASE_SERVICE_ROLE_KEY": "key"},
    {"SUPABASE_URL": "https://flowzen.example", "SUPABASE_SERVICE_ROLE_KEY": "  "},
    {"SUPABASE_URL": "ftp://flowzen.example", "SUPABASE_SERVICE_ROLE_KEY": "key"},
    {"SUPABASE_URL": "https://user:secret@flowzen.example", "SUPABASE_SERVICE_ROLE_KEY": "key"},
    {"SUPABASE_URL": "https://flowzen.example/?token=secret-query", "SUPABASE_SERVICE_ROLE_KEY": "key"},
])
def test_missing_blank_or_invalid_config_fails_closed_before_network(mcp_server, monkeypatch, configured_env):
    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("must not request"))
    with mock.patch.dict(os.environ, configured_env, clear=True):
        response = call_status(mcp_server, request_id="config")

    assert response["id"] == "config"
    assert response["result"]["isError"] is True
    assert payload(response) == {"error": "flowzen_status_unverified"}
    text = response["result"]["content"][0]["text"]
    assert "secret" not in text
    assert "flowzen.example" not in text


def test_uses_exactly_two_independent_get_requests_with_safe_projection(mcp_server, monkeypatch):
    seen = []
    monkeypatch.setattr(
        mcp_server.urllib.request,
        "urlopen",
        opener_for(json.dumps([CHECKIN]), json.dumps([ACTIVITY]), seen=seen),
    )
    with mock.patch.dict(os.environ, ENV, clear=True):
        response = call_status(mcp_server, request_id=42)

    assert response["id"] == 42
    assert response["result"]["isError"] is False
    assert payload(response) == {
        "event_id": "rose.status",
        "mutated": False,
        "observation": "independent-latest-rows",
        "ok": True,
        "source": "flowzen-supabase",
        "status": {
            "latest_checkin": CHECKIN,
            "latest_activity": {
                "created_at": "2026-08-04T09:01:00Z",
                "hour": 9,
                "status": "accepted",
                "time_window": "morning",
            },
        },
    }
    assert len(seen) == 2
    expected = [
        ("flowzen_checkins", "mood,energy,time_window,hour,created_at"),
        ("flowzen_activity", "id,event_type,time_window,hour,created_at"),
    ]
    for (request, timeout), (table, select) in zip(seen, expected):
        parsed = urllib.parse.urlsplit(request.full_url)
        assert request.get_method() == "GET"
        assert parsed.scheme == "https"
        assert parsed.netloc == "flowzen.supabase.example"
        assert parsed.path == f"/rest/v1/{table}"
        assert urllib.parse.parse_qs(parsed.query) == {
            "select": [select], "order": ["created_at.desc"], "limit": ["1"],
        }
        assert request.get_header("Apikey") == "service-role-secret"
        assert request.get_header("Authorization") == "Bearer service-role-secret"
        assert request.get_header("Accept") == "application/json"
        assert timeout == 15
    text = response["result"]["content"][0]["text"]
    assert "activity-private-id" not in text
    assert "recommendation_accepted:" not in text
    assert "service-role-secret" not in text
    assert "flowzen.supabase.example" not in text


def test_empty_lists_project_to_null_without_treating_the_read_as_failure(mcp_server, monkeypatch):
    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", opener_for("[]", "[]"))
    with mock.patch.dict(os.environ, ENV, clear=True):
        response = call_status(mcp_server)

    assert response["result"]["isError"] is False
    assert payload(response)["status"] == {"latest_checkin": None, "latest_activity": None}


def test_activity_without_accepted_event_is_safely_observed(mcp_server, monkeypatch):
    activity = {**ACTIVITY, "event_type": "recommendation_presented:fzr_private"}
    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", opener_for("[]", json.dumps([activity])))
    with mock.patch.dict(os.environ, ENV, clear=True):
        response = call_status(mcp_server)

    assert payload(response)["status"] == {
        "latest_checkin": None,
        "latest_activity": {
            "created_at": "2026-08-04T09:01:00Z",
            "hour": 9,
            "status": "observed",
            "time_window": "morning",
        },
    }


@pytest.mark.parametrize("checkin", [
    {**CHECKIN, "mood": "private mood"},
    {**CHECKIN, "time_window": "overnight"},
    {**CHECKIN, "energy": True},
    {**CHECKIN, "energy": 11},
    {**CHECKIN, "hour": False},
    {**CHECKIN, "hour": 24},
    {**CHECKIN, "created_at": "not-a-timestamp"},
    {key: value for key, value in CHECKIN.items() if key != "mood"},
])
def test_checkin_domain_type_and_timestamp_errors_fail_closed(mcp_server, monkeypatch, checkin):
    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", opener_for(json.dumps([checkin]), "[]"))
    with mock.patch.dict(os.environ, ENV, clear=True):
        response = call_status(mcp_server)

    assert response["result"]["isError"] is True
    assert payload(response) == {"error": "flowzen_status_unverified"}


@pytest.mark.parametrize("activity", [
    {**ACTIVITY, "id": ""},
    {**ACTIVITY, "event_type": 7},
    {**ACTIVITY, "time_window": "overnight"},
    {**ACTIVITY, "hour": True},
    {**ACTIVITY, "hour": -1},
    {**ACTIVITY, "created_at": "2026-08-04"},
    {key: value for key, value in ACTIVITY.items() if key != "event_type"},
])
def test_activity_domain_type_and_timestamp_errors_fail_closed(mcp_server, monkeypatch, activity):
    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", opener_for("[]", json.dumps([activity])))
    with mock.patch.dict(os.environ, ENV, clear=True):
        response = call_status(mcp_server)

    assert response["result"]["isError"] is True
    assert payload(response) == {"error": "flowzen_status_unverified"}


@pytest.mark.parametrize("body", [
    json.dumps({"mood": "focused"}),
    json.dumps([CHECKIN, CHECKIN]),
    json.dumps(["not-a-mapping"]),
    "not-json",
])
def test_nonlist_multirow_malformed_row_and_invalid_json_fail_closed(mcp_server, monkeypatch, body):
    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", opener_for(body, "[]"))
    with mock.patch.dict(os.environ, ENV, clear=True):
        response = call_status(mcp_server)

    assert response["result"]["isError"] is True
    assert payload(response) == {"error": "flowzen_status_unverified"}


def test_activity_nonlist_or_multirow_also_fails_closed_without_partial_result(mcp_server, monkeypatch):
    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", opener_for(json.dumps([CHECKIN]), json.dumps([ACTIVITY, ACTIVITY])))
    with mock.patch.dict(os.environ, ENV, clear=True):
        response = call_status(mcp_server)

    assert response["result"]["isError"] is True
    assert payload(response) == {"error": "flowzen_status_unverified"}


@pytest.mark.parametrize("failure", [
    urllib.error.URLError("transport secret=service-role-secret"),
    OSError("body secret=service-role-secret"),
])
def test_http_and_transport_errors_are_stable_and_redacted(mcp_server, monkeypatch, failure):
    if isinstance(failure, urllib.error.HTTPError):
        raise AssertionError("HTTP errors are covered by the HTTPError branch below")
    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", mock.Mock(side_effect=failure))
    with mock.patch.dict(os.environ, ENV, clear=True):
        response = call_status(mcp_server)

    assert response["result"]["isError"] is True
    assert payload(response) == {"error": "flowzen_status_unverified"}
    assert "secret" not in response["result"]["content"][0]["text"]


def test_http_error_body_and_url_are_not_returned(mcp_server, monkeypatch):
    request = urllib.request.Request("https://flowzen.supabase.example/rest/v1/flowzen_checkins")
    error = urllib.error.HTTPError(request.full_url, 503, "secret failure", {}, None)
    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", mock.Mock(side_effect=error))
    with mock.patch.dict(os.environ, ENV, clear=True):
        response = call_status(mcp_server)

    assert response["result"]["isError"] is True
    assert payload(response) == {"error": "flowzen_status_unverified"}
    assert "flowzen.supabase.example" not in response["result"]["content"][0]["text"]


def test_unknown_tool_preserves_json_rpc_id_and_fails_closed(mcp_server):
    response = call_status(mcp_server, name="flowzen_mutate", request_id={"request": 9})

    assert response["id"] == {"request": 9}
    assert response["result"]["isError"] is True
    assert payload(response) == {"error": "flowzen_status_unverified"}


def test_mcp_executor_treats_flowzen_iserror_as_hard_failure(mcp_server, monkeypatch):
    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", mock.Mock(side_effect=OSError("offline")))
    with mock.patch.dict(os.environ, ENV, clear=True):
        response = call_status(mcp_server)
    executor = McpExecutor("mcp:brain-flowzen:spaces-flowzen:flowzen_status")
    monkeypatch.setattr(executor, "_configuration", lambda: ("https://openfang.example", "token"))
    monkeypatch.setattr(executor, "_resolve_agent_id", lambda *_args: "agent-1")
    monkeypatch.setattr(
        executor,
        "_request_json",
        lambda _method, _url, *, payload, **_kwargs: {
            "jsonrpc": "2.0", "id": payload["id"], "result": response["result"],
        },
    )

    result = executor.call()

    assert result["ok"] is False
    assert "iserror" in result["error"].lower()
