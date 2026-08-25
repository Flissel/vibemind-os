from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from spaces.learning.contracts.penecho import (
    CanvasArtifactV1,
    CanvasDocumentV1,
    CanvasHelpPolicyV1,
    CanvasObjectV1,
    CanvasRubricCriterionV1,
    CanvasRubricResultV1,
    CanvasSaveV1,
    CanvasSubmissionV1,
    PenEchoLaunchContextV1,
    SourceReferenceV1,
)


def _source() -> SourceReferenceV1:
    return SourceReferenceV1(
        source_id="course-handbook",
        revision=3,
        locator="source://course-handbook/chapter-2",
        title="Course handbook",
    )


def _help() -> CanvasHelpPolicyV1:
    return CanvasHelpPolicyV1(
        hint_mode="selected_region",
        tutor_allowed=True,
        max_hints=3,
    )


def _launch(**updates) -> PenEchoLaunchContextV1:
    values = {
        "activity_id": uuid4(),
        "session_id": uuid4(),
        "course_id": uuid4(),
        "course_revision": 2,
        "actor_id": "student-1",
        "mode": "training",
        "help_policy": _help(),
        "source_refs": [_source()],
        "penecho_origin": "http://127.0.0.1:4173",
        "bridge_session_ref": "canvas-session-1",
    }
    values.update(updates)
    return PenEchoLaunchContextV1(**values)


def _document(revision: int = 4) -> CanvasDocumentV1:
    return CanvasDocumentV1(
        project_id=uuid4(),
        canvas_id=uuid4(),
        revision=revision,
        background="transparent",
        objects=[
            CanvasObjectV1(
                object_id="object-1",
                object_type="text",
                x=120,
                y=80,
                width=320,
                height=90,
                rotation=0,
                text="Authorized execution path",
            ),
            CanvasObjectV1(
                object_id="object-2",
                object_type="line",
                x=440,
                y=125,
                width=180,
                height=0,
                rotation=0,
            ),
        ],
    )


def _artifact(kind: str, media_type: str) -> CanvasArtifactV1:
    artifact_id = uuid4()
    return CanvasArtifactV1(
        artifact_id=artifact_id,
        artifact_kind=kind,
        media_type=media_type,
        sha256="a" * 64,
        size_bytes=2048,
        locator=f"artifact://learning/{artifact_id}",
        canvas_revision=4,
    )


def test_launch_context_freezes_mode_help_sources_and_loopback_origin() -> None:
    launch = _launch()
    assert launch.version == "1"
    assert launch.mode == "training"
    assert launch.help_policy.hint_mode == "selected_region"
    assert launch.source_refs[0].revision == 3

    with pytest.raises(ValidationError, match="exam"):
        _launch(mode="exam")
    exam = _launch(
        mode="exam",
        help_policy=CanvasHelpPolicyV1(
            hint_mode="none", tutor_allowed=False, max_hints=0
        ),
    )
    assert exam.mode == "exam"


@pytest.mark.parametrize(
    "origin",
    [
        "https://penecho.example.com",
        "http://192.168.1.20:4173",
        "http://127.0.0.1:4173/admin",
        "http://user:pass@127.0.0.1:4173",
        "file:///C:/canvas",
    ],
)
def test_launch_rejects_non_loopback_arbitrary_or_credentialed_origins(origin: str) -> None:
    with pytest.raises(ValidationError, match="origin"):
        _launch(penecho_origin=origin)


def test_canvas_save_is_versioned_bounded_and_contains_structured_objects() -> None:
    document = _document()
    saved = CanvasSaveV1(expected_revision=4, document=document)
    assert saved.document.objects[0].object_type == "text"
    assert saved.document.objects[0].text == "Authorized execution path"

    with pytest.raises(ValidationError, match="revision"):
        CanvasSaveV1(expected_revision=3, document=document)
    with pytest.raises(ValidationError):
        CanvasSaveV1.model_validate({"document": document.model_dump(mode="json")})


def test_canvas_contract_rejects_data_urls_provider_fields_and_host_paths() -> None:
    base = _document().model_dump(mode="json")
    base["objects"][0]["asset_ref"] = "data:image/png;base64,AAAA"
    with pytest.raises(ValidationError, match="artifact"):
        CanvasDocumentV1.model_validate(base)

    with pytest.raises(ValidationError):
        CanvasObjectV1.model_validate(
            {
                **_document().objects[0].model_dump(mode="json"),
                "provider_api_key": "secret",
            }
        )
    with pytest.raises(ValidationError, match="locator"):
        CanvasArtifactV1(
            artifact_id=uuid4(),
            artifact_kind="rendered_snapshot",
            media_type="image/png",
            sha256="b" * 64,
            size_bytes=100,
            locator="file:///C:/private/canvas.png",
            canvas_revision=1,
        )


def test_submission_keeps_structured_and_rendered_artifacts_distinct() -> None:
    submission = CanvasSubmissionV1(
        document=_document(),
        structured_artifact=_artifact("structured_canvas", "application/json"),
        rendered_snapshot=_artifact("rendered_snapshot", "image/png"),
    )
    assert submission.structured_artifact.canvas_revision == 4
    assert submission.rendered_snapshot.media_type == "image/png"

    with pytest.raises(ValidationError, match="snapshot"):
        CanvasSubmissionV1(
            document=_document(),
            structured_artifact=_artifact("structured_canvas", "application/json"),
            rendered_snapshot=_artifact("structured_canvas", "application/json"),
        )


def test_rubric_result_is_source_grounded_bounded_and_tracks_misconceptions() -> None:
    result = CanvasRubricResultV1(
        evaluation_id=uuid4(),
        submission_id=uuid4(),
        score=0.75,
        confidence=0.8,
        accepted=True,
        criteria=[
            CanvasRubricCriterionV1(
                criterion_id="authority",
                score=0.75,
                feedback="The diagram preserves the authorized boundary.",
                source_refs=[_source()],
            )
        ],
        misconception_tags=["authority_bypass"],
        evaluator_versions={
            "model": "openfang-canvas-v1",
            "prompt": "canvas-rubric-v1",
            "sources": "course-revision-2",
        },
    )
    assert result.confidence == 0.8
    assert result.misconception_tags == ("authority_bypass",)

    with pytest.raises(ValidationError, match="source"):
        CanvasRubricResultV1(
            evaluation_id=uuid4(),
            submission_id=uuid4(),
            score=0.5,
            confidence=0.8,
            accepted=True,
            criteria=[
                {
                    "criterion_id": "authority",
                    "score": 0.5,
                    "feedback": "Unsupported.",
                    "source_refs": [],
                }
            ],
            misconception_tags=[],
            evaluator_versions={"model": "x", "prompt": "y", "sources": "z"},
        )
