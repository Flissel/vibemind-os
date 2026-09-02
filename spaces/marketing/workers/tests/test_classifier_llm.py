"""Klassifikator-LLM-Tuer: seit dem API-Budget-Stopp laeuft sie ueber den
Claude-Shim (Subscription), nicht mehr gegen api.openai.com.

Getestet ohne Netz: `requests.post` wird ersetzt und beweist Ziel-URL,
Modell und Kopfzeilen. Der Shim prueft keinen Schluessel — ein fehlender
OPENAI_API_KEY darf gegen ihn also KEIN Abbruchgrund sein (gegen die
echte OpenAI-Adresse schon).
"""
import json
import unittest
from unittest import mock

from spaces.marketing.workers import bubble_classifier_runner as k


class Antwort:
    def __init__(self, inhalt, status=200):
        self.status_code = status
        self._inhalt = inhalt
        self.text = inhalt if isinstance(inhalt, str) else json.dumps(inhalt)

    def json(self):
        return {"choices": [{"message": {"content": self._inhalt}}]}


GUT = json.dumps({"category": "marketing", "channels": ["telegram"],
                  "confidence": 0.9, "reason": "klar"})


class TestKlassifikatorTuer(unittest.TestCase):
    def test_vorgabe_ist_der_shim_mit_haiku(self):
        aufrufe = []

        def post(url, **kw):
            aufrufe.append({"url": url, **kw})
            return Antwort(GUT)

        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False), \
             mock.patch.object(k, "_BASIS", "http://127.0.0.1:8114/v1"), \
             mock.patch.object(k, "_MODEL", "claude-code-haiku"), \
             mock.patch.object(k.requests, "post", post):
            out = k.classify("Titel", "Beschreibung")

        self.assertEqual(out["category"], "marketing")
        self.assertEqual(aufrufe[0]["url"], "http://127.0.0.1:8114/v1/chat/completions")
        self.assertEqual(aufrufe[0]["json"]["model"], "claude-code-haiku")
        # Kein Schluessel noetig, und keiner mitgeschickt.
        self.assertNotIn("Authorization", aufrufe[0].get("headers", {}))

    def test_gegen_openai_bleibt_der_schluessel_pflicht(self):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False), \
             mock.patch.object(k, "_BASIS", "https://api.openai.com/v1"), \
             mock.patch.object(k, "_load_env_fallback", lambda: None):
            with self.assertRaises(RuntimeError):
                k.classify("T", "B")

    def test_antwort_in_codefences_wird_gelesen(self):
        def post(url, **kw):
            return Antwort("```json\n" + GUT + "\n```")

        with mock.patch.object(k, "_BASIS", "http://127.0.0.1:8114/v1"), \
             mock.patch.object(k.requests, "post", post):
            out = k.classify("T", "B")
        self.assertEqual(out["category"], "marketing")

    def test_unbekannte_kategorie_wird_abgelehnt(self):
        def post(url, **kw):
            return Antwort(json.dumps({"category": "erfunden", "confidence": 1.0}))

        with mock.patch.object(k, "_BASIS", "http://127.0.0.1:8114/v1"), \
             mock.patch.object(k.requests, "post", post):
            with self.assertRaises(RuntimeError):
                k.classify("T", "B")


if __name__ == "__main__":
    unittest.main()
