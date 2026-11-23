"""'Tabelle' baut Spalten und Zeilen ohne Modell oder aendert nichts (Arbeitslauf 2, Punkt 5)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest import mock

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("spaces_ideas_mcp_server_tabelle", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TabelleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = load_server()

    def test_spalten_aus_text(self) -> None:
        f = self.server._table_from_text
        self.assertEqual(f("Preise\nStand: 900 €\nDruck: 120 €", "Preise"),
                         {"columns": ["Punkt", "Angabe"], "rows": [["Stand", "900 €"], ["Druck", "120 €"]]})
        self.assertEqual(f("Posten;Preis\nZelt;300\nBühne;900", ""),
                         {"columns": ["Posten", "Preis"], "rows": [["Zelt", "300"], ["Bühne", "900"]]})
        self.assertEqual(f("| Posten | Preis |\n|---|---|\n| Zelt | 300 |", ""),
                         {"columns": ["Posten", "Preis"], "rows": [["Zelt", "300"]]})
        self.assertEqual(f("Posten\tPreis\nZelt\t300", "")["rows"], [["Zelt", "300"]])

    def test_freier_text_hat_keine_spalten(self) -> None:
        f = self.server._table_from_text
        self.assertIsNone(f("Wir brauchen ein Zelt und eine Bühne. Das kostet etwa 1200 Euro.", ""))
        self.assertIsNone(f("Posten;Preis\nZelt", ""))            # ungleiche Zellen
        self.assertIsNone(f("Preise\nStand: 900", "Preise"))      # eine Zeile reicht nicht

    def _format(self, node):
        aufrufe = []

        def antwort(method, path, **kwargs):
            aufrufe.append((method, kwargs))
            return [dict(node)] if method == "GET" else [{**node, **kwargs["body"]}]
        with mock.patch.object(self.server, "_request", side_effect=antwort):
            self.server.call_tool("idea_format", {"id": "a0000001", "format": "table"})
        return [k["body"] for m, k in aufrufe if m == "PATCH"]

    def test_format_tabelle_schreibt_columns_und_rows(self) -> None:
        node = {"id": "a0000001", "title": "Preise", "content": "Stand: 900\nDruck: 120", "content_json": None}
        (body,) = self._format(node)
        self.assertEqual(body["content_json"]["columns"], ["Punkt", "Angabe"])
        self.assertEqual(body["content_json"]["rows"], [["Stand", "900"], ["Druck", "120"]])
        self.assertEqual(body["content_json"]["text"], "Stand: 900\nDruck: 120")

    def test_format_tabelle_ohne_spalten_aendert_nichts(self) -> None:
        node = {"id": "a0000001", "title": "Preise", "content": "Ein Zelt kostet ungefähr 300 Euro.",
                "content_json": None}
        with self.assertRaisesRegex(self.server.ToolError, "^idea_format_table_no_columns$"):
            self._format(node)


if __name__ == "__main__":
    unittest.main()
