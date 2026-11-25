"""desktop_hand_klick: Klick aus der Ansicht durchreichen, mit Not-Aus, Sperrliste und Buchung.

Kein Test klickt wirklich: der Desktop-Dienst (_http) und das Fenster unter dem Punkt sind
ersetzt. Alle Titel sind erfunden.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("spaces_desktop_mcp_server_hand", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HandKlickTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = load_server()
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)
        env = mock.patch.dict(os.environ, {"VIBEMIND_DESKTOP_AUFGABEN_DIR": self.ordner.name,
                                           "VIBEMIND_DESKTOP_AUSFUEHREN": "1"}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.unter = {"hwnd": 7, "titel": "Unbenannt - Editor", "programm": "notepad"}
        self.posts: list = []
        self.antwort = {"success": True}

        def dienst(method, pfad, body=None, *, timeout):
            self.posts.append((method, pfad, body))
            return self.antwort

        for ziel, name, wert in ((self.server, "_http", dienst),
                                 (self.server.blick, "fenster_unter_punkt", lambda x, y: self.unter)):
            p = mock.patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)

    def klick(self, **argumente):
        return self.server.call_tool("desktop_hand_klick", argumente or {"x": 100, "y": 200})

    def buch(self, kennung: str) -> dict:
        return json.loads((Path(self.ordner.name) / f"{kennung}.json").read_text(encoding="utf-8"))

    def test_klick_wird_durchgereicht_und_gebucht(self) -> None:
        stand = self.klick()
        self.assertEqual((stand["ok"], stand["echt"], stand["grund"], stand["trocken"]), (True, True, None, False))
        self.assertEqual(self.posts, [("POST", "/api/mcp/click", {"x": 100, "y": 200})])
        buch = self.buch(stand["task_id"])
        self.assertEqual((buch["aufgabe"], buch["zustand"]), ("Hand: Klick", "fertig"))
        self.assertEqual([(s["art"], s["angaben"], s["ok"], s["echt"]) for s in buch["schritte"]],
                         [("click", {"x": 100, "y": 200, "quelle": "hand"}, True, True)])
        self.assertNotIn("Unbenannt", json.dumps(buch) + json.dumps(stand))  # kein Titel

    def test_gesperrtes_fenster_unter_dem_punkt_wird_nicht_geklickt(self) -> None:
        for unter in ({"hwnd": 2, "titel": "Tresor - KeePass", "programm": "keepass"},
                      {"hwnd": 3, "titel": "Bei Beispielbank anmelden", "programm": "chrome"},
                      {"hwnd": 4, "titel": "Neuer Tab - InPrivate", "programm": "msedge"}):
            self.unter = unter
            stand = self.klick()
            self.assertEqual((stand["ok"], stand["echt"], stand["grund"]), (False, False, "gesperrt"))
            buch = self.buch(stand["task_id"])
            self.assertEqual(buch["zustand"], "fehlgeschlagen")
            self.assertTrue(buch["schritte"][0]["meldung"].startswith("gesperrt:"))
            for teil in ("KeePass", "Beispielbank", "InPrivate"):
                self.assertNotIn(teil, json.dumps(buch) + json.dumps(stand))
        self.assertEqual(self.posts, [])

    def test_unlesbares_fenster_im_zweifel_nicht_klicken(self) -> None:
        self.unter = None
        stand = self.klick()
        self.assertEqual((stand["ok"], stand["grund"]), (False, "fenster_unlesbar"))
        self.assertEqual(self.posts, [])

    def test_not_aus_lehnt_ab_ohne_buchung(self) -> None:
        (Path(self.ordner.name) / "STOP").write_text("", encoding="utf-8")
        stand = self.klick()
        self.assertEqual((stand["ok"], stand["grund"], stand["task_id"]), (False, "not_aus", None))
        self.assertEqual((self.posts, list(Path(self.ordner.name).glob("dt-*.json"))), ([], []))

    def test_laufende_aufgabe_lehnt_ab(self) -> None:
        self.server.call_tool("desktop_task_start", {"task": "Etwas anderes"})
        stand = self.klick()
        self.assertEqual((stand["ok"], stand["grund"]), (False, "aufgabe_laeuft"))
        self.assertEqual(self.posts, [])

    def test_trockenlauf_bucht_aber_klickt_nicht(self) -> None:
        with mock.patch.dict(os.environ, {"VIBEMIND_DESKTOP_AUSFUEHREN": ""}):
            stand = self.klick()
        self.assertEqual((stand["ok"], stand["echt"], stand["trocken"]), (True, False, True))
        self.assertEqual(self.posts, [])
        self.assertTrue(self.buch(stand["task_id"])["trocken"])

    def test_dienst_ohne_erfolg_ist_ein_fehlschlag_mit_grund(self) -> None:
        self.antwort = {"success": False}
        stand = self.klick()
        self.assertEqual((stand["ok"], stand["echt"], stand["grund"]), (False, True, "dienst_fehler"))
        self.assertEqual(self.buch(stand["task_id"])["zustand"], "fehlgeschlagen")

    def test_nur_ganze_zahlen_und_keine_weiteren_angaben(self) -> None:
        for argumente in ({"x": "1", "y": 2}, {"x": 1.5, "y": 2}, {"x": True, "y": 2}, {"x": 1},
                          {"x": 99999, "y": 1}, {"x": 1, "y": 2, "button": "right"}):
            with self.assertRaises(self.server.ToolError):
                self.server.call_tool("desktop_hand_klick", argumente)
        self.assertEqual(self.posts, [])

    def test_hand_klick_zaehlt_nicht_als_blick_fuer_den_agenten(self) -> None:
        self.assertNotIn("desktop_hand_klick", self.server.BLICK_WERKZEUGE)


if __name__ == "__main__":
    unittest.main()
