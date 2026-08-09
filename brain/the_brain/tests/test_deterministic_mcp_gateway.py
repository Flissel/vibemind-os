"""Offline contract for the one-tool deterministic Rowboat MCP gateway."""

from __future__ import annotations

from io import StringIO
import json
import os
import urllib.request

import pytest
import requests

from deterministic_gateway import (
    ROWBOAT_AGENT,
    ROWBOAT_SPACE,
    ROWBOAT_TARGET,
    ROWBOAT_TOOL,
    DeterministicRowboatGateway,
    main,
)
from core.openfang_runtime_authority import runtime_invocation_id
from core.capability_router import CapabilityRouter
from core.capability_targets import DirectExecutor
from core.plan_executor import PlanExecutor
from core.planner_llm import PlannerLLM


class _Response:
    status_code = 200

    def __init__(self, body: object) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._body


def _gateway() -> DeterministicRowboatGateway:
    return DeterministicRowboatGateway()


def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-key")


def _invoke(gateway: DeterministicRowboatGateway, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "correlation_id": "corr-rowboat-1",
        "plan_id": "plan-rowboat-1",
        "plan_revision": 1,
        "space_id": ROWBOAT_SPACE,
        "agent_name": ROWBOAT_AGENT,
        "tool": ROWBOAT_TOOL,
    }
    values.update(overrides)
    return gateway.execute(**values)


def test_gateway_uses_only_the_ordered_openfang_authority_and_mcp_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch)
    calls: list[tuple[str, str]] = []

    def _get(url: str, **_kwargs: object) -> _Response:
        calls.append(("GET", url))
        assert url == "http://openfang.test/api/agents"
        return _Response({"agents": [{"name": ROWBOAT_AGENT, "id": "agent-rowboat-uuid"}]})

    def _post(url: str, *, json: dict[str, object], **_kwargs: object) -> _Response:
        calls.append(("POST", url))
        if url.endswith("/api/runtime/approvals/admit"):
            assert json["max_plan_cost_microusd"] == 0
            assert json["correlation_id"] == "corr-rowboat-1"
            assert json["plan_id"] == "plan-rowboat-1"
            assert json["plan_revision"] == 1
            assert json["space_id"] == ROWBOAT_SPACE
            return _Response({"status": "approved", "approval_ref": "approval-server-ref"})
        if url.endswith("/api/runtime/cost-reservations"):
            assert json["max_cost_microusd"] == 0
            return _Response({"status": "reserved", "reservation": {"cost_ref": "cost-server-ref"}})
        if url.endswith("/mcp"):
            assert json == {
                "jsonrpc": "2.0",
                "id": runtime_invocation_id("plan-rowboat-1", 1, "rowboat.status"),
                "method": "tools/call",
                "params": {"name": "mcp_spaces_rowboat_rowboat_status", "arguments": {}},
            }
            return _Response({
                "jsonrpc": "2.0",
                "id": json["id"],
                "result": {"content": [{"type": "text", "text": "ok"}], "isError": False},
            })
        raise AssertionError(f"unexpected OpenFang request: {url}")

    monkeypatch.setattr("core.openfang_runtime_authority.requests.get", _get)
    monkeypatch.setattr("core.openfang_runtime_authority.requests.post", _post)

    forbidden_calls: list[str] = []

    def _forbidden(name: str):
        def _raise(*_args: object, **_kwargs: object) -> None:
            forbidden_calls.append(name)
            raise AssertionError(f"{name} must not run in the deterministic gateway")
        return _raise

    monkeypatch.setattr(DirectExecutor, "call", _forbidden("direct_executor"))
    monkeypatch.setattr(PlanExecutor, "execute", _forbidden("planner"))
    monkeypatch.setattr(PlannerLLM, "plan", _forbidden("provider_model"))
    monkeypatch.setattr(CapabilityRouter, "_embed_text", _forbidden("embedding"))
    monkeypatch.setattr(urllib.request, "build_opener", _forbidden("direct_rowboat"))
    monkeypatch.setattr(urllib.request, "urlopen", _forbidden("direct_rowboat"))
    monkeypatch.setattr(requests, "request", _forbidden("provider_model"))

    result = _invoke(_gateway())

    assert result == {
        "ok": True,
        "status": "completed",
        "target": ROWBOAT_TARGET,
        "result": {"content": [{"type": "text", "text": "ok"}], "isError": False},
    }
    assert calls == [
        ("GET", "http://openfang.test/api/agents"),
        ("POST", "http://openfang.test/api/runtime/approvals/admit"),
        ("POST", "http://openfang.test/api/runtime/cost-reservations"),
        ("POST", "http://openfang.test/mcp"),
    ]
    assert forbidden_calls == []


