from __future__ import annotations

import base64
import binascii
import hashlib
import json
from pathlib import Path

from spaces.learning.bridge.penecho_client import PenEchoExportV1
from spaces.learning.contracts.penecho import (
    CanvasArtifactV1,
    CanvasSubmissionV1,
)


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


def _camel_key(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.title() for part in rest)


def _wire(value: object) -> object:
    if isinstance(value, dict):
        return {_camel_key(key): _wire(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(child) for child in value]
    return value


def _encoded_document(export: PenEchoExportV1) -> bytes:
    return json.dumps(
        _wire(export.document.model_dump(mode="json")),
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _reject_reparse_point(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    status = path.lstat()
    file_attributes = getattr(status, "st_file_attributes", 0)
    if path.is_symlink() or file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("canvas artifact path contains a reparse point")


def _secure_parent(artifact_root: Path, parent: Path) -> None:
    if not parent.is_relative_to(artifact_root):
        raise ValueError("canvas artifact path escaped its artifact root")
    current = artifact_root
    for part in parent.relative_to(artifact_root).parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _reject_reparse_point(current)
            if not current.is_dir():
                raise ValueError("canvas artifact parent must be a directory")
        else:
            current.mkdir(mode=0o700)
        if not current.resolve(strict=True).is_relative_to(artifact_root):
            raise ValueError("canvas artifact path escaped its artifact root")


class CanvasArtifactStore:
    """Copies verified PenEcho exports into Learning-owned immutable storage."""

    def __init__(self, *, artifact_root: Path) -> None:
        self._artifact_root = artifact_root.resolve(strict=True)
        if not self._artifact_root.is_dir():
            raise ValueError("Learning artifact root must be a directory")
        _reject_reparse_point(self._artifact_root)

    def import_export(self, export: PenEchoExportV1) -> CanvasSubmissionV1:
        structured = _encoded_document(export)
        try:
            snapshot = base64.b64decode(export.snapshot_png_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("PenEcho snapshot encoding failed integrity verification") from error
        if not snapshot.startswith(_PNG_SIGNATURE):
            raise ValueError("PenEcho snapshot failed PNG integrity verification")

        by_kind = {item.artifact_kind: item for item in export.artifacts}
        structured_metadata = by_kind["structured_canvas"]
        snapshot_metadata = by_kind["rendered_snapshot"]
        self._verify(structured_metadata, structured)
        self._verify(snapshot_metadata, snapshot)

        relative_root = Path("canvas", str(export.id), f"revision-{export.revision}")
        structured_relative = relative_root / f"{structured_metadata.artifact_id}.json"
        snapshot_relative = relative_root / f"{snapshot_metadata.artifact_id}.png"
        self._write_immutable(structured_relative, structured)
        self._write_immutable(snapshot_relative, snapshot)

        return CanvasSubmissionV1(
            document=export.document,
            structured_artifact=self._learning_metadata(
                structured_metadata, structured_relative
            ),
            rendered_snapshot=self._learning_metadata(
                snapshot_metadata, snapshot_relative
            ),
        )

    @staticmethod
    def _verify(metadata: CanvasArtifactV1, content: bytes) -> None:
        if (
            len(content) != metadata.size_bytes
            or hashlib.sha256(content).hexdigest() != metadata.sha256
        ):
            raise ValueError("PenEcho artifact failed integrity verification")

    def _write_immutable(self, relative: Path, content: bytes) -> None:
        destination = (self._artifact_root / relative).resolve(strict=False)
        if not destination.is_relative_to(self._artifact_root):
            raise ValueError("canvas artifact path escaped its artifact root")
        _secure_parent(self._artifact_root, destination.parent)
        _reject_reparse_point(destination)
        try:
            with destination.open("xb") as handle:
                handle.write(content)
        except FileExistsError:
            if destination.read_bytes() != content:
                raise RuntimeError("Learning canvas artifact is immutable") from None

    @staticmethod
    def _learning_metadata(
        source: CanvasArtifactV1, relative: Path
    ) -> CanvasArtifactV1:
        locator = "artifact://learning/" + relative.as_posix()
        return source.model_copy(update={"locator": locator})
