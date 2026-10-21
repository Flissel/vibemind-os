"""Newsletter-Anmeldung: Aufsteller, Flyer-Rueckseite und Website-Link (21.10.2026).

Was diese Suite verteidigt:
1. Der GEDRUCKTE QR-Code fuehrt genau zur Anmeldung (zurueckgelesen aus dem PDF).
2. Adresse und Betreff waehlt nie der Agent - ohne eingerichtete Adresse entsteht nichts.
3. Englisch bleibt gesperrt, bis sales-claw "Subscribe" versteht.
4. Es entsteht eine Seite A6, und sie landet im Medienordner - versendet wird nichts."""
import importlib.util
import os
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, unquote, urlparse

if importlib.util.find_spec("reportlab") is None:  # pragma: no cover
    raise unittest.SkipTest("reportlab fehlt - Suite unter .venv fahren")

from spaces.marketing.claw import anmelde_material as am  # noqa: E402
from spaces.marketing.claw import werkzeuge  # noqa: E402

ADRESSE = "felix@vibemind.space"
UMGEBUNG = {"NEWSLETTER_ANMELDE_ADRESSE": ADRESSE, "NEWSLETTER_ANMELDE_EN": ""}


def _qr_lesen(roh: bytes) -> str:
    import cv2
    import numpy
    import pypdfium2
    seite = pypdfium2.PdfDocument(roh)[0]
    bild = seite.render(scale=6).to_pil().convert("RGB")
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(numpy.array(bild))
    return text


def _seiten(roh: bytes) -> list:
    import pypdfium2
    return [s.get_size() for s in pypdfium2.PdfDocument(roh)]


@mock.patch.dict(os.environ, UMGEBUNG)
class TestLink(unittest.TestCase):
    def test_link_fuehrt_zur_anmeldung(self):
        teile = urlparse(am.anmelde_link("de"))
        self.assertEqual(teile.scheme, "mailto")
        self.assertEqual(teile.path, ADRESSE)
        q = parse_qs(teile.query)
        self.assertEqual(q["subject"], ["Anmelden"])
        self.assertIn("Newsletter", q["body"][0])

    def test_zweisprachig_nutzt_den_deutschen_betreff(self):
        self.assertIn("subject=Anmelden", am.anmelde_link("de_en"))

    def test_englisch_ist_gesperrt_bis_zum_schalter(self):
        self.assertIn("gesperrt", am.pruefen("aufsteller", "en"))
        with mock.patch.dict(os.environ, {"NEWSLETTER_ANMELDE_EN": "1"}):
            self.assertEqual(am.pruefen("aufsteller", "en"), "")
            self.assertIn("subject=Subscribe", am.anmelde_link("en"))

    def test_englischer_satz_nennt_nur_was_sales_claw_versteht(self):
        en = am._texte("de_en", "", "")[1]
        self.assertIn("Abmelden", en["datenschutz"])
        self.assertNotIn("“Unsubscribe”", en["datenschutz"])
        self.assertIn("Anmelden", en["rueckfall"])

    def test_gedruckter_link_ohne_mailtext(self):
        self.assertNotIn("body=", am.anmelde_link("de", mit_text=False))
        self.assertIn("body=", am.anmelde_link("de"))

    def test_ohne_adresse_entsteht_nichts(self):
        with mock.patch.dict(os.environ, {"NEWSLETTER_ANMELDE_ADRESSE": ""}):
            self.assertIn("Anmeldeadresse", am.pruefen("flyer", "de"))
            with self.assertRaises(ValueError):
                am.bauen("flyer", "de")

    def test_unbekannte_art_und_sprache(self):
        self.assertIn("gibt es nicht", am.pruefen("plakat", "de"))
        self.assertIn("gibt es nicht", am.pruefen("aufsteller", "fr"))

    def test_website_schnipsel(self):
        w = am.website("de")
        self.assertEqual(w["knopf"], "Newsletter abonnieren")
        self.assertIn(f'href="{w["link"]}"', w["html"])


@unittest.skipUnless(all(importlib.util.find_spec(m) for m in ("cv2", "pypdfium2", "numpy")),
                     "cv2/pypdfium2 fehlen - QR-Rueckleseprobe nur am PC")
