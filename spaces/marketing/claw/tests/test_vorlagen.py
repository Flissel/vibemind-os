"""Layout-Vorlagen: Daten statt Code, und ein Tor davor.

Auftrag des Betreibers (12.09.2026): „die verschiedenen Layouts via Templates
und Skills ... Template muss vom User abgesegnet werden."

Was diese Tests festhalten:

  * GESETZT WIRD NUR, WAS FREIGEGEBEN IST. Ein Vorschlag wird abgewiesen -
    mit Begruendung, und ohne stillschweigend durch ein anderes Layout
    ersetzt zu werden. Ein Kunde soll nie etwas sehen, das niemand
    abgenommen hat.
  * DAS TOR HAENGT AN BEIDEN TUEREN. `pdf_erstellen` und `pdf_aus_entwurf`
    sind zwei Eingaenge zum selben Setzer; ein Tor vor nur einem waere keins.
  * DER AGENT KANN NICHT FREIGEBEN. Es gibt hier kein Werkzeug dafuer, und
    das ist die Architektur, nicht Hoeflichkeit.
  * EINE UNVOLLSTAENDIGE GESTALT WIRFT. Sie faellt nicht auf die Vorgabe
    zurueck: ein Dokument, dem heimlich die halbe Farbtafel eines anderen
    Layouts untergeschoben wird, sieht falsch aus, ohne dass jemand erfaehrt
    warum.
"""
import json
import unittest
from unittest import mock

from spaces.marketing.claw import werkzeuge

GESTALT = {"grund": "#0f2422", "flaeche": "#1d3b39", "akzent": "#5eead4",
           "gold": "#fbbf24", "text": "#cfe3df", "text_hell": "#e9fbf6",
           "text_leise": "#8aa3a0", "handlung_text": "#0f2422"}


def _vorlagen(*zeilen):
    """Antwort der Liste-Route."""
    return (200, json.dumps({"success": True, "data": list(zeilen)}))


def _zeile(name, status, grund=""):
    return {"name": name, "status": status, "gestalt": GESTALT,
            "grund": grund, "beschreibung": "", "vorgeschlagen_von": "test",
            "entschieden_von": "", "entschieden_am": "", "muster_datei": ""}


class Rekorder:
    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.aufrufe = []

    def __call__(self, url, daten, kopfzeilen):
        self.aufrufe.append({"url": url, "daten": daten})
        return self.antworten.pop(0)


class DasTor(unittest.TestCase):

    def _mit(self, rekorder, fn, *a, **kw):
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510"}):
            return fn(*a, **kw)

    def test_freigegebene_vorlage_wird_gesetzt(self):
        r = Rekorder([_vorlagen(_zeile("dunkel", "freigegeben"))])
        with mock.patch.object(werkzeuge.ablage, "ablegen",
                               return_value={"ok": True, "daten": {"pfad": "/x.pdf"}}):
            out = self._mit(r, werkzeuge.pdf_erstellen, name="p", titel="T",
                            text="Ein Satz.", vorlage="dunkel")
        self.assertTrue(out["ok"], out)

    def test_vorschlag_wird_abgewiesen_mit_begruendung(self):
        """Der Kern des Auftrags."""
        r = Rekorder([_vorlagen(_zeile("hell", "vorschlag", "nie angesehen"),
                                _zeile("dunkel", "freigegeben"))])
        with mock.patch.object(werkzeuge.ablage, "ablegen") as abgelegt:
            out = self._mit(r, werkzeuge.pdf_erstellen, name="p", titel="T",
                            text="Ein Satz.", vorlage="hell")
        self.assertFalse(out["ok"])
        self.assertIn("vorschlag", out["fehler"])
        self.assertIn("ausschliesslich der Betreiber", out["fehler"])
        self.assertIn("nie angesehen", out["fehler"])
        abgelegt.assert_not_called()

    def test_abgelehnte_vorlage_traegt_ihren_grund_mit(self):
        r = Rekorder([_vorlagen(_zeile("rot", "abgelehnt", "zu schrill"))])
        out = self._mit(r, werkzeuge.pdf_erstellen, name="p", titel="T",
                        text="Ein Satz.", vorlage="rot")
        self.assertFalse(out["ok"])
        self.assertIn("zu schrill", out["fehler"])

    def test_unbekannte_vorlage_nennt_die_freigegebenen(self):
        """Eine Absage, die nicht sagt, was stattdessen ginge, kostet eine
        Runde."""
        r = Rekorder([_vorlagen(_zeile("dunkel", "freigegeben"),
                                _zeile("hell", "vorschlag"))])
        out = self._mit(r, werkzeuge.pdf_erstellen, name="p", titel="T",
                        text="Ein Satz.", vorlage="gibtsnicht")
        self.assertFalse(out["ok"])
        self.assertIn("dunkel", out["fehler"])
        self.assertNotIn("hell", out["fehler"])

    def test_das_tor_haengt_auch_an_pdf_aus_entwurf(self):
        """Zwei Eingaenge zum selben Setzer - ein Tor vor nur einem waere
        keins. Die Liste kommt VOR dem Entwurf, also greift es, bevor
        ueberhaupt gelesen wird."""
        r = Rekorder([
            (200, json.dumps({"success": True, "data": {
                "channel": "email", "draft_subject": "B",
                "draft_body_text": "T", "draft_channel_params": {}}})),
            _vorlagen(_zeile("hell", "vorschlag")),
        ])
        with mock.patch.object(werkzeuge.ablage, "ablegen") as abgelegt:
            out = self._mit(r, werkzeuge.pdf_aus_entwurf, "p-1", vorlage="hell")
        self.assertFalse(out["ok"])
        self.assertIn("vorschlag", out["fehler"])
        abgelegt.assert_not_called()

    def test_der_altname_layout_geht_durch_dasselbe_tor(self):
        """`layout=` bleibt fuer bestehende Aufrufe erhalten - aber nicht als
        Schlupfloch."""
        r = Rekorder([_vorlagen(_zeile("hell", "vorschlag"))])
        out = self._mit(r, werkzeuge.pdf_erstellen, name="p", titel="T",
                        text="Ein Satz.", layout="hell")
        self.assertFalse(out["ok"])
        self.assertIn("vorschlag", out["fehler"])


