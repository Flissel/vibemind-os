"""entwuerfe_lesen: der Agent sieht, was er schon geschrieben hat.

WARUM DAS EIN WERKZEUG BRAUCHT. Am 04.09.2026 lagen SIEBEN Telegram-
Entwuerfe derselben Kampagne im Bestand — und alle sieben waren dieselbe
Aufzaehlung von vier Funktionen, nur mit anderen Emojis. Der Agent konnte
seine eigene Historie nicht lesen: das Schaufenster liegt auf dem Host,
der Gateway-Container kommt nicht heran, und `/api/proposals` liest eine
ANDERE Tabelle (audience_proposals — wen man anschreibt, nicht was).

Ohne Historie schreibt er beim achten Mal dieselbe Liste.
"""
import json
import unittest
from unittest import mock

from spaces.marketing.claw import werkzeuge
from spaces.marketing.claw.tests.test_werkzeuge import Rekorder


class TestEntwuerfeLesen(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict("os.environ", {
            "MARKETING_API_URL": "http://x:5510", "MARKETING_API_KEY": "k1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_liest_die_kampagnen_entwuerfe(self):
        zeilen = {"success": True, "data": [
            {"id": "1", "channel": "telegram", "draft_subject": "Early Access"}]}
        rekorder = Rekorder([(200, json.dumps(zeilen))])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder):
            r = werkzeuge.entwuerfe_lesen()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["daten"]["data"][0]["draft_subject"], "Early Access")

    def test_trifft_die_broadcast_route_nicht_die_publikums_route(self):
        """Der ganze Punkt: /api/proposals waere die falsche Tabelle."""
        rekorder = Rekorder([(200, json.dumps({"success": True, "data": []}))])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder):
            werkzeuge.entwuerfe_lesen()
        url = rekorder.aufrufe[0]["url"]
        self.assertIn("/api/broadcast_proposals", url)
        self.assertNotIn("/api/proposals?", url)

    def test_kanal_und_status_gehen_mit(self):
        rekorder = Rekorder([(200, json.dumps({"success": True, "data": []}))])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder):
            werkzeuge.entwuerfe_lesen(kanal="telegram", status="rejected")
        url = rekorder.aufrufe[0]["url"]
        self.assertIn("channel=telegram", url)
        self.assertIn("status=rejected", url)

    def test_leerer_status_heisst_alle(self):
        rekorder = Rekorder([(200, json.dumps({"success": True, "data": []}))])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder):
            werkzeuge.entwuerfe_lesen(status="")
        self.assertNotIn("status=", rekorder.aufrufe[0]["url"])

    def test_ist_fail_soft(self):
        def kaputt(url, daten, kopfzeilen):
            raise OSError("weg")
        with mock.patch.object(werkzeuge, "_roh_anfrage", kaputt):
            self.assertFalse(werkzeuge.entwuerfe_lesen()["ok"])


if __name__ == "__main__":
    unittest.main()