@pytest.mark.parametrize("missing", ["OPENFANG_URL", "OPENFANG_API_KEY"])
def test_gateway_fails_closed_for_missing_openfang_configuration(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    calls: list[str] = []
    monkeypatch.delenv("OPENFANG_URL", raising=False)
    monkeypatch.delenv("OPENFANG_API_KEY", raising=False)
    monkeypatch.setattr("core.openfang_runtime_authority.requests.get", lambda *_args, **_kwargs: calls.append("agents"))
    monkeypatch.setattr("core.openfang_runtime_authority.requests.post", lambda *_args, **_kwargs: calls.append("post"))
    if missing == "OPENFANG_URL":
        monkeypatch.setenv("OPENFANG_API_KEY", "test-key")
    else:
        monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")

    result = _invoke(_gateway())

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert missing in str(result["reason"])
    assert calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"correlation_id": ""},
        {"plan_id": ""},
        {"plan_revision": 0},
        {"plan_revision": True},
        {"space_id": "roarboot"},
        {"agent_name": "other-agent"},
        {"tool": "other_tool"},
    ],
)
def test_gateway_rejects_noncanonical_inputs_before_authority_or_mcp(monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object]) -> None:
    _configured(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr("core.openfang_runtime_authority.requests.get", lambda *_args, **_kwargs: calls.append("agents"))
    monkeypatch.setattr("core.openfang_runtime_authority.requests.post", lambda *_args, **_kwargs: calls.append("post"))

    result = _invoke(_gateway(), **overrides)

    assert result == {"ok": False, "status": "blocked", "target": ROWBOAT_TARGET, "reason": "invalid deterministic Rowboat gateway input"}
    assert calls == []


def test_gateway_surfaces_pending_without_cost_or_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch)
    calls: list[str] = []

    def _get(_url: str, **_kwargs: object) -> _Response:
        calls.append("agents")
        return _Response([{"name": ROWBOAT_AGENT, "id": "agent-rowboat-uuid"}])

    def _post(url: str, **_kwargs: object) -> _Response:
        calls.append(url)
        if url.endswith("/approvals/admit"):
            return _Response({"status": "pending_approval", "approval_ref": "approval-server-ref"})
        raise AssertionError("pending approval must stop before cost reservation and MCP")

    monkeypatch.setattr("core.openfang_runtime_authority.requests.get", _get)
    monkeypatch.setattr("core.openfang_runtime_authority.requests.post", _post)

    result = _invoke(_gateway())

    assert result == {"ok": False, "status": "pending", "target": ROWBOAT_TARGET, "retryable": False}
    assert calls == ["agents", "http://openfang.test/api/runtime/approvals/admit"]


def test_gateway_surfaces_denial_without_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr("core.openfang_runtime_authority.requests.get", lambda *_args, **_kwargs: calls.append("agents") or _Response([{"name": ROWBOAT_AGENT, "id": "agent-rowboat-uuid"}]))
    monkeypatch.setattr(
        "core.openfang_runtime_authority.requests.post",
        lambda url, **_kwargs: calls.append(url) or _Response({"status": "denied", "approval_ref": "approval-server-ref"}),
    )

    result = _invoke(_gateway())

    assert result == {"ok": False, "status": "denied", "target": ROWBOAT_TARGET, "retryable": False}
    assert calls == ["agents", "http://openfang.test/api/runtime/approvals/admit"]


