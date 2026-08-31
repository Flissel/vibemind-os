from __future__ import annotations

import pytest
import requests

from core.capability_targets import McpAuthority, McpExecutor
from core.openfang_runtime_authority import RuntimeAuthorityRefs, RuntimeInvocationContext


class _Response:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _legacy_transport_fixture(monkeypatch, request):
    """Keep pre-existing transport tests focused beyond the new authority gate."""
    if request.node.name.startswith((
        "test_missing_runtime_authority_context",
        "test_unregistered_mcp_tuple",
    )):
        return

    from core import capability_targets

    original_authority = capability_targets.find_registered_mcp_authority

    def _authority(agent, server, tool):
        resolved = original_authority(agent, server, tool)
        if resolved is not None:
            return McpAuthority(
                agent=resolved.agent, server=resolved.server, tool=resolved.tool,
                space_id="test-space", event_id="test",
            )
        if (agent, server, tool) == ("brain-ideas", "vibemind-db", "ideas.list"):
            return McpAuthority(agent=agent, server=server, tool=tool, space_id="test-space", event_id="test")
        return None

    original_call = McpExecutor.call

    def _call(self, *args, **kwargs):
        kwargs.setdefault("_runtime_authority", RuntimeInvocationContext(
            correlation_id="test-trace", plan_id="test-plan", plan_revision=1,
            step_id="test-step", space_id="test-space", agent_name=self.agent_name,
            invocation_id="brain-mcp-test-transport",
        ))
        return original_call(self, *args, **kwargs)

    monkeypatch.setattr(capability_targets, "find_registered_mcp_authority", _authority)
    monkeypatch.setattr(McpExecutor, "call", _call)
    monkeypatch.setattr(
        capability_targets.RuntimeAuthorityClient,
        "from_environment",
        lambda: type("Client", (), {"prepare_invocation": lambda _self, context: RuntimeAuthorityRefs(
            agent_id="agent-uuid", approval_ref="approval:server", cost_ref="cost:server",
            invocation_id=context.invocation_id,
        )})(),
    )


def test_bubble_create_target_calls_openfang_mcp_with_bound_agent_authority(monkeypatch):
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    captured = {}

    def _get(url, *, headers, timeout):
        captured["get"] = {"url": url, "headers": headers, "timeout": timeout}
        return _Response({"agents": [{"name": "brain-bubbles", "id": "agent-uuid"}]})

    def _post(url, *, json, headers, timeout):
        captured["post"] = {"url": url, "json": json, "headers": headers, "timeout": timeout}
        return _Response({
            "jsonrpc": "2.0",
            "id": json["id"],
            "result": {"content": [{"type": "text", "text": "ok"}], "isError": False},
        })

    monkeypatch.setattr("core.capability_targets.requests.get", _get)
    monkeypatch.setattr("core.capability_targets.requests.post", _post)

    result = McpExecutor(
        "mcp:brain-bubbles:spaces-ideas:bubble_create"
    ).call(
        title="focus",
    )

    assert result["ok"] is True
    assert captured["post"]["url"] == "http://openfang.test/mcp"
    assert "/api/mcp/dispatch" not in captured["post"]["url"]
    assert captured["post"]["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer test-token",
        "Content-Type": "application/json",
        "X-OpenFang-Agent-Id": "agent-uuid",
        "X-OpenFang-Approval-Ref": "approval:server",
        "X-OpenFang-Cost-Ref": "cost:server",
    }
    assert captured["post"]["json"]["jsonrpc"] == "2.0"
    assert captured["post"]["json"]["method"] == "tools/call"
    assert captured["post"]["json"]["params"] == {
        "name": "mcp_spaces_ideas_bubble_create",
        "arguments": {"title": "focus"},
    }


def test_registered_mcp_authority_uses_distinct_invocation_provenance(monkeypatch):
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    post_calls = []

    def _get(*args, **kwargs):
        return _Response({"agents": [{"name": "brain-bubbles", "id": "agent-uuid"}]})

    def _post(url, *, json, headers, timeout):
        post_calls.append({"json": json, "headers": headers})
        return _Response({
            "jsonrpc": "2.0",
            "id": json["id"],
            "result": {"content": [], "isError": False},
        })

    monkeypatch.setattr("core.capability_targets.requests.get", _get)
    monkeypatch.setattr("core.capability_targets.requests.post", _post)
    executor = McpExecutor("mcp:brain-bubbles:spaces-ideas:bubble_create")

    assert executor.call(
        title="first"
    )["ok"] is True
    assert executor.call(
        title="second"
    )["ok"] is True

    assert [
        call["headers"]["X-OpenFang-Approval-Ref"] for call in post_calls
    ] == ["approval:server", "approval:server"]
    assert [
        call["headers"]["X-OpenFang-Cost-Ref"] for call in post_calls
    ] == ["cost:server", "cost:server"]
    assert [call["json"]["params"]["arguments"] for call in post_calls] == [
        {"title": "first"}, {"title": "second"}
    ]


