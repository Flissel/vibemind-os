from __future__ import annotations

from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from spaces.learning.deployment import runtime_api


@dataclass(frozen=True)
class _Chunk:
    chunk_id: str = "chunk-1"
    score: float = 0.91
    course_id: str = "11111111-1111-1111-1111-111111111111"
    source_id: str = "22222222-2222-2222-2222-222222222222"
    source_revision: int = 3
    content: str = "Grounded course content"
    content_hash: str = "a" * 64
    locator: dict[str, object] = field(default_factory=lambda: {"page": 4})
    metadata: dict[str, object] = field(default_factory=lambda: {"title": "Source"})
    ingestion_spec_version: str = "1.0"


class _Retriever:
    def retrieve(self, query: str, *, course_id: str, limit: int):
        assert query == "What matters?"
        assert course_id == "11111111-1111-1111-1111-111111111111"
        assert limit == 5
        return (_Chunk(),)


def test_retrieval_api_requires_internal_credentials(monkeypatch) -> None:
    monkeypatch.setenv("LEARNING_RETRIEVAL_SERVICE_KEY", "k" * 32)
    client = TestClient(runtime_api.app)

    response = client.post(
        "/api/v1/retrieval/query",
        json={
            "query": "What matters?",
            "course_id": "11111111-1111-1111-1111-111111111111",
            "limit": 5,
        },
    )

    assert response.status_code == 403


def test_retrieval_api_returns_bounded_grounded_chunks(monkeypatch) -> None:
    monkeypatch.setenv("LEARNING_RETRIEVAL_SERVICE_KEY", "k" * 32)
    monkeypatch.setattr(runtime_api, "_build_grounded_retriever", lambda: _Retriever())
    client = TestClient(runtime_api.app)

    response = client.post(
        "/api/v1/retrieval/query",
        headers={"X-VibeMind-Learning-Retrieval-Key": "k" * 32},
        json={
            "query": "What matters?",
            "course_id": "11111111-1111-1111-1111-111111111111",
            "limit": 5,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "chunks": [
            {
                "chunk_id": "chunk-1",
                "score": 0.91,
                "course_id": "11111111-1111-1111-1111-111111111111",
                "source_id": "22222222-2222-2222-2222-222222222222",
                "source_revision": 3,
                "content": "Grounded course content",
                "content_hash": "a" * 64,
                "locator": {"page": 4},
                "metadata": {"title": "Source"},
                "ingestion_spec_version": "1.0",
            }
        ]
    }
