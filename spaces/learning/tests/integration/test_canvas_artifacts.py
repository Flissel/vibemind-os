from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

import pytest

from spaces.learning.bridge.penecho_client import PenEchoExportV1
from spaces.learning.contracts.penecho import CanvasArtifactV1, CanvasDocumentV1
from spaces.learning.services.evaluation.canvas_artifacts import CanvasArtifactStore


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
SESSION_ID = UUID("123e4567-e89b-42d3-a456-426614174603")


def _document() -> CanvasDocumentV1:
    return CanvasDocumentV1(
        project_id=UUID("123e4567-e89b-42d3-a456-426614174600"),
        canvas_id=UUID("123e4567-e89b-42d3-a456-426614174601"),
        revision=1,
        background="white",
        objects=(),
    )


def _encoded_document() -> bytes:
    value = _document().model_dump(mode="json")
    wire = {"version":value["version"],"projectId":value["project_id"],"canvasId":value["canvas_id"],"revision":value["revision"],"background":value["background"],"objects":value["objects"]}
    return json.dumps(wire, separators=(",", ":"), ensure_ascii=True).encode()


def _export(*, structured_hash: str | None = None) -> PenEchoExportV1:
    encoded = _encoded_document()
    return PenEchoExportV1(
        version="1",
        id=SESSION_ID,
        revision=1,
        document=_document(),
        artifacts=(
            CanvasArtifactV1(
                artifact_id=UUID("123e4567-e89b-42d3-a456-426614174610"),
                artifact_kind="structured_canvas",
                media_type="application/json",
                sha256=structured_hash or hashlib.sha256(encoded).hexdigest(),
                size_bytes=len(encoded),
                locator="artifact://penecho/session/document",
                canvas_revision=1,
            ),
            CanvasArtifactV1(
                artifact_id=UUID("123e4567-e89b-42d3-a456-426614174611"),
                artifact_kind="rendered_snapshot",
                media_type="image/png",
                sha256=hashlib.sha256(PNG).hexdigest(),
                size_bytes=len(PNG),
                locator="artifact://penecho/session/snapshot",
                canvas_revision=1,
            ),
        ),
        snapshot_png_base64=base64.b64encode(PNG).decode(),
    )


def test_learning_copies_and_reverifies_penecho_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    submission = CanvasArtifactStore(artifact_root=root).import_export(_export())

    assert submission.document.revision == 1
    assert submission.structured_artifact.locator.startswith("artifact://learning/canvas/")
    assert submission.rendered_snapshot.locator.startswith("artifact://learning/canvas/")
    files = sorted(path for path in root.rglob("*") if path.is_file())
    assert [path.suffix for path in files] == [".json", ".png"]
    assert hashlib.sha256(files[0].read_bytes()).hexdigest() == submission.structured_artifact.sha256
    assert hashlib.sha256(files[1].read_bytes()).hexdigest() == submission.rendered_snapshot.sha256


def test_learning_rejects_hash_mismatch_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    with pytest.raises(ValueError, match="integrity"):
        CanvasArtifactStore(artifact_root=root).import_export(_export(structured_hash="f" * 64))

    outside = tmp_path / "outside"
    outside.mkdir()
    canvas_parent = root / "canvas"
    try:
        os.symlink(outside, canvas_parent, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink unavailable: {error}")
    with pytest.raises(ValueError, match="reparse|artifact root"):
        CanvasArtifactStore(artifact_root=root).import_export(_export())
