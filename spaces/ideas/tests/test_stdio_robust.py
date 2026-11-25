"""Der Ideen-Server als echter Prozess ueber stdio, so nackt wie OpenFang ihn startet.

Am 23.11.2026 meldete OpenFang fuer jedes Werkzeug "Transport closed": der Prozess war
beim ersten grossen idea_list gestorben. Unter Windows ist eine umgeleitete Ausgabe ohne
PYTHONIOENCODING nicht UTF-8; ein Zeichen ausserhalb der Codepage (Pfeil, Emoji) in einer
Notiz liess ``print`` scheitern, und die Schleife endete. Diese Tests starten den Server
ohne jede Kodierungs-Vorgabe gegen eine nachgestellte Datenbank.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"
SCHWIERIG = "Preise prüfen → ✅ 🚀 „Zitat“   Zeile"
LANG = "Bericht " + ("äöü → 🚀 " * 9000)   # deutlich ueber 64 KB in einer Antwortzeile


class _Datenbank(BaseHTTPRequestHandler):
    gespeichert: list = []

    def _send(self, payload) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if "ilike" in self.path:
            self._send([])
            return
        self._send([
            {"id": "n1", "title": SCHWIERIG, "content": LANG, "node_type": "note", "x": 0, "y": 0,
             "linked_idea_id": "bub1", "metadata": {"bewertung": {"wert": 80}}, "format_schema": {"type": "table"}},
            {"id": "n2", "title": "kurz", "content": "kurz", "node_type": "note", "x": 1, "y": 1,
             "linked_idea_id": "bub1", "metadata": {}, "format_schema": None},
        ])

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8"))
        type(self).gespeichert.append(body)
        self._send([body])

    def log_message(self, *args) -> None:
        pass


class StdioRobustTests(unittest.TestCase):
    def setUp(self) -> None:
        _Datenbank.gespeichert = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Datenbank)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        # Nur was Windows braucht, plus der Zugang: KEIN PYTHONIOENCODING, KEIN PYTHONUTF8.
        self.env = {name: os.environ[name] for name in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR") if name in os.environ}
        self.env.update({
            "SUPABASE_URL": f"http://127.0.0.1:{self.httpd.server_address[1]}",
            "SUPABASE_SERVICE_ROLE_KEY": "nur-im-test",
        })

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def _lauf(self, *aufrufe):
        zeilen = [json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize"})]
        for nummer, (name, argumente) in enumerate(aufrufe, start=1):
            zeilen.append(json.dumps(
                {"jsonrpc": "2.0", "id": nummer, "method": "tools/call",
                 "params": {"name": name, "arguments": argumente}},
                ensure_ascii=False))   # OpenFang schickt UTF-8, nicht \u-Folgen
        completed = subprocess.run(
            [sys.executable, str(SERVER_PATH)],
            input=("\n".join(zeilen) + "\n").encode("utf-8"),
            capture_output=True, env=self.env, check=False, timeout=60,
        )
        antworten = [json.loads(zeile) for zeile in completed.stdout.decode("utf-8").splitlines() if zeile.strip()]
        return completed, antworten

    @staticmethod
    def _inhalt(antwort):
        return json.loads(antwort["result"]["content"][0]["text"])

    def test_sonderzeichen_und_lange_antwort_beenden_den_prozess_nicht(self) -> None:
        completed, antworten = self._lauf(
            ("idea_list", {"bubble_id": "bub1", "full": True}),
            ("idea_list", {"bubble_id": "bub1"}),
            ("idea_get", {"id": "n1"}),
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace")[-600:])
        self.assertEqual([antwort.get("id") for antwort in antworten], [0, 1, 2, 3])
        voll = self._inhalt(antworten[1])
        self.assertEqual(voll[0]["title"], SCHWIERIG)
        self.assertEqual(voll[0]["content"], LANG)
        # Jede Antwort ist genau eine Zeile; auch U+2028 trennt sie nicht.
        self.assertEqual(len(completed.stdout.decode("utf-8").splitlines()), 4)
        self.assertTrue(completed.stdout.isascii())

    def test_umlaute_aus_der_anfrage_kommen_unverfaelscht_in_der_datenbank_an(self) -> None:
        completed, antworten = self._lauf(("idea_create", {"bubble_id": "bub1", "title": SCHWIERIG}))

        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace")[-600:])
        self.assertFalse(antworten[1]["result"]["isError"], antworten[1])
        self.assertEqual(_Datenbank.gespeichert[0]["title"], SCHWIERIG)

    def test_ein_absturz_in_einem_aufruf_beendet_die_schleife_nicht(self) -> None:
        zeilen = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []}),
            "kein json",
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "idea_list", "arguments": {"limit": {"kaputt": 1}}}}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
        ]) + "\n"
        completed = subprocess.run([sys.executable, str(SERVER_PATH)], input=zeilen.encode("utf-8"),
                                   capture_output=True, env=self.env, check=False, timeout=60)
        antworten = [json.loads(zeile) for zeile in completed.stdout.decode("utf-8").splitlines()]

        self.assertEqual(completed.returncode, 0)
        self.assertEqual([antwort.get("id") for antwort in antworten], [1, None, 2, 3])
        self.assertIn("tools", antworten[3]["result"])


class IdeaListKurzTests(unittest.TestCase):
    """idea_list liefert in der Vorgabe nur, was Canvas und Abkuerzung brauchen."""

    def _server(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("spaces_ideas_mcp_server_kurz", SERVER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    ZEILEN = [
        {"id": "n1", "title": "Preise", "content": "x" * 5000, "node_type": "note", "x": 10.0, "y": 20.0,
         "linked_idea_id": "bub1", "metadata": {"bewertung": {"wert": 80}, "width": 200.0},
         "format_schema": {"type": "table"}, "content_json": {"type": "table", "text": "x" * 5000},
         "previous_content_json": {"text": "alt"}, "summary": None},
        {"id": "n2", "title": "Kurz", "content": "nur kurz", "node_type": "idea", "x": 0, "y": 0,
         "linked_idea_id": "bub1", "metadata": None, "format_schema": None},
    ]

    def test_vorgabe_ist_kurz(self) -> None:
        from unittest import mock
        server = self._server()
        with mock.patch.object(server, "_request", return_value=[dict(zeile) for zeile in self.ZEILEN]) as request:
            antwort = server.call_tool("idea_list", {"bubble_id": "bub1"})

        result = antwort.pop("karten")
        # Seit dem Blaettern (Kartentext fuer alle Leser): versatz 0, keine weitere Seite.
        self.assertEqual(antwort, {"gezeigt": 2, "gesamt": 2, "gekuerzt": False, "versatz": 0, "weiter_ab": None})
        self.assertEqual(request.call_count, 1)  # nicht voll: keine zweite Abfrage zum Zaehlen
        karten = [eintrag.pop("karte") for eintrag in result]
        self.assertEqual([(k["titel"], k["art"], k["leer"]) for k in karten],
                         [("Preise", "Tabelle", False), ("Kurz", "Notiz", False)])
        self.assertEqual(karten[1]["zeilen"], ["nur kurz"])
        self.assertLessEqual(len(karten[0]["zeilen"][0]), server.CARD_LINE_CHARS)
        self.assertEqual(result, [
            {"id": "n1", "title": "Preise", "format": "table", "score": 80, "node_type": "note",
             "x": 10.0, "y": 20.0, "linked_idea_id": "bub1",
             "preview": "x" * server.IDEA_PREVIEW_CHARS, "content_length": 5000},
            {"id": "n2", "title": "Kurz", "format": "note", "score": None, "node_type": "idea",
             "x": 0, "y": 0, "linked_idea_id": "bub1", "preview": "nur kurz", "content_length": 8},
        ])
        params = request.call_args.kwargs["params"]
        self.assertNotIn("format_schema", params["select"])  # die grossen Schemata bleiben in der Datenbank
        self.assertNotIn("previous_content_json", params["select"])
        self.assertEqual(params["linked_idea_id"], "eq.bub1")

    def test_voller_inhalt_nur_auf_ausdrueckliche_anfrage(self) -> None:
        from unittest import mock
        server = self._server()
        with mock.patch.object(server, "_request", return_value=[dict(zeile) for zeile in self.ZEILEN]) as request:
            result = server.call_tool("idea_list", {"bubble_id": "bub1", "full": True})
        self.assertEqual(result[0]["content"], "x" * 5000)
        self.assertEqual(request.call_args.kwargs["params"]["select"], "*")

        with mock.patch.object(server, "_request") as request:
            for full in ("ja", 1, None):
                with self.assertRaisesRegex(server.ToolError, "^invalid_arguments: 'full'"):
                    server.call_tool("idea_list", {"full": full})
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
