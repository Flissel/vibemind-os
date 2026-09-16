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
        sent = {}
        with mock.patch.object(server, "_read_bubble", return_value=BUBBLE), \
             mock.patch.object(server, "_read_nodes", return_value=NODES), \
             mock.patch.object(server, "_spawn_agent_call",
                               side_effect=lambda job_id, text: sent.update(text=text)), \
             mock.patch.object(server, "_write_job_file"):
            server.call_tool(
                "research_start",
                {"bubble_id": "bub123", "brief": "ignoriert", "confirm": True,
                 "final_brief": "MEIN BEARBEITETER AUFTRAG"},
            )

        self.assertEqual(sent["text"], "MEIN BEARBEITETER AUFTRAG")


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


if __name__ == "__main__":
    unittest.main()
