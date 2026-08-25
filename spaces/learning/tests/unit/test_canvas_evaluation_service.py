from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from spaces.learning.contracts.penecho import (
    CanvasArtifactV1,
    CanvasDocumentV1,
    CanvasObjectV1,
    CanvasSubmissionV1,
    SourceReferenceV1,
)
from spaces.learning.deployment import evaluation_api
from spaces.learning.mcp.tools.canvas import CanvasEvaluationClient
from spaces.learning.services.evaluation.canvas_artifacts import encode_canvas_document
from spaces.learning.services.evaluation.schemas import (
    RubricCriterion,
    RubricDecision,
    RubricEvaluationRequest,
)


PNG = b"\x89PNG\r\n\x1a\nworker-boundary"


def _submission() -> CanvasSubmissionV1:
    document = CanvasDocumentV1(
        project_id=uuid4(),
        canvas_id=uuid4(),
        revision=1,
        background="white",
        objects=(
            CanvasObjectV1(
                object_id="authority",
                object_type="text",
                x=0,
                y=0,
                width=100,
                height=30,
                rotation=0,
                text="OpenFang",
            ),
        ),
    )
    structured = encode_canvas_document(document)
    return CanvasSubmissionV1(
        document=document,
        structured_artifact=CanvasArtifactV1(
            artifact_id=uuid4(),
            artifact_kind="structured_canvas",
            media_type="application/json",
            sha256=hashlib.sha256(structured).hexdigest(),
            size_bytes=len(structured),
            locator="artifact://learning/structured.json",
            canvas_revision=1,
        ),
        rendered_snapshot=CanvasArtifactV1(
            artifact_id=uuid4(),
            artifact_kind="rendered_snapshot",
            media_type="image/png",
            sha256=hashlib.sha256(PNG).hexdigest(),
            size_bytes=len(PNG),
            locator="artifact://learning/snapshot.png",
            canvas_revision=1,
        ),
    )


def test_canvas_client_uses_authenticated_worker_contract() -> None:
    session_id, evaluation_id, submission_id = uuid4(), uuid4(), uuid4()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.headers["X-VibeMind-Learning-Evaluation-Key"] == "k" * 32
        if request.url.path == "/internal/canvas/submit":
            payload = json.loads(request.content)
            assert payload["session_id"] == str(session_id)
            assert base64.b64decode(payload["snapshot_png_base64"]) == PNG
            assert "snapshot_png" not in payload
            return httpx.Response(200, json={
                "result": {
                    "version": "1",
                    "evaluation_id": str(evaluation_id),
                    "submission_id": str(submission_id),
                    "score": 1,
                    "confidence": 0.9,
                    "accepted": True,
                    "criteria": [{
                        "version": "1",
                        "criterion_id": "authority",
                        "score": 1,
                        "feedback": "Grounded.",
                        "source_refs": payload["source_refs"],
                    }],
                    "misconception_tags": [],
                    "evaluator_versions": {
                        "model": "worker-v1",
                        "prompt": "canvas-v1",
                        "sources": "course-r1",
                    },
                },
                "turn": {
                    "session_id": str(session_id),
                    "course_id": str(uuid4()),
                    "session_revision": 4,
                    "state": "completed",
                    "mode": "training",
                    "task": None,
                    "progress": {"completed": 1, "total": 1},
                    "feedback": None,
                    "summary": {"attempted": 1, "correct": 1, "score": 1, "results": []},
                    "audit": {
                        "response_id": str(uuid4()),
                        "evaluation_id": str(evaluation_id),
                        "mastery_applied": True,
                        "readback_verified": True,
                    },
                },
            })
        if request.url.path == "/internal/rubric/evaluate":
            payload = json.loads(request.content)
            return httpx.Response(200, json={
                "decision": {
                    "evaluation_id": payload["evaluation_id"],
                    "response_id": payload["response_id"],
                    "score": 1,
                    "confidence": 0.9,
                    "accepted": True,
                    "rationale_codes": ["rubric_accepted"],
                    "criterion_results": [{
                        "criterion_id": "authority",
                        "awarded_points": 1,
                        "feedback": "Grounded.",
                        "source_refs": ["source://authority/1"],
                    }],
                    "misconception_tags": [],
                    "evaluator_versions": {
                        "model": "worker-v1", "prompt": "rubric-v1", "sources": "course-r1",
                    },
                    "source_refs": ["source://authority/1"],
                    "evidence_ref": "openfang://completion/rubric-worker-1",
                }
            })
        if request.url.path.endswith("/review"):
            return httpx.Response(200, json={"review": {"review_status": "not_required"}})
        return httpx.Response(200, json={"exists": True})

    transport = httpx.MockTransport(handler)
    client = CanvasEvaluationClient(
        base_url="http://learning-worker:8092",
        service_key="k" * 32,
        http_client=httpx.Client(transport=transport),
    )
    source = SourceReferenceV1(
        source_id="authority",
        revision=1,
        locator="source://authority/1",
        title="Authority",
    )

    outcome = client.submit(
        session_id=str(session_id),
        actor_id="student-1",
        expected_session_revision=2,
        submission_id=str(submission_id),
        submission=_submission(),
        snapshot_png=PNG,
        source_refs=(source,),
    )

    assert outcome.result.evaluation_id == evaluation_id
    rubric = client.evaluate(RubricEvaluationRequest(
        evaluation_id=str(uuid4()), response_id=str(uuid4()),
        response={"text": "OpenFang"}, expected_answer="Use OpenFang.",
        rubric=[RubricCriterion(
            criterion_id="authority", description="Uses authority", points=1,
        )],
        source_refs=["source://authority/1"], model_version="worker-v1",
        prompt_version="rubric-v1", source_version="course-r1",
    ))
    assert rubric.accepted is True
    assert client.has_evaluation(str(evaluation_id)) is True
    assert client.has_session_revision(str(session_id), 4) is True
    assert client.review(str(evaluation_id)) == {"review_status": "not_required"}
    assert calls == [
        "/internal/canvas/submit",
        "/internal/rubric/evaluate",
        f"/internal/evaluations/{evaluation_id}",
        f"/internal/sessions/{session_id}/revisions/4",
        f"/internal/evaluations/{evaluation_id}/review",
    ]


