from fastapi.testclient import TestClient
from unittest.mock import MagicMock

import app as app_module


def test_health_ok_when_api_key_present(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    app_module._client = None  # reset the lazy singleton between tests
    client = TestClient(app_module.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_503_when_no_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY_FILE", raising=False)
    app_module._client = None
    client = TestClient(app_module.app)
    resp = client.get("/health")
    assert resp.status_code == 503


class _FakeEmbeddingDatum:
    def __init__(self, vector):
        self.embedding = vector


class _FakeEmbeddingResponse:
    def __init__(self, vectors):
        self.data = [_FakeEmbeddingDatum(v) for v in vectors]


def test_embed_returns_vector(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    app_module._client = None
    fake_client = MagicMock()
    fake_client.embeddings.create.return_value = _FakeEmbeddingResponse([[0.1, 0.2, 0.3]])
    monkeypatch.setattr(app_module, "get_client", lambda: fake_client)

    client = TestClient(app_module.app)
    resp = client.post("/embed", json={"text": "hello world"})

    assert resp.status_code == 200
    assert resp.json()["vector"] == [0.1, 0.2, 0.3]
    fake_client.embeddings.create.assert_called_once_with(
        model=app_module.MODEL, input=["hello world"],
    )


def test_embed_retries_then_succeeds_on_transient_error(monkeypatch):
    from openai import APIConnectionError

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    app_module._client = None
    fake_client = MagicMock()
    fake_request = MagicMock()
    fake_client.embeddings.create.side_effect = [
        APIConnectionError(request=fake_request),
        _FakeEmbeddingResponse([[0.4, 0.5]]),
    ]
    monkeypatch.setattr(app_module, "get_client", lambda: fake_client)
    monkeypatch.setattr(app_module.time, "sleep", lambda _seconds: None)

    client = TestClient(app_module.app)
    resp = client.post("/embed", json={"text": "retry me"})

    assert resp.status_code == 200
    assert resp.json()["vector"] == [0.4, 0.5]
    assert fake_client.embeddings.create.call_count == 2


def test_embed_hard_fails_after_exhausting_retries(monkeypatch):
    from openai import APIConnectionError

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    app_module._client = None
    fake_client = MagicMock()
    fake_request = MagicMock()
    fake_client.embeddings.create.side_effect = APIConnectionError(request=fake_request)
    monkeypatch.setattr(app_module, "get_client", lambda: fake_client)
    monkeypatch.setattr(app_module.time, "sleep", lambda _seconds: None)

    client = TestClient(app_module.app)
    resp = client.post("/embed", json={"text": "always fails"})

    assert resp.status_code == 502
