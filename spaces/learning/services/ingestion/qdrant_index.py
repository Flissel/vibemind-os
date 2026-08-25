from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import httpx


COLLECTION_NAME = "learning_knowledge_v1"
COLLECTION_ALIAS = "learning_knowledge_current"


class QdrantUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class QdrantPoint:
    point_id: str
    vector: list[float]
    payload: dict[str, object]


@dataclass(frozen=True)
class QdrantHit:
    point_id: str
    score: float
    payload: dict[str, object]


class QdrantIndex:
    def __init__(
        self,
        base_url: str,
        *,
        vector_size: int,
        timeout_seconds: float = 10.0,
        collection_name: str = COLLECTION_NAME,
        collection_alias: str = COLLECTION_ALIAS,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Qdrant URL must use HTTP or HTTPS")
        if vector_size < 1 or timeout_seconds <= 0:
            raise ValueError("Qdrant vector size and timeout must be positive")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )
        self._vector_size = vector_size
        self._collection_name = collection_name
        self._collection_alias = collection_alias

    def ensure_schema(self) -> None:
        response = self._request("GET", f"/collections/{self._collection_name}")
        if response.status_code == 404:
            self._request(
                "PUT",
                f"/collections/{self._collection_name}",
                json={
                    "vectors": {
                        "size": self._vector_size,
                        "distance": "Cosine",
                    }
                },
                expected={200},
            )
        else:
            self._expect(response, {200})
            result = _result_object(response)
            vectors = (
                result.get("config", {}).get("params", {}).get("vectors", {})
                if isinstance(result.get("config"), dict)
                else {}
            )
            if not isinstance(vectors, dict) or (
                vectors.get("size") != self._vector_size
                or str(vectors.get("distance", "")).lower() != "cosine"
            ):
                raise QdrantUnavailable("Qdrant collection schema mismatch")
        aliases = self._request(
            "GET", f"/collections/{self._collection_name}/aliases", expected={200}
        ).json()
        alias_rows = aliases.get("result", {}).get("aliases", [])
        if not any(
            isinstance(row, dict) and row.get("alias_name") == self._collection_alias
            for row in alias_rows
        ):
            self._request(
                "POST",
                "/collections/aliases",
                json={
                    "actions": [
                        {
                            "create_alias": {
                                "collection_name": self._collection_name,
                                "alias_name": self._collection_alias,
                            }
                        }
                    ]
                },
                expected={200},
            )
        for field_name, field_schema in (
            ("course_id", "keyword"),
            ("source_id", "keyword"),
            ("source_revision", "integer"),
            ("tombstone", "bool"),
        ):
            self._request(
                "PUT",
                f"/collections/{self._collection_name}/index",
                params={"wait": "true"},
                json={"field_name": field_name, "field_schema": field_schema},
                expected={200},
            )

    def upsert(self, points: list[QdrantPoint]) -> None:
        if not points:
            return
        rows: list[dict[str, object]] = []
        for point in points:
            if len(point.vector) != self._vector_size or not _valid_vector(point.vector):
                raise ValueError("Qdrant vector dimension mismatch")
            _validate_payload(point.payload)
            rows.append(
                {
                    "id": point.point_id,
                    "vector": point.vector,
                    "payload": point.payload,
                }
            )
        self._request(
            "PUT",
            f"/collections/{self._collection_name}/points",
            params={"wait": "true"},
            json={"points": rows},
            expected={200},
        )

    def tombstone(
        self,
        point_ids: list[str],
        *,
        source_id: str,
        source_revision: int,
    ) -> None:
        if not point_ids:
            return
        self._request(
            "POST",
            f"/collections/{self._collection_name}/points/payload",
            params={"wait": "true"},
            json={
                "points": point_ids,
                "payload": {
                    "tombstone": True,
                    "tombstone_source_id": source_id,
                    "tombstone_source_revision": source_revision,
                },
            },
            expected={200, 404},
        )

    def tombstone_all(self, *, rebuild_run_id: str) -> None:
        self._request(
            "POST",
            f"/collections/{self._collection_name}/points/payload",
            params={"wait": "true"},
            json={
                "filter": {},
                "payload": {
                    "tombstone": True,
                    "tombstone_rebuild_run_id": rebuild_run_id,
                },
            },
            expected={200},
        )

    def retrieve(self, point_ids: list[str]) -> tuple[QdrantHit, ...]:
        if not point_ids:
            return ()
        response = self._request(
            "POST",
            f"/collections/{self._collection_alias}/points",
            json={
                "ids": point_ids,
                "with_payload": True,
                "with_vector": False,
            },
            expected={200},
        )
        rows = response.json().get("result", [])
        if not isinstance(rows, list):
            raise QdrantUnavailable("Qdrant retrieve response is invalid")
        return tuple(_hit_from_row(row, score=1.0) for row in rows)

    def count_active(self) -> int:
        response = self._request(
            "POST",
            f"/collections/{self._collection_alias}/points/count",
            json={
                "filter": {
                    "must": [
                        {"key": "tombstone", "match": {"value": False}}
                    ]
                },
                "exact": True,
            },
            expected={200},
        )
        count = response.json().get("result", {}).get("count")
        if isinstance(count, bool) or not isinstance(count, int):
            raise QdrantUnavailable("Qdrant count response is invalid")
        return count

    def search(
        self,
        vector: list[float],
        *,
        course_id: str,
        limit: int,
        source_id: str | None = None,
        source_revision: int | None = None,
    ) -> tuple[QdrantHit, ...]:
        if (
            len(vector) != self._vector_size
            or not _valid_vector(vector)
            or limit < 1
            or limit > 100
        ):
            raise ValueError("Qdrant search input is invalid")
        conditions: list[dict[str, object]] = [
            {"key": "course_id", "match": {"value": course_id}},
            {"key": "tombstone", "match": {"value": False}},
        ]
        if source_id is not None:
            conditions.append({"key": "source_id", "match": {"value": source_id}})
        if source_revision is not None:
            conditions.append(
                {
                    "key": "source_revision",
                    "match": {"value": source_revision},
                }
            )
        response = self._request(
            "POST",
            f"/collections/{self._collection_alias}/points/search",
            json={
                "vector": vector,
                "limit": limit,
                "with_payload": True,
                "with_vector": False,
                "filter": {"must": conditions},
            },
            expected={200},
        )
        rows = response.json().get("result", [])
        if not isinstance(rows, list):
            raise QdrantUnavailable("Qdrant search response is invalid")
        hits: list[QdrantHit] = []
        for row in rows:
            hits.append(_hit_from_row(row))
        return tuple(hits)

    def collection_names(self) -> tuple[str, str]:
        return self._collection_name, self._collection_alias

    def close(self) -> None:
        self._client.close()

    def delete_collection_for_test(self) -> None:
        response = self._request("DELETE", f"/collections/{self._collection_name}")
        if response.status_code not in {200, 404}:
            self._expect(response, {200, 404})

    def _request(
        self,
        method: str,
        path: str,
        *,
        expected: set[int] | None = None,
        **kwargs: object,
    ) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise QdrantUnavailable("Qdrant request failed") from error
        if expected is not None:
            self._expect(response, expected)
        return response

    @staticmethod
    def _expect(response: httpx.Response, expected: Iterable[int]) -> None:
        if response.status_code not in expected:
            raise QdrantUnavailable(
                f"Qdrant returned unexpected HTTP {response.status_code}"
            )


def _result_object(response: httpx.Response) -> dict[str, object]:
    result = response.json().get("result")
    if not isinstance(result, dict):
        raise QdrantUnavailable("Qdrant collection response is invalid")
    return result


def _validate_payload(payload: dict[str, object]) -> None:
    required = {
        "chunk_id",
        "course_id",
        "source_id",
        "source_revision",
        "source_title",
        "artifact_id",
        "ordinal",
        "content",
        "content_hash",
        "locator",
        "metadata",
        "ingestion_spec_version",
        "tombstone",
    }
    if (
        not required.issubset(payload)
        or not isinstance(payload.get("locator"), dict)
        or not payload["locator"]
    ):
        raise ValueError("Qdrant point payload is incomplete")


def _hit_from_row(row: object, *, score: float | None = None) -> QdrantHit:
    if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
        raise QdrantUnavailable("Qdrant point response is invalid")
    return QdrantHit(
        point_id=str(row.get("id")),
        score=float(row.get("score", score if score is not None else 0.0)),
        payload=dict(row["payload"]),
    )


def _valid_vector(vector: list[float]) -> bool:
    return all(
        not isinstance(component, bool)
        and isinstance(component, (int, float))
        and math.isfinite(component)
        for component in vector
    )
