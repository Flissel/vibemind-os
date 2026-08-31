"""Read-only stdio MCP status probe for the Video space.

This server is deliberately narrow: it forwards only canonical status reads to
the existing Video execution target.  It never accepts media inputs, provider
selection, paths, commands, or lifecycle operations.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from spaces.video.execution_target import execute_video


SERVER_NAME = "spaces-video"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"
_JOB_ID_PATTERN = re.compile(r"^video-[0-9a-f]{32}$")
_JOB_EVENT_TYPES = frozenset({
    "video.team_run",
    "video.vision",
    "video.demo_build",
    "video.lipsync",
    "video.voice_clone",
    "video.voice_tts",
})

TOOLS: list[dict[str, Any]] = [{
    "name": "video_status",
    "description": "Read video provider availability or one durable video job.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "pattern": "^video-[0-9a-f]{32}$"},
        },
        "additionalProperties": False,
    },
}]


class ToolError(Exception):
    """Expected safe-to-return MCP tool failure."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.payload = {"error": error}


def _tool_result(payload: Mapping[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}],
        "isError": is_error,
    }


def _canonical_job_id(arguments: Mapping[str, Any]) -> str | None:
    """Admit only the one durable ID parameter before any target call."""
    if set(arguments) - {"job_id"}:
        raise ToolError("invalid_arguments: video_status accepts only optional canonical job_id")
    if "job_id" not in arguments:
        return None
    job_id = arguments["job_id"]
    if not isinstance(job_id, str) or _JOB_ID_PATTERN.fullmatch(job_id) is None:
        raise ToolError("invalid_arguments: video_status accepts only optional canonical job_id")
    return job_id


def _provider_status() -> dict[str, Any]:
    """Return a deliberately small projection of the existing status result."""
    try:
        result = execute_video({"event_type": "video.status"})
    except Exception as exc:
        raise ToolError("video_status_unverified") from exc
    if not isinstance(result, Mapping):
        raise ToolError("video_status_unverified")
    vibevideo = result.get("vibevideo_installed") is True
    deepfake = result.get("deepfake_installed") is True
    if result.get("success") is not True or not (vibevideo and deepfake):
        raise ToolError("video_status_unverified")
    return {
        "ok": True,
        "source": "video-providers",
        "vibevideo_available": True,
        "deepfake_available": True,
    }


def _valid_artifacts(record: Mapping[str, Any]) -> int | None:
    """Verify durable completed-job evidence without returning any path."""
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return None
    for artifact in artifacts:
        if (
            not isinstance(artifact, Mapping)
            or not isinstance(artifact.get("path"), str)
            or not artifact["path"].strip()
            or artifact.get("exists") is not True
        ):
            return None
        try:
            artifact_path = Path(artifact["path"])
            if artifact_path.is_symlink() or not artifact_path.is_file():
                return None
        except OSError:
            return None
    return len(artifacts)


def _job_store() -> Path:
    """Return the execution target's store location without creating it."""
    configured = os.environ.get("VIBEMIND_VIDEO_JOB_DIR", "").strip()
    return Path(configured) if configured else Path.home() / ".vibemind" / "video-jobs"


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_consistent_job_boundary(record: Mapping[str, Any]) -> bool:
    """Require the durable lifecycle fields written by the Video target."""
    event_type = record.get("event_type")
    status = record.get("status")
    if event_type not in _JOB_EVENT_TYPES or not _nonempty_string(record.get("created_at")):
        return False
    if status in {"accepted", "running"}:
        if record.get("success") is not True:
            return False
        return status != "running" or _nonempty_string(record.get("started_at"))
    if status == "completed":
        return (
            record.get("success") is True
            and _nonempty_string(record.get("completed_at"))
            and _valid_artifacts(record) is not None
        )
    if status == "failed":
        return record.get("success") is False and _nonempty_string(record.get("completed_at"))
    return False


def _read_job_record(job_id: str) -> Mapping[str, Any]:
    """Read one durable job record without invoking the mutating target helper."""
    try:
        store = _job_store()
        if store.is_symlink() or not store.is_dir():
            raise ToolError("video_job_unverified")
        path = store / f"{job_id}.json"
        if path.is_symlink() or not path.is_file():
            raise ToolError("video_job_unverified")
        with path.open("r", encoding="utf-8") as handle:
            record = json.load(handle)
    except ToolError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ToolError("video_job_unverified") from exc
    if (
        not isinstance(record, Mapping)
        or record.get("job_id") != job_id
        or not _has_consistent_job_boundary(record)
    ):
        raise ToolError("video_job_unverified")
    return record


def _job_status(job_id: str) -> dict[str, Any]:
    """Read exactly one job and project state, not internal job evidence."""
    try:
        record = _read_job_record(job_id)
    except Exception as exc:
        if isinstance(exc, ToolError):
            raise
        raise ToolError("video_job_unverified") from exc
    if not isinstance(record, Mapping) or record.get("job_id") != job_id:
        raise ToolError("video_job_unverified")

    status = record.get("status")
    if status in {"accepted", "running"} and record.get("success") is True:
        return {
            "ok": True,
            "source": "video-job",
            "job_id": job_id,
            "status": status,
            "terminal": False,
            "success": None,
        }
    if status == "completed" and record.get("success") is True:
        artifact_count = _valid_artifacts(record)
        if artifact_count is not None:
            return {
                "ok": True,
                "source": "video-job",
                "job_id": job_id,
                "status": "completed",
                "terminal": True,
                "success": True,
                "artifact_count": artifact_count,
            }
    raise ToolError("video_job_unverified")


def video_status(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Serve only provider availability or a single canonical job read."""
    job_id = _canonical_job_id(arguments)
    if job_id is None:
        return _provider_status()
    return _job_status(job_id)


def handle_message(message: Any) -> dict[str, Any] | None:
    """Handle the small JSON-RPC MCP surface without writing to stdout."""
    if not isinstance(message, Mapping):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    request_id = message.get("id")
    method = message.get("method")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {}},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, Mapping):
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(
                {"error": "invalid_arguments: params must be an object"}, is_error=True,
            )}
        if params.get("name") != "video_status":
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(
                {"error": "unknown_tool: video_status is the only supported tool"}, is_error=True,
            )}
        arguments = params.get("arguments", {})
        if not isinstance(arguments, Mapping):
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(
                {"error": "invalid_arguments: arguments must be an object"}, is_error=True,
            )}
        try:
            payload = video_status(arguments)
        except ToolError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(exc.payload, is_error=True)}
        return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(payload)}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}}


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            response = handle_message(message)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
