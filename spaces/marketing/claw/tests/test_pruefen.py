"""kampagne_pruefen: Entwurf gegen simuliertes Publikum (Mirofish).

Ohne Netz: der bestehende Mirofish-Client wird ersetzt. Die Tests halten
fest, dass (1) ein Report als Schaufenster-Datei entsteht, (2) eine
laufende Simulation nach der Frist ehrlich als unfertig gemeldet wird
statt endlos zu warten, (3) eine ausgeschaltete Mirofish kein Absturz ist.
"""
import os
import tempfile
import unittest
from unittest import mock

from spaces.marketing.claw import werkzeuge


class TestPruefen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict("os.environ", {"SCHAUFENSTER_DIR": self.tmp})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _client(self, phasen, report=None, kick=None):
        """Ein Mirofish-Ersatz: poll_status laeuft die Phasenliste ab."""
        zustaende = iter(phasen)

        class Klient:
            @staticmethod
            def kick_off(bubble_id, content, channel, bubble_title=None,
                         agent_count=100, rounds=10):
                if kick:
                    raise kick
                return {"phase": phasen[0], "project_id": "pr1"}

            @staticmethod
            def poll_status(state):
                try:
                    return {**state, "phase": next(zustaende)}
                except StopIteration:
                    return state

            @staticmethod
            def read_report(report_id):
                return report or {"report_id": report_id, "score": 72,
                                  "persona_summary": [{"name": "Skeptiker",
                                                       "stance": "kritisch"}],
                                  "full_report": {"summary": "Zurueckhaltend positiv."}}

        return Klient

    def test_report_landet_im_schaufenster(self):
        klient = self._client(["graph_ready", "sim_done", "done"])
        with mock.patch.object(werkzeuge, "_mirofish", lambda: klient), \
             mock.patch.object(werkzeuge, "_SCHLAF", lambda s: None):
            r = werkzeuge.kampagne_pruefen(
                "Fruehbucher-Rabatt bis Freitag, danach reguläre Preise.",
                kanal="telegram", titel="Herbst")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["score"], 72)
        self.assertTrue(os.path.exists(r["dateien"][0]))
        with open(r["dateien"][0], encoding="utf-8") as f:
            inhalt = f.read()
        self.assertIn("72", inhalt)
        self.assertIn("Skeptiker", inhalt)

    def test_zu_langsame_simulation_wird_ehrlich_gemeldet(self):
        klient = self._client(["sim_running"] * 3)
        with mock.patch.object(werkzeuge, "_mirofish", lambda: klient), \
             mock.patch.object(werkzeuge, "_SCHLAF", lambda s: None):
            r = werkzeuge.kampagne_pruefen("Ein hinreichend langer Entwurfstext.",
                                           kanal="telegram", frist_s=0)
        self.assertFalse(r["ok"])
        self.assertIn("sim_running", r["fehler"])

    def test_mirofish_aus_ist_kein_absturz(self):
        klient = self._client(["done"], kick=OSError("connection refused"))
        with mock.patch.object(werkzeuge, "_mirofish", lambda: klient):
            r = werkzeuge.kampagne_pruefen("Ein hinreichend langer Entwurfstext.",
                                           kanal="telegram")
        self.assertFalse(r["ok"])
        self.assertIn("Mirofish", r["fehler"])

    def test_zu_kurzer_text_wird_abgelehnt(self):
        with mock.patch.object(werkzeuge, "_mirofish", lambda: self._client(["done"])):
            r = werkzeuge.kampagne_pruefen("zu kurz", kanal="telegram")
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
