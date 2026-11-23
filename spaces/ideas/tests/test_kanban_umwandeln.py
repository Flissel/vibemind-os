"""'Kanban' baut Spalten mit Karten aus dem Text, ohne Modell (Lauf 5, Punkt 15). Namen erfunden."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest import mock

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("spaces_ideas_mcp_server_kanban", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _namen(board):
    return [(s["name"], [k["title"] for k in s["cards"]]) for s in board["columns"]]


class KanbanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = load_server()

    def test_ohne_spalten_steht_alles_in_offen(self) -> None:
        board = self.server._kanban_from_text("Aufgaben\n- Logo\n- [x] Plakat drucken\n- Bühne mieten", "Aufgaben")
        self.assertEqual(_namen(board), [("Offen", ["Logo", "Bühne mieten"]), ("In Arbeit", []),
                                         ("Erledigt", ["Plakat drucken"])])

    def test_ueberschriften_und_statuszeilen_werden_spalten(self) -> None:
        text = "## Offen\n- Logo\n- Sponsoren\nIn Arbeit:\n- Bühnenplan\nErledigt: Druckerei anfragen"
        self.assertEqual(_namen(self.server._kanban_from_text(text, "")),
                         [("Offen", ["Logo", "Sponsoren"]), ("In Arbeit", ["Bühnenplan"]),
                          ("Erledigt", ["Druckerei anfragen"])])

    def test_name_wert_zeile_ist_eine_karte_keine_spalte(self) -> None:
        board = self.server._kanban_from_text("Preis: 900 Euro", "")
        self.assertEqual(_namen(board)[0], ("Offen", ["Preis: 900 Euro"]))

    def test_nur_titel_gibt_kein_board(self) -> None:
        self.assertIsNone(self.server._kanban_from_text("Aufgaben", "Aufgaben"))

    def test_kartenansicht_liest_das_gebaute_board(self) -> None:
        board = self.server._kanban_from_text("- Logo\n- [x] Plakat", "")
        sections, count = self.server._structured_sections("kanban", board, 200)
        self.assertEqual(count, "2 Karten in 3 Spalten")

    def _format(self, node):
        aufrufe = []

        def antwort(method, path, **kwargs):
            aufrufe.append((method, kwargs))
            return [dict(node)] if method == "GET" else [{**node, **kwargs["body"]}]
        with mock.patch.object(self.server, "_request", side_effect=antwort):
            self.server.call_tool("idea_format", {"id": "a0000001", "format": "kanban"})
        return [k["body"] for m, k in aufrufe if m == "PATCH"]

    def test_format_kanban_schreibt_spalten(self) -> None:
        node = {"id": "a0000001", "title": "Aufgaben", "content": "- Logo\n- Bühne mieten", "content_json": None}
        (body,) = self._format(node)
        self.assertEqual(body["content_json"]["type"], "kanban")
        self.assertEqual(_namen(body["content_json"])[0], ("Offen", ["Logo", "Bühne mieten"]))
        self.assertEqual(body["content_json"]["text"], "- Logo\n- Bühne mieten")


if __name__ == "__main__":
    unittest.main()