class Vorschlagen(unittest.TestCase):

    def test_gestalt_muss_ein_objekt_sein(self):
        r = Rekorder([])
        with mock.patch.object(werkzeuge, "_roh_anfrage", r):
            out = werkzeuge.vorlage_vorschlagen("x", "y", "keine Farben")
        self.assertFalse(out["ok"])
        self.assertIn("grund", out["fehler"])       # nennt die Schluessel
        self.assertEqual(r.aufrufe, [], "kein Netzgriff fuer offensichtlich Falsches")

    def test_absage_der_datenbank_kommt_flach_zurueck(self):
        """Wie bei versand_beauftragen: `ok: true` mit einem zweiten
        `ok: false` darin wird ueberlesen."""
        r = Rekorder([(200, json.dumps({"success": True, "data": {
            "ok": False, "grund": "es fehlen: gold"}}))])
        with mock.patch.object(werkzeuge, "_roh_anfrage", r), \
             mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510"}):
            out = werkzeuge.vorlage_vorschlagen("x-y", "y", GESTALT)
        self.assertFalse(out["ok"])
        self.assertEqual(out["fehler"], "es fehlen: gold")
        self.assertNotIn("daten", out)

    def test_erfolg_weist_auf_das_musterblatt_hin(self):
        """Eine Vorlage freizugeben, ohne sie gesehen zu haben, ist keine
        Freigabe - also muss der naechste Schritt in der Antwort stehen."""
        r = Rekorder([(200, json.dumps({"success": True, "data": {
            "ok": True, "id": "u-1", "neu": True, "grund": "angelegt"}}))])
        with mock.patch.object(werkzeuge, "_roh_anfrage", r), \
             mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510"}):
            out = werkzeuge.vorlage_vorschlagen("Warm-Sand", "y", GESTALT)
        self.assertTrue(out["ok"])
        self.assertEqual(out["daten"]["name"], "warm-sand")   # kleingeschrieben
        self.assertIn("vorlage_muster", out["daten"]["naechster_schritt"])


class KeinFreigabeWerkzeug(unittest.TestCase):

    def test_der_agent_hat_kein_werkzeug_zum_freigeben(self):
        """Das Tor ist Architektur, nicht Hoeflichkeit: es gibt hier nichts,
        womit ein Agent seine eigene Vorlage gueltig machen koennte."""
        from spaces.marketing.claw import server
        namen = {fn.__name__ for fn in server.WERKZEUGE}
        self.assertIn("vorlage_vorschlagen", namen)
        self.assertIn("vorlage_muster", namen)
        self.assertIn("vorlagen_auflisten", namen)
        for verboten in ("vorlage_freigeben", "vorlage_entscheiden",
                         "vorlage_genehmigen", "layout_entscheiden"):
            self.assertNotIn(verboten, namen)


class GestaltImSetzer(unittest.TestCase):
    """Import erst hier: `pdf` zieht reportlab (siehe test_stil.py)."""

    def setUp(self):
        from spaces.marketing.claw import pdf
        self.pdf = pdf

    def test_gestalt_schlaegt_den_eingebauten_namen(self):
        f = self.pdf._farben("dunkel", {**GESTALT, "grund": "#123456"})
        self.assertEqual(f["grund"], self.pdf.colors.HexColor("#123456"))

    def test_unvollstaendige_gestalt_wirft_statt_still_zurueckzufallen(self):
        unvollstaendig = {k: v for k, v in GESTALT.items() if k != "gold"}
        with self.assertRaises(ValueError) as fehler:
            self.pdf._farben("dunkel", unvollstaendig)
        self.assertIn("gold", str(fehler.exception))

    def test_ohne_gestalt_bleibt_die_eingebaute_tafel(self):
        """Der Setzer muss ohne Datenbank lauffaehig bleiben - sonst ist er
        nicht testbar."""
        self.assertEqual(self.pdf._farben("dunkel")["grund"],
                         self.pdf.colors.HexColor(self.pdf.LAYOUTS["dunkel"]["grund"]))

    def test_die_schluessel_decken_sich_mit_der_eingebauten_tafel(self):
        """Sie stehen auch in marketing.gestalt_pruefen (Migration 044).
        Laufen die auseinander, gibt die Datenbank eine Vorlage frei, die der
        Setzer nicht setzen kann."""
        self.assertEqual(set(self.pdf.GESTALT_SCHLUESSEL),
                         set(self.pdf.LAYOUTS["dunkel"]))


if __name__ == "__main__":
    unittest.main()
