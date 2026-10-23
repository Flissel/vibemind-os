"""Reels fuer den Marketing-Agenten (17.10.2026): beauftragen beim Video-Claw - Laura bleibt hier lesend."""
import json
import unittest
from unittest import mock

from spaces.marketing.claw import werkzeuge
from spaces.marketing.claw.tests.test_versand_beauftragen import Rekorder


class Reel(unittest.TestCase):

    def _lauf(self, rekorder, fn, umgebung=None, **kw):
        env = {"MARKETING_API_URL": "http://x:5510", **(umgebung or {})}
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), mock.patch.dict("os.environ", env):
            return fn(**kw)

    def test_bilder_reel_geht_mit_agent_key_an_die_video_route(self):
        rekorder = Rekorder([(200, json.dumps({"auftrag": "v-1"}))])
        r = self._lauf(rekorder, werkzeuge.reel_beauftragen, {"MARKETING_AGENT_KEY": "agent-k"},
                       art="bilder", titel="Herbst — Reel", thema="bAV", sprechertext="Drei Fehler — kurz.",
                       bilder=["team.png"], logo="logo.png", farbe_hintergrund="#0f2422")
        self.assertEqual(r["daten"]["auftrag_id"], "v-1")
        aufruf = rekorder.aufrufe[0]
        self.assertTrue(aufruf["url"].endswith("/api/video/auftraege"))
        self.assertEqual(aufruf["kopf"].get("X-Agent-Key"), "agent-k")
        koerper = json.loads(aufruf["daten"])
        self.assertEqual(koerper["art"], "bilder")
        self.assertNotIn("—", koerper["titel"] + koerper["briefing"]["sprechertext"])
        self.assertEqual(koerper["briefing"]["bilder"], ["team.png"])
        self.assertEqual(koerper["briefing"]["farben"], {"hintergrund": "#0f2422"})
        self.assertNotIn("videos", koerper["briefing"])

    def test_material_reel(self):
        rekorder = Rekorder([(200, json.dumps({"auftrag": "v-2"}))])
        self._lauf(rekorder, werkzeuge.reel_beauftragen, art="material", titel="T", thema="t",
                   sprechertext="s", videos=["a1b2c3d4e5"])
        b = json.loads(rekorder.aufrufe[0]["daten"])["briefing"]
        self.assertEqual(b["videos"], ["a1b2c3d4e5"])
        self.assertNotIn("bilder", b)

    def test_auftraege_lesen(self):
        rekorder = Rekorder([(200, json.dumps({"auftraege": [{"status": "fertig", "datei": "vid-1.mp4"}]}))])
        r = self._lauf(rekorder, werkzeuge.reel_auftraege, firma="vibemind", anzahl=5)
        self.assertEqual(r["daten"][0]["datei"], "vid-1.mp4")
        self.assertIn("/api/video/auftraege?firma=vibemind&limit=5", rekorder.aufrufe[0]["url"])

    def test_am_sidecar_und_in_der_freigabeliste(self):
        from pathlib import Path
        from spaces.marketing.claw import server
        self.assertTrue({"reel_beauftragen", "reel_auftraege"} <= {fn.__name__ for fn in server.WERKZEUGE})
        mcp = json.loads((Path(werkzeuge.__file__).parent / "shim" / "marketing-mcp.json").read_text(encoding="utf-8"))
        self.assertIn("mcp__marketing__reel_beauftragen", mcp["allowedTools"])
        # Der Marketing-Agent bekommt KEINE schreibenden Laura-Werkzeuge.
        self.assertFalse(any(t.startswith("mcp__video__") for t in mcp["allowedTools"]))


class ReelFormat(unittest.TestCase):
    """Reels Stufe 2 (22.10.2026): das Format geht im Briefing mit; reel bleibt ohne Feld."""

    def _briefing(self, **kw):
        rekorder = Rekorder([(200, json.dumps({"auftrag": "v-3"}))])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
                mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510"}):
            werkzeuge.reel_beauftragen(art="material", titel="T", thema="t", sprechertext="s", **kw)
        return json.loads(rekorder.aufrufe[0]["daten"])["briefing"]

    def test_reel_beauftragen_format(self):
        self.assertEqual(self._briefing(format="quadrat")["format"], "quadrat")
        self.assertEqual(self._briefing(format="quer")["format"], "quer")
        self.assertNotIn("format", self._briefing(format="reel"))
        self.assertNotIn("format", self._briefing())
        # Unsinn geht unveraendert an die API, die ihn mit 422 ablehnt (eine Pruefstelle)
        self.assertEqual(self._briefing(format="9:16")["format"], "9:16")

    def test_beschreibung_nennt_formate(self):
        doc = werkzeuge.reel_beauftragen.__doc__
        self.assertNotIn("Hochformat-Video", doc)
        for wort in ("reel", "quadrat", "quer"):
            self.assertIn(wort, doc)


class ReelKi(unittest.TestCase):
    """Reels Stufe 3 (23.10.2026, E8): ki_anzahl bestellt KI-Bilder; 0 = aus. Geprueft wird in der API."""

    def _koerper(self, **kw):
        rekorder = Rekorder([(200, json.dumps({"auftrag": "v-4"}))])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
                mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510"}):
            r = werkzeuge.reel_beauftragen(titel="T", thema="t", sprechertext="s", **kw)
        return r, json.loads(rekorder.aufrufe[0]["daten"])

    def test_reel_beauftragen_ki_anzahl(self):
        r, k = self._koerper(art="bilder", ki_anzahl=4, format="quadrat")
        self.assertEqual(k["art"], "bilder")
        self.assertEqual(k["briefing"]["ki"], {"anzahl": 4})
        self.assertNotIn("bilder", k["briefing"])
        self.assertEqual(k["briefing"]["format"], "quadrat")
        self.assertIn("prueft", r["daten"]["hinweis"])
        # 0 = aus: wie bisher mit Bildern, ohne ki
        _, k = self._koerper(art="bilder", bilder=["team.png"])
        self.assertNotIn("ki", k["briefing"])
        self.assertEqual(k["briefing"]["bilder"], ["team.png"])
        # Unsinn geht an die API (eine Pruefstelle)
        _, k = self._koerper(art="material", ki_anzahl=9)
        self.assertEqual(k["briefing"]["ki"], {"anzahl": 9})

    def test_beschreibung_nennt_ki_anzahl(self):
        self.assertIn("ki_anzahl", werkzeuge.reel_beauftragen.__doc__)
