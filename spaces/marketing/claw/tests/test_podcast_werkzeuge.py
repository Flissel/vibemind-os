"""Podcast fuer den Marketing-Agenten (17.10.2026): podcast_beauftragen / podcast_auftraege.
Der Agent schreibt das Skript; gesprochen wird am PC (Ton-Arbeiter, Chatterbox); versendet wird nur ueber
versand_beauftragen mit Freigabe."""
import json
import unittest
from unittest import mock

from spaces.marketing.claw import werkzeuge
from spaces.marketing.claw.tests.test_versand_beauftragen import Rekorder

SKRIPT = [{"sprecher": "Anna", "text": "Willkommen zur ersten Folge."}]


class PodcastBeauftragen(unittest.TestCase):

    def _lauf(self, rekorder, fn, **kw):
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510"}):
            return fn(**kw)

    def test_auftrag_geht_an_die_ton_route(self):
        rekorder = Rekorder([(200, json.dumps({"auftrag": "a-1"}))])
        r = self._lauf(rekorder, werkzeuge.podcast_beauftragen, titel="Herbst im Laden", skript=SKRIPT,
                       firma="vibemind")
        self.assertEqual(r, {"ok": True, "daten": {"auftrag_id": "a-1",
                                                   "hinweis": unittest.mock.ANY}})
        aufruf = rekorder.aufrufe[0]
        self.assertTrue(aufruf["url"].endswith("/api/ton/auftraege"))
        gesendet = json.loads(aufruf["daten"])
        self.assertEqual(gesendet["skript"], SKRIPT)
        self.assertEqual(gesendet["sprache"], "de")
        self.assertEqual(gesendet["firma"], "vibemind")

    def test_hausstil_gilt_auch_fuer_das_skript(self):
        rekorder = Rekorder([(200, json.dumps({"auftrag": "a-1"}))])
        self._lauf(rekorder, werkzeuge.podcast_beauftragen, titel="T",
                   skript=[{"sprecher": "Anna", "text": "Herbst — neu im Laden"}])
        self.assertNotIn("—", json.loads(rekorder.aufrufe[0]["daten"])["skript"][0]["text"])

    def test_leeres_skript_fragt_gar_nicht_erst(self):
        rekorder = Rekorder([])
        r = self._lauf(rekorder, werkzeuge.podcast_beauftragen, titel="T", skript=[])
        self.assertFalse(r["ok"])
        self.assertEqual(rekorder.aufrufe, [])

    def test_api_fehler_kommt_flach_zurueck(self):
        rekorder = Rekorder([(422, '{"detail":"Unbekannte firma"}')])
        r = self._lauf(rekorder, werkzeuge.podcast_beauftragen, titel="T", skript=SKRIPT, firma="x")
        self.assertFalse(r["ok"])
        self.assertIn("Unbekannte firma", r["fehler"])

    def test_auftraege_lesen(self):
        rekorder = Rekorder([(200, json.dumps({"auftraege": [{"id": "a-1", "status": "fertig",
                                                              "datei": "pod-a1-t.mp3"}]}))])
        r = self._lauf(rekorder, werkzeuge.podcast_auftraege, anzahl=3)
        self.assertTrue(r["ok"])
        self.assertEqual(r["daten"][0]["datei"], "pod-a1-t.mp3")
        self.assertIn("limit=3", rekorder.aufrufe[0]["url"])


class Registrierung(unittest.TestCase):

    def test_am_sidecar_angemeldet(self):
        from spaces.marketing.claw import server
        namen = {fn.__name__ for fn in server.WERKZEUGE}
        self.assertTrue({"podcast_beauftragen", "podcast_auftraege"} <= namen)


if __name__ == "__main__":
    unittest.main()


class AgentSchluessel(unittest.TestCase):

    def test_sidecar_schickt_den_agent_key_mit(self):
        rekorder = Rekorder([(200, json.dumps({"auftraege": []}))])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510", "MARKETING_AGENT_KEY": "agent-k"}):
            werkzeuge.podcast_auftraege()
        self.assertEqual(rekorder.aufrufe[0]["kopf"].get("X-Agent-Key"), "agent-k")