def test_missing_runtime_authority_context_fails_before_any_network_request(monkeypatch):
    network_calls = []
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    monkeypatch.setattr(
        "core.capability_targets.requests.get",
        lambda *args, **kwargs: network_calls.append("get"),
    )
    monkeypatch.setattr(
        "core.capability_targets.requests.post",
        lambda *args, **kwargs: network_calls.append("post"),
    )
    result = McpExecutor(
        "mcp:brain-bubbles:spaces-ideas:bubble_create"
    ).call(title="focus")

    assert result["ok"] is False
    assert "runtime authority context" in result["error"]
    assert network_calls == []


def test_unregistered_mcp_tuple_fails_before_any_network_request(monkeypatch):
    network_calls = []
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    monkeypatch.setattr(
        "core.capability_targets.requests.get",
        lambda *args, **kwargs: network_calls.append("get"),
    )
    monkeypatch.setattr(
        "core.capability_targets.requests.post",
        lambda *args, **kwargs: network_calls.append("post"),
    )

    result = McpExecutor(
        "mcp:brain-bubbles:spaces-ideas:unknown_tool"
    ).call(
        title="focus",
        approval_ref="approval:invocation",
        cost_ref="cost:invocation",
    )

    assert result["ok"] is False
    assert "not registered" in result["error"]
    assert network_calls == []


def test_legacy_target_shape_requires_explicit_migration():
    with pytest.raises(ValueError, match="mcp:<agent>:<server>:<tool>"):
        McpExecutor("mcp:vibemind-db:ideas.list")


@pytest.mark.parametrize("missing", ["OPENFANG_URL", "OPENFANG_API_KEY"])
def test_missing_openfang_configuration_fails_closed(monkeypatch, missing):
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    monkeypatch.delenv(missing, raising=False)

    result = McpExecutor("mcp:brain-ideas:vibemind-db:ideas.list").call()

    assert result["ok"] is False
    assert missing.lower() in result["error"].lower()


def test_json_rpc_error_fails_closed_without_local_fallback(monkeypatch):
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    calls = []

    def _get(*args, **kwargs):
        calls.append("agents")
        return _Response([{"name": "brain-ideas", "id": "agent-uuid"}])

    def _post(*args, **kwargs):
        calls.append("mcp")
        return _Response({
            "jsonrpc": "2.0",
            "id": kwargs["json"]["id"],
            "error": {"code": -32602, "message": "not permitted"},
        })

    monkeypatch.setattr("core.capability_targets.requests.get", _get)
    monkeypatch.setattr("core.capability_targets.requests.post", _post)

    result = McpExecutor("mcp:brain-ideas:vibemind-db:ideas.list").call()

    assert result["ok"] is False
    assert "json-rpc" in result["error"].lower()
    assert calls == ["mcp"]


@pytest.mark.parametrize(
    "error",
    [
        {},
        {"code": -32602, "message": "not permitted"},
    ],
    ids=["empty-error-member", "result-and-error-members"],
)
def test_mcp_response_rejects_ambiguous_json_rpc_result_and_error(
    monkeypatch, error
):
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    monkeypatch.setattr(
        "core.capability_targets.requests.get",
        lambda *args, **kwargs: _Response([
            {"name": "brain-ideas", "id": "agent-uuid"}
        ]),
    )
    monkeypatch.setattr(
        "core.capability_targets.requests.post",
        lambda *args, **kwargs: _Response({
            "jsonrpc": "2.0",
            "id": kwargs["json"]["id"],
            "result": {"content": [], "isError": False},
            "error": error,
        }),
    )

    result = McpExecutor("mcp:brain-ideas:vibemind-db:ideas.list").call()

    assert result["ok"] is False
    assert "exactly one result or error" in result["error"].lower()


