"""Entwurfs-Werkzeuge: LLM gefaked, HTTP aufgezeichnet, Dateien in tmp.

Der wichtigste Test steht am Ende: die Liste der aufgerufenen URLs wird
gegen eine Allowlist geprueft — ein Aufruf mit 'send' oder 'approve' im
Pfad waere ein Testversagen, egal was er tut.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from spaces.marketing.claw import llm, werkzeuge
from spaces.marketing.claw.tests.test_werkzeuge import Rekorder


class TestEntwurf(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict("os.environ", {
            "SCHAUFENSTER_DIR": self.tmp,
            "MARKETING_API_URL": "http://x:5510",
            "MARKETING_PROPOSAL_API_KEY": "pk1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_kampagne_entwerfen_erzeugt_draft_und_dateien(self):
        antwort = json.dumps({"betreff": "B", "text": "T", "begruendung": "G"})
        rekorder = Rekorder([(200, json.dumps({"id": "bp-1", "status": "draft"}))])
        with mock.patch.object(llm, "frage",
                               return_value={"ok": True, "text": antwort}), \
             mock.patch.object(werkzeuge, "_roh_anfrage", rekorder):
            r = werkzeuge.kampagne_entwerfen("Herbst-Launch", "KMU-Chefs", "telegram")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["proposal_id"], "bp-1")
        self.assertEqual(rekorder.aufrufe[0]["url"],
                         "http://x:5510/api/curator/broadcast_proposals")
        gesendet = json.loads(rekorder.aufrufe[0]["daten"])
        self.assertEqual(gesendet["channel"], "telegram")
        self.assertEqual(gesendet["draft_body_text"], "T")
        self.assertEqual(gesendet["api_key"], "pk1")
        self.assertTrue(any(p.endswith("briefing.md") for p in r["dateien"]), r)
        for pfad in r["dateien"]:
            self.assertTrue(os.path.exists(pfad))

    def test_kampagne_failsoft_bei_totem_llm(self):
        with mock.patch.object(llm, "frage",
                               return_value={"ok": False, "fehler": "Shim tot"}):
            r = werkzeuge.kampagne_entwerfen("Z", "G", "email")
        self.assertFalse(r["ok"])
        self.assertIn("Shim", r["fehler"])

    def test_ad_texte_entwerfen_schreibt_n_dateien(self):
        with mock.patch.object(llm, "frage",
                               return_value={"ok": True, "text": "Variante"}):
            r = werkzeuge.ad_texte_entwerfen("Herbst-Launch", n=2)
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(r["dateien"]), 2)
        for pfad in r["dateien"]:
            self.assertTrue(os.path.exists(pfad))

    def test_layout_entwerfen_schreibt_html(self):
        with mock.patch.object(llm, "frage",
                               return_value={"ok": True, "text": "<html>x</html>"}):
            r = werkzeuge.layout_entwerfen("Herbst-Launch")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["dateien"][0].endswith(".html"))
        self.assertTrue(os.path.exists(r["dateien"][0]))

    def test_nie_ein_sendepfad(self):
        rekorder = Rekorder([(200, json.dumps({"id": "bp-1", "status": "draft"}))])
        with mock.patch.object(llm, "frage",
                               return_value={"ok": True, "text": json.dumps(
                                   {"betreff": "B", "text": "T", "begruendung": "G"})}), \
             mock.patch.object(werkzeuge, "_roh_anfrage", rekorder):
            werkzeuge.kampagne_entwerfen("Z", "G", "email")
        for aufruf in rekorder.aufrufe:
            self.assertNotIn("send", aufruf["url"].lower(), aufruf["url"])
            self.assertNotIn("approve", aufruf["url"].lower(), aufruf["url"])


if __name__ == "__main__":
    unittest.main()
