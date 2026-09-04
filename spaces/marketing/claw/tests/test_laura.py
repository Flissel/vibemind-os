"""Laura-Passthrough im Sidecar: Videos und Transkripte als Belegquelle.

Getestet ohne Netz — der Rekorder beweist Ziel-URL und Kopfzeilen. Zwei
Eigenheiten der Laura-API sind hier festgenagelt, weil sie leicht wieder
verloren gehen (beide am 04.09.2026 an der laufenden API gemessen):

  * es gibt KEINE globale Asset-Liste. Assets haengen an Projekten
    (`GET /projects`, dann `GET /projects/{id}/assets`) — `videos()`
    laeuft deshalb ueber zwei Stufen.
  * die Routen tragen KEIN `/api`-Praefix, und das Transkript liegt unter
    `/assets/{id}/transcript` (analysis.py:116), nicht beim Asset.
"""
import json
import unittest
from unittest import mock

from spaces.marketing.claw import laura, werkzeuge
from spaces.marketing.claw.tests.test_werkzeuge import Rekorder


class TestVideos(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict("os.environ", {
            "LAURA_API_URL": "http://laura:8765",
            "LAURA_TOKEN": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_videos_sammelt_ueber_alle_projekte(self):
        rekorder = Rekorder([
            (200, json.dumps([{"id": "p1", "name": "Produkt"},
                              {"id": "p2", "name": "Messe"}])),
            (200, json.dumps([{"id": "a1", "name": "VibeMind-Laura-Produktvideo.mp4"}])),
            (200, json.dumps([{"id": "a2", "name": "Messe-Schnitt.mp4"}])),
        ])
        with mock.patch.object(laura, "_roh_anfrage", rekorder):
            ergebnis = werkzeuge.videos()
        self.assertTrue(ergebnis["ok"], ergebnis)
        namen = [v["name"] for v in ergebnis["daten"]]
        self.assertEqual(namen, ["VibeMind-Laura-Produktvideo.mp4", "Messe-Schnitt.mp4"])
        # Jedes Video traegt sein Projekt mit — ohne das ist es als Beleg wertlos.
        self.assertEqual(ergebnis["daten"][0]["projekt"], "Produkt")
        self.assertEqual([a["url"] for a in rekorder.aufrufe], [
            "http://laura:8765/projects",
            "http://laura:8765/projects/p1/assets",
            "http://laura:8765/projects/p2/assets",
        ])

    def test_leere_laura_ist_kein_fehler(self):
        """Heute ist Laura leer. Das ist eine Antwort, keine Stoerung."""
        rekorder = Rekorder([(200, "[]")])
        with mock.patch.object(laura, "_roh_anfrage", rekorder):
            ergebnis = werkzeuge.videos()
        self.assertTrue(ergebnis["ok"], ergebnis)
        self.assertEqual(ergebnis["daten"], [])

    def test_ein_stummes_projekt_kippt_nicht_die_ganze_liste(self):
        def antwort(url, daten, kopfzeilen):
            if url.endswith("/projects"):
                return (200, json.dumps([{"id": "p1", "name": "A"},
                                         {"id": "p2", "name": "B"}]))
            if url.endswith("/projects/p1/assets"):
                raise OSError("weg")
            return (200, json.dumps([{"id": "a2", "name": "B.mp4"}]))
        with mock.patch.object(laura, "_roh_anfrage", antwort):
            ergebnis = werkzeuge.videos()
        self.assertTrue(ergebnis["ok"], ergebnis)
        self.assertEqual([v["name"] for v in ergebnis["daten"]], ["B.mp4"])

    def test_videos_ist_fail_soft_wenn_laura_aus_ist(self):
        def kaputt(url, daten, kopfzeilen):
            raise OSError("Connection refused")
        with mock.patch.object(laura, "_roh_anfrage", kaputt):
            ergebnis = werkzeuge.videos()
        self.assertFalse(ergebnis["ok"])
        self.assertIn("nicht erreichbar", ergebnis["fehler"])


class TestTranskript(unittest.TestCase):
    def test_transkript_trifft_die_analysis_route(self):
        rekorder = Rekorder([(200, json.dumps([{"start": 0.0, "text": "Hallo"}]))])
        with mock.patch.object(laura, "_roh_anfrage", rekorder), \
             mock.patch.dict("os.environ", {"LAURA_API_URL": "http://laura:8765"}):
            ergebnis = werkzeuge.video_transkript("a1")
        self.assertTrue(ergebnis["ok"], ergebnis)
        self.assertEqual(ergebnis["daten"]["segmente"][0]["text"], "Hallo")
        self.assertEqual(ergebnis["daten"]["text"], "Hallo")
        self.assertEqual(rekorder.aufrufe[0]["url"],
                         "http://laura:8765/assets/a1/transcript")

    def test_leere_kennung_fragt_gar_nicht_erst(self):
        rekorder = Rekorder([])
        with mock.patch.object(laura, "_roh_anfrage", rekorder):
            ergebnis = werkzeuge.video_transkript("  ")
        self.assertFalse(ergebnis["ok"])
        self.assertEqual(rekorder.aufrufe, [])

    def test_kennung_wird_nicht_zum_pfadausbruch(self):
        rekorder = Rekorder([(200, "[]")])
        with mock.patch.object(laura, "_roh_anfrage", rekorder), \
             mock.patch.dict("os.environ", {"LAURA_API_URL": "http://laura:8765"}):
            werkzeuge.video_transkript("../../admin/shutdown")
        self.assertEqual(rekorder.aufrufe[0]["url"],
                         "http://laura:8765/assets/..%2F..%2Fadmin%2Fshutdown/transcript")


class TestSicherheit(unittest.TestCase):
    def test_token_geht_als_kopfzeile_mit(self):
        rekorder = Rekorder([(200, "[]")])
        with mock.patch.object(laura, "_roh_anfrage", rekorder), \
             mock.patch.dict("os.environ", {"LAURA_API_URL": "http://laura:8765",
                                            "LAURA_TOKEN": "streng-geheim"}):
            werkzeuge.videos()
        self.assertEqual(rekorder.aufrufe[0]["kopf"].get("X-Laura-Token"), "streng-geheim")

    def test_kein_token_im_fehlertext(self):
        def kaputt(url, daten, kopfzeilen):
            raise OSError("X-Laura-Token streng-geheim abgelehnt")
        with mock.patch.object(laura, "_roh_anfrage", kaputt), \
             mock.patch.dict("os.environ", {"LAURA_API_URL": "http://laura:8765",
                                            "LAURA_TOKEN": "streng-geheim"}):
            ergebnis = werkzeuge.videos()
        self.assertFalse(ergebnis["ok"])
        self.assertNotIn("streng-geheim", json.dumps(ergebnis))

    def test_fehlerstatus_wird_gemeldet(self):
        rekorder = Rekorder([(401, "invalid or missing X-Laura-Token")])
        with mock.patch.object(laura, "_roh_anfrage", rekorder), \
             mock.patch.dict("os.environ", {"LAURA_API_URL": "http://laura:8765"}):
            ergebnis = werkzeuge.videos()
        self.assertFalse(ergebnis["ok"])
        self.assertIn("401", ergebnis["fehler"])


class TestKompakteAntwort(unittest.TestCase):
    """Was der Agent bekommt, muss er auch lesen koennen.

    Am ersten echten Video gesehen (04.09.2026): die Antwort von `videos()`
    war 2.400 Zeichen fuer EIN Video — sechs Dateieintraege mit
    Container-Pfaden, Pruefsummen und Groessen. Bei sieben Videos waere das
    eine Wand aus Innereien, in der die zwei Angaben untergehen, die zaehlen:
    wie das Video heisst und wie lang es ist. Der Agent kann mit
    `/data/workspace/project-…/proxies/…/proxy.mp4` nichts anfangen — er hat
    keinen Zugriff auf dieses Dateisystem.
    """

    def setUp(self):
        self.env = mock.patch.dict("os.environ", {"LAURA_API_URL": "http://laura:8765"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _antwort(self):
        return [
            (200, json.dumps([{"id": "p1", "name": "Produktvideos"}])),
            (200, json.dumps([{
                "id": "a1", "display_name": "VibeMind Laura Produktvideo",
                "type": "video", "duration_frames": 1390,
                "rate_num": 30, "rate_den": 1, "width": 1912, "height": 1034,
                "codec_video": "h264", "online": True,
                "source_path": "/data/eingang/x.mp4",
                "sha256": "7426a86d5883156e0c333eac0cf90fcc362fd31d0cbb4473174eb3f55a9e825d",
                "files": [{"id": "f1", "kind": "proxy", "path": "/data/w/p.mp4",
                           "size_bytes": 91456110}] * 6,
            }])),
        ]

    def test_die_innereien_bleiben_draussen(self):
        rekorder = Rekorder(self._antwort())
        with mock.patch.object(laura, "_roh_anfrage", rekorder):
            ergebnis = werkzeuge.videos()
        video = ergebnis["daten"][0]
        self.assertNotIn("files", video)
        self.assertNotIn("sha256", video)
        self.assertNotIn("source_path", video)

    def test_die_angaben_die_zaehlen_bleiben(self):
        rekorder = Rekorder(self._antwort())
        with mock.patch.object(laura, "_roh_anfrage", rekorder):
            video = werkzeuge.videos()["daten"][0]
        self.assertEqual(video["kennung"], "a1")
        self.assertEqual(video["name"], "VibeMind Laura Produktvideo")
        self.assertEqual(video["projekt"], "Produktvideos")
        self.assertEqual(video["aufloesung"], "1912x1034")

    def test_dauer_in_sekunden_statt_bildern(self):
        """1390 Bilder bei 30/s sind 46 s. Mit `duration_frames` allein
        kann niemand etwas anfangen, ohne die Bildrate danebenzulegen."""
        rekorder = Rekorder(self._antwort())
        with mock.patch.object(laura, "_roh_anfrage", rekorder):
            video = werkzeuge.videos()["daten"][0]
        self.assertEqual(video["dauer_s"], 46)

    def test_bildrate_null_stuerzt_nicht_ab(self):
        antwort = self._antwort()
        antwort[1] = (200, json.dumps([{"id": "a1", "display_name": "X",
                                        "duration_frames": 100, "rate_num": 0,
                                        "rate_den": 1}]))
        rekorder = Rekorder(antwort)
        with mock.patch.object(laura, "_roh_anfrage", rekorder):
            video = werkzeuge.videos()["daten"][0]
        self.assertIsNone(video["dauer_s"])

    def test_die_antwort_ist_deutlich_kuerzer(self):
        rekorder = Rekorder(self._antwort())
        with mock.patch.object(laura, "_roh_anfrage", rekorder):
            ergebnis = werkzeuge.videos()
        self.assertLess(len(json.dumps(ergebnis, ensure_ascii=False)), 400)


class TestTranskriptText(unittest.TestCase):
    """Ein Transkript ist zum ZITIEREN da."""

    def setUp(self):
        self.env = mock.patch.dict("os.environ", {"LAURA_API_URL": "http://laura:8765"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _segmente(self):
        return (200, json.dumps([
            {"id": "s1", "start_frame": 0, "end_frame": 60,
             "text": "This is Laura,", "confidence": 0.9},
            {"id": "s2", "start_frame": 60, "end_frame": 120,
             "text": "an autonomous AI video editor.", "confidence": 0.9},
        ]))

    def test_der_fortlaufende_text_liegt_bei(self):
        rekorder = Rekorder([self._segmente()])
        with mock.patch.object(laura, "_roh_anfrage", rekorder):
            daten = werkzeuge.video_transkript("a1")["daten"]
        self.assertEqual(daten["text"],
                         "This is Laura, an autonomous AI video editor.")

    def test_die_segmente_bleiben_fuer_stellenangaben(self):
        rekorder = Rekorder([self._segmente()])
        with mock.patch.object(laura, "_roh_anfrage", rekorder):
            daten = werkzeuge.video_transkript("a1")["daten"]
        self.assertEqual(len(daten["segmente"]), 2)
        self.assertEqual(daten["segmente"][0]["start_frame"], 0)

    def test_ohne_analyse_ist_die_liste_leer_und_sagt_es(self):
        """Ein importiertes Video ohne ASR-Lauf hat kein Transkript. Das ist
        kein Fehler, aber der Agent muss es merken, statt Stille zu deuten."""
        rekorder = Rekorder([(200, "[]")])
        with mock.patch.object(laura, "_roh_anfrage", rekorder):
            ergebnis = werkzeuge.video_transkript("a1")
        self.assertTrue(ergebnis["ok"], ergebnis)
        self.assertEqual(ergebnis["daten"]["text"], "")
        self.assertIn("hinweis", ergebnis["daten"])


if __name__ == "__main__":
    unittest.main()
