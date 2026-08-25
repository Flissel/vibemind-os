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


def run(compose_file: Path) -> None:
    _run(compose_file, "up", "-d", "--build", "--wait")
    mcp_base = f"http://127.0.0.1:{os.environ.get('LEARNING_MCP_PORT', '8091')}"
    _wait_for_mcp(f"{mcp_base}/health/ready")

    request = _status_request()
    before = _post_json(f"{mcp_base}/mcp", request)
    before_payload = json.loads(before["result"]["content"][0]["text"])
    if before_payload.get("evidence", {}).get("evidence_type") != "structural_status":
        raise RuntimeError("status call returned no structural readback evidence")

    _run(compose_file, "restart", "postgres", timeout=180)
    _wait_for_mcp(f"{mcp_base}/health/ready")
    after = _post_json(f"{mcp_base}/mcp", request)
    after_payload = json.loads(after["result"]["content"][0]["text"])
    if after_payload != before_payload:
        raise RuntimeError("persisted MCP receipt changed across PostgreSQL restart")
    print("restart_receipt=verified")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose-file", type=Path, required=True)
    args = parser.parse_args()
    run(args.compose_file.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