@pytest.mark.parametrize(
    "mcp_response",
    [
        {"jsonrpc": "1.0", "id": "request", "result": {}},
        {"jsonrpc": "2.0", "id": "request"},
    ],
)
def test_gateway_fails_closed_for_malformed_mcp_response_without_retry(monkeypatch: pytest.MonkeyPatch, mcp_response: dict[str, object]) -> None:
    _configured(monkeypatch)
    mcp_calls = 0

    def _get(_url: str, **_kwargs: object) -> _Response:
        return _Response([{"name": ROWBOAT_AGENT, "id": "agent-rowboat-uuid"}])

    def _post(url: str, *, json: dict[str, object], **_kwargs: object) -> _Response:
        nonlocal mcp_calls
        if url.endswith("/approvals/admit"):
            return _Response({"status": "approved", "approval_ref": "approval-server-ref"})
        if url.endswith("/cost-reservations"):
            return _Response({"status": "reserved", "reservation": {"cost_ref": "cost-server-ref"}})
        mcp_calls += 1
        response = dict(mcp_response)
        response["id"] = json["id"] if response.get("id") == "request" else response.get("id")
        return _Response(response)

    monkeypatch.setattr("core.openfang_runtime_authority.requests.get", _get)
    monkeypatch.setattr("core.openfang_runtime_authority.requests.post", _post)

    result = _invoke(_gateway())

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert mcp_calls == 1


def test_gateway_marks_outcome_unknown_nonretryable_without_another_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch)
    mcp_calls = 0

    def _get(_url: str, **_kwargs: object) -> _Response:
        return _Response([{"name": ROWBOAT_AGENT, "id": "agent-rowboat-uuid"}])

    def _post(url: str, *, json: dict[str, object], **_kwargs: object) -> _Response:
        nonlocal mcp_calls
        if url.endswith("/approvals/admit"):
            return _Response({"status": "approved", "approval_ref": "approval-server-ref"})
        if url.endswith("/cost-reservations"):
            return _Response({"status": "reserved", "reservation": {"cost_ref": "cost-server-ref"}})
        mcp_calls += 1
        return _Response({"jsonrpc": "2.0", "id": json["id"], "error": {"message": "outcome_unknown"}})

    monkeypatch.setattr("core.openfang_runtime_authority.requests.get", _get)
    monkeypatch.setattr("core.openfang_runtime_authority.requests.post", _post)

    result = _invoke(_gateway())

    assert result == {"ok": False, "status": "outcome_unknown", "target": ROWBOAT_TARGET, "retryable": False}
    assert mcp_calls == 1


def test_main_reads_one_request_and_writes_one_result(monkeypatch: pytest.MonkeyPatch) -> None:
    request = {
        "correlation_id": "corr-rowboat-cli",
        "plan_id": "plan-rowboat-cli",
        "plan_revision": 2,
        "space_id": ROWBOAT_SPACE,
        "agent_name": ROWBOAT_AGENT,
        "tool": ROWBOAT_TOOL,
    }
    expected = {
        "ok": True,
        "status": "completed",
        "target": ROWBOAT_TARGET,
        "result": {"isError": False, "content": []},
    }
    calls: list[dict[str, object]] = []

    def _execute(_self: object, **values: object) -> dict[str, object]:
        calls.append(values)
        return expected

    monkeypatch.setattr(DeterministicRowboatGateway, "execute", _execute)
    output = StringIO()

    exit_code = main(stdin=StringIO(json.dumps(request)), stdout=output)

    assert exit_code == 0
    assert calls == [request]
    assert output.getvalue().count("\n") == 1
    assert json.loads(output.getvalue()) == expected


