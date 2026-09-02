"""Rowboat-Passthrough im Sidecar: der Bearer-Schluessel bleibt auf dem
Host — er erreicht NIE das Gateway-Volume. Getestet ohne Netz; der
Rekorder beweist Ziel-URL, Bearer-Kopf und feste Projektbindung.
"""
import json
import unittest
from unittest import mock

from spaces.marketing.claw import werkzeuge
from spaces.marketing.claw.tests.test_werkzeuge import Rekorder


def _mcp_ok(daten):
    return (200, json.dumps({"jsonrpc": "2.0", "id": 1, "result": {
        "content": [{"type": "text", "text": json.dumps(daten)}],
        "isError": False}}))


class TestWissensbasis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict("os.environ", {
            "ROWBOAT_URL": "http://vm:3100",
            "ROWBOAT_PROJECT_ID": "proj-1",
            "ROWBOAT_API_KEY": "geheim-999",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_wissensquellen_fragt_projekt_aus_env(self):
        rekorder = Rekorder([_mcp_ok([{"id": "s1", "name": "Q"}])])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder):
            r = werkzeuge.wissensquellen()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["daten"][0]["id"], "s1")
        self.assertEqual(rekorder.aufrufe[0]["url"], "http://vm:3100/api/mcp")
        self.assertEqual(rekorder.aufrufe[0]["kopf"].get("Authorization"), "Bearer geheim-999")
        gesendet = json.loads(rekorder.aufrufe[0]["daten"])
        self.assertEqual(gesendet["params"]["name"], "rowboat_wissensquellen")
        self.assertEqual(gesendet["params"]["arguments"]["projectId"], "proj-1")

    def test_wissensquelle_und_dokumente(self):
        rekorder = Rekorder([_mcp_ok({"id": "s1"}), _mcp_ok([{"id": "d1"}])])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder):
            self.assertTrue(werkzeuge.wissensquelle("s1")["ok"])
            self.assertTrue(werkzeuge.dokumente("s1", mit_inhalt=True)["ok"])
        w1 = json.loads(rekorder.aufrufe[0]["daten"])["params"]
        w2 = json.loads(rekorder.aufrufe[1]["daten"])["params"]
        self.assertEqual(w1["name"], "rowboat_wissensquelle")
        self.assertEqual(w1["arguments"], {"sourceId": "s1"})
        self.assertEqual(w2["name"], "rowboat_dokumente")
        self.assertEqual(w2["arguments"], {"sourceId": "s1", "mitInhalt": True})

    def test_ablehnung_wird_gemeldet(self):
        rekorder = Rekorder([(200, json.dumps({"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": "Aufruf abgelehnt (nicht gefunden)"}],
            "isError": True}}))])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder):
            r = werkzeuge.wissensquelle("gibtsnicht")
        self.assertFalse(r["ok"])
        self.assertIn("nicht gefunden", r["fehler"])

    def test_kein_schluessel_in_fehlern(self):
        def kaputt(url, daten, kopfzeilen):
            raise OSError("Bearer geheim-999 abgelehnt")
        with mock.patch.object(werkzeuge, "_roh_anfrage", kaputt):
            r = werkzeuge.wissensquellen()
        self.assertFalse(r["ok"])
        self.assertNotIn("geheim-999", json.dumps(r))

    def test_fehlende_konfiguration_hoerbar(self):
        with mock.patch.dict("os.environ", {"ROWBOAT_URL": "", "ROWBOAT_API_KEY": "",
                                            "ROWBOAT_PROJECT_ID": ""}):
            r = werkzeuge.wissensquellen()
        self.assertFalse(r["ok"])
        self.assertIn("ROWBOAT", r["fehler"])


if __name__ == "__main__":
    unittest.main()