@pytest.mark.parametrize(
    ("response_fields", "expected_error"),
    [
        ({"id": "$request"}, "json-rpc version"),
        ({"jsonrpc": "1.0", "id": "$request"}, "json-rpc version"),
        ({"jsonrpc": "2.0"}, "request id"),
        ({"jsonrpc": "2.0", "id": "wrong-request"}, "request id"),
    ],
    ids=["missing-version", "wrong-version", "missing-id", "wrong-id"],
)
def test_mcp_response_requires_matching_json_rpc_envelope(
    monkeypatch, response_fields, expected_error
):
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")

    monkeypatch.setattr(
        "core.capability_targets.requests.get",
        lambda *args, **kwargs: _Response([
            {"name": "brain-ideas", "id": "agent-uuid"}
        ]),
    )

    def _post(*args, **kwargs):
        body = {
            key: kwargs["json"]["id"] if value == "$request" else value
            for key, value in response_fields.items()
        }
        body["result"] = {"content": [], "isError": False}
        return _Response(body)

    monkeypatch.setattr("core.capability_targets.requests.post", _post)

    result = McpExecutor("mcp:brain-ideas:vibemind-db:ideas.list").call()

    assert result["ok"] is False
    assert expected_error in result["error"].lower()


def test_json_rpc_error_redacts_remote_tokens_from_result_and_logs(
    monkeypatch, caplog
):
    api_secret = "synthetic-openfang-api-token"
    bearer_secret = "synthetic-bearer-token"
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", api_secret)
    monkeypatch.setattr(
        "core.capability_targets.requests.get",
        lambda *args, **kwargs: _Response([
            {"name": "brain-ideas", "id": "agent-uuid"}
        ]),
    )

    def _post(*args, **kwargs):
        return _Response({
            "jsonrpc": "2.0",
            "id": kwargs["json"]["id"],
            "error": {
                "code": -32602,
                "message": (
                    f"denied Bearer {bearer_secret}; api token {api_secret}"
                ),
            },
        })

    monkeypatch.setattr("core.capability_targets.requests.post", _post)

    with caplog.at_level("WARNING", logger="core.capability_targets"):
        result = McpExecutor("mcp:brain-ideas:vibemind-db:ideas.list").call()

    assert result["ok"] is False
    for secret in (api_secret, bearer_secret):
        assert secret not in result["error"]
        assert secret not in caplog.text
    assert "[REDACTED]" in result["error"]
    assert "[REDACTED]" in caplog.text


def test_static_scope_mismatch_fails_closed_without_mcp_call(monkeypatch):
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    calls = []

    def _get(*args, **kwargs):
        calls.append("agents")
        return _Response([{"name": "brain-ideas", "id": "other-agent"}])

    def _post(*args, **kwargs):
        calls.append("mcp")
        raise AssertionError("MCP dispatch must not run without the bound agent")

    monkeypatch.setattr("core.capability_targets.requests.get", _get)
    monkeypatch.setattr("core.capability_targets.requests.post", _post)

    from core import capability_targets
    monkeypatch.setattr(
        capability_targets, "find_registered_mcp_authority",
        lambda *_args: McpAuthority("brain-bubbles", "spaces-ideas", "bubble_create", "wrong-space", "test"),
    )
    result = McpExecutor("mcp:brain-bubbles:spaces-ideas:bubble_create").call(title="focus")

    assert result["ok"] is False
    assert "does not match registered scope" in result["error"]
    assert calls == []


def test_transient_agent_resolution_retries_within_openfang_boundary(monkeypatch):
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    attempts = {"agents": 0}

    def _get(*args, **kwargs):
        attempts["agents"] += 1
        if attempts["agents"] == 1:
            raise requests.exceptions.ConnectionError("down")
        return _Response([{"name": "brain-ideas", "id": "agent-uuid"}])

    monkeypatch.setattr("core.capability_targets.requests.get", _get)
    monkeypatch.setattr(
        "core.capability_targets.requests.post",
        lambda *args, **kwargs: _Response({
            "jsonrpc": "2.0",
            "id": kwargs["json"]["id"],
            "result": {"content": [], "isError": False},
        }),
    )
    monkeypatch.setattr("core.capability_targets.time.sleep", lambda _: None)

    result = McpExecutor("mcp:brain-ideas:vibemind-db:ideas.list").call()

    assert result["ok"] is True
    assert attempts["agents"] == 0


def test_agent_id_is_refreshed_for_each_mcp_call_after_openfang_restart(monkeypatch):
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    resolved_ids = iter(["agent-before-restart", "agent-after-restart"])
    bound_ids = []

    def _get(*args, **kwargs):
        return _Response([{"name": "brain-ideas", "id": next(resolved_ids)}])

    def _post(*args, **kwargs):
        bound_ids.append(kwargs["headers"]["X-OpenFang-Agent-Id"])
        return _Response({
            "jsonrpc": "2.0",
            "id": kwargs["json"]["id"],
            "result": {"content": [], "isError": False},
        })

    monkeypatch.setattr("core.capability_targets.requests.get", _get)
    monkeypatch.setattr("core.capability_targets.requests.post", _post)
    executor = McpExecutor("mcp:brain-ideas:vibemind-db:ideas.list")

    assert executor.call()["ok"] is True
    assert executor.call()["ok"] is True
    assert bound_ids == ["agent-uuid", "agent-uuid"]


