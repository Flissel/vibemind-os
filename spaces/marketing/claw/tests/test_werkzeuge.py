"""Tests fuer die marketing-claw-Werkzeuge — ohne Netz, ohne Dienste.

Der HTTP-Zugriff laeuft ueber werkzeuge._roh_anfrage(url, daten, kopfzeilen),
das die Tests durch einen Rekorder ersetzen. So beweisen die Tests auch,
WOHIN gesprochen wird — insbesondere: nie an einen Sendepfad.
"""
import json
import unittest
from unittest import mock

from spaces.marketing.claw import werkzeuge


class Rekorder:
    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.aufrufe = []

    def __call__(self, url, daten, kopfzeilen):
        self.aufrufe.append({"url": url, "daten": daten, "kopf": kopfzeilen})
        return self.antworten.pop(0)


class TestStatistik(unittest.TestCase):
    def test_statistik_liest_api_stats(self):
        rekorder = Rekorder([(200, json.dumps({"accounts": 14746}))])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510",
                                            "MARKETING_API_KEY": "k1"}):
            r = werkzeuge.statistik()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["daten"]["accounts"], 14746)
        self.assertEqual(rekorder.aufrufe[0]["url"], "http://x:5510/api/stats")
        self.assertEqual(rekorder.aufrufe[0]["kopf"].get("X-API-Key"), "k1")

    def test_statistik_failsoft_bei_totem_dienst(self):
        def kaputt(url, daten, kopfzeilen):
            raise OSError("connection refused")
        with mock.patch.object(werkzeuge, "_roh_anfrage", kaputt):
            r = werkzeuge.statistik()
        self.assertFalse(r["ok"])
        self.assertIn("fehler", r)

    def test_kein_schluessel_in_fehlertexten(self):
        def kaputt(url, daten, kopfzeilen):
            raise OSError("Bearer geheim-777 abgelehnt")
        with mock.patch.dict("os.environ", {"MARKETING_API_KEY": "geheim-777"}), \
             mock.patch.object(werkzeuge, "_roh_anfrage", kaputt):
            r = werkzeuge.statistik()
        self.assertNotIn("geheim-777", json.dumps(r))


if __name__ == "__main__":
    unittest.main()
