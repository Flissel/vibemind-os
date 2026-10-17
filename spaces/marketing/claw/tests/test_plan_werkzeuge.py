"""Kampagnenkalender fuer den Agenten (17.10.2026): lesen und VORSCHLAGEN - bestaetigt wird im Pult."""
import json
import unittest
from unittest import mock

from spaces.marketing.claw import werkzeuge
from spaces.marketing.claw.tests.test_versand_beauftragen import Rekorder


class Plan(unittest.TestCase):

    def _lauf(self, rekorder, fn, umgebung=None, **kw):
        env = {"MARKETING_API_URL": "http://x:5510", **(umgebung or {})}
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), mock.patch.dict("os.environ", env):
            return fn(**kw)

    def test_vorschlag_geht_an_die_agent_route_mit_agent_key(self):
        rekorder = Rekorder([(200, json.dumps({"id": "p-1", "status": "vorschlag"}))])
        r = self._lauf(rekorder, werkzeuge.plan_vorschlagen, {"MARKETING_AGENT_KEY": "agent-k"},
                       datum="2026-10-22", art="newsletter", titel="Herbst \u2014 Newsletter", firma="vibemind")
        self.assertEqual(r["daten"]["status"], "vorschlag")
        aufruf = rekorder.aufrufe[0]
        self.assertTrue(aufruf["url"].endswith("/api/plan/vorschlag"))
        self.assertEqual(aufruf["kopf"].get("X-Agent-Key"), "agent-k")
        self.assertNotIn("\u2014", json.loads(aufruf["daten"])["titel"])

    def test_lesen(self):
        rekorder = Rekorder([(200, json.dumps({"eintraege": [{"titel": "Herbst", "status": "geplant"}]}))])
        r = self._lauf(rekorder, werkzeuge.plan_lesen, firma="vibemind", tage=14)
        self.assertEqual(r["daten"][0]["titel"], "Herbst")
        self.assertIn("/api/plan?firma=vibemind&tage=14", rekorder.aufrufe[0]["url"])

    def test_leerer_titel_fragt_gar_nicht_erst(self):
        rekorder = Rekorder([])
        r = self._lauf(rekorder, werkzeuge.plan_vorschlagen, datum="2026-10-22", art="post", titel="  ")
        self.assertFalse(r["ok"])
        self.assertEqual(rekorder.aufrufe, [])

    def test_am_sidecar_angemeldet(self):
        from spaces.marketing.claw import server
        self.assertTrue({"plan_lesen", "plan_vorschlagen"} <= {fn.__name__ for fn in server.WERKZEUGE})
