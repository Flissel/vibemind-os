"""Verbotsliste (F1): Normalisierung, Sende-Gate 13, Vorschlagsfilter.

Ohne Datenbank: der Griff `_db.query_via_docker` wird ersetzt. Der Test
haelt fest, dass (1) Kennungen auf beiden Seiten gleich gebildet werden,
(2) das Gate bei einem Treffer HART abbricht (SperrlisteFehler) statt zu
warnen, (3) der Vorschlagsfilter gesperrte Kandidaten entfernt und zaehlt.
"""
import unittest
from unittest import mock

from spaces.marketing.sync import _db
from spaces.marketing.tools import sperrliste as sl


class TestNormalisierung(unittest.TestCase):
    def test_email_klein_und_getrimmt(self):
        self.assertEqual(sl.kennung_email("  Max.Muster@Example.DE "), "email:max.muster@example.de")

    def test_email_ohne_at_ist_keine_kennung(self):
        self.assertIsNone(sl.kennung_email("kein-mail"))

    def test_telefon_e164(self):
        self.assertEqual(sl.kennung_tel("+49 (0)171 / 123-4567"), "tel:+491711234567")
        self.assertEqual(sl.kennung_tel("0049 171 1234567"), "tel:+491711234567")
        self.assertEqual(sl.kennung_tel("0171 1234567"), "tel:+491711234567")
        self.assertEqual(sl.kennung_tel("491711234567@c.us"), "tel:+491711234567")

    def test_telefon_zu_kurz(self):
        self.assertIsNone(sl.kennung_tel("123"))


class TestGate(unittest.TestCase):
    def test_treffer_bricht_hart_ab(self):
        rows = [{"kennung": "email:a@x.de", "quelle": "sales:widerruf", "grund": "am Telefon"}]
        with mock.patch.object(_db, "query_via_docker", return_value=rows):
            with self.assertRaises(sl.SperrlisteFehler) as f:
                sl.pruefe_empfaenger([{"email": "A@x.de"}, {"email": "b@x.de"}])
        self.assertIn("a@x.de", str(f.exception))
        self.assertIn("sales:widerruf", str(f.exception))

    def test_ohne_treffer_kein_abbruch(self):
        with mock.patch.object(_db, "query_via_docker", return_value=[]):
            sl.pruefe_empfaenger([{"email": "b@x.de"}])

    def test_leere_liste_fragt_die_db_nicht(self):
        with mock.patch.object(_db, "query_via_docker", side_effect=AssertionError("nicht fragen")):
            sl.pruefe_empfaenger([])


class TestVorschlagsfilter(unittest.TestCase):
    def test_gesperrte_werden_entfernt_und_gezaehlt(self):
        rows = [{"kennung": "email:a@x.de", "quelle": "marketing:unsubscribe", "grund": ""}]
        with mock.patch.object(_db, "query_via_docker", return_value=rows):
            erlaubt, gesperrt = sl.filtere_kandidaten(
                [{"email": "a@x.de"}, {"email": "B@x.de"}, {"email": "kaputt"}])
        self.assertEqual([c["email"] for c in erlaubt], ["B@x.de", "kaputt"])
        self.assertEqual(gesperrt, 1)


if __name__ == "__main__":
    unittest.main()
