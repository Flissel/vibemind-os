"""Ergaenzen auf formatierten Karten (Arbeitslauf 2): der Text muss sichtbar werden. Erfundene Namen."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest import mock

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("spaces_ideas_mcp_server_ergaenzen", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TABELLE_LEER = {"id": "a0000001", "title": "Preise", "content": "Preise",
                "content_json": {"type": "table", "title": "Preise", "text": ""}}
KANBAN = {"id": "a0000002", "title": "Aufbau", "content": "Aufbau",
          "content_json": {"type": "kanban", "columns": [{"name": "Offen", "cards": [{"title": "Zelt"}]}]}}


class ErgaenzenTests(unittest.TestCase):
    def _schreiben(self, server, karte, argumente):
        aufrufe = []

        def antwort(method, path, **kwargs):
            aufrufe.append((method, kwargs))
            if method == "GET":
                return [dict(karte)]
            return [{**karte, **kwargs["body"]}]
        with mock.patch.object(server, "_request", side_effect=antwort):
            server.call_tool("idea_update", argumente)
        return [k for m, k in aufrufe if m == "PATCH"][0]["body"]

    def test_ergaenzen_haelt_den_text_der_formatierten_karte_mit(self) -> None:
        server = load_server()
        body = self._schreiben(server, TABELLE_LEER, {"id": "a0000001", "content_append": "Stand 900"})
        self.assertEqual(body["content"], "Stand 900")
        self.assertEqual(body["content_json"], {"type": "table", "title": "Preise", "text": "Stand 900"})

    def test_struktur_bleibt_und_der_text_kommt_dazu(self) -> None:
        server = load_server()
        body = self._schreiben(server, KANBAN, {"id": "a0000002", "content_append": "Strom klären"})
        self.assertEqual(body["content_json"]["columns"], KANBAN["content_json"]["columns"])
        self.assertEqual(body["content_json"]["text"], "Strom klären")
        karte = server._card_read({**KANBAN, **body}, [], {})
        self.assertIn({"ueberschrift": "Notiz", "zeilen": ["Strom klären"]}, karte["abschnitte"])

    def test_ganzer_text_ersetzt_und_notiz_ohne_struktur_unveraendert(self) -> None:
        server = load_server()
        body = self._schreiben(server, TABELLE_LEER, {"id": "a0000001", "content": "A kostet 1"})
        self.assertEqual(body["content_json"]["text"], "A kostet 1")
        notiz = {"id": "a0000003", "title": "Helfer", "content": "Zwölf Leute", "content_json": None}
        body = self._schreiben(server, notiz, {"id": "a0000003", "content_append": "Zwei Schichten"})
        self.assertNotIn("content_json", body)

    def test_alte_karte_mit_leerem_text_zeigt_den_inhalt(self) -> None:
        server = load_server()
        alt = {**TABELLE_LEER, "content": "Stand 900\nDruck 120"}
        self.assertEqual(server._card_short(alt)["zeilen"], ["Stand 900", "Druck 120"])
        self.assertEqual(server._card_read(alt, [], {})["text"], "Stand 900\nDruck 120")


if __name__ == "__main__":
    unittest.main()
