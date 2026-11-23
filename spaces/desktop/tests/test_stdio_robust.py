"""Der Desktop-Werkzeug-Dienst als echter Prozess über stdio, so nackt wie OpenFang ihn startet.

OpenFang startet Werkzeug-Dienste mit leerer Umgebung. Unter Windows ist eine umgeleitete
Ausgabe dann nicht UTF-8; am 23.11.2026 starb daran der Ideen-Dienst ("Transport closed").
Diese Tests starten den Dienst ohne jede Kodierungs-Vorgabe. Es wird nichts geklickt: Die
Aufrufe berühren nur das Aufgabenbuch in einem Wegwerf-Ordner.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"
SCHWIERIG = "Preise prüfen → ✅ 🚀 „Zitat“   Zeile"
LANG = "Bericht " + ("äöü → 🚀 " * 9000)  # deutlich über 64 KB in einer Antwortzeile
ALTE_AUFGABE = "dt-20260101-000000-abcdef"


class StdioRobustTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)
        # Nur was Windows braucht: KEIN PYTHONIOENCODING, KEIN PYTHONUTF8, kein LOCALAPPDATA.
        self.env = {name: os.environ[name] for name in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR") if name in os.environ}

    def _lauf(self, *zeilen: bytes):
        completed = subprocess.run(
            [sys.executable, str(SERVER_PATH)],
            input=b"\n".join(zeilen) + b"\n",
            capture_output=True, env=self.env, check=False, timeout=60,
        )
        antworten = [json.loads(z) for z in completed.stdout.decode("utf-8").splitlines() if z.strip()]
        return completed, antworten

    @staticmethod
    def _aufruf(nummer: int, name: str, argumente: dict) -> bytes:
        nachricht = {"jsonrpc": "2.0", "id": nummer, "method": "tools/call", "params": {"name": name, "arguments": argumente}}
        return json.dumps(nachricht, ensure_ascii=False).encode("utf-8")  # OpenFang schickt UTF-8, keine \u-Folgen

    @staticmethod
    def _inhalt(antwort):
        return json.loads(antwort["result"]["content"][0]["text"])

    def test_sonderzeichen_und_lange_antwort_beenden_den_prozess_nicht(self) -> None:
        self.env["VIBEMIND_DESKTOP_AUFGABEN_DIR"] = self.ordner.name
        alt = {"id": ALTE_AUFGABE, "space": "desktop", "aufgabe": SCHWIERIG, "zustand": "fertig", "trocken": True,
               "begonnen": 1.0, "beendet": 2.0, "schritte": [], "ergebnis": LANG, "blick": False}
        Path(self.ordner.name, ALTE_AUFGABE + ".json").write_text(json.dumps(alt, ensure_ascii=False), encoding="utf-8")

        completed, antworten = self._lauf(
            self._aufruf(1, "desktop_task_status", {"task_id": ALTE_AUFGABE}),
            self._aufruf(2, "desktop_task_start", {"task": SCHWIERIG}),
            b'{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": \xff\xfe kaputt',
            self._aufruf(4, "desktop_task_status", {}),
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace")[-600:])
        self.assertEqual([a.get("id") for a in antworten], [1, 2, None, 4])
        self.assertEqual(self._inhalt(antworten[0])["ergebnis"], LANG)
        self.assertGreater(len(completed.stdout.splitlines()[0]), 64 * 1024)
        self.assertEqual(self._inhalt(antworten[1])["aufgabe"], SCHWIERIG)
        self.assertEqual(self._inhalt(antworten[3])["aufgabe"], SCHWIERIG)
        # Jede Antwort ist genau eine Zeile (auch U+2028 trennt sie nicht) und reines ASCII.
        self.assertEqual(len(completed.stdout.decode("utf-8").splitlines()), 4)
        self.assertTrue(completed.stdout.isascii())

    def test_ohne_ordner_in_der_umgebung_gibt_es_einen_klaren_fehler_und_der_dienst_lebt_weiter(self) -> None:
        completed, antworten = self._lauf(
            self._aufruf(1, "desktop_task_start", {"task": "ohne Ordner"}),
            self._aufruf(2, "desktop_task_status", {}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}).encode("utf-8"),
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace")[-600:])
        self.assertEqual([a.get("id") for a in antworten], [1, 2, 3])
        for antwort in antworten[:2]:
            self.assertTrue(antwort["result"]["isError"])
            self.assertTrue(self._inhalt(antwort)["error"].startswith("konfiguration:"))
        self.assertEqual(len(antworten[2]["result"]["tools"]), 9)
        # Nichts wurde irgendwo unter dem Benutzerordner angelegt, weil kein Ort bekannt war.
        self.assertEqual(list(Path(self.ordner.name).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
