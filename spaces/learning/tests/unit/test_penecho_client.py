from __future__ import annotations

import json
from uuid import UUID

import httpx
import pytest

from spaces.learning.bridge.penecho_client import (
    PenEchoClient,
    PenEchoMalformedResponse,
    PenEchoRevisionConflict,
    PenEchoTransportError,
)
from spaces.learning.contracts.penecho import (
    CanvasDocumentV1,
    CanvasHelpPolicyV1,
    CanvasObjectV1,
    CanvasSaveV1,
    PenEchoLaunchContextV1,
    SourceReferenceV1,
)


TOKEN = "penecho-learning-token-with-32-characters"
SESSION_ID = UUID("123e4567-e89b-42d3-a456-426614174503")
PROJECT_ID = UUID("123e4567-e89b-42d3-a456-426614174500")
CANVAS_ID = UUID("123e4567-e89b-42d3-a456-426614174501")
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


def _launch(*, origin: str = "http://127.0.0.1:3888") -> PenEchoLaunchContextV1:
    return PenEchoLaunchContextV1(
        activity_id=UUID("123e4567-e89b-42d3-a456-426614174502"),
        session_id=SESSION_ID,
        course_id=UUID("123e4567-e89b-42d3-a456-426614174504"),
        course_revision=2,
        actor_id="student-1",
        mode="training",
        help_policy=CanvasHelpPolicyV1(
            hint_mode="guided", tutor_allowed=True, max_hints=2
        ),
        source_refs=(
            SourceReferenceV1(
                source_id="source-1",
                revision=1,
                locator="source://courses/topic",
                title="Topic",
            ),
        ),
        penecho_origin=origin,
        bridge_session_ref="bridge-session-1",
    )


def _document(revision: int = 0) -> CanvasDocumentV1:
    return CanvasDocumentV1(
        project_id=PROJECT_ID,
        canvas_id=CANVAS_ID,
        revision=revision,
        background="grid",
        objects=(
            CanvasObjectV1(
                object_id="text-1",
                object_type="text",
                x=10,
                y=20,
                width=100,
                height=40,
                rotation=0,
                text="Grounded answer",
            ),
        ),
    )


