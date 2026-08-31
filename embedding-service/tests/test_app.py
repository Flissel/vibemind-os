from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from urllib.error import URLError

import pytest
from fastapi.testclient import TestClient


class _Backend:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors
        self.calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return self.vectors


class _HealthResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _HealthResponse:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


@pytest.fixture
def service_module(monkeypatch: pytest.MonkeyPatch):
    backend = _Backend([[0.1, 0.2, 0.3]])
    shared = types.ModuleType("vibemind_shared")
    shared.get_embedding_config = lambda role: {
        "driver": "openai",
        "provider": "openfang",
        "model": "text-embedding-3-large",
        "dim": 3072,
    }
    shared.get_embedding_model = lambda role: backend
    shared.get_provider_info = lambda role: {
        "provider": "openfang",
        "base_url": "http://openfang.test/v1",
        "timeout_seconds": 8.0,
    }
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)
    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    return module, backend


def test_service_source_has_no_service_owned_provider_or_transport_policy(service_module) -> None:
    module, _ = service_module
    source = Path(module.__file__).read_text(encoding="utf-8")

    for forbidden in (
        "OPENFANG_URL",
        "OPENFANG_API_KEY",
        "httpx",
        "EMBEDDING_MODEL",
        "MAX_RETRIES",
        "RETRY_BACKOFF",
    ):
        assert forbidden not in source


def test_container_uses_one_pinned_shared_source_with_the_existing_service_context() -> None:
    service_dir = Path(__file__).resolve().parents[1]
    dockerfile = (service_dir / "Dockerfile").read_text(encoding="utf-8")
    requirements = (service_dir / "requirements.txt").read_text(encoding="utf-8")

    assert "COPY shared/" not in dockerfile
    assert "COPY requirements.txt ." in dockerfile
    assert "COPY app.py ." in dockerfile
    assert "apt-get install -y --no-install-recommends git" in dockerfile
    shared_requirements = [
        line
        for line in requirements.splitlines()
        if line.startswith("vibemind-shared @")
    ]
    assert shared_requirements == [
        "vibemind-shared @ git+https://github.com/Flissel/vibemind-shared.git"
        "@609dda92e0f4370c03085a7e2ff6a6f492693d0d"
    ]


