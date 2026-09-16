"""Vertragstests fuer den Research-MCP-Server."""

from __future__ import annotations

import importlib.util
import io
import json as _json
import tempfile
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


class _FakeSupabase:
    """Honest-enough PostgREST stand-in for research_report_artifacts and
    canvas_nodes.

    Unlike a fixed canned response per path, this one actually stores rows
    and answers the eq. filters the server sends - including enforcing the
    real UNIQUE(job_id, name) constraint on research_report_artifacts. That
    is what lets a retry test be an actual retry (the second call finds
    what the first call wrote, or collides the way Postgres would) instead
    of a hand-scripted call sequence that can't tell a fixed implementation
    from a broken one.
    """

    def __init__(self, tool_error_cls: type[Exception]) -> None:
        self._tool_error_cls = tool_error_cls
        self.artifacts: list[dict] = []
        self.nodes: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self.fail_next_node_insert = False

    def __call__(self, method: str, path: str, *, params=None, body=None):
        self.calls.append((method, path))
        if path == "research_report_artifacts":
            return self._artifacts(method, params, body)
        if path == "canvas_nodes":
            return self._nodes(method, params, body)
        raise AssertionError(f"unexpected path: {path}")

    @staticmethod
    def _eq(params: dict | None, key: str) -> str | None:
        if not params or key not in params:
            return None
        value = params[key]
        return value[3:] if value.startswith("eq.") else value

    def _artifacts(self, method: str, params, body):
        if method == "GET":
            job_id = self._eq(params, "job_id")
            rows = [r for r in self.artifacts if job_id is None or r["job_id"] == job_id]
            limit = int(params["limit"]) if params and "limit" in params else None
            return rows[:limit] if limit is not None else rows
        if method == "POST":
            if any(r["job_id"] == body["job_id"] and r["name"] == body["name"] for r in self.artifacts):
                # Mirrors research_report_artifacts_job_name_key: a second
                # insert for the same job collides, exactly like a real
                # UNIQUE(job_id, name) violation would.
                raise self._tool_error_cls("supabase_http_error: status=409")
            row = dict(body)
            self.artifacts.append(row)
            return [row]
        raise AssertionError(f"unexpected method {method} for research_report_artifacts")

    def _nodes(self, method: str, params, body):
        if method == "GET":
            linked = self._eq(params, "linked_idea_id")
            node_type = self._eq(params, "node_type")
            rows = [
                r for r in self.nodes
                if (linked is None or r.get("linked_idea_id") == linked)
                and (node_type is None or r.get("node_type") == node_type)
            ]
            return rows
        if method == "POST":
            if self.fail_next_node_insert:
                self.fail_next_node_insert = False
                raise self._tool_error_cls("supabase_unreachable")
            row = dict(body)
            row["id"] = f"node-row-{len(self.nodes) + 1}"
            self.nodes.append(row)
            return [row]
        raise AssertionError(f"unexpected method {method} for canvas_nodes")


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


class StartTests(unittest.TestCase):
    def test_confirm_starts_a_run_and_returns_a_job_id(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call") as spawn, \
             mock.patch.object(server, "_write_job_file") as write:
            result = server.call_tool(
                "research_start",
                {"bubble_id": "bub123", "brief": "Aufgabe\nX.", "confirm": True},
            )

        self.assertEqual(result["status"], "started")
        self.assertRegex(result["job_id"], r"^job_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$")
        self.assertIn(result["job_id"], result["report_path"])
        spawn.assert_called_once()
        write.assert_called_once()

    def test_job_file_records_the_bubble_and_the_settings(self) -> None:
        server = load_server()
        captured = {}
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call"), \
             mock.patch.object(server, "_write_job_file",
                               side_effect=lambda job_id, payload: captured.update(payload)):
            server.call_tool(
                "research_start",
                {"bubble_id": "bub123", "brief": "Aufgabe\nX.", "confirm": True,
                 "depth": "exhaustive", "output_style": "academic"},
            )

        self.assertEqual(captured["bubble_id"], "bub123")
        self.assertEqual(captured["depth"], "exhaustive")
        self.assertEqual(captured["output_style"], "academic")
        self.assertIn("brief", captured)

    def test_edited_brief_is_used_verbatim_when_supplied(self) -> None:
        server = load_server()
        # Muss einen Ausgabepfad tragen: seit der job_id-Kopplung an den
        # bestaetigten Brief (siehe FinalBriefPathTests) ist ein final_brief
        # ohne research_<job_id>.md-Pfad ein Fehler vor dem Start, kein
        # gueltiger Verbatim-Fall mehr.
        edited = (
            "MEIN BEARBEITETER AUFTRAG\n"
            "Schreibe nach: /tmp/research_job_v1_0000000000000000000000000A.md\n"
        )
        sent = {}
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call",
                               side_effect=lambda job_id, text: sent.update(text=text)), \
             mock.patch.object(server, "_write_job_file"):
            server.call_tool(
                "research_start",
                {"bubble_id": "bub123", "brief": "ignoriert", "confirm": True,
                 "final_brief": edited},
            )

        self.assertEqual(sent["text"], edited)


