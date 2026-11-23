"""desktop_fenster: Liste der sichtbaren Fenster, durch dieselbe Sperrliste wie der Blick.

Kein Test liest den echten Bildschirm: blick.sichtbare_fenster ist ersetzt. Alle Titel sind erfunden.
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("spaces_desktop_mcp_server_fenster", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FensterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = load_server()
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)
        env = mock.patch.dict(os.environ, {"VIBEMIND_DESKTOP_AUFGABEN_DIR": self.ordner.name}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.liste = [
            {"hwnd": 1, "titel": "Unbenannt - Editor", "programm": "notepad"},
            {"hwnd": 2, "titel": "Tresor - KeePass", "programm": "keepass"},
            {"hwnd": 3, "titel": "Neuer Tab - InPrivate", "programm": "msedge"},
            {"hwnd": 4, "titel": "Bei Beispielbank anmelden", "programm": "chrome"},
            {"hwnd": 5, "titel": "Dokumente   -  Explorer", "programm": "explorer"},
        ]
        self.http = mock.patch.object(self.server, "_http", side_effect=AssertionError("kein Dienst"))
        self.http.start()
        self.addCleanup(self.http.stop)
        p = mock.patch.object(self.server.blick, "sichtbare_fenster", side_effect=lambda: self.liste)
        p.start()
        self.addCleanup(p.stop)

    def fenster(self):
        return self.server.call_tool("desktop_fenster", {})

    def test_liste_mit_programm_und_titel_gesperrte_nur_als_gesperrt(self) -> None:
        stand = self.fenster()
        self.assertEqual(stand["fenster"], [
            {"gesperrt": False, "programm": "notepad", "titel": "Unbenannt - Editor"},
            {"gesperrt": True},
            {"gesperrt": True},
            {"gesperrt": True},
            {"gesperrt": False, "programm": "explorer", "titel": "Dokumente - Explorer"},
        ])
        self.assertEqual((stand["anzahl"], stand["gesperrt"], stand["gekuerzt"]), (5, 3, False))
        self.assertRegex(stand["zeitpunkt"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_gesperrte_fenster_verraten_weder_titel_noch_programm(self) -> None:
        text = repr(self.fenster())
        for geheim in ("KeePass", "keepass", "InPrivate", "Beispielbank", "Tresor"):
            self.assertNotIn(geheim, text)

    def test_dieselbe_sperrliste_wie_der_blick(self) -> None:
        with mock.patch.object(self.server.blick, "gesperrt", return_value=True) as sperre:
            stand = self.fenster()
        self.assertEqual(stand["gesperrt"], 5)
        self.assertEqual(sperre.call_args_list[0].args, ("notepad", "Unbenannt - Editor"))

    def test_liste_wird_gekuerzt_anzahl_nennt_alle(self) -> None:
        self.liste = [{"hwnd": i, "titel": f"Fenster {i}", "programm": "notepad"} for i in range(55)]
        stand = self.fenster()
        self.assertEqual((len(stand["fenster"]), stand["anzahl"], stand["gekuerzt"]),
                         (self.server.FENSTER_HOECHSTENS, 55, True))

    def test_langer_titel_wird_gekuerzt(self) -> None:
        self.liste = [{"hwnd": 1, "titel": "x" * 300, "programm": "notepad"}]
        self.assertEqual(len(self.fenster()["fenster"][0]["titel"]), 120)

    def test_unlesbare_liste_ist_ein_fehler(self) -> None:
        self.liste = None
        with self.assertRaisesRegex(self.server.ToolError, "^fenster_unlesbar"):
            self.fenster()

    def test_nimmt_keine_argumente(self) -> None:
        with self.assertRaises(self.server.ToolError):
            self.server.call_tool("desktop_fenster", {"monitor": 1})

    def test_zaehlt_nicht_als_blick_fuer_einen_schritt(self) -> None:
        self.assertNotIn("desktop_fenster", self.server.BLICK_WERKZEUGE)


if __name__ == "__main__":
    unittest.main()