def test_worker_api_rejects_missing_key_before_service_access(monkeypatch) -> None:
    monkeypatch.setenv(evaluation_api.KEY_ENV, "k" * 32)
    monkeypatch.setattr(
        evaluation_api,
        "_services",
        lambda: (_ for _ in ()).throw(AssertionError("service must not be called")),
    )
    response = TestClient(evaluation_api.app).get(
        f"/internal/evaluations/{uuid4()}"
    )
    assert response.status_code == 403


def test_worker_api_accepts_authenticated_rubric_evaluation(monkeypatch) -> None:
    request = RubricEvaluationRequest(
        evaluation_id=str(uuid4()),
        response_id=str(uuid4()),
        response={"text": "OpenFang"},
        expected_answer="Use OpenFang.",
        rubric=[RubricCriterion(
            criterion_id="authority",
            description="Uses authority",
            points=1,
        )],
        source_refs=["source://authority/1"],
        model_version="worker-v1",
        prompt_version="rubric-v1",
        source_version="course-r1",
    )

    class _Rubric:
        calls = 0

        def evaluate(self, received):
            self.calls += 1
            assert received == request
            return RubricDecision(
                evaluation_id=request.evaluation_id,
                response_id=request.response_id,
                score=1,
                confidence=0.9,
                accepted=True,
                rationale_codes=["rubric_accepted"],
                criterion_results=[{
                    "criterion_id": "authority",
                    "awarded_points": 1,
                    "feedback": "Grounded.",
                    "source_refs": ["source://authority/1"],
                }],
                misconception_tags=[],
                evaluator_versions={
                    "model": "worker-v1",
                    "prompt": "rubric-v1",
                    "sources": "course-r1",
                },
                source_refs=["source://authority/1"],
                evidence_ref="openfang://completion/rubric-worker-1",
            )

    rubric = _Rubric()
    monkeypatch.setenv(evaluation_api.KEY_ENV, "k" * 32)
    monkeypatch.setattr(
        evaluation_api,
        "_services",
        lambda: SimpleNamespace(rubric=rubric),
    )
    response = TestClient(evaluation_api.app).post(
        "/internal/rubric/evaluate",
        headers={evaluation_api.KEY_HEADER: "k" * 32},
        json=request.model_dump(mode="json"),
    )

    assert response.status_code == 200
    assert response.json()["decision"]["accepted"] is True
    assert rubric.calls == 1
