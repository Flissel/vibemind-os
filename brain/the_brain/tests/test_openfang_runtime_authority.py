from __future__ import annotations

import logging

import pytest
import requests

from core.openfang_runtime_authority import (
    RuntimeAuthorityClient,
    RuntimeAuthorityPending,
    RuntimeInvocationContext,
)


class _Response:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def _context() -> RuntimeInvocationContext:
    return RuntimeInvocationContext(
        correlation_id="trace-runtime-authority",
        plan_id="plan-runtime-authority",
        plan_revision=1,
        step_id="step-1",
        space_id="ideas",
        agent_name="brain-ideas",
        invocation_id="brain-mcp-test-invocation",
    )


def _client(monkeypatch) -> RuntimeAuthorityClient:
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    return RuntimeAuthorityClient.from_environment()


def test_pending_approval_stops_before_cost_and_mcp(monkeypatch):
    calls = []

    def _get(url, **_kwargs):
        calls.append("agents")
        return _Response({"agents": [{"name": "brain-ideas", "id": "agent-uuid"}]})

    def _post(url, **_kwargs):
        calls.append(url)
        if url.endswith("/api/runtime/approvals/admit"):
            return _Response({"status": "pending_approval", "approval_ref": "approval-secret"})
        raise AssertionError("cost reservation and MCP dispatch must not run while approval is pending")

    monkeypatch.setattr("core.openfang_runtime_authority.requests.get", _get)
    monkeypatch.setattr("core.openfang_runtime_authority.requests.post", _post)

    result = _client(monkeypatch).prepare_invocation(_context())

    assert isinstance(result, RuntimeAuthorityPending)
    assert result.invocation_id == "brain-mcp-test-invocation"
    assert calls == ["agents", "http://openfang.test/api/runtime/approvals/admit"]


def test_approved_zero_cost_invocation_uses_server_refs_and_stable_id(monkeypatch):
    calls = []

    def _get(_url, **_kwargs):
        calls.append("agents")
        return _Response([{"name": "brain-ideas", "id": "agent-uuid"}])

    def _post(url, *, json, **_kwargs):
        calls.append((url, json))
        if url.endswith("/approvals/admit"):
            return _Response({"status": "approved", "approval_ref": "approval-server-only"})
        if url.endswith("/cost-reservations"):
            return _Response({"status": "reserved", "reservation": {
                "cost_ref": "cost-server-only", "approval_ref": "approval-server-only",
                "invocation_id": "brain-mcp-test-invocation",
            }})
        raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr("core.openfang_runtime_authority.requests.get", _get)
    monkeypatch.setattr("core.openfang_runtime_authority.requests.post", _post)

    first = _client(monkeypatch).prepare_invocation(_context())
    second = _client(monkeypatch).prepare_invocation(_context())

    assert first == second
    assert first.invocation_id == "brain-mcp-test-invocation"
    assert first.approval_ref == "approval-server-only"
    assert first.cost_ref == "cost-server-only"
    assert calls[1][1]["max_plan_cost_microusd"] == 0
    assert calls[2][1]["max_cost_microusd"] == 0
    assert calls[1][1]["ttl_seconds"] == calls[2][1]["ttl_seconds"] == 300


def test_outcome_unknown_is_never_retried_or_reported_as_success(monkeypatch):
    from core.capability_targets import McpExecutor

    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    posts = []
    monkeypatch.setattr(
        "core.capability_targets.find_registered_mcp_authority",
        lambda *_args: type("Authority", (), {
            "agent": "brain-ideas", "space_id": "ideas", "event_id": "ideas.list",
        })(),
    )

    def _authority(_self, _context):
        return type("Refs", (), {
            "agent_id": "agent-uuid", "approval_ref": "approval-server-only",
            "cost_ref": "cost-server-only", "invocation_id": _context.invocation_id,
        })()

    def _post(_url, *, json, **_kwargs):
        posts.append(json)
        return _Response({
            "jsonrpc": "2.0", "id": json["id"],
            "error": {"code": -32000, "message": "outcome_unknown"},
        })

    monkeypatch.setattr("core.capability_targets.RuntimeAuthorityClient.from_environment", lambda: type("Client", (), {"prepare_invocation": _authority})())
    monkeypatch.setattr("core.capability_targets.requests.post", _post)
    result = McpExecutor("mcp:brain-ideas:vibemind-db:ideas.list").call(
        _runtime_authority=_context(), title="focus"
    )

    assert result["ok"] is False
    assert result.get("retryable") is False
    assert len(posts) == 1


@pytest.mark.parametrize(
    "authority_status",
    ["outcome_unknown", "in_progress", "pending_approval"],
)
def test_structured_runtime_authority_status_controls_mcp_retryability(
    monkeypatch, authority_status,
):
    from core.capability_targets import McpExecutor

    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    monkeypatch.setattr(
        "core.capability_targets.find_registered_mcp_authority",
        lambda *_args: type("Authority", (), {
            "agent": "brain-ideas", "space_id": "ideas", "event_id": "ideas.list",
        })(),
    )
    monkeypatch.setattr(
        "core.capability_targets.RuntimeAuthorityClient.from_environment",
        lambda: type("Client", (), {"prepare_invocation": lambda _self, context: type("Refs", (), {
            "agent_id": "agent-uuid", "approval_ref": "approval-server-only",
            "cost_ref": "cost-server-only", "invocation_id": context.invocation_id,
        })()})(),
    )
    monkeypatch.setattr(
        "core.capability_targets.requests.post",
        lambda _url, *, json, **_kwargs: _Response({
            "jsonrpc": "2.0", "id": json["id"],
            "error": {
                "code": -32000, "message": "runtime admission state changed",
                "data": {"authority_status": authority_status},
            },
        }),
    )

    result = McpExecutor("mcp:brain-ideas:vibemind-db:ideas.list").call(
        _runtime_authority=_context(), title="focus"
    )

    assert result["ok"] is False
    if authority_status == "pending_approval":
        assert result["pending"] is True
        assert result["authority_status"] == "pending_approval"
        assert result["invocation_id"] == "brain-mcp-test-invocation"
    else:
        assert result["retryable"] is False


def test_client_never_logs_or_returns_complete_authority_refs(monkeypatch, caplog):
    approval_ref = "approval-synthetic-complete-ref"
    cost_ref = "cost-synthetic-complete-ref"

    monkeypatch.setattr(
        "core.openfang_runtime_authority.requests.get",
        lambda *_args, **_kwargs: _Response([{"name": "brain-ideas", "id": "agent-uuid"}]),
    )
    monkeypatch.setattr(
        "core.openfang_runtime_authority.requests.post",
        lambda url, **_kwargs: _Response(
            {"status": "approved", "approval_ref": approval_ref}
            if url.endswith("/approvals/admit")
            else {"status": "reserved", "reservation": {"cost_ref": cost_ref, "approval_ref": approval_ref, "invocation_id": "brain-mcp-test-invocation"}}
        ),
    )

    with caplog.at_level(logging.WARNING):
        result = _client(monkeypatch).prepare_invocation(_context())

    rendered = repr(result) + caplog.text
    assert approval_ref not in rendered
    assert cost_ref not in rendered
    assert result.invocation_id == "brain-mcp-test-invocation"
