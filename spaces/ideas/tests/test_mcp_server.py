"""Contract tests for the versioned Ideas/Bubbles MCP server."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
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
