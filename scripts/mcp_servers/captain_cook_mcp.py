"""Captain Cook MCP Server — agentfarm space runtime via MCP.

Bridges the agentfarm space (Captain Cook delivery orchestrator) to any MCP
client (OpenFang /mcp, Claude Code, ...). All calls go to Captain Cook's
single execute endpoint; this server maps seven tools onto it.

Decisions this implements (docs/operations/2026-08-18-p1-agentfarm-captain-
cook-befund.md, user-approved):
  - Captain Cook IS the agentfarm runtime (space redefined by its runtime).
  - Composite tool `captain_deliver` = hermes.plan -> codex.run, fail-closed.
  - Coarse events outside, fine refs inside: the Brain never invents
    batch/subtask/workspace refs — it only passes them through.
  - Rule: delegated work goes through the Captain (audited, ledger);
    direct Codex sessions are the user's manual channel.

Usage (stdio):
    python captain_cook_mcp.py

Environment:
    CAPTAIN_RUNTIME_URL    (default: http://127.0.0.1:8091 - the RUNTIME port; 8090 is the Gateway)
    CAPTAIN_RUNTIME_TOKEN  (required — Bearer token, own config key on
                            purpose: never inherited from another credential,
                            same lesson as memory.embedding_api_key_env)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("CAPTAIN_RUNTIME_URL", "http://127.0.0.1:8091").rstrip("/")
TOKEN = os.environ.get("CAPTAIN_RUNTIME_TOKEN", "")

DEFAULT_LIMITS = {"wall_seconds": 900, "max_iterations": 5}

REF_FIELDS = ("batch_id", "subtask_id", "workspace_ref")

# operation -> (tool name, required top-level params beyond project_id)
ATOMIC_TOOLS = {
    "captain_hermes_plan": "hermes.plan",
    "captain_hermes_design": "hermes.design_agent",
    "captain_codex_run": "codex.run",
    "captain_codex_resume": "codex.resume",
    "captain_codex_status": "codex.status",
    "captain_codex_cancel": "codex.cancel",
}
CODEX_OPS = {"codex.run", "codex.resume", "codex.status", "codex.cancel"}

ARTIFACT_REF_SCHEMA = {
    "type": "object",
    "properties": {
        "uri": {"type": "string", "description": "artifact:// URI"},
        "sha256": {"type": "string", "description": "64 hex chars"},
        "media_type": {"type": "string", "description": "e.g. text/plain"},
    },
    "required": ["uri", "sha256", "media_type"],
}

LIMITS_SCHEMA = {
    "type": "object",
    "properties": {
        "wall_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
        "max_iterations": {"type": "integer", "minimum": 1, "maximum": 10},
    },
}


def _base_props() -> dict:
    return {
        "project_id": {"type": "string"},
        "prompt_ref": ARTIFACT_REF_SCHEMA,
        "capability_profile": {
            "type": "string",
            "description": "planner|agent-designer|code-builder|n8n-builder|factory-*",
        },
        "limits": LIMITS_SCHEMA,
    }


def _tool_defs() -> list[dict]:
    tools = []
    for name, op in ATOMIC_TOOLS.items():
        props = _base_props()
        required = ["project_id", "prompt_ref", "capability_profile"]
        if op in CODEX_OPS:
            props.update({
                "batch_id": {"type": "string"},
                "subtask_id": {"type": "string"},
                "workspace_ref": {"type": "string", "description": "workspace:// ref"},
            })
            required += ["batch_id", "subtask_id", "workspace_ref"]
        tools.append({
            "name": name,
            "description": f"Captain Cook operation {op} via /v1/runtime/execute.",
            "inputSchema": {"type": "object", "properties": props, "required": required},
        })
    props = _base_props()
    tools.append({
        "name": "captain_deliver",
        "description": (
            "Composite: hermes.plan then codex.run. Returns the refs the plan "
            "produced (batch_id, subtask_id, workspace_ref) for later "
            "captain_codex_status calls. Fails closed if the plan yields no refs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": props,
            "required": ["project_id", "prompt_ref", "capability_profile"],
        },
    })
    return tools


def _execute(operation: str, args: dict) -> dict:
    """One call to Captain Cook's single execute endpoint. Raises on HTTP error."""
    payload = {
        "operation": operation,
        "project_id": args["project_id"],
        "prompt_ref": args["prompt_ref"],
        "capability_profile": args["capability_profile"],
        "limits": args.get("limits") or DEFAULT_LIMITS,
        "integration_intent": args.get("integration_intent", "none"),
    }
    for f in REF_FIELDS:
        if args.get(f):
            payload[f] = args[f]
    req = urllib.request.Request(
        BASE_URL + "/v1/runtime/execute",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TOKEN}",
        },
    )
    with urllib.request.urlopen(req, timeout=int(payload["limits"].get("wall_seconds", 900)) + 30) as resp:
        return json.loads(resp.read().decode())


def _extract_refs(result: dict) -> dict:
    """Find batch/subtask/workspace refs anywhere at the top two levels."""
    found = {}
    scopes = [result] + [v for v in result.values() if isinstance(v, dict)]
    for scope in scopes:
        for f in REF_FIELDS:
            if f not in found and isinstance(scope.get(f), str) and scope[f]:
                found[f] = scope[f]
    return found


def handle_tool(name: str, args: dict) -> str:
    if not TOKEN:
        return json.dumps({"ok": False, "error": "CAPTAIN_RUNTIME_TOKEN not set (fail-closed; no inherited credentials)"})
    try:
        if name in ATOMIC_TOOLS:
            result = _execute(ATOMIC_TOOLS[name], args)
            return json.dumps({"ok": True, "operation": ATOMIC_TOOLS[name], "result": result}, ensure_ascii=False)
        if name == "captain_deliver":
            plan = _execute("hermes.plan", args)
            refs = _extract_refs(plan)
            missing = [f for f in REF_FIELDS if f not in refs]
            if missing:
                return json.dumps({
                    "ok": False,
                    "error": f"plan produced no usable refs (missing: {', '.join(missing)}) - refusing to guess",
                    "plan_result": plan,
                }, ensure_ascii=False)
            run_args = dict(args)
            run_args.update(refs)
            run = _execute("codex.run", run_args)
            return json.dumps({"ok": True, "operation": "deliver", "refs": refs,
                               "plan_result": plan, "run_result": run}, ensure_ascii=False)
        return json.dumps({"ok": False, "error": f"unknown tool {name}"})
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:400]
        except Exception:
            pass
        return json.dumps({"ok": False, "error": f"captain runtime HTTP {e.code}", "detail": body})
    except urllib.error.URLError as e:
        return json.dumps({"ok": False, "error": f"captain runtime unreachable: {e.reason}"})
    except KeyError as e:
        return json.dumps({"ok": False, "error": f"missing required argument: {e}"})


def send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> None:
    tools = _tool_defs()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid = msg.get("id")
        method = msg.get("method", "")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "captain-cook", "version": "1.0.0"},
            }})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}})
        elif method == "tools/call":
            params = msg.get("params", {})
            text = handle_tool(params.get("name", ""), params.get("arguments", {}) or {})
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}],
            }})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601, "message": f"method not supported: {method}"}})


if __name__ == "__main__":
    main()

