"""Inhalt als DATEN, nicht als fertige Darstellung.

DER BEFUND (04.09.2026, an den 17 echten Zeilen gemessen): der Kampagnentext
steht richtig in `broadcast_proposals.draft_body_text` — Belege und
"Zu klaeren" aber als fertig gerendertes HTML in `draft_body_html`, einer
Spalte, die eigentlich der HTML-Rumpf der Nachricht ist. Daten in
Darstellung gebacken. Genau deshalb liess sich Layout und Fuellung nicht
trennen: wer das Aussehen aendern wollte, musste den Text neu erzeugen
lassen.

`draft_channel_params` ist jsonb, steht auf ALLEN Zeilen auf `{}`, wird von
der Schreibroute entgegengenommen und von der Leseroute zurueckgegeben. Das
Schema hatte den Platz die ganze Zeit; er war nur nie belegt.

Ab hier gilt: `kampagne_entwerfen` schreibt Struktur, `pdf_erstellen` liest
sie und setzt sie. Ein anderes Layout ist ein Aufruf, kein neuer Entwurf.
"""
import importlib.util
import json
import unittest
from unittest import mock

# Nur die PDF-Klasse unten braucht reportlab (sie patcht `pdf.bauen`, was das
# Modul laedt). Der Sidecar laeuft unter `.venv`, wo es liegt; wer die Suite
# unter pyenv faehrt, soll die Struktur-Tests trotzdem bekommen.
OHNE_REPORTLAB = importlib.util.find_spec("reportlab") is None

from spaces.marketing.claw import werkzeuge
from spaces.marketing.claw.tests.test_werkzeuge import Rekorder


def _entwurf_antwort():
    return (200, json.dumps({"success": True, "data": {"id": "prop-1",
                                                       "status": "draft"}}))