class SpawnAgentCallTests(unittest.TestCase):
    """Deckt die gezielte Abweichung vom Brief ab: der Request-Body geht als
    Datei an das Kind, nicht als argv. Windows kappt eine Kommandozeile bei
    rund 32767 Zeichen; ein langer Brief (Auftrag + Bubble-Inhalt) kann das
    reissen - genau dann, wenn der Auftrag gross und teuer ist. Diese Tests
    fallen gegen die woertliche Brief-Referenzimplementierung (die die JSON-
    Payload direkt als argv[2] uebergibt) durch, nicht nur gegen den Stub.
    """

    def test_request_body_goes_to_a_file_not_to_argv(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = load_server()
            server.ARTIFACT_DIR = tmp
            job_id = "job_v1_0000000000000000000000000F"
            brief_text = "Sehr langer Auftragstext mit vielen Details. " * 200

            with mock.patch.object(server.subprocess, "Popen") as popen:
                server._spawn_agent_call(job_id, brief_text)

            popen.assert_called_once()
            args = popen.call_args[0][0]
            for item in args:
                self.assertNotIn(brief_text, item)

            request_path = Path(args[-1])
            self.assertTrue(request_path.is_file())
            self.assertEqual(request_path.parent, tmp)
            self.assertEqual(request_path.name, f"research_{job_id}.request.json")
            body = _json.loads(request_path.read_text(encoding="utf-8"))
            self.assertEqual(body["message"], brief_text)

    def test_does_not_wait_for_the_child_process(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = load_server()
            server.ARTIFACT_DIR = tmp
            job_id = "job_v1_0000000000000000000000000G"

            with mock.patch.object(server.subprocess, "Popen") as popen, \
                 mock.patch.object(server.subprocess, "run") as run, \
                 mock.patch.object(server.subprocess, "call") as call:
                server._spawn_agent_call(job_id, "x")

            popen.assert_called_once()
            run.assert_not_called()
            call.assert_not_called()
            popen.return_value.wait.assert_not_called()
            popen.return_value.communicate.assert_not_called()

    def test_output_is_redirected_to_a_spawn_log_file_and_stdin_is_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = load_server()
            server.ARTIFACT_DIR = tmp
            job_id = "job_v1_0000000000000000000000000H"

            with mock.patch.object(server.subprocess, "Popen") as popen:
                server._spawn_agent_call(job_id, "x")

            kwargs = popen.call_args[1]
            self.assertEqual(kwargs["stdin"], server.subprocess.DEVNULL)
            self.assertIs(kwargs["stdout"], kwargs["stderr"])
            log_path = tmp / f"research_{job_id}.spawn.log"
            self.assertTrue(log_path.is_file())


class SpawnAgentCallErrorHandlingTests(unittest.TestCase):
    """Review-Nachtrag zu Task 4: die E/A in _spawn_agent_call (Verzeichnis
    anlegen, Request-Datei schreiben, Log oeffnen, Popen) war ungefangen.
    Ein OSError dort riss den gesamten lang laufenden stdio-Server mit, nicht
    nur den einen Aufruf; und wenn der Fehler eintrat, bevor das spawn.log
    ueberhaupt geschrieben wurde, blieb ein bereits abgelegter Job-Zustand
    fuer research_status (Task 5) fuer immer 'pending' statt 'failed', weil
    dessen Kriterium eine nicht-leere Logdatei ist.
    """

    def test_a_failing_file_write_surfaces_as_a_tool_error_not_a_raw_os_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = load_server()
            server.ARTIFACT_DIR = tmp
            job_id = "job_v1_0000000000000000000000000I"

            with mock.patch.object(server.pathlib.Path, "write_text",
                                    side_effect=OSError("Datentraeger voll")):
                with self.assertRaises(server.ToolError) as ctx:
                    server._spawn_agent_call(job_id, "x")

            # Der urspruengliche Grund muss sichtbar bleiben, nicht in einer
            # generischen Meldung verschwinden.
            self.assertIn("Datentraeger voll", str(ctx.exception))

    def test_a_failed_popen_leaves_a_non_empty_spawn_log(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = load_server()
            server.ARTIFACT_DIR = tmp
            job_id = "job_v1_0000000000000000000000000J"

            with mock.patch.object(server.subprocess, "Popen",
                                    side_effect=OSError("sys.executable nicht gefunden")):
                with self.assertRaises(server.ToolError):
                    server._spawn_agent_call(job_id, "x")

            log_path = tmp / f"research_{job_id}.spawn.log"
            self.assertTrue(log_path.is_file())
            content = log_path.read_text(encoding="utf-8")
            # research_status (Task 5) liest 'failed' nur, wenn die Logdatei
            # existiert UND nicht leer ist - eine leere Datei waere 'pending'
            # fuer immer.
            self.assertGreater(len(content), 0)
            self.assertIn("sys.executable nicht gefunden", content)

    def test_a_failing_directory_creation_does_not_raise_a_second_exception(self) -> None:
        # Scheitert schon das Anlegen von ARTIFACT_DIR, kann auch der
        # Best-effort-Schreibversuch fuer das spawn.log nicht klappen (er
        # zielt auf denselben, nicht vorhandenen/gesperrten Ordner). Das darf
        # keine zweite, andere Exception nach aussen werfen - nur den einen
        # ToolError mit dem urspruenglichen Grund.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = load_server()
            server.ARTIFACT_DIR = tmp
            job_id = "job_v1_0000000000000000000000000K"

            with mock.patch.object(server.pathlib.Path, "mkdir",
                                    side_effect=PermissionError("Zugriff verweigert")):
                with self.assertRaises(server.ToolError) as ctx:
                    server._spawn_agent_call(job_id, "x")

            self.assertIn("Zugriff verweigert", str(ctx.exception))


class MainLoopResilienceTests(unittest.TestCase):
    """Review-Nachtrag zu Task 4: main()'s stdin-Schleife hatte kein
    try/except um handle_message(). Jede unerwartete Exception aus einem
    Handler (nicht nur ToolError, das handle_message schon selbst faengt)
    beendete den gesamten Prozess - fuer alle kuenftigen Aufrufer, nicht nur
    fuer die eine fehlerhafte Anfrage.
    """

    def test_stdio_loop_survives_an_unexpected_exception_and_keeps_serving(self) -> None:
        server = load_server()
        calls = []

        def flaky_handle_message(message):
            calls.append(message)
            if len(calls) == 1:
                raise RuntimeError("unerwarteter Bug, kein ToolError")
            return {"jsonrpc": "2.0", "id": message.get("id"), "result": {"ok": True}}

        stdin = io.StringIO(
            '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}\n'
            '{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}\n'
        )
        stdout = io.StringIO()

        with mock.patch.object(server, "handle_message", side_effect=flaky_handle_message), \
             mock.patch.object(server.sys, "stdin", stdin), \
             mock.patch.object(server.sys, "stdout", stdout):
            server.main()

        # Beide Zeilen wurden an handle_message weitergereicht - die
        # Schleife ist nach der ersten, fehlschlagenden Anfrage nicht
        # gestorben.
        self.assertEqual(len(calls), 2)

        lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        first = _json.loads(lines[0])
        second = _json.loads(lines[1])

        self.assertEqual(first["id"], 1)
        self.assertNotIn("result", first)
        self.assertIn("error", first)
        self.assertEqual(second, {"jsonrpc": "2.0", "id": 2, "result": {"ok": True}})


class StatusTests(unittest.TestCase):
    """Deckt research_status ab: Beleg ist die Reportdatei am diktierten Pfad
    plus selbst gezaehlte Quellen - nie der Selbstbericht des Agenten (dessen
    tool_calls die zugrundeliegende Anbindung ohnehin immer leer liefert).
    """

    def _server_with_artifacts(self, tmp: Path):
        server = load_server()
        server.ARTIFACT_DIR = tmp
        return server

    def _full_brief(self, server) -> str:
        # Ein realer, von compose_brief erzeugter Brief - traegt den
        # Bubble-Hintergrundabschnitt, wie ein unbearbeiteter Lauf ihn sendet.
        return server.brief_mod.compose_brief(
            bubble_title="Sheerlay",
            bubble_nodes=NODES,
            user_brief="Aufgabe\nWettbewerbsanalyse.",
            output_path="/tmp/report.md",
            depth="thorough",
            output_style="detailed",
            citation_style="academic_apa",
            language="german",
        )

    def _job_state(self, server, job_id: str, *, brief: str | None = None) -> None:
        server._job_state_path(job_id).write_text(_json.dumps({
            "job_id": job_id, "bubble_id": "bub123", "bubble_title": "Sheerlay",
            "depth": "thorough", "output_style": "detailed",
            "citation_style": "academic_apa", "language": "german",
            "brief": brief if brief is not None else self._full_brief(server),
            "report_path": str(server._job_path(job_id)),
            "started_at": 0,
        }, ensure_ascii=False), encoding="utf-8")

    def test_missing_report_is_pending_not_failed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            job_id = "job_v1_0000000000000000000000000A"
            self._job_state(server, job_id)
            result = server.call_tool("research_status", {"job_id": job_id})
        self.assertEqual(result["status"], "pending")

    def test_dead_spawn_is_reported_as_failed_not_pending_forever(self) -> None:
        """Ist OpenFang unerreichbar, stirbt das Kind mit Traceback im spawn.log.

        Ohne diese Pruefung meldete research_status bis in alle Ewigkeit
        'pending', obwohl nie wieder etwas passieren wird.
        """
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            job_id = "job_v1_0000000000000000000000000E"
            self._job_state(server, job_id)
            (tmp / f"research_{job_id}.spawn.log").write_text(
                "Traceback (most recent call last):\n"
                "urllib.error.URLError: <urlopen error [Errno 111] Connection refused>\n",
                encoding="utf-8",
            )
            result = server.call_tool("research_status", {"job_id": job_id})

        self.assertEqual(result["status"], "failed")
        self.assertIn("agent call failed", result["error"])
        self.assertIn("Connection refused", result["detail"])

    def test_report_without_citations_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            job_id = "job_v1_0000000000000000000000000B"
            self._job_state(server, job_id)
            server._job_path(job_id).write_text("Kein Beleg.", encoding="utf-8")
            with mock.patch.object(server, "_request") as request:
                result = server.call_tool("research_status", {"job_id": job_id})
            request.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertIn("citation", result["error"])

    def test_cited_report_is_persisted_as_artifact_and_node(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            job_id = "job_v1_0000000000000000000000000C"
            self._job_state(server, job_id)
            server._job_path(job_id).write_text(
                "# Report\nQuelle: https://example.test/a\n", encoding="utf-8"
            )
            fake = _FakeSupabase(server.ToolError)

            with mock.patch.object(server, "_request", side_effect=fake):
                result = server.call_tool("research_status", {"job_id": job_id})

        self.assertEqual(result["status"], "done")
        self.assertEqual(result["citation_count"], 1)
        self.assertRegex(result["artifact_ref"], r"^artifact_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$")
        self.assertIn(("POST", "research_report_artifacts"), fake.calls)
        self.assertIn(("POST", "canvas_nodes"), fake.calls)

    def test_artifact_row_binds_the_report_to_the_triggering_bubble(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            job_id = "job_v1_0000000000000000000000000D"
            self._job_state(server, job_id)
            server._job_path(job_id).write_text("https://example.test/a\n", encoding="utf-8")
            fake = _FakeSupabase(server.ToolError)

            with mock.patch.object(server, "_request", side_effect=fake):
                server.call_tool("research_status", {"job_id": job_id})

        self.assertEqual(len(fake.artifacts), 1)
        artifact = fake.artifacts[0]
        self.assertEqual(artifact["subject_type"], "bubble")
        self.assertEqual(artifact["bubble_id"], "bub123")
        self.assertEqual(artifact["citation_count"], 1)
        self.assertEqual(artifact["depth"], "thorough")
        # Der volle, tatsaechlich gesendete Brief traegt den Bubble-Hintergrund
        # (siehe _full_brief) - also ist die Behauptung hier wahr, nicht bloss
        # angenommen.
        self.assertTrue(artifact["internal_context_used"])
        self.assertIsNone(artifact["context_disclosure"])
        self.assertEqual(len(fake.nodes), 1)
        self.assertEqual(fake.nodes[0]["linked_idea_id"], "bub123")

    def test_internal_context_used_true_when_brief_still_carries_bubble_background(self) -> None:
        # Gezielte Abweichung vom Brief: internal_context_used wird aus dem
        # tatsaechlich gesendeten Brief abgeleitet, nicht pauschal True
        # geschrieben. Dieser Test deckt die positive Richtung ab.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            job_id = "job_v1_0000000000000000000000000L"
            self._job_state(server, job_id)  # Default: voller Brief mit Hintergrund
            server._job_path(job_id).write_text("https://example.test/a\n", encoding="utf-8")
            fake = _FakeSupabase(server.ToolError)

            with mock.patch.object(server, "_request", side_effect=fake):
                server.call_tool("research_status", {"job_id": job_id})

        self.assertTrue(fake.artifacts[0]["internal_context_used"])
        self.assertIsNone(fake.artifacts[0]["context_disclosure"])

    def test_internal_context_used_false_with_disclosure_when_final_brief_dropped_the_background(self) -> None:
        # Negative Richtung: ein Nutzer kann final_brief frei bearbeiten und
        # den von compose_brief erzeugten Bubble-Hintergrundabschnitt
        # loeschen. Dann darf internal_context_used nicht True behaupten, und
        # das CHECK verlangt eine Disclosure genau dann, wenn sie fehlt.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            job_id = "job_v1_0000000000000000000000000M"
            edited_brief = "[Auftrag]\nNur das hier, kein Bubble-Hintergrund mehr.\n"
            self.assertNotIn(server._BUBBLE_CONTEXT_MARKER, edited_brief)
            self._job_state(server, job_id, brief=edited_brief)
            server._job_path(job_id).write_text("https://example.test/a\n", encoding="utf-8")
            fake = _FakeSupabase(server.ToolError)

            with mock.patch.object(server, "_request", side_effect=fake):
                server.call_tool("research_status", {"job_id": job_id})

        artifact = fake.artifacts[0]
        self.assertFalse(artifact["internal_context_used"])
        self.assertIsInstance(artifact["context_disclosure"], str)
        self.assertTrue(artifact["context_disclosure"].strip())

    def test_second_call_recovers_after_the_node_insert_failed_on_the_first(self) -> None:
        """Review finding (Important, plan-mandated): _persist_result does two
        sequential POSTs with no transaction. If the artifact insert succeeds
        and the canvas_nodes insert then raises, research_report_artifacts'
        UNIQUE(job_id, name) - name is deterministic - made a naive retry
        collide on the same insert forever: the job was permanently stuck
        with an orphaned, node-less artifact row. A real, cited report must
        not become permanently unusable because one HTTP call failed.
        """
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            job_id = "job_v1_0000000000000000000000000N"
            self._job_state(server, job_id)
            server._job_path(job_id).write_text("https://example.test/a\n", encoding="utf-8")
            fake = _FakeSupabase(server.ToolError)
            fake.fail_next_node_insert = True

            with mock.patch.object(server, "_request", side_effect=fake):
                with self.assertRaises(server.ToolError):
                    server.call_tool("research_status", {"job_id": job_id})

            # Halbfertiger Zustand nach dem ersten, gescheiterten Versuch:
            # die Artifact-Zeile existiert, der Node fehlt noch.
            self.assertEqual(len(fake.artifacts), 1)
            self.assertEqual(len(fake.nodes), 0)
            first_artifact_ref = fake.artifacts[0]["artifact_ref"]

            with mock.patch.object(server, "_request", side_effect=fake):
                result = server.call_tool("research_status", {"job_id": job_id})

        self.assertEqual(result["status"], "done")
        self.assertEqual(result["artifact_ref"], first_artifact_ref)
        self.assertIsNotNone(result["node_id"])
        # Genau eine Artifact-Zeile insgesamt ueber beide Aufrufe - der
        # zweite Aufruf hat NICHT erneut inseriert (das waere der 409 auf
        # UNIQUE(job_id, name), den die Fake-DB oben simuliert).
        self.assertEqual(fake.calls.count(("POST", "research_report_artifacts")), 1)
        # Der Node-Insert wurde zweimal versucht - einmal gescheitert, einmal
        # erfolgreich - aber nicht dupliziert.
        self.assertEqual(fake.calls.count(("POST", "canvas_nodes")), 2)
        self.assertEqual(len(fake.artifacts), 1)
        self.assertEqual(len(fake.nodes), 1)

    def test_repeat_call_when_both_rows_already_exist_is_idempotent_and_writes_nothing_new(self) -> None:
        """Review finding, second interleaving: both rows already exist (the
        first call fully succeeded, and something - the caller, a retry
        after a timeout on the response - calls research_status again for
        the same job). Must not insert a second artifact row or a duplicate
        canvas node.
        """
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            server = self._server_with_artifacts(tmp)
            job_id = "job_v1_0000000000000000000000000O"
            self._job_state(server, job_id)
            server._job_path(job_id).write_text("https://example.test/a\n", encoding="utf-8")
            fake = _FakeSupabase(server.ToolError)
            existing_ref = server.brief_mod.new_artifact_ref()
            fake.artifacts.append({
                "artifact_ref": existing_ref,
                "job_id": job_id,
                "name": f"research_{job_id}.md",
                "bubble_id": "bub123",
            })
            fake.nodes.append({
                "id": "existing-node-1",
                "linked_idea_id": "bub123",
                "node_type": "research",
                "metadata": {"artifact_ref": existing_ref},
            })

            with mock.patch.object(server, "_request", side_effect=fake):
                result = server.call_tool("research_status", {"job_id": job_id})

        self.assertEqual(result["status"], "done")
        self.assertEqual(result["artifact_ref"], existing_ref)
        self.assertEqual(result["node_id"], "existing-node-1")
        self.assertNotIn(("POST", "research_report_artifacts"), fake.calls)
        self.assertNotIn(("POST", "canvas_nodes"), fake.calls)
        self.assertEqual(len(fake.artifacts), 1)
        self.assertEqual(len(fake.nodes), 1)

    def test_unknown_job_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            server = self._server_with_artifacts(Path(raw))
            with self.assertRaises(server.ToolError):
                server.call_tool("research_status", {"job_id": "job_v1_0000000000000000000000000Z"})


class FinalBriefPathTests(unittest.TestCase):
    def test_confirm_with_final_brief_uses_the_job_id_from_that_brief(self) -> None:
        server = load_server()
        edited = (
            "MEIN BEARBEITETER AUFTRAG\n"
            "Schreibe nach: /tmp/research_job_v1_0000000000000000000000000A.md\n"
        )
        seen = {}
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call"), \
             mock.patch.object(server, "_write_job_file",
                               side_effect=lambda job_id, payload: seen.update(
                                   job_id=job_id, payload=payload)):
            result = server.call_tool("research_start", {
                "bubble_id": "bub123", "brief": "x", "confirm": True,
                "final_brief": edited,
            })

        self.assertEqual(result["job_id"], "job_v1_0000000000000000000000000A")
        self.assertEqual(seen["job_id"], "job_v1_0000000000000000000000000A")
        self.assertIn("job_v1_0000000000000000000000000A", result["report_path"])
        self.assertIn("job_v1_0000000000000000000000000A", seen["payload"]["report_path"])

    def test_final_brief_without_a_path_is_rejected_before_the_run(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call") as spawn, \
             mock.patch.object(server, "_write_job_file") as write:
            with self.assertRaises(server.ToolError) as ctx:
                server.call_tool("research_start", {
                    "bubble_id": "bub123", "brief": "x", "confirm": True,
                    "final_brief": "Auftrag ohne jeden Pfad.",
                })

        self.assertTrue(str(ctx.exception).startswith("invalid_arguments"))
        spawn.assert_not_called()
        write.assert_not_called()

    def test_confirm_without_final_brief_still_uses_a_fresh_job_id(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call"), \
             mock.patch.object(server, "_write_job_file"):
            result = server.call_tool("research_start", {
                "bubble_id": "bub123", "brief": "x", "confirm": True,
            })

        self.assertRegex(result["job_id"], r"^job_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$")
        self.assertIn(result["job_id"], result["report_path"])


if __name__ == "__main__":
    unittest.main()
