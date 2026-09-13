"""Laura-Videokatalog nach Rowboat: was liegt an Material bereit, worueber
redet es, wie lang ist es — als nachschlagbare Metadaten fuer beide claws.

Ohne Netz: Lauras HTTP-Grenze und der Rowboat-Griff werden ersetzt. Der Test
haelt fest: (1) die Videodatei selbst wandert NICHT nach Rowboat, nur ihr
Steckbrief samt Pfad, (2) das Transkript wird gekuerzt, (3) derselbe Bestand
wird nicht zweimal geschrieben, (4) faellt Laura aus, sagt der Worker das.
"""
import json
import unittest
from unittest import mock

from spaces.marketing.workers import laura_rowboat_export as lx


PROJEKTE = [{"id": "p1", "name": "Kampagne Herbst", "sequence_rate_num": 25,
             "sequence_rate_den": 1, "workspace_root": "E:/laura/p1",
             "created_at": "2026-09-01T09:00:00Z", "drop_frame": False}]

ASSETS = [{"id": "a1", "project_id": "p1", "type": "video",
           "display_name": "interview-felix.mp4",
           "source_path": "E:/material/interview-felix.mp4",
           "duration_frames": 7500, "rate_num": 25, "rate_den": 1,
           "width": 1920, "height": 1080, "codec_video": "h264"}]

SEGMENTE = [{"id": "s1", "text": "VibeMind nimmt jede Kundenantwort auf.",
             "start_frame": 0, "end_frame": 50},
            {"id": "s2", "text": "Der Betreiber gibt frei, nicht der Bot.",
             "start_frame": 51, "end_frame": 120}]


def laura_fake(pfad):
    if pfad == "/projects":
        return PROJEKTE
    if pfad == "/projects/p1/assets":
        return ASSETS
    if pfad == "/assets/a1/transcript":
        return SEGMENTE
    raise AssertionError("unerwarteter Pfad " + pfad)


class TestLauraExport(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict("os.environ", {
            "LAURA_API_URL": "http://127.0.0.1:8765",
            "ROWBOAT_URL": "http://vm:3100",
            "ROWBOAT_API_KEY": "geheim-999",
            "LAURA_EXPORT_SOURCE_ID": "src-videos",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_steckbrief_statt_video(self):
        geschrieben = []

        def rowboat(werkzeug, argumente):
            if werkzeug == "rowboat_dokumente":
                return {"ok": True, "daten": []}
            geschrieben.append(argumente)
            return {"ok": True, "daten": {}}

        with mock.patch.object(lx, "_laura", laura_fake), \
             mock.patch.object(lx, "_rowboat", rowboat):
            anzahl = lx.run_pass()

        self.assertEqual(anzahl, 1)
        dok = geschrieben[0]["dokumente"][0]
        self.assertEqual(dok["name"], "video-a1")
        self.assertEqual(geschrieben[0]["sourceId"], "src-videos")
        for teil in ("interview-felix.mp4", "E:/material/interview-felix.mp4",
                     "Kampagne Herbst", "300", "1920x1080",
                     "VibeMind nimmt jede Kundenantwort auf."):
            self.assertIn(teil, dok["inhalt"])
        # Der Steckbrief ist Text — keine Binaerdaten, kein base64.
        self.assertNotIn("base64", dok["inhalt"].lower())

    def test_transkript_wird_gekuerzt(self):
        lang = [{"id": f"s{i}", "text": "Wort " * 200, "start_frame": i,
                 "end_frame": i + 1} for i in range(50)]

        def laura(pfad):
            return lang if pfad.endswith("/transcript") else laura_fake(pfad)

        geschrieben = []

        def rowboat(werkzeug, argumente):
            if werkzeug == "rowboat_dokumente":
                return {"ok": True, "daten": []}
            geschrieben.append(argumente)
            return {"ok": True, "daten": {}}

        with mock.patch.object(lx, "_laura", laura), \
             mock.patch.object(lx, "_rowboat", rowboat):
            lx.run_pass()

        inhalt = geschrieben[0]["dokumente"][0]["inhalt"]
        self.assertLessEqual(len(inhalt), lx._MAX_ZEICHEN + 500)
        self.assertIn("gekuerzt", inhalt)

    def test_unveraendertes_wird_nicht_erneut_geschrieben(self):
        with mock.patch.object(lx, "_laura", laura_fake):
            inhalt = lx.dokument_inhalt(PROJEKTE[0], ASSETS[0], SEGMENTE)
        vorhanden = [{"id": "d1", "name": "video-a1", "status": "ready",
                      "content": inhalt}]
        geschrieben = []

        def rowboat(werkzeug, argumente):
            if werkzeug == "rowboat_dokumente":
                return {"ok": True, "daten": vorhanden}
            geschrieben.append(argumente)
            return {"ok": True, "daten": {}}

        with mock.patch.object(lx, "_laura", laura_fake), \
             mock.patch.object(lx, "_rowboat", rowboat):
            anzahl = lx.run_pass()

        self.assertEqual(anzahl, 0)
        self.assertEqual(geschrieben, [])

    def test_steckbrief_nennt_die_objekt_adresse_wenn_es_eine_gibt(self):
        """Ohne die Adresse weiss marketing nur, DASS ein Video existiert.

        Laura fuellt `object_key`, sobald ihr Objektspeicher an ist. Der Steckbrief
        ist die einzige Stelle, an der beide claws davon erfahren koennen -- steht
        sie nicht drin, bleibt das Video unerreichbar und jemand kopiert wieder von
        Hand ueber SSH.
        """
        asset = dict(ASSETS[0], object_key="laura/assets/a1.mp4")

        inhalt = lx.dokument_inhalt(PROJEKTE[0], asset, SEGMENTE)

        self.assertIn("laura/assets/a1.mp4", inhalt)
        # Der lokale Pfad bleibt daneben stehen: er ist weiterhin der Arbeitsweg
        # fuer alles, was auf derselben Maschine schneidet.
        self.assertIn("E:/material/interview-felix.mp4", inhalt)

    def test_steckbrief_ohne_objekt_adresse_bleibt_wie_bisher(self):
        """Der Normalfall: kein Objektspeicher, kein zusaetzliches Feld, keine
        irrefuehrende Zeile ueber einen Bucket, den es nicht gibt."""
        inhalt = lx.dokument_inhalt(PROJEKTE[0], ASSETS[0], SEGMENTE)

        self.assertNotIn("Objekt", inhalt)
        self.assertIn("Die Datei bleibt im Dateisystem", inhalt)

    def test_laura_aus_sagt_es_und_schreibt_nichts(self):
        def laura_tot(pfad):
            raise RuntimeError("Laura nicht erreichbar (ConnectionRefusedError)")

        geschrieben = []

        def rowboat(werkzeug, argumente):
            geschrieben.append(argumente)
            return {"ok": True, "daten": []}

        with mock.patch.object(lx, "_laura", laura_tot), \
             mock.patch.object(lx, "_rowboat", rowboat):
            with self.assertRaises(RuntimeError) as f:
                lx.run_pass()
        self.assertIn("Laura", str(f.exception))
        self.assertEqual(geschrieben, [])


if __name__ == "__main__":
    unittest.main()