class TestStrukturWirdGeschrieben(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict("os.environ", {
            "MARKETING_API_URL": "http://x:5510", "MARKETING_API_KEY": "k1",
            "MARKETING_PROPOSAL_API_KEY": "p1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.llm = mock.patch.object(
            werkzeuge, "_llm_json",
            return_value={"ok": True, "daten": {"betreff": "B", "text": "T",
                                                "begruendung": "G"}})
        self.llm.start()
        self.addCleanup(self.llm.stop)
        self.schau = mock.patch.object(werkzeuge.schaufenster, "ablegen",
                                       return_value="C:/x/briefing.md")
        self.schau.start()
        self.addCleanup(self.schau.stop)

    def _senden(self, **kw):
        rekorder = Rekorder([_entwurf_antwort()])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder):
            ergebnis = werkzeuge.kampagne_entwerfen(
                "Early Access", "Solo-Gruender", "telegram", **kw)
        gesendet = json.loads(rekorder.aufrufe[0]["daten"])
        return ergebnis, gesendet

    def test_belege_stehen_als_liste_in_den_parametern(self):
        _, gesendet = self._senden(belege=["Quelle / Dok: Aussage"],
                                   zu_klaeren=["Preis unklar"])
        p = gesendet["draft_channel_params"]
        self.assertEqual(p["belege"], ["Quelle / Dok: Aussage"])
        self.assertEqual(p["zu_klaeren"], ["Preis unklar"])

    def test_ziel_und_zielgruppe_reisen_mit(self):
        """Ohne sie kann ein spaeteres Layout keine Ueberschrift bauen."""
        _, gesendet = self._senden()
        p = gesendet["draft_channel_params"]
        self.assertEqual(p["ziel"], "Early Access")
        self.assertEqual(p["zielgruppe"], "Solo-Gruender")

    def test_belege_nicht_mehr_als_html_in_den_rumpf(self):
        """Der eigentliche Fehler: Metadaten in der Spalte fuer den
        Nachrichtenrumpf. Wer dort spaeter den Versand anschliesst,
        verschickt die internen Notizen mit."""
        _, gesendet = self._senden(belege=["Geheime interne Quelle"])
        self.assertNotIn("Geheime interne Quelle",
                         gesendet.get("draft_body_html") or "")

    def test_ohne_belege_bleibt_die_liste_leer_statt_zu_fehlen(self):
        _, gesendet = self._senden()
        p = gesendet["draft_channel_params"]
        self.assertEqual(p["belege"], [])
        self.assertEqual(p["zu_klaeren"], [])

    def test_begruendung_bleibt_erhalten(self):
        _, gesendet = self._senden()
        self.assertEqual(gesendet["draft_channel_params"]["begruendung"], "G")


@unittest.skipIf(OHNE_REPORTLAB,
                 "reportlab fehlt in diesem Interpreter — der Sidecar laeuft "
                 "unter .venv/Scripts/python.exe")
class TestPdfAusDemEntwurf(unittest.TestCase):
    """`pdf_aus_entwurf` bekommt eine Kennung, keinen Inhalt."""

    def setUp(self):
        self.env = mock.patch.dict("os.environ", {
            "MARKETING_API_URL": "http://x:5510", "MARKETING_API_KEY": "k1",
            "MARKETING_PROPOSAL_API_KEY": "p1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _entwurf(self):
        return (200, json.dumps({"success": True, "data": {
            "id": "prop-1", "channel": "email",
            "draft_subject": "Du entscheidest, was rausgeht",
            "draft_body_text": "Erster Absatz.\n\nZweiter Absatz.",
            "draft_channel_params": {
                "ziel": "Early Access", "zielgruppe": "Solo-Gruender",
                "belege": ["Quelle / Dok: Aussage"],
                "zu_klaeren": ["Adresse fehlt"],
                "handlung": "Jetzt eintragen: vibemind.space/early"}}}))

    def test_holt_den_inhalt_aus_der_datenbank(self):
        gesehen = {}
        rekorder = Rekorder([self._entwurf()])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.object(werkzeuge.ablage, "ablegen",
                               return_value={"ok": True, "daten": {"pfad": "/x/a.pdf"}}), \
             mock.patch("spaces.marketing.claw.pdf.bauen",
                        side_effect=lambda **kw: gesehen.update(kw) or b"%PDF-x"):
            ergebnis = werkzeuge.pdf_aus_entwurf("prop-1")
        self.assertTrue(ergebnis["ok"], ergebnis)
        self.assertEqual(gesehen["titel"], "Du entscheidest, was rausgeht")
        self.assertIn("Zweiter Absatz", gesehen["text"])
        self.assertEqual(gesehen["belege"], ["Quelle / Dok: Aussage"])
        self.assertEqual(gesehen["zu_klaeren"], ["Adresse fehlt"])
        self.assertIn("vibemind.space/early", gesehen["handlung"])

    def test_der_kanal_bestimmt_den_zweck(self):
        rekorder = Rekorder([self._entwurf()])
        gesehen = {}
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.object(werkzeuge.ablage, "ablegen",
                               side_effect=lambda n, i, **kw: gesehen.update(
                                   name=n, ersetzen=kw.get("ersetzen")) or
                               {"ok": True, "daten": {"pfad": "/x/" + n}}), \
             mock.patch("spaces.marketing.claw.pdf.bauen", return_value=b"%PDF-x"):
            werkzeuge.pdf_aus_entwurf("prop-1")
        self.assertTrue(gesehen["name"].startswith("email-"), gesehen["name"])
        # Neu setzen heisst ersetzen: derselbe Entwurf, ein anderes Layout —
        # da will niemand zehn Fassungen im Medienordner.
        self.assertTrue(gesehen["ersetzen"])

    def test_unbekannte_kennung_ist_fail_soft(self):
        rekorder = Rekorder([(404, json.dumps({"success": False,
                                               "message": "no such proposal"}))])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder):
            ergebnis = werkzeuge.pdf_aus_entwurf("gibtsnicht")
        self.assertFalse(ergebnis["ok"])

    def test_leere_kennung_fragt_gar_nicht_erst(self):
        rekorder = Rekorder([])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder):
            self.assertFalse(werkzeuge.pdf_aus_entwurf("  ")["ok"])
        self.assertEqual(rekorder.aufrufe, [])

    def test_die_gestalt_der_vorlage_erreicht_den_setzer(self):
        """Der ganze Punkt: anderes Aussehen, gleicher Inhalt, ein Aufruf.

        SEIT DEM 12.09.2026 REICHT NICHT MEHR DER NAME DURCH, SONDERN DIE
        GESTALT. Vorher stand hier `gesehen["layout"] == "hell"` - der Setzer
        kannte die zwei Tafeln selbst. Jetzt kommen Layouts aus Vorlagen, und
        `pdf_aus_entwurf` loest sie auf, BEVOR es setzt. Das ist die
        staerkere Zusicherung: sie prueft, dass die Farben der GEWUENSCHTEN
        Vorlage ankommen, nicht nur ihr Name.
        """
        gesehen = {}
        eigen = {"success": True, "data": [
            {"name": "probe", "status": "freigegeben", "grund": "",
             "gestalt": dict(Rekorder.GESTALT_DUNKEL, grund="#abcdef")}]}
        rekorder = Rekorder([self._entwurf()], vorlagen=eigen)
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder),              mock.patch.object(werkzeuge.ablage, "ablegen",
                               return_value={"ok": True, "daten": {"pfad": "/x/a.pdf"}}),              mock.patch("spaces.marketing.claw.pdf.bauen",
                        side_effect=lambda **kw: gesehen.update(kw) or b"%PDF-x"):
            ergebnis = werkzeuge.pdf_aus_entwurf("prop-1", vorlage="probe")
        self.assertTrue(ergebnis["ok"], ergebnis)
        self.assertEqual(gesehen["gestalt"]["grund"], "#abcdef")
        self.assertNotIn("layout", gesehen,
                         "der Setzer bekommt die Gestalt, nicht mehr den Namen")

    def test_alter_entwurf_ohne_parameter_geht_trotzdem(self):
        """Die 17 Zeilen von vorher haben `{}` — sie duerfen nicht scheitern."""
        alt = (200, json.dumps({"success": True, "data": {
            "id": "prop-alt", "channel": "telegram",
            "draft_subject": "Alt", "draft_body_text": "Text",
            "draft_channel_params": {}}}))
        gesehen = {}
        with mock.patch.object(werkzeuge, "_roh_anfrage", Rekorder([alt])), \
             mock.patch.object(werkzeuge.ablage, "ablegen",
                               return_value={"ok": True, "daten": {"pfad": "/x/a.pdf"}}), \
             mock.patch("spaces.marketing.claw.pdf.bauen",
                        side_effect=lambda **kw: gesehen.update(kw) or b"%PDF-x"):
            ergebnis = werkzeuge.pdf_aus_entwurf("prop-alt")
        self.assertTrue(ergebnis["ok"], ergebnis)
        self.assertEqual(gesehen["belege"], [])


if __name__ == "__main__":
    unittest.main()