@mock.patch.dict(os.environ, UMGEBUNG)
class TestPdf(unittest.TestCase):
    def _pruefe(self, art, sprache, breite_hoehe):
        roh = am.bauen(art, sprache, titel="Bleib auf dem Laufenden")
        self.assertTrue(roh.startswith(b"%PDF"))
        seiten = _seiten(roh)
        self.assertEqual(len(seiten), 1)
        self.assertAlmostEqual(seiten[0][0], breite_hoehe[0], delta=1)
        self.assertAlmostEqual(seiten[0][1], breite_hoehe[1], delta=1)
        self.assertEqual(unquote(_qr_lesen(roh)), unquote(am.anmelde_link(sprache, mit_text=False)))

    def test_aufsteller_a6_hoch_qr_lesbar(self):
        self._pruefe("aufsteller", "de", am.A6)

    def test_flyer_a6_quer_qr_lesbar(self):
        self._pruefe("flyer", "de", (am.A6[1], am.A6[0]))

    def test_zweisprachig_qr_lesbar(self):
        self._pruefe("aufsteller", "de_en", am.A6)
        self._pruefe("flyer", "de_en", (am.A6[1], am.A6[0]))

    def test_lange_texte_werden_gekuerzt(self):
        roh = am.bauen("aufsteller", "de", titel="x" * 200, satz="y" * 500)
        self.assertEqual(unquote(_qr_lesen(roh)), unquote(am.anmelde_link("de", mit_text=False)))


@mock.patch.dict(os.environ, UMGEBUNG)
class TestWerkzeug(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.umgebung = mock.patch.dict(os.environ, {"MEDIA_ERZEUGT_DIR": self.ordner,
                                                     "MEDIA_ERZEUGT_SSH_HOST": ""})
        self.umgebung.start()
        self.vorlage = mock.patch.object(werkzeuge, "_vorlage_holen", return_value=({}, ""))
        self.vorlage.start()

    def tearDown(self):
        self.vorlage.stop()
        self.umgebung.stop()

    def test_aufsteller_landet_im_medienordner(self):
        r = werkzeuge.newsletter_anmeldung_material("aufsteller", "de")
        self.assertTrue(r["ok"], r)
        dateien = os.listdir(self.ordner)
        self.assertEqual(len(dateien), 1)
        self.assertTrue(dateien[0].startswith("marketing-newsletter-aufsteller-de"))
        self.assertTrue(dateien[0].endswith(".pdf"))
        self.assertEqual(r["daten"]["link"], am.anmelde_link("de"))

    def test_website_legt_keine_datei_an(self):
        r = werkzeuge.newsletter_anmeldung_material("website", "de")
        self.assertTrue(r["ok"], r)
        self.assertEqual(os.listdir(self.ordner), [])
        self.assertIn("mailto:", r["daten"]["link"])

    def test_englisch_gesperrt_legt_nichts_ab(self):
        r = werkzeuge.newsletter_anmeldung_material("flyer", "en")
        self.assertFalse(r["ok"])
        self.assertEqual(os.listdir(self.ordner), [])

    def test_platzhalter_im_titel_wird_abgewiesen(self):
        r = werkzeuge.newsletter_anmeldung_material("flyer", "de", titel="[TITEL HIER]")
        self.assertFalse(r["ok"], r)
        self.assertEqual(os.listdir(self.ordner), [])

    def test_nicht_freigegebene_vorlage_wird_abgewiesen(self):
        self.vorlage.stop()
        with mock.patch.object(werkzeuge, "_vorlage_holen", return_value=(None, "nicht freigegeben")):
            r = werkzeuge.newsletter_anmeldung_material("flyer", "de", vorlage="hell")
        self.vorlage.start()
        self.assertFalse(r["ok"])
        self.assertEqual(os.listdir(self.ordner), [])

    def test_werkzeug_ist_im_sidecar_registriert(self):
        from spaces.marketing.claw import server
        self.assertIn(werkzeuge.newsletter_anmeldung_material, server.WERKZEUGE)

    def test_sidecar_laedt_die_adresse_aus_der_env(self):
        from spaces.marketing.claw import server
        self.assertIn("NEWSLETTER_ANMELDE_ADRESSE", server._ENV_KEYS)
        self.assertIn("NEWSLETTER_ANMELDE_EN", server._ENV_KEYS)


if __name__ == "__main__":
    unittest.main()
