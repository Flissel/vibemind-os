"""Geschaeftsfakten fuer den Agenten (Paket D2, 19.10.2026): nur Zahlen aus dem Sales-Bestand, lesend."""
import json
import unittest
from unittest import mock

from spaces.marketing.claw import werkzeuge
from spaces.marketing.claw.tests.test_versand_beauftragen import Rekorder


class Geschaeftsfakten(unittest.TestCase):

    def _lauf(self, rekorder, umgebung=None, **kw):
        env = {"MARKETING_API_URL": "http://x:5510", **(umgebung or {})}
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), mock.patch.dict("os.environ", env):
            return werkzeuge.geschaeftsfakten(**kw)

    def test_liest_die_agent_route_mit_agent_key(self):
        rekorder = Rekorder([(200, json.dumps({"firma": "vibemind", "kontakte": 558, "newsletter_hoechstens": 1}))])
        r = self._lauf(rekorder, {"MARKETING_AGENT_KEY": "agent-k"}, firma="vibemind")
        self.assertTrue(r["ok"])
        self.assertEqual(r["daten"]["kontakte"], 558)
        aufruf = rekorder.aufrufe[0]
        self.assertTrue(aufruf["url"].endswith("/api/fakten?firma=vibemind"))
        self.assertEqual(aufruf["kopf"].get("X-Agent-Key"), "agent-k")
        self.assertIsNone(aufruf["daten"])                     # GET - das Werkzeug schreibt nichts

    def test_firma_wird_kodiert(self):
        rekorder = Rekorder([(200, "{}")])
        self._lauf(rekorder, firma="a&b=c")
        self.assertIn("firma=a%26b%3Dc", rekorder.aufrufe[0]["url"])

    def test_fehler_bleibt_weich(self):
        rekorder = Rekorder([(404, json.dumps({"detail": "keinen Laden"}))])
        r = self._lauf(rekorder, firma="fin2gether")
        self.assertFalse(r["ok"])
        self.assertIn("404", r["fehler"])

    def test_am_sidecar_angemeldet_und_freigegeben(self):
        from spaces.marketing.claw import server
        self.assertIn(werkzeuge.geschaeftsfakten, server.WERKZEUGE)
        self.assertIn("Nur Zahlen", werkzeuge.geschaeftsfakten.__doc__)
