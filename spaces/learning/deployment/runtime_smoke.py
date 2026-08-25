from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from uuid import uuid4


RESTART_ORDER = (
    "postgres",
    "redis",
    "qdrant",
    "learnhouse-api",
    "learnhouse-collab",
    "learnhouse-web",
    "learning-api",
    "learning-worker",
    "learning-mcp",
    "penecho",
)


def _run(compose_file: Path, *args: str, timeout: int = 900) -> str:
    command = ["docker", "compose", "-f", str(compose_file), *args]
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=os.environ.copy(),
    )
    if result.returncode:
        raise RuntimeError((result.stdout or "") + (result.stderr or ""))
    return result.stdout or ""


def _docker(*args: str, timeout: int = 180) -> str:
    result = subprocess.run(
        ["docker", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=os.environ.copy(),
    )
    if result.returncode:
        raise RuntimeError((result.stdout or "") + (result.stderr or ""))
    return result.stdout or ""


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def _wait_for_mcp(url: str, deadline_seconds: int = 180) -> None:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(2)
    raise RuntimeError("Learning MCP did not recover before the smoke deadline")


def _status_request() -> dict[str, Any]:
    invocation_id = str(uuid4())
    correlation_id = str(uuid4())
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "learning_status",
            "arguments": {
                "event_type": "learning.status",
                "invocation_id": invocation_id,
                "correlation_id": correlation_id,
                "idempotency_key": f"runtime-smoke-status-{invocation_id}",
                "actor": {"actor_id": "runtime-smoke", "actor_type": "system"},
                "payload": {},
            },
        },
    }


def _existing_containers(project: str) -> dict[str, str]:
    names = _docker(
        "ps",
        "-a",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--format",
        "{{.Names}}",
    ).splitlines()
    containers: dict[str, str] = {}
    for name in names:
        service = _docker(
            "inspect",
            "--format",
            '{{ index .Config.Labels "com.docker.compose.service" }}',
            name,
        ).strip()
        if service:
            containers[service] = name
    missing = set(RESTART_ORDER) - containers.keys()
    if missing:
        raise RuntimeError(
            "existing Learning project is incomplete: " + ", ".join(sorted(missing))
        )
    return containers


def _wait_container(container: str, deadline_seconds: int = 180) -> None:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        state = _docker(
            "inspect",
            "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
            container,
        ).strip()
        if state in {"healthy", "running"}:
            return
        if state in {"exited", "dead"}:
            raise RuntimeError(f"container {container} stopped during restart recovery")
        time.sleep(2)
    raise RuntimeError(f"container {container} did not become healthy")


def _assert_receipt(mcp_base: str, request: dict[str, Any], expected: dict[str, Any]) -> None:
    _wait_for_mcp(f"{mcp_base}/health/ready")
    response = _post_json(f"{mcp_base}/mcp", request)
    payload = json.loads(response["result"]["content"][0]["text"])
    if payload != expected:
        raise RuntimeError("persisted MCP receipt changed across service restart")


def run(compose_file: Path, *, existing_project: str | None = None) -> None:
    if existing_project is None:
        _run(compose_file, "up", "-d", "--build", "--wait")
    mcp_base = f"http://127.0.0.1:{os.environ.get('LEARNING_MCP_PORT', '8091')}"
    _wait_for_mcp(f"{mcp_base}/health/ready")

    request = _status_request()
    before = _post_json(f"{mcp_base}/mcp", request)
    before_payload = json.loads(before["result"]["content"][0]["text"])
    if before_payload.get("evidence", {}).get("evidence_type") != "structural_status":
        raise RuntimeError("status call returned no structural readback evidence")

    containers = _existing_containers(existing_project) if existing_project else None
    for service in RESTART_ORDER:
        if containers is None:
            _run(compose_file, "restart", service, timeout=180)
        else:
            _docker("restart", containers[service])
            _wait_container(containers[service])
        _assert_receipt(mcp_base, request, before_payload)
        print(f"restart_service={service} receipt=verified")
    print("restart_receipt=verified services=all")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose-file", type=Path, required=True)
    parser.add_argument("--existing-project")
    args = parser.parse_args()
    run(args.compose_file.resolve(), existing_project=args.existing_project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
