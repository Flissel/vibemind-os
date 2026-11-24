"""Befoerdern zuruecknehmen, auch wenn Software-Design das Projekt schon geoeffnet hat.

Pruefung 25.11.2026: Der Wizard legt beim Oeffnen einen Shuttle mit Verweis aufs Projekt an.
Das Loeschen des Projekts setzte dessen project_id leer, ausserhalb der Aenderung, und
ideen_zurueck brach mit aenderung_konflikt ab. Nur erfundene Kennungen.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"
PROJEKT = "a0000001"
ANTWORT = {"gruppe": "plan-7", "anzahl": 2, "eintraege": [
    {"tabelle": "public.ideas", "vorgang": "UPDATE", "zeilen_id": "b0b1e001", "titel": "Stadtfest"},
    {"tabelle": "public.projects", "vorgang": "INSERT", "zeilen_id": PROJEKT, "name": "Stadtfest"}]}


def load_server():
    spec = importlib.util.spec_from_file_location("spaces_ideas_mcp_server_zurueck", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Datenbank:
    """Attrappe: erster Aufruf von ideen_zurueck scheitert, solange der Wizard-Shuttle steht."""

    def __init__(self, server, shuttles, gruppe="plan-7", projekte=(PROJEKT,)):
        self.server, self.shuttles, self.gruppe, self.projekte = server, list(shuttles), gruppe, projekte
        self.aufrufe: list = []

    def __call__(self, method, path, **kwargs):
        self.aufrufe.append((method, path, kwargs))
        params = kwargs.get("params") or {}
        if path == "rpc/ideen_zurueck":
            if any(s["project_id"] == PROJEKT for s in self.shuttles):
                raise self.server.ToolError(f"aenderung_konflikt: public.shuttles {self.shuttles[0]['id']}")
            return ANTWORT
        if path == "ideen_aenderungen" and params.get("select") == "gruppe":
            return [{"gruppe": self.gruppe}]
        if path == "ideen_aenderungen":
            assert params["gruppe"] == f"eq.{self.gruppe}" and params["tabelle"] == "eq.public.projects"
            return [{"zeilen_id": p} for p in self.projekte]
        if path == "shuttles" and method == "GET":
            pid = params["project_id"].removeprefix("eq.")
            return [s for s in self.shuttles if s["project_id"] == pid]
        if path == "shuttles" and method == "DELETE":
            sid = params["id"].removeprefix("eq.")
            self.shuttles = [s for s in self.shuttles if s["id"] != sid]
            return []
        raise AssertionError((method, path))


def _wizard_shuttle(kennung="c0000001", quelle="befoerdertes_projekt"):
    return {"id": kennung, "project_id": PROJEKT, "metadata": {"quelle": quelle}}


class BefoerdernZurueck(unittest.TestCase):
    def test_wizard_shuttle_geht_und_zurueck_gelingt(self) -> None:
        server = load_server()
        db = _Datenbank(server, [_wizard_shuttle()])
        with mock.patch.object(server, "_request", side_effect=db):
            ergebnis = server.call_tool("aenderung_zurueck", {})
        self.assertEqual(ergebnis["anzahl"], 2)
        self.assertEqual(db.shuttles, [])
        wege = [(m, p) for m, p, _k in db.aufrufe]
        self.assertEqual(wege[0], ("POST", "rpc/ideen_zurueck"))
        self.assertEqual(wege[-1], ("POST", "rpc/ideen_zurueck"))
        self.assertIn(("DELETE", "shuttles"), wege)
        loeschen = next(k for m, p, k in db.aufrufe if m == "DELETE")
        self.assertEqual(loeschen["params"]["project_id"], f"eq.{PROJEKT}")

    def test_mit_gruppe_wird_genau_sie_gelesen(self) -> None:
        server = load_server()
        db = _Datenbank(server, [_wizard_shuttle()], gruppe="plan-9")
        with mock.patch.object(server, "_request", side_effect=db):
            server.call_tool("aenderung_zurueck", {"gruppe": "plan-9"})
        self.assertNotIn("gruppe", [k.get("params", {}).get("select") for _m, _p, k in db.aufrufe])

    def test_fremder_shuttle_bleibt_und_der_konflikt_auch(self) -> None:
        server = load_server()
        db = _Datenbank(server, [_wizard_shuttle(quelle="hand")])
        with mock.patch.object(server, "_request", side_effect=db):
            with self.assertRaises(server.ToolError) as fehler:
                server.call_tool("aenderung_zurueck", {})
        self.assertTrue(str(fehler.exception).startswith("aenderung_konflikt"))
        self.assertEqual(len(db.shuttles), 1)
        self.assertEqual([p for _m, p, _k in db.aufrufe].count("rpc/ideen_zurueck"), 1)

    def test_ohne_projekt_in_der_aenderung_kein_zweiter_versuch(self) -> None:
        server = load_server()
        db = _Datenbank(server, [_wizard_shuttle()], projekte=())
        with mock.patch.object(server, "_request", side_effect=db):
            with self.assertRaises(server.ToolError):
                server.call_tool("aenderung_zurueck", {})
        self.assertNotIn("DELETE", [m for m, _p, _k in db.aufrufe])

    def test_anderer_fehler_raeumt_nichts(self) -> None:
        server = load_server()
        aufrufe = []

        def request(method, path, **kwargs):
            aufrufe.append((method, path))
            raise server.ToolError("aenderung_unbekannt: plan-7")

        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaises(server.ToolError):
                server.call_tool("aenderung_zurueck", {"gruppe": "plan-7"})
        self.assertEqual(aufrufe, [("POST", "rpc/ideen_zurueck")])


if __name__ == "__main__":
    unittest.main()
