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
                "bubble_promote",
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

        promote = response["result"]["tools"][5]
        self.assertEqual(promote["inputSchema"], {
            "type": "object",
            "properties": {
                "bubble_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$",
                },
            },
            "required": ["bubble_id"],
            "additionalProperties": False,
        })

        connect = response["result"]["tools"][11]
        self.assertEqual(connect["inputSchema"], {
            "type": "object",
            "properties": {
                "from_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$",
                },
                "to_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$",
                },
                "edge_type": {
                    "type": "string",
                    "default": "related",
                    "minLength": 1,
                    "maxLength": 64,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9:_-]{0,63}$",
                },
            },
            "required": ["from_id", "to_id"],
            "additionalProperties": False,
        })

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

    def test_bubble_promote_reads_top_level_bubble_creates_project_then_links_it(self) -> None:
        server = load_server()
        calls = []
        bubble = {
            "id": "bubble-1",
            "title": "Launch Plan",
            "description": "Canonical bubble",
            "score": 82,
        }
        project = {}

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET" and path == "ideas":
                return [bubble]
            if method == "POST" and path == "projects":
                project.update(kwargs["body"])
                return [project]
            if method == "PATCH" and path == "ideas":
                return [{
                    "id": "bubble-1",
                    "status": "promoted",
                    "promoted_to_project_id": project["id"],
                }]
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            result = server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        self.assertEqual(result, project)
        self.assertEqual([(method, path) for method, path, _kwargs in calls], [
            ("GET", "ideas"),
            ("POST", "projects"),
            ("PATCH", "ideas"),
        ])
        self.assertEqual(calls[0][2]["params"], {
            "select": "*",
            "id": "eq.bubble-1",
            "parent_id": "is.null",
            "limit": "1",
        })
        self.assertEqual(calls[1][2]["body"], {
            "id": mock.ANY,
            "name": "Launch Plan",
            "description": "Canonical bubble",
            "status": "active",
            "from_idea_id": "bubble-1",
            "metadata": {
                "source_space": "bubbles",
                "source_bubble_id": "bubble-1",
                "source_score": 82,
                "promotion_attempt_id": mock.ANY,
            },
        })
        self.assertRegex(
            calls[1][2]["body"]["metadata"]["promotion_attempt_id"],
            r"^[0-9a-f]{32}$",
        )
        self.assertEqual(calls[2][2], {
            "params": {
                "id": "eq.bubble-1",
                "parent_id": "is.null",
                "promoted_to_project_id": "is.null",
            },
            "body": {"status": "promoted", "promoted_to_project_id": project["id"]},
        })

    def test_bubble_promote_uses_the_legacy_description_coercion_without_content_fallback(self) -> None:
        server = load_server()
        observed = {}

        def request(method, path, **kwargs):
            if method == "GET":
                return [{"id": "bubble-1", "title": "Launch", "description": None, "content": "ignore me"}]
            if method == "POST":
                observed["project"] = kwargs["body"]
                return [kwargs["body"]]
            if method == "PATCH":
                return [{"id": "bubble-1", "promoted_to_project_id": kwargs["body"]["promoted_to_project_id"]}]
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        self.assertEqual(observed["project"]["description"], "")

    def test_bubble_promote_coerces_nonstring_legacy_description_without_failing(self) -> None:
        server = load_server()
        observed = {}

        def request(method, path, **kwargs):
            if method == "GET":
                return [{"id": "bubble-1", "title": "Launch", "description": 42}]
            if method == "POST":
                observed["project"] = kwargs["body"]
                return [kwargs["body"]]
            if method == "PATCH":
                return [{"id": "bubble-1", "promoted_to_project_id": kwargs["body"]["promoted_to_project_id"]}]
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        self.assertEqual(observed["project"]["description"], "42")

    def test_bubble_promote_rejects_missing_or_noncanonical_id_without_requests(self) -> None:
        server = load_server()

        with mock.patch.object(server, "_request") as request:
            for durable_id in (
                "bubble:1",
                "abcdef12",
                "550e8400-e29b-41d4-a716-446655440000",
            ):
                with self.subTest(durable_id=durable_id):
                    self.assertEqual(
                        server._canonical_bubble_id({"bubble_id": durable_id}),
                        durable_id,
                    )
            with self.assertRaisesRegex(server.ToolError, "'bubble_id' must be a canonical non-empty string"):
                server.call_tool("bubble_promote", {})
            with self.assertRaisesRegex(server.ToolError, "'bubble_id' must be a canonical non-empty string"):
                server.call_tool("bubble_promote", {"bubble_id": " bubble-1 "})
            with self.assertRaisesRegex(server.ToolError, "only accepts 'bubble_id'"):
                server.call_tool("bubble_promote", {"bubble_id": "bubble-1", "title": "Launch"})
            for unsafe_id in ("bubble-1,other", "eq.bubble-1", "bubble-1)", "bubble/1"):
                with self.subTest(unsafe_id=unsafe_id):
                    with self.assertRaisesRegex(server.ToolError, "canonical durable id"):
                        server.call_tool("bubble_promote", {"bubble_id": unsafe_id})

        request.assert_not_called()

    def test_bubble_promote_fails_when_the_canonical_top_level_bubble_is_missing(self) -> None:
        server = load_server()

        with mock.patch.object(server, "_request", return_value=[]) as request:
            with self.assertRaisesRegex(server.ToolError, "bubble_not_found"):
                server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        request.assert_called_once_with(
            "GET",
            "ideas",
            params={
                "select": "*",
                "id": "eq.bubble-1",
                "parent_id": "is.null",
                "limit": "1",
            },
        )

    def test_bubble_promote_compensates_when_linking_fails_without_false_success(self) -> None:
        server = load_server()
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET":
                return [{"id": "bubble-1", "title": "Launch", "score": 4}]
            if method == "POST":
                return [kwargs["body"]]
            if method == "PATCH":
                return []
            if method == "DELETE":
                return []
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(server.ToolError, "bubble_promotion_link_failed"):
                server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        self.assertEqual([(method, path) for method, path, _kwargs in calls], [
            ("GET", "ideas"),
            ("POST", "projects"),
            ("PATCH", "ideas"),
            ("DELETE", "projects"),
        ])
        self.assertEqual(calls[-1][2]["params"], {"id": f"eq.{calls[1][2]['body']['id']}"})
        self.assertEqual(calls[2][2]["params"], {
            "id": "eq.bubble-1",
            "parent_id": "is.null",
            "promoted_to_project_id": "is.null",
        })

    def test_bubble_promote_allows_only_the_conditional_link_winner_to_succeed(self) -> None:
        server = load_server()
        calls = []
        project_uuids = iter(("a" * 32, "b" * 32, "c" * 32, "d" * 32))
        winner_id = "a" * 8
        loser_id = "c" * 8

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET":
                return [{"id": "bubble-1", "title": "Launch"}]
            if method == "POST":
                return [kwargs["body"]]
            if method == "PATCH":
                if kwargs["body"]["promoted_to_project_id"] == winner_id:
                    return [{"id": "bubble-1", "promoted_to_project_id": winner_id}]
                return []
            if method == "DELETE":
                return []
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request), mock.patch.object(
            server.uuid,
            "uuid4",
            side_effect=[server.uuid.UUID(value) for value in project_uuids],
        ):
            winner = server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})
            with self.assertRaisesRegex(server.ToolError, "bubble_promotion_link_failed: link_returned_no_row"):
                server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        self.assertEqual(winner["id"], winner_id)
        patch_calls = [call for call in calls if call[0] == "PATCH"]
        self.assertEqual(len(patch_calls), 2)
        self.assertTrue(all(
            call[2]["params"] == {
                "id": "eq.bubble-1",
                "parent_id": "is.null",
                "promoted_to_project_id": "is.null",
            }
            for call in patch_calls
        ))
        self.assertEqual(calls[-1], ("DELETE", "projects", {"params": {"id": f"eq.{loser_id}"}}))

    def test_bubble_promote_compensates_when_the_conditional_link_response_mismatches(self) -> None:
        server = load_server()
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET":
                return [{"id": "bubble-1", "title": "Launch"}]
            if method == "POST":
                return [kwargs["body"]]
            if method == "PATCH":
                return [{
                    "id": "other-bubble",
                    "promoted_to_project_id": kwargs["body"]["promoted_to_project_id"],
                }]
            if method == "DELETE":
                return []
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(server.ToolError, "bubble_promotion_link_failed: link_mismatch"):
                server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        self.assertEqual(calls[-1], ("DELETE", "projects", {"params": {"id": f"eq.{calls[1][2]['body']['id']}"}}))

    def test_bubble_promote_compensates_owned_project_after_post_has_no_representation(self) -> None:
        server = load_server()
        calls = []
        fixed_uuid = "a" * 32
        fixed_id = fixed_uuid[:8]

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET" and path == "ideas":
                return [{"id": "bubble-1", "title": "Launch"}]
            if method == "POST":
                return []
            if method == "GET" and path == "projects":
                return [{
                    "id": fixed_id,
                    "from_idea_id": "bubble-1",
                    "metadata": {
                        "source_bubble_id": "bubble-1",
                        "promotion_attempt_id": fixed_uuid,
                    },
                }]
            if method == "DELETE":
                return []
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request), mock.patch.object(
            server.uuid, "uuid4", return_value=server.uuid.UUID(fixed_uuid),
        ):
            with self.assertRaisesRegex(server.ToolError, "bubble_promotion_create_unverified"):
                server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        self.assertEqual([(method, path) for method, path, _kwargs in calls], [
            ("GET", "ideas"),
            ("POST", "projects"),
            ("GET", "projects"),
            ("DELETE", "projects"),
        ])
        self.assertEqual(calls[-1], ("DELETE", "projects", {"params": {"id": f"eq.{fixed_id}"}}))

    def test_bubble_promote_rejects_unowned_create_representation_without_link_or_delete(self) -> None:
        server = load_server()
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET" and path == "ideas":
                return [{"id": "bubble-1", "title": "Launch"}]
            if method == "POST" and path == "projects":
                return [{**kwargs["body"], "id": "other-project"}]
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(server.ToolError, "^bubble_promotion_create_unverified$"):
                server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        self.assertEqual([(method, path) for method, path, _kwargs in calls], [
            ("GET", "ideas"),
            ("POST", "projects"),
        ])

    def test_bubble_promote_skips_delete_when_empty_create_readback_is_unowned(self) -> None:
        server = load_server()
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET" and path == "ideas":
                return [{"id": "bubble-1", "title": "Launch"}]
            if method == "POST":
                return []
            if method == "GET" and path == "projects":
                return [{"id": "other-project", "from_idea_id": "bubble-1", "metadata": {}}]
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(
                server.ToolError,
                "^bubble_promotion_create_unverified: ownership_mismatch$",
            ):
                server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        self.assertEqual([(method, path) for method, path, _kwargs in calls], [
            ("GET", "ideas"),
            ("POST", "projects"),
            ("GET", "projects"),
        ])

    def test_bubble_promote_skips_delete_when_empty_create_readback_has_no_project(self) -> None:
        server = load_server()
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET" and path == "ideas":
                return [{"id": "bubble-1", "title": "Launch"}]
            if method == "POST":
                return []
            if method == "GET" and path == "projects":
                return []
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(
                server.ToolError,
                "^bubble_promotion_create_unverified: not_found$",
            ):
                server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        self.assertEqual([(method, path) for method, path, _kwargs in calls], [
            ("GET", "ideas"),
            ("POST", "projects"),
            ("GET", "projects"),
        ])

    def test_bubble_promote_skips_delete_when_empty_create_readback_fails(self) -> None:
        server = load_server()
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET" and path == "ideas":
                return [{"id": "bubble-1", "title": "Launch"}]
            if method == "POST":
                return []
            if method == "GET" and path == "projects":
                raise server.ToolError("supabase_unreachable")
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(
                server.ToolError,
                "^bubble_promotion_create_unverified: readback_failed: supabase_unreachable$",
            ):
                server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        self.assertEqual([(method, path) for method, path, _kwargs in calls], [
            ("GET", "ideas"),
            ("POST", "projects"),
            ("GET", "projects"),
        ])

    def test_bubble_promote_does_not_delete_after_definitive_project_post_failure(self) -> None:
        server = load_server()
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET":
                return [{"id": "bubble-1", "title": "Launch"}]
            if method == "POST":
                raise server.ToolError("supabase_http_error: status=409")
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(
                server.ToolError,
                "^bubble_promotion_create_failed: supabase_http_error: status=409$",
            ):
                server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        self.assertEqual([(method, path) for method, path, _kwargs in calls], [
            ("GET", "ideas"),
            ("POST", "projects"),
        ])

    def test_bubble_promote_reconciles_and_deletes_only_an_owned_project_after_ambiguous_post_error(self) -> None:
        server = load_server()
        calls = []
        project_uuid = "a" * 32
        attempt_uuid = "b" * 32
        project_id = project_uuid[:8]

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET" and path == "ideas":
                return [{"id": "bubble-1", "title": "Launch"}]
            if method == "POST":
                raise server.ToolError("supabase_unreachable")
            if method == "GET" and path == "projects":
                return [{
                    "id": project_id,
                    "from_idea_id": "bubble-1",
                    "metadata": {
                        "source_bubble_id": "bubble-1",
                        "promotion_attempt_id": attempt_uuid,
                    },
                }]
            if method == "DELETE" and path == "projects":
                return []
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request), mock.patch.object(
            server.uuid,
            "uuid4",
            side_effect=[server.uuid.UUID(project_uuid), server.uuid.UUID(attempt_uuid)],
        ):
            with self.assertRaisesRegex(server.ToolError, "^bubble_promotion_create_failed: supabase_unreachable$"):
                server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        self.assertEqual([(method, path) for method, path, _kwargs in calls], [
            ("GET", "ideas"),
            ("POST", "projects"),
            ("GET", "projects"),
            ("DELETE", "projects"),
        ])
        self.assertEqual(calls[2][2]["params"], {
            "select": "*",
            "id": f"eq.{project_id}",
            "limit": "1",
        })
        self.assertEqual(calls[3][2]["params"], {"id": f"eq.{project_id}"})

    def test_bubble_promote_skips_cleanup_for_unverified_project_after_ambiguous_post_error(self) -> None:
        server = load_server()
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET" and path == "ideas":
                return [{"id": "bubble-1", "title": "Launch"}]
            if method == "POST":
                raise server.ToolError("supabase_invalid_response")
            if method == "GET" and path == "projects":
                return [{
                    "id": "different-project",
                    "from_idea_id": "bubble-1",
                    "metadata": {"source_bubble_id": "bubble-1"},
                }]
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(
                server.ToolError,
                "^bubble_promotion_create_failed: supabase_invalid_response; cleanup_skipped_unverified: ownership_mismatch$",
            ):
                server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        self.assertEqual([(method, path) for method, path, _kwargs in calls], [
            ("GET", "ideas"),
            ("POST", "projects"),
            ("GET", "projects"),
        ])

    def test_bubble_promote_keeps_original_error_when_ambiguous_post_reconciliation_finds_no_project(self) -> None:
        server = load_server()
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET" and path == "ideas":
                return [{"id": "bubble-1", "title": "Launch"}]
            if method == "POST":
                raise server.ToolError("supabase_http_error: status=500")
            if method == "GET" and path == "projects":
                return []
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(
                server.ToolError,
                "^bubble_promotion_create_failed: supabase_http_error: status=500$",
            ):
                server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        self.assertEqual([(method, path) for method, path, _kwargs in calls], [
            ("GET", "ideas"),
            ("POST", "projects"),
            ("GET", "projects"),
        ])

    def test_bubble_promote_skips_cleanup_when_ambiguous_post_reconciliation_fails(self) -> None:
        server = load_server()
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET" and path == "ideas":
                return [{"id": "bubble-1", "title": "Launch"}]
            if method == "POST":
                raise server.ToolError("supabase_http_error: status=503")
            if method == "GET" and path == "projects":
                raise server.ToolError("supabase_unreachable")
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(
                server.ToolError,
                "^bubble_promotion_create_failed: supabase_http_error: status=503; cleanup_skipped_unverified: supabase_unreachable$",
            ):
                server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

        self.assertEqual([(method, path) for method, path, _kwargs in calls], [
            ("GET", "ideas"),
            ("POST", "projects"),
            ("GET", "projects"),
        ])

    def test_bubble_promote_reports_link_and_compensation_failures_together(self) -> None:
        server = load_server()

        def request(method, path, **kwargs):
            if method == "GET":
                return [{"id": "bubble-1", "title": "Launch"}]
            if method == "POST":
                return [kwargs["body"]]
            if method == "PATCH":
                return []
            if method == "DELETE":
                raise server.ToolError("supabase_unreachable")
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(
                server.ToolError,
                "bubble_promotion_link_failed: link_returned_no_row; compensation_failed: supabase_unreachable",
            ):
                server.call_tool("bubble_promote", {"bubble_id": "bubble-1"})

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
        existing_edge = {
            "id": "edge-1",
            "from_node_id": "idea-1",
            "to_node_id": "idea-2",
            "edge_type": "related",
        }

        def request(method, path, **_kwargs):
            if path == "ideas" and method == "GET":
                return [existing_bubble]
            if path == "canvas_nodes" and method == "GET":
                if "linked_idea_id" in _kwargs["params"]:
                    return [existing_idea]
                return [{"id": "idea-1"}] if _kwargs["params"]["id"] == "eq.idea-1" else [{"id": "idea-2"}]
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
                {"edge_id": "edge-1", "from_id": "idea-1", "to_id": "idea-2", "edge_type": "related"},
            )

    def test_idea_connect_rejects_noncanonical_or_extra_arguments_without_requests(self) -> None:
        server = load_server()

        invalid_cases = (
            {},
            {"from_id": "idea-1", "to_id": "idea-2", "title": "not an id"},
            {"from_id": "idea-1", "to_id": "idea-2", "index": 0},
            {"from_id": "idea-1", "to_id": "idea-2", "alias": "idea-3"},
            {"from_id": " idea-1", "to_id": "idea-2"},
            {"from_id": "idea-1 ", "to_id": "idea-2"},
            {"from_id": "idea-1", "to_id": "eq.idea-2"},
            {"from_id": "idea-1,other", "to_id": "idea-2"},
            {"from_id": "idea-1", "to_id": "idea/2"},
            {"from_id": "idea-1", "to_id": "idea-1"},
            {"from_id": "idea-1", "to_id": "idea-2", "edge_type": " related"},
            {"from_id": "idea-1", "to_id": "idea-2", "edge_type": "related)"},
        )

        with mock.patch.object(server, "_request") as request:
            for arguments in invalid_cases:
                with self.subTest(arguments=arguments):
                    with self.assertRaisesRegex(server.ToolError, "^invalid_arguments:"):
                        server.call_tool("idea_connect", arguments)

        request.assert_not_called()

    def test_idea_connect_reads_exact_nodes_then_uses_safe_deterministic_edge_queries(self) -> None:
        server = load_server()
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path == "canvas_nodes":
                durable_id = kwargs["params"]["id"].removeprefix("eq.")
                return [{"id": durable_id, "title": "never used"}]
            if path == "canvas_edges" and method == "GET":
                return []
            if path == "canvas_edges" and method == "POST":
                return [{"id": "edge-1", "from_node_id": "idea-1", "to_node_id": "idea-2", "edge_type": "related"}]
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            result = server.call_tool("idea_connect", {"from_id": "idea-1", "to_id": "idea-2"})

        self.assertEqual(result, {"edge_id": "edge-1", "from_id": "idea-1", "to_id": "idea-2", "edge_type": "related"})
        self.assertEqual(calls, [
            ("GET", "canvas_nodes", {"params": {"select": "id", "id": "eq.idea-1", "limit": "1"}}),
            ("GET", "canvas_nodes", {"params": {"select": "id", "id": "eq.idea-2", "limit": "1"}}),
            ("GET", "canvas_edges", {"params": {"select": "id,from_node_id,to_node_id,edge_type", "from_node_id": "eq.idea-1", "to_node_id": "eq.idea-2", "limit": "1"}}),
            ("GET", "canvas_edges", {"params": {"select": "id,from_node_id,to_node_id,edge_type", "from_node_id": "eq.idea-2", "to_node_id": "eq.idea-1", "limit": "1"}}),
            ("POST", "canvas_edges", {"body": mock.ANY}),
        ])
        self.assertEqual(calls[-1][2]["body"], {
            "id": calls[-1][2]["body"]["id"],
            "from_node_id": "idea-1",
            "to_node_id": "idea-2",
            "edge_type": "related",
        })

    def test_idea_connect_rejects_missing_or_mismatched_nodes_before_edge_requests(self) -> None:
        server = load_server()

        cases = (
            ([], "from_id"),
            ([{"id": "other-id"}], "from_id"),
            ([{"id": "idea-1"}], "to_id"),
        )
        for node_rows, expected_error in cases:
            with self.subTest(node_rows=node_rows, expected_error=expected_error):
                calls = []

                def request(method, path, **kwargs):
                    calls.append((method, path, kwargs))
                    if expected_error == "to_id" and len(calls) == 1:
                        return [{"id": "idea-1"}]
                    return node_rows

                with mock.patch.object(server, "_request", side_effect=request):
                    with self.assertRaisesRegex(server.ToolError, f"^idea_connect_node_unverified: {expected_error}$"):
                        server.call_tool("idea_connect", {"from_id": "idea-1", "to_id": "idea-2"})

                self.assertEqual(calls, [
                    ("GET", "canvas_nodes", {"params": {"select": "id", "id": "eq.idea-1", "limit": "1"}}),
                ] if expected_error == "from_id" else [
                    ("GET", "canvas_nodes", {"params": {"select": "id", "id": "eq.idea-1", "limit": "1"}}),
                    ("GET", "canvas_nodes", {"params": {"select": "id", "id": "eq.idea-2", "limit": "1"}}),
                ])

    def test_idea_connect_preserves_pair_idempotency_when_existing_edge_type_differs(self) -> None:
        server = load_server()
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path == "canvas_nodes":
                return [{"id": kwargs["params"]["id"].removeprefix("eq.")}]
            if path == "canvas_edges" and method == "GET":
                if kwargs["params"].get("from_node_id") == "eq.idea-1":
                    return []
                return [{"id": "edge-2", "from_node_id": "idea-2", "to_node_id": "idea-1", "edge_type": "blocks"}]
            if path == "canvas_edges" and method == "POST":
                return []
            self.fail(f"unexpected request: {method} {path}")

        with mock.patch.object(server, "_request", side_effect=request):
            try:
                result = server.call_tool(
                    "idea_connect",
                    {"from_id": "idea-1", "to_id": "idea-2", "edge_type": "related"},
                )
            except server.ToolError as exc:
                self.fail(f"existing pair must remain idempotent: {exc}")

        self.assertEqual(result, {"edge_id": "edge-2", "from_id": "idea-2", "to_id": "idea-1", "edge_type": "blocks"})
        self.assertEqual([(method, path) for method, path, _kwargs in calls], [
            ("GET", "canvas_nodes"),
            ("GET", "canvas_nodes"),
            ("GET", "canvas_edges"),
            ("GET", "canvas_edges"),
        ])

    def test_idea_connect_fails_closed_for_malformed_or_mismatched_edge_representations(self) -> None:
        server = load_server()

        for edge_response in (
            [],
            [{}],
            [{"id": "", "from_node_id": "idea-1", "to_node_id": "idea-2", "edge_type": "related"}],
            [{"id": "edge-1", "from_node_id": "other", "to_node_id": "idea-2", "edge_type": "related"}],
            [{"id": "edge-1", "from_node_id": "idea-2", "to_node_id": "idea-1", "edge_type": "related"}],
        ):
            with self.subTest(edge_response=edge_response):
                calls = []

                def request(method, path, **kwargs):
                    calls.append((method, path, kwargs))
                    if path == "canvas_nodes":
                        return [{"id": kwargs["params"]["id"].removeprefix("eq.")}]
                    if path == "canvas_edges" and method == "GET":
                        return []
                    if path == "canvas_edges" and method == "POST":
                        return edge_response
                    self.fail(f"unexpected request: {method} {path}")

                with mock.patch.object(server, "_request", side_effect=request):
                    with self.assertRaisesRegex(server.ToolError, "^idea_connect_create_unverified$"):
                        server.call_tool("idea_connect", {"from_id": "idea-1", "to_id": "idea-2"})

                self.assertEqual([(method, path) for method, path, _kwargs in calls][-1], ("POST", "canvas_edges"))

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
