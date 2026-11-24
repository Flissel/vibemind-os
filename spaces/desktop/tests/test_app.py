"""desktop_app: Programm starten oder nach vorn holen, nur aus der festen Liste.

Kein Test beruehrt den echten Bildschirm oder startet ein Programm: Fensterliste, Vorholen und
Starten sind ersetzt. Alle Titel sind erfunden.
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
    spec = importlib.util.spec_from_file_location("spaces_desktop_mcp_server_app", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = load_server()
        self.app = self.server.app_start
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)
        self.env = {"VIBEMIND_DESKTOP_AUFGABEN_DIR": self.ordner.name, "VIBEMIND_DESKTOP_AUSFUEHREN": "1"}
        env = mock.patch.dict(os.environ, self.env, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.liste = [
            {"hwnd": 11, "titel": "Bei Beispielbank anmelden", "programm": "chrome"},
            {"hwnd": 12, "titel": "Neuer Tab - Google Chrome", "programm": "chrome"},
            {"hwnd": 13, "titel": "Taskleiste", "programm": "explorer"},
        ]
        self.vorgeholt: list[int] = []
        self.gestartet: list[str] = []
        self.vorn_klappt = True
        self.klassen = {13: "Shell_TrayWnd"}
        ersatz = {
            (self.server.blick, "sichtbare_fenster"): lambda: list(self.liste),
            (self.app, "nach_vorn"): self._nach_vorn,
            (self.app, "starten"): self._starten,
            (self.app, "fensterklasse"): lambda hwnd: self.klassen.get(hwnd, ""),
            (self.app, "vordergrund_hwnd"): lambda: self.vorgeholt[-1] if self.vorgeholt else 0,
            (self.app, "programmdatei"): lambda s: Path(f"C:/fest/{self.app.PROGRAMME[s]['programm']}.exe"),
            (self.server, "_http"): mock.Mock(side_effect=AssertionError("kein Dienst")),
        }
        for (ziel, name), wert in ersatz.items():
            p = mock.patch.object(ziel, name, wert)
            p.start()
            self.addCleanup(p.stop)
        self.schlafen = mock.patch.object(self.app.time, "sleep", lambda s: None)
        self.schlafen.start()
        self.addCleanup(self.schlafen.stop)

    def _nach_vorn(self, hwnd: int) -> bool:
        self.vorgeholt.append(hwnd)
        return self.vorn_klappt

    def _starten(self, pfad: Path) -> int:
        self.gestartet.append(str(pfad))
        # Nach dem Start erscheint ein Fenster des Programms.
        programm = Path(pfad).stem
        self.liste.insert(0, {"hwnd": 99, "titel": "Neu", "programm": programm})
        if programm == "explorer":
            self.klassen[99] = "CabinetWClass"
        return 4242

    def app_aufruf(self, **argumente):
        return self.server.call_tool("desktop_app", argumente)

    def buch(self, kennung: str) -> dict:
        return json.loads((Path(self.ordner.name) / f"{kennung}.json").read_text(encoding="utf-8"))

    # ─── Vorholen ────────────────────────────────────────────────────────────

    def test_offenes_fenster_wird_vorgeholt_gesperrtes_uebersprungen(self) -> None:
        stand = self.app_aufruf(programm="chrome", modus="oeffnen")
        self.assertEqual((stand["ok"], stand["echt"], stand["getan"]), (True, True, "vorgeholt"))
        self.assertEqual(self.vorgeholt, [12])  # nie das Anmeldefenster
        self.assertEqual(stand["gesperrt_uebersprungen"], 1)
        self.assertEqual(self.gestartet, [])
        self.assertNotIn("Google Chrome", json.dumps(stand))  # kein Titel im Ergebnis

    def test_vorholen_ohne_offenes_fenster_startet_nichts(self) -> None:
        stand = self.app_aufruf(programm="editor", modus="vorholen")
        self.assertEqual((stand["ok"], stand["getan"]), (False, "nicht_offen"))
        self.assertEqual((self.vorgeholt, self.gestartet), ([], []))
        self.assertEqual(self.buch(stand["task_id"])["zustand"], "fehlgeschlagen")

    def test_nur_gesperrte_fenster_werden_nie_vorgeholt(self) -> None:
        self.liste = [{"hwnd": 11, "titel": "Bei Beispielbank anmelden", "programm": "chrome"}]
        stand = self.app_aufruf(programm="chrome", modus="vorholen")
        self.assertEqual((stand["getan"], stand["gesperrt_uebersprungen"]), ("nicht_offen", 1))
        self.assertEqual(self.vorgeholt, [])

    def test_windows_laesst_nicht_nach_vorn(self) -> None:
        self.vorn_klappt = False
        stand = self.app_aufruf(programm="chrome", modus="vorholen")
        self.assertEqual((stand["ok"], stand["getan"]), (False, "nicht_vorn"))

    # ─── Starten ─────────────────────────────────────────────────────────────

    def test_editor_wird_ohne_argumente_gestartet_und_sein_fenster_gesehen(self) -> None:
        stand = self.app_aufruf(programm="editor", modus="oeffnen")
        self.assertEqual((stand["ok"], stand["getan"], stand["fenster_da"]), (True, "gestartet", True))
        self.assertEqual(self.gestartet, ["C:\\fest\\notepad.exe" if os.sep == "\\" else "C:/fest/notepad.exe"])

    def test_taskleiste_zaehlt_nicht_als_explorer_fenster(self) -> None:
        stand = self.app_aufruf(programm="explorer", modus="oeffnen")
        self.assertEqual(stand["getan"], "gestartet")
        self.assertNotIn(13, self.vorgeholt)

    def test_kein_fenster_nach_dem_start_wird_ehrlich_gemeldet(self) -> None:
        with mock.patch.object(self.app, "starten", lambda pfad: 1), \
                mock.patch.object(self.app.time, "monotonic", side_effect=[0.0, 100.0, 100.0, 100.0]):
            ergebnis = self.app.ausfuehren("editor", "oeffnen", schlafen=lambda s: None)
        self.assertEqual((ergebnis["getan"], ergebnis["fenster_da"]), ("gestartet", False))

    # ─── Liste, Buch, Not-Aus, Trockenlauf ───────────────────────────────────

    def test_nur_die_feste_liste_und_keine_weiteren_angaben(self) -> None:
        for argumente in ({"programm": "cmd", "modus": "oeffnen"}, {"programm": "C:/x/notepad.exe", "modus": "oeffnen"},
                          {"programm": "editor", "modus": "schliessen"},
                          {"programm": "editor", "modus": "oeffnen", "argumente": "datei.txt"}):
            with self.assertRaises(self.server.ToolError) as fehler:
                self.server.call_tool("desktop_app", argumente)
            self.assertTrue(str(fehler.exception).startswith("eingabe_ungueltig"))
        self.assertEqual((self.vorgeholt, self.gestartet), ([], []))
        self.assertEqual(list(Path(self.ordner.name).glob("*.json")), [])  # nichts gebucht

    def test_jede_aktion_steht_im_aufgabenbuch(self) -> None:
        stand = self.app_aufruf(programm="chrome", modus="vorholen")
        buch = self.buch(stand["task_id"])
        self.assertEqual((buch["aufgabe"], buch["zustand"]), ("App: Chrome nach vorn holen", "fertig"))
        self.assertEqual([(s["art"], s["angaben"], s["ok"], s["echt"]) for s in buch["schritte"]],
                         [("app", {"programm": "chrome", "modus": "vorholen"}, True, True)])
        self.assertIn("vorgeholt", buch["ergebnis"])
        self.assertNotIn("Google Chrome", json.dumps(buch))

    def test_not_aus_lehnt_ab_bevor_etwas_geschieht(self) -> None:
        (Path(self.ordner.name) / "STOP").write_text("", encoding="utf-8")
        with self.assertRaises(self.server.ToolError) as fehler:
            self.app_aufruf(programm="editor", modus="oeffnen")
        self.assertTrue(str(fehler.exception).startswith("not_aus"))
        self.assertEqual((self.vorgeholt, self.gestartet), ([], []))

    def test_laufende_aufgabe_lehnt_ab(self) -> None:
        self.server.call_tool("desktop_task_start", {"task": "Etwas anderes"})
        with self.assertRaises(self.server.ToolError) as fehler:
            self.app_aufruf(programm="editor", modus="oeffnen")
        self.assertTrue(str(fehler.exception).startswith("aufgabe_laeuft"))
        self.assertEqual(self.gestartet, [])

    def test_trockenlauf_bucht_aber_tut_nichts(self) -> None:
        with mock.patch.dict(os.environ, {"VIBEMIND_DESKTOP_AUSFUEHREN": ""}):
            stand = self.app_aufruf(programm="editor", modus="oeffnen")
        self.assertEqual((stand["ok"], stand["echt"], stand["trocken"], stand["getan"]), (True, False, True, "trocken"))
        self.assertEqual((self.vorgeholt, self.gestartet), ([], []))
        buch = self.buch(stand["task_id"])
        self.assertTrue(buch["trocken"])
        self.assertEqual(buch["schritte"][0]["meldung"], "trocken: gebucht, nicht ausgeführt")

    def test_werkzeug_steht_in_der_liste_und_zaehlt_nicht_als_blick(self) -> None:
        namen = [t["name"] for t in self.server.TOOLS]
        self.assertIn("desktop_app", namen)
        schema = next(t for t in self.server.TOOLS if t["name"] == "desktop_app")["inputSchema"]
        self.assertEqual(schema["properties"]["programm"]["enum"], ["editor", "chrome", "explorer"])
        self.assertNotIn("desktop_app", self.server.BLICK_WERKZEUGE)


class EchteHelferTests(unittest.TestCase):
    """Die echten Funktionen starten() und programmdatei(); nur Popen und Orte sind ersetzt."""

    def setUp(self) -> None:
        self.app = load_server().app_start

    def test_starten_ohne_shell_und_ohne_argumente(self) -> None:
        with mock.patch.object(self.app.subprocess, "Popen") as popen:
            popen.return_value.pid = 7
            self.assertEqual(self.app.starten(Path("C:/fest/notepad.exe")), 7)
        args, kwargs = popen.call_args
        self.assertEqual(len(args[0]), 1)
        self.assertNotIn("shell", kwargs)

    def test_programmdatei_nur_mit_erwartetem_namen(self) -> None:
        with tempfile.TemporaryDirectory() as wurzel:
            falsch = Path(wurzel) / "boese.exe"
            falsch.write_bytes(b"")
            with mock.patch.object(self.app, "_app_pfad_aus_registry", lambda d: falsch), \
                    mock.patch.dict(os.environ, {"ProgramFiles": wurzel, "ProgramFiles(x86)": wurzel,
                                                 "LocalAppData": wurzel}):
                self.assertIsNone(self.app.programmdatei("chrome"))
                richtig = Path(wurzel) / "Google" / "Chrome" / "Application" / "chrome.exe"
                richtig.parent.mkdir(parents=True)
                richtig.write_bytes(b"")
                self.assertEqual(self.app.programmdatei("chrome"), richtig)
        self.assertIsNone(self.app.programmdatei("cmd"))


if __name__ == "__main__":
    unittest.main()
