"""Belegpflicht im Briefing: kampagne_entwerfen nimmt `belege` und `zu_klaeren`
entgegen und schreibt sie als eigene Abschnitte — der Agent (der die
Wissensbasis gelesen hat) liefert sie, nicht das innere LLM.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from spaces.marketing.claw import llm, werkzeuge
from spaces.marketing.claw.tests.test_werkzeuge import Rekorder


class TestBelege(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict("os.environ", {
            "SCHAUFENSTER_DIR": self.tmp,
            "MARKETING_API_URL": "http://x:5510",
            "MARKETING_PROPOSAL_API_KEY": "pk1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def _lauf(self, **extra):
        antwort = json.dumps({"betreff": "B", "text": "T", "begruendung": "G"})
        rekorder = Rekorder([(200, json.dumps(
            {"success": True, "data": {"id": "bp-9", "status": "draft"}}))])
        with mock.patch.object(llm, "frage", return_value={"ok": True, "text": antwort}), \
             mock.patch.object(werkzeuge, "_roh_anfrage", rekorder):
            r = werkzeuge.kampagne_entwerfen("Ziel", "Gruppe", "telegram", **extra)
        self.assertTrue(r["ok"], r)
        with open(r["dateien"][0], encoding="utf-8") as f:
            return f.read()

    def test_belege_und_zu_klaeren_werden_abschnitte(self):
        inhalt = self._lauf(
            belege=["VibeMind Knowledge / VIbeMind Core: Sprachsteuerung des PCs"],
            zu_klaeren=["Aussage zu 'kleinen Agenturen' unbelegt"])
        self.assertIn("## Belege", inhalt)
        self.assertIn("VIbeMind Core", inhalt)
        self.assertIn("## Zu klaeren", inhalt)
        self.assertIn("kleinen Agenturen", inhalt)

    def test_ohne_belege_steht_das_ehrlich_da(self):
        inhalt = self._lauf()
        self.assertIn("## Belege", inhalt)
        self.assertIn("keine Belege angegeben", inhalt)

    def test_belege_landen_auch_im_draft_kontext(self):
        """Der Draft traegt die Belege mit, damit der Betreiber sie in der UI
        sieht — die Anforderung bleibt, der ORT hat sich geaendert.

        Frueher standen sie als gerendertes HTML in `draft_body_html`, der
        Spalte fuer den Nachrichtenrumpf. Zwei Fehler: interne Notizen an
        einer Stelle, die spaeter versendet wird, und Daten in Darstellung
        gebacken — das Aussehen liess sich damit nicht mehr wechseln, ohne
        den Text neu erzeugen zu lassen. Seit 04.09.2026 stehen sie als
        Struktur in `draft_channel_params` (jsonb, war auf allen 17 Zeilen
        leer), und die Freigabe-Ansicht liest sie von dort.
        """
        antwort = json.dumps({"betreff": "B", "text": "T", "begruendung": "G"})
        rekorder = Rekorder([(200, json.dumps(
            {"success": True, "data": {"id": "bp-9", "status": "draft"}}))])
        with mock.patch.object(llm, "frage", return_value={"ok": True, "text": antwort}), \
             mock.patch.object(werkzeuge, "_roh_anfrage", rekorder):
            werkzeuge.kampagne_entwerfen("Z", "G", "telegram", belege=["Quelle A: Fakt"])
        gesendet = json.loads(rekorder.aufrufe[0]["daten"])
        self.assertIn("Quelle A: Fakt",
                      gesendet["draft_channel_params"]["belege"])
        self.assertNotIn("Quelle A", gesendet.get("draft_body_html") or "")


if __name__ == "__main__":
    unittest.main()