@pytest.mark.parametrize(
    "request_text",
    [
        "",
        "{}",
        "[]",
        "{",
        json.dumps({"correlation_id": "corr-only"}),
        json.dumps({
            "correlation_id": "corr-rowboat-cli",
            "plan_id": "plan-rowboat-cli",
            "plan_revision": 2,
            "space_id": ROWBOAT_SPACE,
            "agent_name": ROWBOAT_AGENT,
            "tool": ROWBOAT_TOOL,
            "unexpected": True,
        }),
    ],
)
def test_main_fails_closed_for_invalid_or_missing_request(
    monkeypatch: pytest.MonkeyPatch, request_text: str,
) -> None:
    monkeypatch.setattr(
        DeterministicRowboatGateway,
        "execute",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid CLI input must not reach the gateway")
        ),
    )
    output = StringIO()

    exit_code = main(stdin=StringIO(request_text), stdout=output)

    assert exit_code == 2
    assert output.getvalue().count("\n") == 1
    assert json.loads(output.getvalue()) == {
        "ok": False,
        "status": "blocked",
        "target": ROWBOAT_TARGET,
        "reason": "invalid deterministic Rowboat gateway request",
    }


@pytest.mark.parametrize(
    "transport_failure",
    [
        lambda: requests.exceptions.Timeout("timed out"),
        lambda: _ServerError(),
    ],
)
def test_gateway_never_retries_a_tools_call_transport_failure(
    monkeypatch: pytest.MonkeyPatch, transport_failure: object,
) -> None:
    _configured(monkeypatch)
    mcp_calls = 0

    def _get(_url: str, **_kwargs: object) -> _Response:
        return _Response([{"name": ROWBOAT_AGENT, "id": "agent-rowboat-uuid"}])

    def _post(url: str, **_kwargs: object) -> _Response:
        nonlocal mcp_calls
        if url.endswith("/approvals/admit"):
            return _Response({"status": "approved", "approval_ref": "approval-server-ref"})
        if url.endswith("/cost-reservations"):
            return _Response({"status": "reserved", "reservation": {"cost_ref": "cost-server-ref"}})
        mcp_calls += 1
        failure = transport_failure()
        if isinstance(failure, BaseException):
            raise failure
        if isinstance(failure, _ServerError):
            return failure
        raise AssertionError("test transport failure factory must return a transport failure")

    monkeypatch.setattr("core.openfang_runtime_authority.requests.get", _get)
    monkeypatch.setattr("core.openfang_runtime_authority.requests.post", _post)

    result = _invoke(_gateway())

    assert result == {
        "ok": False,
        "status": "blocked",
        "target": ROWBOAT_TARGET,
        "reason": "OpenFang deterministic MCP invocation failed",
    }
    assert mcp_calls == 1


class _ServerError:
    status_code = 503

    def raise_for_status(self) -> None:
        error = requests.exceptions.HTTPError("OpenFang unavailable")
        error.response = self
        raise error

    def json(self) -> object:
        raise AssertionError("a 5xx response must not be parsed")


def test_dockerfile_is_a_small_gateway_only_build_recipe() -> None:
    dockerfile = os.path.join(os.path.dirname(__file__), "..", "Dockerfile.deterministic-gateway")
    text = open(dockerfile, encoding="utf-8").read()
    lowered = text.lower()

    for forbidden in ("torch", "transformers", "sentence-transformers", "ollama", "start_server.py", "model cache", "accelerator"):
        assert forbidden not in lowered
    assert "deterministic_gateway.py" in lowered
    assert "ENV SPACE_AGENT_REGISTRY_PATH=/app/config/space_agent_registry.yml" in text
    assert "COPY config/space_agent_registry.yml /app/config/space_agent_registry.yml" in text
    assert 'CMD ["python", "-m", "deterministic_gateway"]' in text
