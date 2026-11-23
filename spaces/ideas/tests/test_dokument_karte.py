"""Dokument-Karten des Ideen-Servers: idea_document und die Kartenansicht (Attrappen, erfundene Namen)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest import mock

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"
DOK_ID = "00000000-0000-4000-8000-000000000d01"
BUBBLE_ID = "b0b1e001"


def load_server():
    spec = importlib.util.spec_from_file_location("spaces_ideas_mcp_server_dokument", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def argumente(**mehr):
    return {"id": DOK_ID, "bubble_id": BUBBLE_ID, "title": "White Paper: Stadtfest", "art": "white_paper",
            "content": "# White Paper: Stadtfest\n\n## Inhalt\n", "quellen": ["a0000001", "a0000002", "a0000001"],
            **mehr}


class DokumentSchreibenTests(unittest.TestCase):
    def test_neue_dokument_karte_wird_angelegt(self) -> None:
        server = load_server()
        aufrufe = []

        def antwort(method, path, **kwargs):
            aufrufe.append((method, path, kwargs))
            if method == "GET":
                return []
            return [{**kwargs["body"]}]
        with mock.patch.object(server, "_request", side_effect=antwort):
            zeile = server.call_tool("idea_document", argumente())
        (_, _, abfrage), (methode, tabelle, schreiben) = aufrufe
        self.assertEqual((methode, tabelle), ("POST", "canvas_nodes"))
        body = schreiben["body"]
        self.assertEqual(body["node_type"], "dokument")
        self.assertEqual(body["linked_idea_id"], BUBBLE_ID)
        self.assertEqual(body["metadata"]["art"], "white_paper")
        self.assertEqual(body["metadata"]["quellen"], ["a0000001", "a0000002"])     # ohne Dubletten
        self.assertEqual(zeile["id"], DOK_ID)

    def test_bestehende_dokument_karte_wird_ersetzt(self) -> None:
        server = load_server()
        aufrufe = []

        def antwort(method, path, **kwargs):
            aufrufe.append((method, kwargs))
            if method == "GET":
                return [{"id": DOK_ID, "node_type": "dokument", "linked_idea_id": BUBBLE_ID}]
            return [{"id": DOK_ID, **kwargs["body"]}]
        with mock.patch.object(server, "_request", side_effect=antwort):
            server.call_tool("idea_document", argumente(content="# neu"))
        self.assertEqual(aufrufe[1][0], "PATCH")
        self.assertEqual(aufrufe[1][1]["params"], {"id": f"eq.{DOK_ID}"})
        self.assertEqual(aufrufe[1][1]["body"]["content"], "# neu")

    def test_fremde_karte_oder_bubble_wird_nie_ueberschrieben(self) -> None:
        server = load_server()
        for bestehend in ({"id": DOK_ID, "node_type": "idea", "linked_idea_id": BUBBLE_ID},
                          {"id": DOK_ID, "node_type": "dokument", "linked_idea_id": "b0b1e002"}):
            with mock.patch.object(server, "_request", return_value=[bestehend]) as request:
                with self.assertRaisesRegex(server.ToolError, "^idea_document_conflict$"):
                    server.call_tool("idea_document", argumente())
            self.assertEqual([c.args[0] for c in request.call_args_list], ["GET"])

    def test_argumente_werden_streng_geprueft(self) -> None:
        server = load_server()
        faelle = [
            argumente(art="projektdoku"),
            argumente(content=""),
            argumente(content="x" * 300_001),
            argumente(title="x" * 201),
            argumente(quellen=["nicht gut!"]),
            argumente(geheim="wert"),
        ]
        with mock.patch.object(server, "_request", side_effect=AssertionError("kein Aufruf")):
            for fall in faelle:
                with self.assertRaisesRegex(server.ToolError, "^invalid_arguments"):
                    server.call_tool("idea_document", fall)

    def test_werkzeug_steht_in_der_liste_und_gilt_als_aenderung(self) -> None:
        server = load_server()
        namen = [t["name"] for t in server.TOOLS]
        self.assertIn("idea_document", namen)
        self.assertIn("idea_document", server.CHANGE_WRITE_TOOLS)
        self.assertEqual(server.SERVER_VERSION, "1.3.0")


class DokumentAnsichtTests(unittest.TestCase):
    def test_kartenansicht_nennt_art_und_herkunft(self) -> None:
        server = load_server()
        zeile = {"id": DOK_ID, "title": "Zusammenfassung: Stadtfest", "node_type": "dokument",
                 "content": "Das Fest steht.\n\nGrundlage: „Bühnenplan“.",
                 "metadata": {"art": "zusammenfassung", "quellen": ["a0000001", "a0000002"]}}
        karte = server._card_short(zeile)
        self.assertEqual(karte["art"], "Zusammenfassung")
        self.assertEqual(karte["quelle"], "aus 2 Karten")
        self.assertEqual(karte["zeilen"][0], "Das Fest steht.")
        white = {**zeile, "metadata": {"art": "white_paper", "quellen": ["a0000001"]}}
        self.assertEqual(server._card_short(white)["art"], "White Paper")
        self.assertEqual(server._card_short(white)["quelle"], "aus 1 Karte")
        unbekannt = {**zeile, "metadata": {"art": "projektdoku"}}
        self.assertEqual(server._card_short(unbekannt)["art"], "White Paper")


if __name__ == "__main__":
    unittest.main()
