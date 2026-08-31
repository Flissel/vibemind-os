"""Contract tests for the deterministic, read-only Video status MCP server."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from core.capability_targets import McpExecutor


@pytest.fixture
def mcp_server():
    return importlib.import_module("spaces.video.mcp_server")


def _call(server, arguments=None, *, name="video_status", request_id=1):
    return server.handle_message({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": {} if arguments is None else arguments},
    })


def _payload(response):
    return json.loads(response["result"]["content"][0]["text"])


def test_lists_exactly_one_closed_optional_job_status_tool(mcp_server):
    assert mcp_server.TOOLS == [{
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


def test_provider_status_uses_only_canonical_status_event_and_redacts_result(mcp_server, monkeypatch):
    calls = []

    def execute(payload):
        calls.append(payload)
        return {
            "success": True,
            "status": "available",
            "vibevideo_installed": True,
            "deepfake_installed": True,
            "media_path": "/private/render.mp4",
            "execution_target": "direct:private",
        }

    monkeypatch.setattr(mcp_server, "execute_video", execute)
    response = _call(mcp_server)

    assert response["result"]["isError"] is False
    assert _payload(response) == {
        "deepfake_available": True,
        "ok": True,
        "source": "video-providers",
        "vibevideo_available": True,
    }
    assert calls == [{"event_type": "video.status"}]
    assert "/private" not in response["result"]["content"][0]["text"]


@pytest.mark.parametrize("provider_result", [
    {"success": True, "vibevideo_installed": True, "deepfake_installed": False},
    {"success": True, "vibevideo_installed": True},
    {"success": False, "vibevideo_installed": True, "deepfake_installed": True},
])
def test_provider_status_fails_closed_when_either_provider_is_not_verified(mcp_server, monkeypatch, provider_result):
    monkeypatch.setattr(mcp_server, "execute_video", lambda _payload: provider_result)

    response = _call(mcp_server)

    assert response["result"]["isError"] is True
    assert _payload(response) == {"error": "video_status_unverified"}


@pytest.mark.parametrize("arguments", [
    {"job_id": "video-" + "a" * 31},
    {"job_id": "video-" + "A" * 32},
    {"job_id": "video-" + "a" * 32 + "/../../run"},
    {"job_id": "video-" + "a" * 32, "url": "https://attacker.example"},
    {"path": "C:/secret"},
    {"command": "start"},
    {"provider": "anything"},
])
def test_rejects_noncanonical_or_extra_arguments_before_execution(mcp_server, monkeypatch, arguments):
    monkeypatch.setattr(mcp_server, "execute_video", lambda _payload: pytest.fail("must not execute"))

    response = _call(mcp_server, arguments)

    assert response["result"]["isError"] is True
    assert _payload(response) == {"error": "invalid_arguments: video_status accepts only optional canonical job_id"}


def test_job_status_projects_accepted_without_starting_or_enumerating_jobs(mcp_server, monkeypatch, tmp_path):
    job_id = "video-" + "a" * 32
    store = tmp_path / "jobs"
    store.mkdir()
    (store / f"{job_id}.json").write_text(json.dumps({
        "job_id": job_id,
        "event_type": "video.demo_build",
        "status": "accepted",
        "success": True,
        "created_at": "2026-08-04T00:00:00+00:00",
    }), encoding="utf-8")
    monkeypatch.setenv("VIBEMIND_VIDEO_JOB_DIR", str(store))
    monkeypatch.setattr(mcp_server, "execute_video", lambda _payload: pytest.fail("must not execute"))

    response = _call(mcp_server, {"job_id": job_id})

    assert response["result"]["isError"] is False
    assert _payload(response) == {
        "job_id": job_id,
        "ok": True,
        "source": "video-job",
        "status": "accepted",
        "success": None,
        "terminal": False,
    }


def test_completed_job_requires_valid_artifacts_and_returns_counts_only(mcp_server, monkeypatch, tmp_path):
    job_id = "video-" + "b" * 32
    render = tmp_path / "render.mp4"
    audio = tmp_path / "audio.wav"
    render.write_bytes(b"render")
    audio.write_bytes(b"audio")
    store = tmp_path / "jobs"
    store.mkdir()
    (store / f"{job_id}.json").write_text(json.dumps({
        "job_id": job_id,
        "event_type": "video.demo_build",
        "status": "completed",
        "success": True,
        "created_at": "2026-08-04T00:00:00+00:00",
        "completed_at": "2026-08-04T00:01:00+00:00",
        "artifacts": [
            {"path": str(render), "exists": True},
            {"path": str(audio), "exists": True},
        ],
        "media_paths": [str(render), str(audio)],
    }), encoding="utf-8")
    monkeypatch.setenv("VIBEMIND_VIDEO_JOB_DIR", str(store))
    monkeypatch.setattr(mcp_server, "execute_video", lambda _payload: pytest.fail("must not execute"))

    response = _call(mcp_server, {"job_id": job_id})

    assert response["result"]["isError"] is False
    assert _payload(response) == {
        "artifact_count": 2,
        "job_id": job_id,
        "ok": True,
        "source": "video-job",
        "status": "completed",
        "success": True,
        "terminal": True,
    }
    assert str(tmp_path) not in response["result"]["content"][0]["text"]


@pytest.mark.parametrize("job", [
    {"job_id": "video-" + "c" * 32, "status": "completed", "success": True, "artifacts": []},
    {"job_id": "video-" + "c" * 32, "status": "completed", "success": False, "artifacts": [{"path": "/a.mp4", "exists": True}]},
    {"job_id": "video-" + "c" * 32, "status": "completed", "success": True, "artifacts": [{"path": "/a.mp4", "exists": False}]},
    {"job_id": "video-" + "d" * 32, "status": "running", "success": True},
    {"job_id": "video-" + "c" * 32, "status": "failed", "success": False, "error": "/private/provider-secret"},
    {"job_id": "video-" + "c" * 32, "status": "unknown", "success": True},
])
def test_missing_corrupt_or_inconsistent_job_records_fail_closed(mcp_server, monkeypatch, job):
    requested = "video-" + "c" * 32
    monkeypatch.setattr(mcp_server, "execute_video", lambda _payload: pytest.fail("must not execute"))
    monkeypatch.setattr(mcp_server, "_read_job_record", lambda _job_id: job)

    response = _call(mcp_server, {"job_id": requested})

    assert response["result"]["isError"] is True
    assert _payload(response) == {"error": "video_job_unverified"}
    assert "/private" not in response["result"]["content"][0]["text"]


def test_missing_job_store_fails_without_creating_directory_or_calling_executor(mcp_server, monkeypatch, tmp_path):
    job_id = "video-" + "e" * 32
    absent_store = tmp_path / "absent-jobs"
    monkeypatch.setenv("VIBEMIND_VIDEO_JOB_DIR", str(absent_store))
    monkeypatch.setattr(mcp_server, "execute_video", lambda _payload: pytest.fail("must not execute"))

    response = _call(mcp_server, {"job_id": job_id})

    assert response["result"]["isError"] is True
    assert _payload(response) == {"error": "video_job_unverified"}
    assert not absent_store.exists()


@pytest.mark.parametrize("kind", ["stale", "directory", "symlink"])
def test_completed_job_rejects_nonregular_artifact_evidence(mcp_server, monkeypatch, tmp_path, kind):
    job_id = "video-" + "f" * 32
    artifact = tmp_path / "artifact.mp4"
    if kind == "directory":
        artifact.mkdir()
    elif kind == "symlink":
        target = tmp_path / "target.mp4"
        target.write_bytes(b"video")
        try:
            artifact.symlink_to(target)
        except OSError:
            pytest.skip("symlinks unavailable in this Windows environment")
    # stale intentionally has no filesystem entry.
    monkeypatch.setattr(mcp_server, "execute_video", lambda _payload: pytest.fail("must not execute"))
    monkeypatch.setattr(mcp_server, "_read_job_record", lambda _job_id: {
        "job_id": job_id,
        "status": "completed",
        "success": True,
        "artifacts": [{"path": str(artifact), "exists": True}],
    })

    response = _call(mcp_server, {"job_id": job_id})

    assert response["result"]["isError"] is True
    assert _payload(response) == {"error": "video_job_unverified"}


@pytest.mark.parametrize("job_content", [
    "not json",
    json.dumps({"job_id": "video-" + "0" * 32, "status": "accepted", "success": True}),
])
def test_pure_job_read_rejects_corrupt_or_mismatched_record_without_executor(
    mcp_server, monkeypatch, tmp_path, job_content,
):
    job_id = "video-" + "9" * 32
    store = tmp_path / "jobs"
    store.mkdir()
    (store / f"{job_id}.json").write_text(job_content, encoding="utf-8")
    monkeypatch.setenv("VIBEMIND_VIDEO_JOB_DIR", str(store))
    monkeypatch.setattr(mcp_server, "execute_video", lambda _payload: pytest.fail("must not execute"))

    response = _call(mcp_server, {"job_id": job_id})

    assert response["result"]["isError"] is True
    assert _payload(response) == {"error": "video_job_unverified"}


@pytest.mark.parametrize("record", [
    {"status": "accepted", "success": True, "created_at": "2026-08-04T00:00:00+00:00"},
    {"event_type": "video.unknown", "status": "accepted", "success": True, "created_at": "2026-08-04T00:00:00+00:00"},
    {"event_type": "video.demo_build", "status": "running", "success": True, "created_at": "2026-08-04T00:00:00+00:00"},
    {"event_type": "video.demo_build", "status": "completed", "success": True, "created_at": "2026-08-04T00:00:00+00:00", "artifacts": []},
    {"event_type": "video.demo_build", "status": "failed", "success": False, "created_at": "2026-08-04T00:00:00+00:00"},
])
def test_job_read_rejects_missing_unknown_or_inconsistent_lifecycle_fields(
    mcp_server, monkeypatch, tmp_path, record,
):
    job_id = "video-" + "8" * 32
    store = tmp_path / "jobs"
    store.mkdir()
    (store / f"{job_id}.json").write_text(json.dumps({"job_id": job_id, **record}), encoding="utf-8")
    monkeypatch.setenv("VIBEMIND_VIDEO_JOB_DIR", str(store))
    monkeypatch.setattr(mcp_server, "execute_video", lambda _payload: pytest.fail("must not execute"))

    response = _call(mcp_server, {"job_id": job_id})

    assert response["result"]["isError"] is True
    assert _payload(response) == {"error": "video_job_unverified"}


def test_unknown_tool_and_nonobject_arguments_fail_closed(mcp_server):
    unknown = _call(mcp_server, name="video_start", request_id="unknown")
    nonobject = _call(mcp_server, ["not", "an", "object"], request_id="bad-args")

    assert unknown["id"] == "unknown"
    assert unknown["result"]["isError"] is True
    assert _payload(unknown) == {"error": "unknown_tool: video_status is the only supported tool"}
    assert nonobject["id"] == "bad-args"
    assert nonobject["result"]["isError"] is True
    assert _payload(nonobject) == {"error": "invalid_arguments: arguments must be an object"}


def test_mcp_executor_treats_video_non_ok_payload_as_hard_failure(mcp_server, monkeypatch):
    monkeypatch.setattr(mcp_server, "execute_video", lambda _payload: {
        "success": True,
        "vibevideo_installed": True,
        "deepfake_installed": False,
    })
    response = _call(mcp_server)
    executor = McpExecutor("mcp:brain-video:spaces-video:video_status")
    monkeypatch.setattr(executor, "_configuration", lambda: ("https://openfang.example", "token"))
    monkeypatch.setattr(executor, "_resolve_agent_id", lambda *_args: "agent-1")

    def request_json(_method, _url, *, payload, **_kwargs):
        return {"jsonrpc": "2.0", "id": payload["id"], "result": response["result"]}

    monkeypatch.setattr(executor, "_request_json", request_json)
    result = executor.call()

    assert result["ok"] is False
    assert "iserror" in result["error"].lower()