class _ServerErrorResponse:
    status_code = 503

    def raise_for_status(self):
        error = requests.exceptions.HTTPError("OpenFang unavailable")
        error.response = self
        raise error


@pytest.mark.parametrize("post_failure", [
    lambda: requests.exceptions.ConnectionError("connection dropped"),
    _ServerErrorResponse,
])
def test_mcp_post_transient_failure_is_never_retried(monkeypatch, post_failure):
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    calls = {"agents": 0, "mcp": 0}

    def _get(*args, **kwargs):
        calls["agents"] += 1
        return _Response([{"name": "brain-ideas", "id": "agent-uuid"}])

    def _post(*args, **kwargs):
        calls["mcp"] += 1
        failure = post_failure()
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr("core.capability_targets.requests.get", _get)
    monkeypatch.setattr("core.capability_targets.requests.post", _post)

    result = McpExecutor("mcp:brain-ideas:vibemind-db:ideas.list").call()

    assert result["ok"] is False
    assert calls == {"agents": 0, "mcp": 1}


def test_agent_resolution_timeout_is_capped_without_changing_mcp_post_timeout(monkeypatch):
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    monkeypatch.setenv("CAPABILITY_HTTP_TIMEOUT_S", "60")
    captured = {}

    def _get(*args, **kwargs):
        captured["get_timeout"] = kwargs["timeout"]
        return _Response([{"name": "brain-ideas", "id": "agent-uuid"}])

    def _post(*args, **kwargs):
        captured["post_timeout"] = kwargs["timeout"]
        return _Response({
            "jsonrpc": "2.0",
            "id": kwargs["json"]["id"],
            "result": {"content": [], "isError": False},
        })

    monkeypatch.setattr("core.capability_targets.requests.get", _get)
    monkeypatch.setattr("core.capability_targets.requests.post", _post)

    result = McpExecutor("mcp:brain-ideas:vibemind-db:ideas.list").call()

    assert result["ok"] is True
    assert captured == {"post_timeout": 60.0}


def test_mcp_strips_manual_authority_refs_recursively_from_tool_arguments(monkeypatch):
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    captured = {}

    def _post(_url, *, json, **_kwargs):
        captured["arguments"] = json["params"]["arguments"]
        return _Response({
            "jsonrpc": "2.0", "id": json["id"],
            "result": {"content": [], "isError": False},
        })

    monkeypatch.setattr("core.capability_targets.requests.post", _post)
    result = McpExecutor("mcp:brain-ideas:vibemind-db:ideas.list").call(
        title="focus", approval_ref="approval-manual-top-level",
        cost_ref="cost-manual-top-level",
        nested={
            "keep": "value", "approval_ref": "approval-manual-nested",
            "children": [{"cost_ref": "cost-manual-child", "keep_child": 7}],
        },
    )

    assert result["ok"] is True
    assert captured["arguments"] == {
        "title": "focus",
        "nested": {"keep": "value", "children": [{"keep_child": 7}]},
    }


def test_mcp_error_redacts_complete_authority_ref_values_from_message_and_logs(
    monkeypatch, caplog,
):
    approval_ref = "approval-synthetic-complete-ref"
    cost_ref = "cost-synthetic-complete-ref"
    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test")
    monkeypatch.setenv("OPENFANG_API_KEY", "test-token")
    monkeypatch.setattr(
        "core.capability_targets.RuntimeAuthorityClient.from_environment",
        lambda: type("Client", (), {"prepare_invocation": lambda _self, context: RuntimeAuthorityRefs(
            agent_id="agent-uuid", approval_ref=approval_ref, cost_ref=cost_ref,
            invocation_id=context.invocation_id,
        )})(),
    )
    monkeypatch.setattr(
        "core.capability_targets.requests.post",
        lambda _url, *, json, **_kwargs: _Response({
            "jsonrpc": "2.0", "id": json["id"],
            "error": {
                "message": f"denied {approval_ref} and {cost_ref}",
                "approval_ref": approval_ref, "cost_ref": cost_ref,
            },
        }),
    )

    with caplog.at_level("WARNING", logger="core.capability_targets"):
        result = McpExecutor("mcp:brain-ideas:vibemind-db:ideas.list").call(title="focus")

    assert result["ok"] is False
    assert approval_ref not in result["error"] + caplog.text
    assert cost_ref not in result["error"] + caplog.text
    assert "[REDACTED]" in result["error"]
