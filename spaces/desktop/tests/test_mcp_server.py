"""Vertragstests für den Werkzeug-Dienst des Desktop-Space.

Kein Test bewegt Maus oder Tastatur: Der Desktop-Dienst ist durch ein Double ersetzt,
das nur mitschreibt, was es bekommen hätte.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("spaces_desktop_mcp_server", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DesktopMcpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = load_server()
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)
        self.env = mock.patch.dict(
            os.environ, {"VIBEMIND_DESKTOP_AUFGABEN_DIR": self.ordner.name}, clear=False
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("VIBEMIND_DESKTOP_AUSFUEHREN", None)
        os.environ.pop("DESKTOP_AUTOMATION_URL", None)
        self.aufrufe: list[tuple[str, str, object]] = []

        def dienst(method, pfad, body=None, *, timeout):
            self.aufrufe.append((method, pfad, body))
            return {"success": True}

        self.http = mock.patch.object(self.server, "_http", side_effect=dienst)
        self.http.start()
        self.addCleanup(self.http.stop)

    def start(self, text="Editor öffnen") -> str:
        return self.server.call_tool("desktop_task_start", {"task": text})["id"]

    def test_tools_list_is_the_stable_surface(self) -> None:
        namen = [tool["name"] for tool in self.server.TOOLS]
        self.assertEqual(
            namen,
            [
                "desktop_status",
                "desktop_screen_read",
                "desktop_elements",
                "desktop_find",
                "desktop_screenshot",
                "desktop_task_start",
                "desktop_task_step",
                "desktop_task_finish",
                "desktop_task_status",
            ],
        )

    def test_start_returns_a_running_task_that_the_status_shows(self) -> None:
        kennung = self.start()
        stand = self.server.call_tool("desktop_task_status", {})
        self.assertEqual((stand["id"], stand["zustand"], stand["schritte"]), (kennung, "laeuft", []))
        self.assertEqual(stand["space"], "desktop")

    def test_only_one_task_runs_at_a_time(self) -> None:
        self.start()
        with self.assertRaisesRegex(self.server.ToolError, "^aufgabe_laeuft"):
            self.start("zweite")

    def test_without_permission_a_step_is_recorded_but_not_sent(self) -> None:
        kennung = self.start()
        antwort = self.server.call_tool(
            "desktop_task_step", {"task_id": kennung, "action": "click", "x": 10, "y": 20, "why": "Startknopf"}
        )
        self.assertEqual(self.aufrufe, [])
        self.assertTrue(antwort["schritt"]["meldung"].startswith("trocken"))
        self.assertEqual(antwort["schritt"]["angaben"], {"x": 10, "y": 20, "warum": "Startknopf"})

    def test_with_permission_each_action_reaches_its_route(self) -> None:
        os.environ["VIBEMIND_DESKTOP_AUSFUEHREN"] = "1"
        kennung = self.start()
        for angaben in (
            {"action": "click", "x": 1, "y": 2},
            {"action": "type", "text": "hallo"},
            {"action": "key", "key": "enter"},
            {"action": "hotkey", "keys": ["ctrl", "s"]},
            {"action": "scroll", "direction": "down", "amount": 2},
        ):
            self.server.call_tool("desktop_task_step", {"task_id": kennung, **angaben})
        self.assertEqual(
            self.aufrufe,
            [
                ("POST", "/api/mcp/click", {"x": 1, "y": 2}),
                ("POST", "/api/mcp/type", {"text": "hallo"}),
                ("POST", "/api/mcp/key", {"key": "enter"}),
                ("POST", "/api/mcp/hotkey", {"keys": ["ctrl", "s"]}),
                ("POST", "/api/mcp/scroll", {"direction": "down", "amount": 2}),
            ],
        )
        schritte = self.server.call_tool("desktop_task_status", {"task_id": kennung})["schritte"]
        self.assertEqual([s["nr"] for s in schritte], [1, 2, 3, 4, 5])
        self.assertTrue(all(s["ok"] for s in schritte))

    def test_typed_text_never_lands_in_the_journal(self) -> None:
        os.environ["VIBEMIND_DESKTOP_AUSFUEHREN"] = "1"
        kennung = self.start()
        self.server.call_tool("desktop_task_step", {"task_id": kennung, "action": "type", "text": "streng geheim"})
        datei = Path(self.ordner.name, f"{kennung}.json").read_text(encoding="utf-8")
        self.assertNotIn("streng geheim", datei)
        self.assertIn('"zeichen": 13', datei)

    def test_a_failing_service_step_is_recorded_as_failed(self) -> None:
        os.environ["VIBEMIND_DESKTOP_AUSFUEHREN"] = "1"
        kennung = self.start()
        self.http.stop()
        with mock.patch.object(self.server, "_http", side_effect=self.server.ToolError("dienst_veraltet: x")):
            antwort = self.server.call_tool("desktop_task_step", {"task_id": kennung, "action": "key", "key": "enter"})
        self.http.start()
        self.assertFalse(antwort["schritt"]["ok"])
        self.assertIn("dienst_veraltet", antwort["schritt"]["meldung"])

    def test_no_step_without_a_running_task(self) -> None:
        kennung = self.start()
        self.server.call_tool("desktop_task_finish", {"task_id": kennung, "state": "fertig", "result": "ok"})
        with self.assertRaisesRegex(self.server.ToolError, "^aufgabe_beendet"):
            self.server.call_tool("desktop_task_step", {"task_id": kennung, "action": "click", "x": 1, "y": 1})
        with self.assertRaisesRegex(self.server.ToolError, "^aufgabe_unbekannt"):
            self.server.call_tool("desktop_task_step", {"task_id": "../x", "action": "click", "x": 1, "y": 1})

    def test_task_id_must_match_completely(self) -> None:
        kennung = self.start()
        for falsch in (kennung + chr(10), " " + kennung, kennung + "x", 7, None):
            with self.assertRaisesRegex(self.server.ToolError, "^aufgabe_unbekannt"):
                self.server.call_tool("desktop_task_step", {"task_id": falsch, "action": "wait", "seconds": 0})

    def test_a_damaged_journal_file_is_an_error_not_a_crash(self) -> None:
        kennung = self.start()
        Path(self.ordner.name, f"{kennung}.json").write_text("{kaputt", encoding="utf-8")
        with self.assertRaisesRegex(self.server.ToolError, "^aufgabenbuch_unlesbar"):
            self.server.call_tool("desktop_task_status", {"task_id": kennung})
        Path(self.ordner.name, f"{kennung}.json").write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(self.server.ToolError, "^aufgabenbuch_unlesbar"):
            self.server.call_tool("desktop_task_status", {})

    def test_stop_file_blocks_steps_and_new_tasks(self) -> None:
        os.environ["VIBEMIND_DESKTOP_AUSFUEHREN"] = "1"
        kennung = self.start()
        Path(self.ordner.name, "STOP").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(self.server.ToolError, "^not_aus"):
            self.server.call_tool("desktop_task_step", {"task_id": kennung, "action": "click", "x": 1, "y": 1})
        self.assertEqual(self.aufrufe, [])
        self.server.call_tool("desktop_task_finish", {"task_id": kennung, "state": "abgebrochen"})
        with self.assertRaisesRegex(self.server.ToolError, "^not_aus"):
            self.start("neu")

    def test_step_limit_and_expiry(self) -> None:
        kennung = self.start()
        with mock.patch.object(self.server.aufgaben, "MAX_SCHRITTE", 2):
            for _ in range(2):
                self.server.call_tool("desktop_task_step", {"task_id": kennung, "action": "wait", "seconds": 0})
            with self.assertRaisesRegex(self.server.ToolError, "^schritte_erschoepft"):
                self.server.call_tool("desktop_task_step", {"task_id": kennung, "action": "wait", "seconds": 0})
        stand = self.server.call_tool("desktop_task_status", {})
        self.assertEqual((stand["zustand"], len(stand["schritte"])), ("abgebrochen", 2))
        self.assertIn("Obergrenze", stand["ergebnis"])

        os.environ["VIBEMIND_DESKTOP_AUSFUEHREN"] = "1"
        zweite = self.start("läuft ab")
        with mock.patch.object(self.server.aufgaben, "LAUFZEIT_SEKUNDEN", -1):
            with self.assertRaisesRegex(self.server.ToolError, "^aufgabe_beendet: Zustand ist abgelaufen"):
                self.server.call_tool("desktop_task_step", {"task_id": zweite, "action": "click", "x": 1, "y": 1})
        self.assertEqual(self.aufrufe, [])
        self.start("nach Ablauf geht eine neue")

    def test_a_real_step_is_in_the_journal_before_the_service_is_called(self) -> None:
        os.environ["VIBEMIND_DESKTOP_AUSFUEHREN"] = "1"
        kennung = self.start()
        gesehen = []

        def dienst(method, pfad, body=None, *, timeout):
            gesehen.append(self.server.aufgaben.lesen(kennung)["schritte"][-1].copy())
            return {"success": True}

        self.http.stop()
        with mock.patch.object(self.server, "_http", side_effect=dienst):
            antwort = self.server.call_tool("desktop_task_step", {"task_id": kennung, "action": "click", "x": 5, "y": 6})
        self.http.start()
        self.assertEqual((gesehen[0]["art"], gesehen[0]["angaben"], gesehen[0]["ok"]), ("click", {"x": 5, "y": 6}, None))
        self.assertGreater(gesehen[0]["zeit"], 0)
        self.assertEqual((antwort["schritt"]["ok"], antwort["schritt"]["echt"]), (True, True))

    def test_stop_is_checked_before_every_single_step(self) -> None:
        os.environ["VIBEMIND_DESKTOP_AUSFUEHREN"] = "1"
        kennung = self.start()
        self.server.call_tool("desktop_task_step", {"task_id": kennung, "action": "click", "x": 1, "y": 1})
        Path(self.ordner.name, "STOP").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(self.server.ToolError, "^not_aus"):
            self.server.call_tool("desktop_task_step", {"task_id": kennung, "action": "click", "x": 2, "y": 2})
        self.assertEqual(len(self.aufrufe), 1)
        self.assertEqual(len(self.server.call_tool("desktop_task_status", {"task_id": kennung})["schritte"]), 1)

    def test_dry_steps_are_marked_as_not_real(self) -> None:
        kennung = self.start()
        schritt = self.server.call_tool("desktop_task_step", {"task_id": kennung, "action": "key", "key": "enter"})["schritt"]
        self.assertEqual((schritt["echt"], schritt["ok"]), (False, True))

    def test_invalid_step_input_is_refused_before_anything_happens(self) -> None:
        os.environ["VIBEMIND_DESKTOP_AUSFUEHREN"] = "1"
        kennung = self.start()
        for angaben in (
            {"action": "click", "x": "1", "y": 2},
            {"action": "type", "text": ""},
            {"action": "hotkey", "keys": ["ctrl"]},
            {"action": "scroll", "direction": "left"},
            {"action": "shell", "command": "dir"},
            {"action": "click", "x": 1, "y": 2, "button": "right"},
        ):
            with self.assertRaisesRegex(self.server.ToolError, "^eingabe_ungueltig"):
                self.server.call_tool("desktop_task_step", {"task_id": kennung, **angaben})
        self.assertEqual(self.aufrufe, [])
        self.assertEqual(self.server.call_tool("desktop_task_status", {})["schritte"], [])

    def test_the_service_address_must_be_this_machine(self) -> None:
        self.http.stop()
        try:
            for adresse in ("http://192.0.2.7:8007", "https://127.0.0.1:8007", "http://example.com"):
                with mock.patch.dict(os.environ, {"DESKTOP_AUTOMATION_URL": adresse}):
                    with self.assertRaisesRegex(self.server.ToolError, "^konfiguration"):
                        self.server._http("GET", "/api/health/health", timeout=1)
        finally:
            self.http.start()

    def test_screenshot_is_saved_and_described(self) -> None:
        bild = b"\xff\xd8\xff\xe0 kein echtes Bild"
        self.http.stop()
        antwort = {"screenshot": {"data": base64.b64encode(bild).decode(), "width": 4, "height": 3}}
        with mock.patch.object(self.server, "_http", return_value=antwort):
            ergebnis = self.server.call_tool("desktop_screenshot", {"monitor": 2})
        self.http.start()
        self.assertEqual(Path(ergebnis["pfad"]).read_bytes(), bild)
        self.assertEqual((ergebnis["bytes"], ergebnis["breite"], ergebnis["monitor"]), (len(bild), 4, 2))

    def test_elements_come_back_as_click_points(self) -> None:
        self.http.stop()
        antwort = {
            "success": True,
            "window": {"title": "Editor"},
            "elements": [
                {"name": "Speichern", "control_type": "ButtonControl", "is_enabled": True,
                 "bounds": {"left": 100, "top": 40, "width": 60, "height": 20}},
                {"name": "unsichtbar", "control_type": "ButtonControl", "bounds": {"left": 0, "top": 0, "width": 0, "height": 0}},
            ],
        }
        with mock.patch.object(self.server, "_http", return_value=antwort):
            ergebnis = self.server.call_tool("desktop_elements", {})
        self.http.start()
        self.assertEqual(
            ergebnis,
            {"fenster": "Editor", "elemente": [{"name": "Speichern", "typ": "Button", "x": 130, "y": 50, "aktiv": True}]},
        )

    def test_stdio_round_trip(self) -> None:
        zeilen = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "desktop_task_start", "arguments": {"task": "Prüfung äöü"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
        ]
        lauf = subprocess.run(
            [sys.executable, str(SERVER_PATH)],
            input="\n".join(json.dumps(z, ensure_ascii=False) for z in zeilen) + "\n",
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            env={**os.environ},
        )
        antworten = [json.loads(z) for z in lauf.stdout.splitlines()]
        self.assertEqual(antworten[0]["result"]["serverInfo"]["name"], "spaces-desktop")
        aufgabe = json.loads(antworten[1]["result"]["content"][0]["text"])
        self.assertEqual((aufgabe["aufgabe"], aufgabe["trocken"]), ("Prüfung äöü", True))
        self.assertTrue(antworten[2]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
