"""Newsletter-Chat fuer den Agenten (20.10.2026): Entwuerfe finden, einen Chat-Auftrag anlegen, Stand lesen.
Ausgefuehrt wird wie aus dem Editor im Chat-Arbeiter am PC; jede Aenderung ist eine neue Fassung."""
import json
import unittest
from unittest import mock

from spaces.marketing.claw import werkzeuge
from spaces.marketing.claw.tests.test_versand_beauftragen import Rekorder

IID = "0123abcd-3333-3333-3333-333333333333"


class NewsletterChat(unittest.TestCase):

    def _lauf(self, rekorder, fn, umgebung=None, **kw):
        env = {"MARKETING_API_URL": "http://x:5510", "MARKETING_AGENT_KEY": "agent-k", **(umgebung or {})}
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), mock.patch.dict("os.environ", env):
            return fn(**kw)

    def test_chat_geht_an_die_agent_route_mit_agent_key(self):
        rekorder = Rekorder([(200, json.dumps({"auftrag": "a-1", "status": "offen"}))])
        r = self._lauf(rekorder, werkzeuge.newsletter_chat, inhalt=IID, nachricht="Ueberschrift kuerzer")
        self.assertEqual(r["daten"], {"auftrag": "a-1", "status": "offen"})
        aufruf = rekorder.aufrufe[0]
        self.assertTrue(aufruf["url"].endswith(f"/api/agent/inhalte/{IID}/chat"))
        self.assertEqual(aufruf["kopf"].get("X-Agent-Key"), "agent-k")
        self.assertEqual(json.loads(aufruf["daten"]), {"nachricht": "Ueberschrift kuerzer"})

    def test_ungueltige_id_oder_leere_nachricht_fragt_gar_nicht_erst(self):
        rekorder = Rekorder([])
        self.assertFalse(self._lauf(rekorder, werkzeuge.newsletter_chat, inhalt="../x", nachricht="a")["ok"])
        self.assertFalse(self._lauf(rekorder, werkzeuge.newsletter_chat, inhalt=IID, nachricht="  ")["ok"])
        self.assertEqual(rekorder.aufrufe, [])

    def test_entwuerfe_lesen(self):
        rekorder = Rekorder([(200, json.dumps({"inhalte": [{"id": IID, "titel": "Herbst"}]}))])
        r = self._lauf(rekorder, werkzeuge.newsletter_entwuerfe, firma="fin2gether", status="entwurf")
        self.assertEqual(r["daten"][0]["titel"], "Herbst")
        self.assertIn("/api/agent/inhalte?firma=fin2gether&status=entwurf", rekorder.aufrufe[0]["url"])
        self.assertEqual(rekorder.aufrufe[0]["kopf"].get("X-Agent-Key"), "agent-k")

    def test_stand_lesen(self):
        rekorder = Rekorder([(200, json.dumps({"auftraege": [{"status": "fertig", "antwort": "Erledigt"}]}))])
        r = self._lauf(rekorder, werkzeuge.newsletter_chat_stand, inhalt=IID)
        self.assertEqual(r["daten"][0]["antwort"], "Erledigt")

    def test_am_sidecar_und_in_der_freigabeliste(self):
        from pathlib import Path
        from spaces.marketing.claw import server
        namen = {"newsletter_entwuerfe", "newsletter_chat", "newsletter_chat_stand"}
        self.assertTrue(namen <= {fn.__name__ for fn in server.WERKZEUGE})
        freigabe = json.loads((Path(server.__file__).parent / "shim" / "marketing-mcp.json").read_text("utf-8"))
        erlaubt = json.dumps(freigabe)
        for n in namen:
            self.assertIn(f"mcp__marketing__{n}", erlaubt)