def test_embed_batch_uses_only_the_fungus_search_shared_factory_role(
    service_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, backend = service_module
    roles: list[str] = []
    monkeypatch.setattr(
        module,
        "get_embedding_config",
        lambda role: roles.append(role) or {
            "driver": "openai",
            "provider": "openfang",
            "model": "text-embedding-3-large",
            "dim": 3072,
        },
    )
    monkeypatch.setattr(module, "get_embedding_model", lambda role: roles.append(role) or backend)
    backend.vectors = [[0.1] * 3072, [0.2] * 3072]

    response = TestClient(module.app).post(
        "/embed/batch", json={"texts": ["first", "second"]}
    )

    assert response.status_code == 200
    assert [len(vector) for vector in response.json()["vectors"]] == [3072, 3072]
    assert roles == ["fungus_search", "fungus_search"]
    assert backend.calls == [["first", "second"]]


def test_embed_preserves_fungus_search_model_and_3072_dimension(service_module) -> None:
    module, backend = service_module
    backend.vectors = [[0.1] * 3072]

    response = TestClient(module.app).post("/embed", json={"text": "one"})

    assert response.status_code == 200
    assert len(response.json()["vector"]) == 3072


def test_embed_fails_closed_when_fungus_search_config_dimension_is_not_3072(
    service_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _ = service_module
    monkeypatch.setattr(
        module,
        "get_embedding_config",
        lambda role: {
            "driver": "openai",
            "provider": "openfang",
            "model": "text-embedding-3-large",
            "dim": 1536,
        },
    )

    response = TestClient(module.app).post("/embed", json={"text": "wrong config dim"})

    assert response.status_code == 502
    assert response.json()["detail"] == "embedding request failed"


@pytest.mark.parametrize(
    "vectors",
    [
        [[0.1] * 1536],
        [["not-a-number"] * 3072],
    ],
)
def test_embed_fails_closed_on_invalid_shared_vector_shape_or_values(service_module, vectors) -> None:
    module, backend = service_module
    backend.vectors = vectors

    response = TestClient(module.app, raise_server_exceptions=False).post(
        "/embed", json={"text": "invalid vector"}
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "embedding request failed"


def test_embed_batch_fails_closed_when_shared_vector_count_mismatches_input(service_module) -> None:
    module, backend = service_module
    backend.vectors = [[0.1] * 3072]

    response = TestClient(module.app).post(
        "/embed/batch", json={"texts": ["first", "second"]}
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "embedding request failed"


def test_embed_fails_closed_when_shared_embedding_config_is_missing(
    service_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, backend = service_module
    monkeypatch.setattr(
        module,
        "get_embedding_config",
        lambda role: (_ for _ in ()).throw(FileNotFoundError("missing llm_config.yml")),
    )

    response = TestClient(module.app).post("/embed", json={"text": "missing config"})

    assert response.status_code == 502
    assert response.json()["detail"] == "embedding request failed"
    assert backend.calls == []


def test_embed_does_not_add_a_retry_outside_shared_factory_authority(
    service_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _ = service_module
    calls: list[str] = []

    def unavailable_factory(role: str):
        calls.append(role)
        raise RuntimeError("OpenFang unavailable after shared retry budget")

    monkeypatch.setattr(module, "get_embedding_model", unavailable_factory)

    response = TestClient(module.app).post("/embed", json={"text": "fail closed"})

    assert response.status_code == 502
    assert calls == ["fungus_search"]


def test_health_fails_closed_when_shared_factory_configuration_is_missing(
    service_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _ = service_module
    monkeypatch.setattr(
        module,
        "get_embedding_config",
        lambda role: (_ for _ in ()).throw(FileNotFoundError("missing llm_config.yml")),
    )

    response = TestClient(module.app).get("/health")

    assert response.status_code == 503
    assert response.json()["detail"] == "embedding service unavailable"


def test_health_fails_closed_when_configured_openfang_is_unreachable(
    service_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _ = service_module
    calls: list[tuple[str, float]] = []

    def unreachable(url: str, *, timeout: float):
        calls.append((url, timeout))
        raise URLError("connection refused")

    monkeypatch.setattr(module, "urlopen", unreachable)

    response = TestClient(module.app).get("/health")

    assert response.status_code == 503
    assert response.json()["detail"] == "embedding service unavailable"
    assert calls == [("http://openfang.test/api/health", 8.0)]


def test_health_uses_fungus_search_provider_info_and_fails_closed_on_mismatch(
    service_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _ = service_module
    roles: list[str] = []
    monkeypatch.setattr(
        module,
        "get_provider_info",
        lambda role: roles.append(role) or {
            "provider": "openai",
            "base_url": "http://openfang.test/v1",
            "timeout_seconds": 8.0,
        },
    )

    response = TestClient(module.app).get("/health")

    assert response.status_code == 503
    assert response.json()["detail"] == "embedding service unavailable"
    assert roles == ["fungus_search"]


def test_health_returns_200_only_when_configured_openfang_is_reachable(
    service_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _ = service_module
    roles: list[str] = []
    monkeypatch.setattr(
        module,
        "get_provider_info",
        lambda role: roles.append(role) or {
            "provider": "openfang",
            "base_url": "http://openfang.test/v1",
            "timeout_seconds": 8.0,
        },
    )
    monkeypatch.setattr(module, "urlopen", lambda url, *, timeout: _HealthResponse(200))

    response = TestClient(module.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "model": "text-embedding-3-large",
        "dim": 3072,
    }
    assert roles == ["fungus_search"]


def test_health_fails_closed_on_non_success_openfang_health_response(
    service_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _ = service_module
    monkeypatch.setattr(module, "urlopen", lambda url, *, timeout: _HealthResponse(503))

    response = TestClient(module.app).get("/health")

    assert response.status_code == 503
    assert response.json()["detail"] == "embedding service unavailable"
