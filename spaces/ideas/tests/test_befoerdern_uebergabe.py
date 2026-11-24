"""Befoerdern mit Uebergabe: Sinnpruefung vorher, fester Stand im Projekt, Ziel Software-Design.

Nur erfundene Testdaten (Stadtfest). Die Regeln kommen aus spaces/_contract/kartenregeln.py,
derselben Datei, die die Software-Design-Ansicht liest.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"
BUBBLE = "b0b1e001"


def load_server():
    spec = importlib.util.spec_from_file_location("spaces_ideas_mcp_server_befoerdern", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _karte(kennung, titel, inhalt, **mehr):
    return {"id": kennung, "node_type": mehr.pop("node_type", "note"), "title": titel, "content": inhalt,
            "content_json": None, "metadata": mehr.pop("metadata", {}), "updated_at": mehr.pop("am", "2026-11-20T10:00:00+00:00")}


GUTE_KARTEN = [
    _karte("a0000001", "Sponsoren", "", metadata={"gruppen_kopf": True}),
    _karte("a0000002", "Druckerei", "Die Druckerei liefert Plakate bis Freitag."),
    _karte("a0000003", "Bühnenplan", "Der Bühnenplan steht für Samstag und Sonntag fest."),
    _karte("a0000004", "White Paper", "Das Stadtfest braucht einen Bühnenplan und Sponsoren.",
           node_type="dokument", metadata={"art": "white_paper", "quellen": ["a0000002", "a0000003"]},
           am="2026-11-22T08:30:00+00:00"),
]
GUTE_KANTEN = [
    {"from_node_id": "a0000001", "to_node_id": "a0000002", "edge_type": "related"},
    {"from_node_id": "a0000002", "to_node_id": "a0000003", "edge_type": "related"},
]


class Datenbank:
    """Antwortet wie PostgREST auf genau die Lesewege des Befoerderns; schreibt mit."""

    def __init__(self, karten, kanten, bubble=None):
        self.karten, self.kanten = karten, kanten
        self.bubble = bubble or {"id": BUBBLE, "title": "Stadtfest", "description": "", "score": 70,
                                 "metadata": {}}
        self.aufrufe = []
        self.projekt = None

    def __call__(self, method, path, **kwargs):
        self.aufrufe.append((method, path))
        params = kwargs.get("params") or {}
        if method == "GET" and path == "ideas":
            return [self.bubble]
        if method == "GET" and path == "canvas_nodes":
            return list(self.karten)
        if method == "GET" and path == "canvas_edges":
            von = params["from_node_id"][len("in.("):-1].split(",")
            return [k for k in self.kanten if k["from_node_id"] in von]
        if method == "POST" and path == "projects":
            self.projekt = dict(kwargs["body"])
            return [self.projekt]
        if method == "PATCH" and path == "ideas":
            return [{"id": BUBBLE, "status": "promoted", "promoted_to_project_id": self.projekt["id"]}]
        raise AssertionError(f"unerwartet: {method} {path}")


class BefoerdernTests(unittest.TestCase):
    def _befoerdern(self, db, **argumente):
        server = load_server()
        with mock.patch.object(server, "_request", side_effect=db):
            return server, server.call_tool("bubble_promote", {"bubble_id": BUBBLE, **argumente})

    def test_gute_bubble_wird_projekt_mit_festem_stand_und_ziel(self):
        db = Datenbank(GUTE_KARTEN, GUTE_KANTEN)
        _server, quittung = self._befoerdern(db)
        stand = db.projekt["metadata"]["uebergabe"]

        self.assertEqual(stand["version"], 1)
        self.assertEqual((stand["bubble_id"], stand["titel"]), (BUBBLE, "Stadtfest"))
        self.assertEqual(stand["bewertung"], {"score": 70, "begruendungen": 0})
        self.assertEqual(stand["white_paper"]["node_id"], "a0000004")
        self.assertIn("Bühnenplan", stand["white_paper"]["text"])
        self.assertEqual(stand["white_paper"]["quellen"], ["a0000002", "a0000003"])
        karten = {k["id"]: k for k in stand["karten"]}
        self.assertNotIn("a0000004", karten)                      # das White Paper steht eigens
        self.assertEqual(karten["a0000001"]["art"], "gruppe")
        self.assertEqual(karten["a0000002"]["gruppe"], "Sponsoren")
        self.assertIsNone(karten["a0000003"]["gruppe"])
        self.assertEqual(karten["a0000003"]["text"], "Der Bühnenplan steht für Samstag und Sonntag fest.")
        self.assertEqual(stand["kanten"], [{"von": "a0000001", "nach": "a0000002", "art": "related"},
                                           {"von": "a0000002", "nach": "a0000003", "art": "related"}])
        self.assertTrue(stand["pruefung"]["bestanden"])

        # Pruefsumme so, wie die Ansicht sie nachrechnet: ohne "am" und "pruefsumme".
        kern = {k: v for k, v in stand.items() if k not in ("am", "pruefsumme")}
        erwartet = hashlib.sha256(json.dumps(kern, sort_keys=True, ensure_ascii=False,
                                             separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(stand["pruefsumme"], "sha256:" + erwartet)

        # Quittung: klein, mit Ziel fuer die App.
        self.assertEqual(quittung["ziel"], {"space": "swedesign", "projekt_id": db.projekt["id"]})
        self.assertEqual(quittung["karten"], 2)
        self.assertEqual(quittung["white_paper_vom"], "2026-11-22T08:30:00+00:00")
        self.assertNotIn("uebergabe", quittung["metadata"])
        self.assertEqual(db.aufrufe, [("GET", "ideas"), ("GET", "canvas_nodes"), ("GET", "canvas_edges"),
                                      ("POST", "projects"), ("PATCH", "ideas")])

    def test_durchgefallen_nennt_die_gruende_und_schreibt_nichts(self):
        karten = [
            _karte("a0000002", "Druckerei", "Plakate"),                          # leer, lose
            _karte("a0000003", "Bühnenplan", "Der Bühnenplan steht für Samstag fest."),
        ]
        bubble = {"id": BUBBLE, "title": "Stadtfest", "score": 0.0, "metadata": {}}
        db = Datenbank(karten, [], bubble=bubble)
        server = load_server()
        with mock.patch.object(server, "_request", side_effect=db):
            with self.assertRaises(server.ToolError) as fehler:
                server.call_tool("bubble_promote", {"bubble_id": BUBBLE})
        text = str(fehler.exception)
        self.assertTrue(text.startswith(
            "befoerdern_nicht_bereit: leere_karten:1,lose_karten:2,keine_bewertung,kein_white_paper"), text)
        self.assertIn("leer=a0000002", text)
        self.assertIn("lose=a0000002,a0000003", text)
        self.assertNotIn(("POST", "projects"), db.aufrufe)
        self.assertNotIn(("PATCH", "ideas"), db.aufrufe)

    def test_bewertung_null_ohne_begruendung_fehlt_mit_begruendung_zaehlt(self):
        server = load_server()
        self.assertIsNone(server._bewertung({"score": 0.0, "metadata": {}}))
        self.assertIsNone(server._bewertung({"score": None}))
        self.assertEqual(server._bewertung({"score": 0, "metadata": {"bewertungen": [{"id": "x"}]}}),
                         {"score": 0, "begruendungen": 1})
        self.assertEqual(server._bewertung({"score": 55}), {"score": 55, "begruendungen": 0})

    def test_zu_grosser_stand_wird_nicht_befoerdert(self):
        db = Datenbank(GUTE_KARTEN, GUTE_KANTEN)
        server = load_server()
        with mock.patch.object(server, "_request", side_effect=db), \
                mock.patch.object(server, "UEBERGABE_MAX_ZEICHEN", 500):
            with self.assertRaisesRegex(server.ToolError, "^befoerdern_zu_gross"):
                server.call_tool("bubble_promote", {"bubble_id": BUBBLE})
        self.assertNotIn(("POST", "projects"), db.aufrufe)

    def test_schon_befoerdert_liest_keine_karten(self):
        bubble = {"id": BUBBLE, "title": "Stadtfest", "score": 70, "promoted_to_project_id": "c0000001"}
        db = Datenbank(GUTE_KARTEN, GUTE_KANTEN, bubble=bubble)
        server = load_server()
        with mock.patch.object(server, "_request", side_effect=db):
            with self.assertRaisesRegex(server.ToolError, "^bubble_already_promoted"):
                server.call_tool("bubble_promote", {"bubble_id": BUBBLE})
        self.assertEqual(db.aufrufe, [("GET", "ideas")])

    def test_regeln_kommen_aus_der_gemeinsamen_datei(self):
        server = load_server()
        regeln = server._kartenregeln()
        self.assertTrue(Path(regeln.__file__).as_posix().endswith("spaces/_contract/kartenregeln.py"))


if __name__ == "__main__":
    unittest.main()