def _wire(value: object) -> object:
    if isinstance(value, dict):
        return {
            key.split("_")[0] + "".join(part.title() for part in key.split("_")[1:]): _wire(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_wire(child) for child in value]
    return value


def _session(revision: int = 0) -> dict[str, object]:
    document = _document(revision).model_dump(mode="json")
    return {
        "version": "1",
        "id": str(SESSION_ID),
        "revision": revision,
        "launch": _wire(_launch().model_dump(mode="json")),
        "document": _wire(document),
    }


def test_client_creates_saves_opens_and_exports_with_bridge_headers() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["Origin"] == "http://127.0.0.1:8010"
        assert request.headers["X-VibeMind-Learning-Session"] == "bridge-session-1"
        if request.url.path.endswith("/canvas"):
            body = json.loads(request.content)
            assert body["expectedRevision"] == 0
            return httpx.Response(200, json={"session": _session(1)})
        if request.url.path.endswith("/export"):
            exported_session = _session(1)
            exported_session.pop("launch")
            return httpx.Response(
                200,
                json={
                    "session": {
                        **exported_session,
                        "currentRevision": 1,
                        "snapshotPngBase64": PNG,
                        "artifacts": [
                            {
                                "version": "1",
                                "artifactId": "123e4567-e89b-42d3-a456-426614174510",
                                "artifactKind": "structured_canvas",
                                "mediaType": "application/json",
                                "sha256": "0" * 64,
                                "sizeBytes": 1,
                                "locator": "artifact://penecho/session/document",
                                "canvasRevision": 1,
                            },
                            {
                                "version": "1",
                                "artifactId": "123e4567-e89b-42d3-a456-426614174511",
                                "artifactKind": "rendered_snapshot",
                                "mediaType": "image/png",
                                "sha256": "1" * 64,
                                "sizeBytes": 1,
                                "locator": "artifact://penecho/session/snapshot",
                                "canvasRevision": 1,
                            },
                        ],
                    }
                },
            )
        return httpx.Response(
            201 if request.method == "POST" else 200,
            json={"session": _session()},
        )

    client = PenEchoClient(
        base_url="http://127.0.0.1:3888",
        learning_origin="http://127.0.0.1:8010",
        auth_token=TOKEN,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    created = client.create_session(_launch(), _document())
    saved = client.save_session(
        SESSION_ID,
        "bridge-session-1",
        CanvasSaveV1(expected_revision=0, document=_document()),
        snapshot_png_base64=PNG,
    )
    opened = client.open_session(SESSION_ID, "bridge-session-1")
    exported = client.export_session(SESSION_ID, "bridge-session-1")

    assert created.revision == 0
    assert saved.revision == 1
    assert opened.document.canvas_id == CANVAS_ID
    assert exported.snapshot_png_base64 == PNG
    assert paths == [
        "/api/learning/sessions",
        f"/api/learning/sessions/{SESSION_ID}/canvas",
        f"/api/learning/sessions/{SESSION_ID}",
        f"/api/learning/sessions/{SESSION_ID}/export",
    ]


def test_client_rejects_origin_mismatch_redirects_and_malformed_canvas() -> None:
    client = PenEchoClient(
        base_url="http://127.0.0.1:3888",
        learning_origin="http://127.0.0.1:8010",
        auth_token=TOKEN,
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"session": {**_session(), "extra": True}})
            ),
            follow_redirects=True,
        ),
    )
    with pytest.raises(ValueError, match="origin"):
        client.create_session(_launch(origin="http://127.0.0.1:3999"), _document())
    with pytest.raises(PenEchoMalformedResponse, match="malformed"):
        client.open_session(SESSION_ID, "bridge-session-1")

    redirect = PenEchoClient(
        base_url="http://127.0.0.1:3888",
        learning_origin="http://127.0.0.1:8010",
        auth_token=TOKEN,
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(302, headers={"location": "https://evil.example"})
            ),
            follow_redirects=True,
        ),
    )
    with pytest.raises(PenEchoTransportError):
        redirect.open_session(SESSION_ID, "bridge-session-1")


def test_client_maps_conflicts_timeouts_and_outages_without_leaking_details() -> None:
    def conflict(_: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "private revision detail"})

    client = PenEchoClient(
        base_url="http://127.0.0.1:3888",
        learning_origin="http://127.0.0.1:8010",
        auth_token=TOKEN,
        http_client=httpx.Client(transport=httpx.MockTransport(conflict)),
    )
    with pytest.raises(PenEchoRevisionConflict) as caught:
        client.save_session(
            SESSION_ID,
            "bridge-session-1",
            CanvasSaveV1(expected_revision=0, document=_document()),
            snapshot_png_base64=PNG,
        )
    assert "private revision detail" not in str(caught.value)

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret timeout detail", request=request)

    unavailable = PenEchoClient(
        base_url="http://127.0.0.1:3888",
        learning_origin="http://127.0.0.1:8010",
        auth_token=TOKEN,
        http_client=httpx.Client(transport=httpx.MockTransport(timeout)),
    )
    with pytest.raises(PenEchoTransportError) as timeout_error:
        unavailable.open_session(SESSION_ID, "bridge-session-1")
    assert "secret timeout detail" not in str(timeout_error.value)

    outage = PenEchoClient(
        base_url="http://127.0.0.1:3888",
        learning_origin="http://127.0.0.1:8010",
        auth_token=TOKEN,
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(503, json={"error": "down"}))
        ),
    )
    with pytest.raises(PenEchoTransportError, match="unavailable"):
        outage.open_session(SESSION_ID, "bridge-session-1")
