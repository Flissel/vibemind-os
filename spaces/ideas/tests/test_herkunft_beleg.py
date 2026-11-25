"""Herkunftsbeleg: bubble_bewertung und bewertung_todo (durch agent) schreiben nur mit Beleg (25.11.2026).

Nur erfundene Daten (Stadtfest). Den Weg vom Gehirn bis hierher prueft
brain/the_brain/tests/test_ideen_vertrag_bis_werkzeug.py (Abschnitt "Beleg").
"""
from __future__ import annotations

import importlib.util
import os
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCHLUESSEL = "testschluessel-stadtfest"
AENDERUNG = "ideen_1795000000019-r"


def load_server():
    spec = importlib.util.spec_from_file_location("ideas_mcp_server_beleg", ROOT / "mcp_server.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class Recorder:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        answer = self.answers.get((method, path), [])
        return answer(kwargs) if callable(answer) else answer

    def writes(self):
        return [c for c in self.calls if c[0] in ("PATCH", "POST", "DELETE")]


def _lauf():
    sicht = {"punkte": 20, "urteil": "Kurz begründet."}
    return {"sichten": {n: dict(sicht) for n in ("coding", "swe_design", "research", "rowboat", "ideas")},
            "todos": [], "gekuerzt": False}


class HerkunftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = load_server()
        self.regeln = self.server._kartenregeln()
        self.env = mock.patch.dict(os.environ, {self.regeln.BELEG_EINSTELLUNG: SCHLUESSEL + "\r\n"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _beleg(self, werkzeug, argumente, *, schluessel=SCHLUESSEL, aenderung=AENDERUNG, ablauf=None):
        ablauf = int(time.time()) + 120 if ablauf is None else ablauf
        return self.regeln.beleg_bauen(schluessel, werkzeug, argumente, aenderung, ablauf)

    def _grund(self, name, argumente, aenderung=AENDERUNG):
        request = Recorder({("GET", "ideas"): [{"id": "b0b1e001", "metadata": {
            "bewertung_lauf": {"todos": [{"nr": 1, "titel": "Bühne", "erledigt": False}]}}}]})
        with mock.patch.object(self.server, "_request", side_effect=request):
            try:
                self.server.call_tool(name, {**argumente, "aenderung_id": aenderung})
                grund = ""
            except self.server.ToolError as e:
                grund = str(e)
        return grund, request

    def test_bewertung_ohne_oder_mit_falschem_beleg_liest_und_schreibt_nicht(self) -> None:
        probe = {"bubble_id": "b0b1e001", "lauf": _lauf(), "kartenstand": "sha256:" + "1" * 64}
        gut = self._beleg("bubble_bewertung", probe)
        faelle = {
            "beleg_fehlt": probe,
            "beleg_form": {**probe, "beleg": "v1.kaputt"},
            "beleg_falsch": {**probe, "beleg": self._beleg("bubble_bewertung", probe, schluessel="anderer")},
            "beleg_abgelaufen": {**probe, "beleg": self._beleg("bubble_bewertung", probe, ablauf=1_000_000_000)},
        }
        for erwartet, argumente in faelle.items():
            grund, request = self._grund("bubble_bewertung", argumente)
            self.assertEqual(grund, f"herkunft_unbelegt: {erwartet}")
            self.assertEqual(request.calls, [])
        for anders in ({**probe, "bubble_id": "b0b1e002"}, {**probe, "lauf": {**_lauf(), "gekuerzt": True}},
                       {**probe, "kartenstand": "sha256:" + "2" * 64}):
            grund, request = self._grund("bubble_bewertung", {**anders, "beleg": gut})
            self.assertEqual(grund, "herkunft_unbelegt: beleg_falsch")
            self.assertEqual(request.calls, [])
        grund, _ = self._grund("bubble_bewertung", {**probe, "beleg": gut}, aenderung="ideen_1795000000020-r")
        self.assertEqual(grund, "herkunft_unbelegt: beleg_falsch")

    def test_gueltiger_beleg_kommt_bis_zum_lesen(self) -> None:
        probe = {"bubble_id": "b0b1e001", "lauf": _lauf(), "kartenstand": "sha256:" + "1" * 64}
        grund, request = self._grund("bubble_bewertung", {**probe, "beleg": self._beleg("bubble_bewertung", probe)})
        self.assertFalse(grund.startswith("herkunft_unbelegt"), grund)
        self.assertTrue(any(c[0] == "GET" for c in request.calls))

    def test_ohne_schluessel_im_server_wird_nicht_geschrieben(self) -> None:
        probe = {"bubble_id": "b0b1e001", "lauf": _lauf(), "kartenstand": "sha256:" + "1" * 64}
        beleg = self._beleg("bubble_bewertung", probe)
        for leer in ("", "  \r\n"):
            with mock.patch.dict(os.environ, {self.regeln.BELEG_EINSTELLUNG: leer}):
                grund, request = self._grund("bubble_bewertung", {**probe, "beleg": beleg})
            self.assertEqual(grund, "herkunft_unbelegt: schluessel_fehlt")
            self.assertEqual(request.calls, [])

    def test_abhaken_durch_agent_braucht_beleg_von_hand_nicht(self) -> None:
        agent = {"bubble_id": "b0b1e001", "nr": 1, "durch": "agent"}
        grund, request = self._grund("bewertung_todo", agent)
        self.assertEqual(grund, "herkunft_unbelegt: beleg_fehlt")
        self.assertEqual(request.writes(), [])
        # Ein Beleg fuer bubble_bewertung gilt nicht fuer bewertung_todo.
        fremd = self._beleg("bubble_bewertung", agent)
        self.assertEqual(self._grund("bewertung_todo", {**agent, "beleg": fremd})[0],
                         "herkunft_unbelegt: beleg_falsch")
        grund, request = self._grund("bewertung_todo", {**agent, "beleg": self._beleg("bewertung_todo", agent)})
        self.assertNotIn("herkunft_unbelegt", grund)
        self.assertEqual(len(request.writes()), 1)
        # "To-do N erledigt" von Hand: ohne Beleg, auch ganz ohne Schluessel.
        with mock.patch.dict(os.environ, {self.regeln.BELEG_EINSTELLUNG: ""}):
            grund, request = self._grund("bewertung_todo", {"bubble_id": "b0b1e001", "nr": 1, "durch": "hand"})
        self.assertNotIn("herkunft_unbelegt", grund)
        self.assertEqual(len(request.writes()), 1)


if __name__ == "__main__":
    unittest.main()
