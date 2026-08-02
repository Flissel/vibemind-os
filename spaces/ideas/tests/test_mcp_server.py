"""Contract tests for the versioned Ideas/Bubbles MCP server."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock


SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"


def load_server():
    if not SERVER_PATH.is_file():
        raise AssertionError("spaces/ideas/mcp_server.py must be versioned")
    spec = importlib.util.spec_from_file_location("spaces_ideas_mcp_server", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("MCP server must be importable from its versioned path")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IdeasMcpServerContractTests(unittest.TestCase):
    def test_tools_list_has_stable_deterministic_ideas_and_bubbles_surface(self) -> None:
        server = load_server()

        response = server.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )

        self.assertEqual(response["id"], 1)
        self.assertEqual(
            [tool["name"] for tool in response["result"]["tools"]],
            [
                "bubble_list",
                "bubble_get",
                "bubble_create",
                "bubble_update",
                "bubble_delete",
                "idea_list",
                "idea_get",
                "idea_create",
                "idea_update",
                "idea_delete",
                "idea_connect",
                "idea_disconnect",
            ],
        )
        create = response["result"]["tools"][2]
        self.assertEqual(create["inputSchema"]["required"], ["title"])
        self.assertNotIn("OPENAI_API_KEY", json.dumps(response))

    def test_tool_call_fails_closed_when_proxmox_supabase_credentials_are_missing(self) -> None:
        server = load_server()

        with mock.patch.dict(os.environ, {}, clear=True):
            response = server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "bubble_list", "arguments": {}},
                }
            )

        self.assertTrue(response["result"]["isError"])
        text = response["result"]["content"][0]["text"]
        self.assertIn("SUPABASE_URL", text)
        self.assertIn("SUPABASE_SERVICE_ROLE_KEY", text)

    def test_bubble_list_uses_only_env_configured_supabase_endpoint(self) -> None:
        server = load_server()
        observed = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'[{"id":"bubble-1","title":"MVP"}]'

        def opener(request, timeout):
            observed["url"] = request.full_url
            observed["headers"] = dict(request.header_items())
            observed["timeout"] = timeout
            return Response()

        with mock.patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "https://supabase.proxmox.example",
                "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key",
            },
            clear=True,
        ), mock.patch.object(server.urllib.request, "urlopen", opener):
            response = server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "bubble_list", "arguments": {"limit": 5}},
                }
            )

        self.assertFalse(response["result"].get("isError", False))
        self.assertEqual(
            observed["url"],
            "https://supabase.proxmox.example/rest/v1/ideas?select=id%2Ctitle%2Cparent_id%2Cscore%2Cstatus%2Cmetadata&parent_id=is.null&limit=5",
        )
        self.assertEqual(observed["headers"]["Apikey"], "test-service-role-key")
        self.assertNotIn("localhost", observed["url"])

    def test_bubble_reads_and_mutations_are_scoped_to_top_level_rows(self) -> None:
        server = load_server()
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs["params"]))
            return []

        with mock.patch.object(server, "_request", side_effect=request):
            server.call_tool("bubble_get", {"id": "bubble-1"})
            server.call_tool("bubble_update", {"id": "bubble-1", "title": "Renamed"})
            server.call_tool("bubble_delete", {"id": "bubble-1"})

        self.assertEqual(
            [params.get("parent_id") for _method, _path, params in calls],
            ["is.null", "is.null", "is.null"],
        )

    def test_update_fields_are_type_checked_without_schema_enforcement(self) -> None:
        server = load_server()

        with self.assertRaisesRegex(server.ToolError, "'tags' must be an array of strings"):
            server.call_tool("bubble_update", {"id": "bubble-1", "tags": "not-a-list"})
        with self.assertRaisesRegex(server.ToolError, "'content' must be a string"):
            server.call_tool("idea_update", {"id": "idea-1", "content": {"bad": "type"}})
        with self.assertRaisesRegex(server.ToolError, "'x' and 'y' must be integers"):
            server.call_tool("idea_update", {"id": "idea-1", "x": True})

    def test_create_operations_return_existing_rows_without_posting_duplicates(self) -> None:
        server = load_server()
        existing_bubble = {"id": "bubble-1", "title": "MVP"}
        existing_idea = {"id": "idea-1", "title": "Inbox"}
        existing_edge = {"id": "edge-1", "from_node_id": "idea-1", "to_node_id": "idea-2"}

        def request(method, path, **_kwargs):
            if path == "ideas" and method == "GET":
                return [existing_bubble]
            if path == "canvas_nodes" and method == "GET":
                return [existing_idea]
            if path == "canvas_edges" and method == "GET":
                return [existing_edge]
            self.fail(f"idempotent create must not issue {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            self.assertEqual(server.call_tool("bubble_create", {"title": "MVP"}), existing_bubble)
            self.assertEqual(
                server.call_tool("idea_create", {"bubble_id": "bubble-1", "title": "Inbox"}),
                existing_idea,
            )
            self.assertEqual(
                server.call_tool("idea_connect", {"from_id": "idea-1", "to_id": "idea-2"}),
                existing_edge,
            )

    def test_idea_create_preserves_long_title_as_content_with_a_short_title(self) -> None:
        server = load_server()
        observed = {}
        original_title = "x" * 121

        def request(method, path, **kwargs):
            if method == "GET":
                return []
            observed["body"] = kwargs["body"]
            return [{"id": "idea-1"}]

        with mock.patch.object(server, "_request", side_effect=request):
            server.call_tool("idea_create", {"bubble_id": "bubble-1", "title": original_title})

        self.assertEqual(observed["body"]["title"], "x" * 80 + "…")
        self.assertEqual(observed["body"]["content"], original_title)

    def test_stdio_process_serves_initialize_and_tools_list(self) -> None:
        request_lines = "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            ]
        )
        runtime_env = {"SYSTEMROOT": os.environ["SYSTEMROOT"]}

        completed = subprocess.run(
            [sys.executable, str(SERVER_PATH)],
            input=f"{request_lines}\n",
            text=True,
            capture_output=True,
            env=runtime_env,
            check=False,
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "spaces-ideas")
        self.assertEqual(responses[1]["result"]["tools"][0]["name"], "bubble_list")
