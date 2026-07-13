from fastapi.testclient import TestClient

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
