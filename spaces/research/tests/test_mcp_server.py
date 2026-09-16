"""Vertragstests fuer den Research-MCP-Server."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("spaces_research_mcp_server", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("spaces/research/mcp_server.py must be importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUBBLE = {"id": "bub123", "title": "Sheerlay", "description": "OCR-Etikettenscan"}
NODES = [{"title": "Kernidee", "content": "Etiketten scannen und bewerten"}]


class ToolSurfaceTests(unittest.TestCase):
    def test_tools_list_is_stable(self) -> None:
        server = load_server()
        response = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(
            [tool["name"] for tool in response["result"]["tools"]],
            ["research_start", "research_status"],
        )

    def test_research_start_requires_bubble_id_and_brief(self) -> None:
        server = load_server()
        response = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        start = response["result"]["tools"][0]
        self.assertEqual(sorted(start["inputSchema"]["required"]), ["brief", "bubble_id"])


class PreviewTests(unittest.TestCase):
    def test_preview_returns_the_assembled_brief(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call") as spawn:
            result = server.call_tool(
                "research_start",
                {"bubble_id": "bub123", "brief": "Aufgabe\nWettbewerbsanalyse."},
            )

        self.assertEqual(result["status"], "preview")
        self.assertIn("Sheerlay", result["brief"])
        self.assertIn("Etiketten scannen und bewerten", result["brief"])
        self.assertIn("Wettbewerbsanalyse.", result["brief"])
        spawn.assert_not_called()

    def test_preview_does_not_spend_a_job_id_slot_on_disk(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call"), \
             mock.patch.object(server, "_write_job_file") as write:
            server.call_tool("research_start", {"bubble_id": "bub123", "brief": "x"})
        write.assert_not_called()

    def test_unknown_bubble_is_rejected_before_any_run(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_read_bubble", side_effect=server.ToolError("not_found: bubble")), \
             mock.patch.object(server, "_spawn_agent_call") as spawn:
            with self.assertRaises(server.ToolError):
                server.call_tool("research_start", {"bubble_id": "nope", "brief": "x"})
        spawn.assert_not_called()

    def test_rejects_depth_outside_the_closed_value_set(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES):
            with self.assertRaises(server.ToolError):
                server.call_tool(
                    "research_start",
                    {"bubble_id": "bub123", "brief": "x", "depth": "ludicrous"},
                )

    def test_rejects_output_style_outside_the_closed_value_set(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES):
            with self.assertRaises(server.ToolError):
                server.call_tool(
                    "research_start",
                    {"bubble_id": "bub123", "brief": "x", "output_style": "ludicrous"},
                )

    def test_rejects_citation_style_outside_the_closed_value_set(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES):
            with self.assertRaises(server.ToolError):
                server.call_tool(
                    "research_start",
                    {"bubble_id": "bub123", "brief": "x", "citation_style": "ludicrous"},
                )

    def test_confirm_must_be_an_actual_boolean(self) -> None:
        # A truthy-but-non-boolean confirm (e.g. the string "false") must not
        # bypass the preview gate: it is rejected as invalid_arguments before
        # any run could start - not silently treated as confirm=true.
        server = load_server()
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call") as spawn:
            with self.assertRaises(server.ToolError) as ctx:
                server.call_tool(
                    "research_start",
                    {"bubble_id": "bub123", "brief": "x", "confirm": "false"},
                )
        self.assertTrue(str(ctx.exception).startswith("invalid_arguments"))
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
