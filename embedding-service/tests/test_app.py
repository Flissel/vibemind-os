from __future__ import annotations

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import app as app_module


def _gateway_response(vectors: list[list[float]], status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={"object": "list", "data": [{"embedding": vector} for vector in vectors]},
        request=httpx.Request("POST", "http://openfang.test/v1/embeddings"),
    )


class _FakeGatewayClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, dict[str, object]]] = []

    def post(self, path: str, *, json: dict[str, object]) -> httpx.Response:
        self.calls.append((path, json))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, httpx.Response)
        return outcome


def test_service_source_has_no_direct_openai_provider_or_credential_access() -> None:
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    direct_provider_key = "OPENAI" + "_API_KEY"
    direct_provider_import = "from " + "openai"
    direct_provider_constructor = "Open" + "AI("

    assert direct_provider_key not in source
    assert direct_provider_import not in source
    assert direct_provider_constructor not in source


def test_health_fails_closed_when_openfang_url_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("OPENFANG_URL", raising=False)
    app_module._gateway_client = None

    response = TestClient(app_module.app).get("/health")

    assert response.status_code == 503
    assert response.json()["detail"] == "OpenFang gateway not configured"


def test_openfang_gateway_client_uses_canonical_url_and_optional_gateway_key(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Client:
        pass

    def create_client(**kwargs: object) -> _Client:
        captured.update(kwargs)
        return _Client()

    monkeypatch.setenv("OPENFANG_URL", "http://openfang.test/")
    monkeypatch.setenv("OPENFANG_API_KEY", "gateway-test-key")
    monkeypatch.setattr(app_module.httpx, "Client", create_client)
    app_module._gateway_client = None

    app_module.get_gateway_client()

    assert captured == {
        "base_url": "http://openfang.test",
        "headers": {"Authorization": "Bearer gateway-test-key"},
        "timeout": app_module.REQUEST_TIMEOUT_SECONDS,
    }


def test_embed_batch_posts_model_and_input_to_openfang_gateway(monkeypatch) -> None:
    gateway = _FakeGatewayClient([_gateway_response([[0.1, 0.2], [0.3, 0.4]])])
    monkeypatch.setattr(app_module, "get_gateway_client", lambda: gateway)

    response = TestClient(app_module.app).post(
        "/embed/batch", json={"texts": ["first", "second"]}
    )

    assert response.status_code == 200
    assert response.json() == {"vectors": [[0.1, 0.2], [0.3, 0.4]]}
    assert gateway.calls == [
        (
            "/v1/embeddings",
            {"model": app_module.MODEL, "input": ["first", "second"]},
        )
    ]


def test_embed_preserves_single_vector_shape_and_model_compatibility(monkeypatch) -> None:
    gateway = _FakeGatewayClient([_gateway_response([[0.1, 0.2, 0.3]])])
    monkeypatch.setattr(app_module, "get_gateway_client", lambda: gateway)

    response = TestClient(app_module.app).post("/embed", json={"text": "one"})

    assert response.status_code == 200
    assert response.json() == {"vector": [0.1, 0.2, 0.3]}
    assert gateway.calls[0][1]["model"] == "text-embedding-3-large"


def test_embed_retries_once_per_existing_bounded_retry_policy(monkeypatch) -> None:
    request = httpx.Request("POST", "http://openfang.test/v1/embeddings")
    gateway = _FakeGatewayClient(
        [
            httpx.ConnectError("OpenFang unreachable", request=request),
            _gateway_response([[0.4, 0.5]]),
        ]
    )
    monkeypatch.setattr(app_module, "get_gateway_client", lambda: gateway)
    monkeypatch.setattr(app_module.time, "sleep", lambda _seconds: None)

    response = TestClient(app_module.app).post("/embed", json={"text": "retry"})

    assert response.status_code == 200
    assert len(gateway.calls) == 2


def test_embed_fails_closed_after_bounded_openfang_retries(monkeypatch) -> None:
    request = httpx.Request("POST", "http://openfang.test/v1/embeddings")
    gateway = _FakeGatewayClient(
        [httpx.ConnectError("OpenFang unreachable", request=request)]
        * (app_module.MAX_RETRIES + 1)
    )
    monkeypatch.setattr(app_module, "get_gateway_client", lambda: gateway)
    monkeypatch.setattr(app_module.time, "sleep", lambda _seconds: None)

    response = TestClient(app_module.app).post("/embed", json={"text": "fail closed"})

    assert response.status_code == 502
    assert response.json()["detail"] == "embedding request failed"
    assert len(gateway.calls) == app_module.MAX_RETRIES + 1


def test_embed_does_not_retry_non_transient_openfang_response(monkeypatch) -> None:
    gateway = _FakeGatewayClient([_gateway_response([], status_code=400)])
    monkeypatch.setattr(app_module, "get_gateway_client", lambda: gateway)
    monkeypatch.setattr(app_module.time, "sleep", lambda _seconds: None)

    response = TestClient(app_module.app).post("/embed", json={"text": "bad request"})

    assert response.status_code == 502
    assert len(gateway.calls) == 1


def test_embed_batch_rejects_length_mismatched_openfang_response(monkeypatch) -> None:
    gateway = _FakeGatewayClient([_gateway_response([[0.1, 0.2]])])
    monkeypatch.setattr(app_module, "get_gateway_client", lambda: gateway)

    response = TestClient(app_module.app).post(
        "/embed/batch", json={"texts": ["first", "second"]}
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "embedding request failed"
