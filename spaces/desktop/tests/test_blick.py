"""desktop_blick: schneller Blick ohne OCR, mit fester Sperrliste.

Kein Test liest den echten Bildschirm: Das aktive Fenster (blick.vordergrund) und der
Desktop-Dienst (_http) sind ersetzt.
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
    spec = importlib.util.spec_from_file_location("spaces_desktop_mcp_server_blick", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def element(name, typ="Button", **mehr):
    return {"name": name, "control_type": f"{typ}Control", "is_enabled": True,
            "bounds": {"left": 0, "top": 0, "width": 10, "height": 10}, **mehr}


class BlickTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = load_server()
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)
        env = mock.patch.dict(os.environ, {"VIBEMIND_DESKTOP_AUFGABEN_DIR": self.ordner.name}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("DESKTOP_AUTOMATION_URL", None)
        self.vorn = {"hwnd": 42, "titel": "Unbenannt - Editor", "pid": 7, "programm": "notepad", "monitor": 2}
        self.fenster = {"hwnd": 42, "title": "Unbenannt - Editor", "pid": 7}
        self.elemente = [element("Datei", "MenuItem"), element("Bearbeiten", "MenuItem"), element("", "Button"),
                         element("Datei", "MenuItem"), element("Text-Editor", "Edit")]
        self.aufrufe: list[str] = []

        def dienst(method, pfad, body=None, *, timeout):
            self.aufrufe.append(pfad)
            return {"success": True, "window": self.fenster, "elements": self.elemente}

        p = mock.patch.object(self.server, "_http", side_effect=dienst)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(self.server.blick, "vordergrund", side_effect=lambda: self.vorn)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(self.server.blick, "programm_aus_pid", side_effect=lambda pid: f"prog{pid}")
        p.start()
        self.addCleanup(p.stop)

    def blick(self):
        return self.server.call_tool("desktop_blick", {})

    def test_liefert_programm_titel_monitor_und_kurze_elementliste_ohne_ocr(self) -> None:
        stand = self.blick()
        self.assertEqual(
            {k: stand[k] for k in ("gesperrt", "programm", "fenstertitel", "monitor", "elemente_gekuerzt")},
            {"gesperrt": False, "programm": "notepad", "fenstertitel": "Unbenannt - Editor", "monitor": 2,
             "elemente_gekuerzt": False},
        )
        self.assertEqual(stand["elemente"], [{"name": "Datei", "art": "MenuItem"},
                                             {"name": "Bearbeiten", "art": "MenuItem"},
                                             {"name": "Text-Editor", "art": "Edit"}])
        self.assertRegex(stand["zeitpunkt"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertIsInstance(stand["dauer_ms"], int)
        self.assertEqual(self.aufrufe, ["/api/mcp/elements?max_elements=60"])  # kein read-screen

    def test_ist_gedeckelt_und_zaehlt_nicht_als_blick_fuer_einen_schritt(self) -> None:
        self.elemente = [element(f"Eintrag {i}", "ListItem") for i in range(50)]
        stand = self.blick()
        self.assertEqual((len(stand["elemente"]), stand["elemente_gekuerzt"]), (20, True))
        os.environ["VIBEMIND_DESKTOP_AUSFUEHREN"] = "1"
        self.addCleanup(os.environ.pop, "VIBEMIND_DESKTOP_AUSFUEHREN", None)
        kennung = self.server.call_tool("desktop_task_start", {"task": "x"})["id"]
        self.blick()
        with self.assertRaisesRegex(self.server.ToolError, "^nicht_hingesehen"):
            self.server.call_tool("desktop_task_step", {"task_id": kennung, "action": "click", "x": 1, "y": 1})

    def test_gesperrte_fenster_zeigen_nichts_und_der_dienst_wird_nicht_gefragt(self) -> None:
        faelle = [
            ("keepassxc", "Datenbank.kdbx - KeePassXC"),
            ("1password", "1Password"),
            ("chrome", "Bitwarden Web Vault - Google Chrome"),
            ("msedge", "Neuer Tab - [InPrivate] - Microsoft Edge"),
            ("firefox", "Mozilla Firefox — Privates Surfen"),
            ("chrome", "Sparkasse KölnBonn - Online-Banking - Google Chrome"),
            ("chrome", "Überweisung - comdirect - Google Chrome"),
            ("starmoney", "StarMoney 14"),
            ("chrome", "Anmelden – Google-Konten - Google Chrome"),
            ("credentialuibroker", "Windows-Sicherheit"),
            ("consent", "Benutzerkontensteuerung"),
        ]
        for programm, titel in faelle:
            with self.subTest(titel=titel):
                self.aufrufe.clear()
                self.vorn = {**self.vorn, "programm": programm, "titel": titel}
                stand = self.blick()
                self.assertEqual(set(stand), {"gesperrt", "zeitpunkt", "dauer_ms"})
                self.assertIs(stand["gesperrt"], True)
                self.assertEqual(self.aufrufe, [])
                self.assertNotIn(titel, repr(stand))

    def test_ein_anmeldefeld_unter_den_elementen_sperrt_das_ganze_fenster(self) -> None:
        for feld in (element("Passwort", "Edit"), element("", "Edit", automation_id="txtPassword"),
                     element("PIN", "Edit"), element("TAN eingeben", "Edit"), element("Kennwort vergessen?", "Hyperlink"),
                     element("IBAN", "Edit")):
            with self.subTest(feld=feld):
                self.elemente = [element("Weiter"), feld]
                stand = self.blick()
                self.assertEqual(set(stand), {"gesperrt", "zeitpunkt", "dauer_ms"})
        # Kurze Wörter zählen nur an Eingabefeldern.
        self.elemente = [element("Pin to Quick access"), element("Anheften")]
        self.assertIs(self.blick()["gesperrt"], False)

    def test_wechselt_das_fenster_zwischendurch_gilt_das_gelesene_und_wird_neu_geprueft(self) -> None:
        self.fenster = {"hwnd": 99, "title": "Online-Banking", "pid": 5}
        self.assertEqual(set(self.blick()), {"gesperrt", "zeitpunkt", "dauer_ms"})
        self.fenster = {"hwnd": 99, "title": "Rechner", "pid": 5}
        stand = self.blick()
        self.assertEqual((stand["programm"], stand["fenstertitel"], stand["monitor"]), ("prog5", "Rechner", None))

    def test_ohne_ui_automation_im_dienst_sind_die_elemente_als_unlesbar_markiert(self) -> None:
        self.server._http.side_effect = lambda *a, **k: {"success": False, "error": "uiautomation unavailable",
                                                         "window": self.fenster}
        stand = self.blick()
        self.assertEqual((stand["gesperrt"], stand["elemente"], stand["elemente_lesbar"]), (False, [], False))
        self.assertIs(self.blick()["elemente_lesbar"], False)
        self.vorn = {**self.vorn, "titel": "Online-Banking"}
        self.assertIs(self.blick()["gesperrt"], True)  # Titel und Programm werden trotzdem geprüft

    def test_ohne_eigenen_fensterzugriff_kommt_alles_vom_dienst(self) -> None:
        self.vorn = None
        stand = self.blick()
        self.assertEqual((stand["programm"], stand["fenstertitel"]), ("prog7", "Unbenannt - Editor"))

    def test_im_zweifel_gesperrt(self) -> None:
        with mock.patch.object(self.server.blick, "programm_gesperrt", side_effect=RuntimeError("x")):
            self.assertTrue(self.server.blick.gesperrt("notepad", "Editor"))

    def test_nimmt_keine_argumente(self) -> None:
        with self.assertRaisesRegex(self.server.ToolError, "^eingabe_ungueltig"):
            self.server.call_tool("desktop_blick", {"monitor": 1})


if __name__ == "__main__":
    unittest.main()
