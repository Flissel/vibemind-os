"""Der einzige Weg nach draussen — Betreiber-Entscheid 12.09.2026.

Dieser Space versendet nichts mehr selbst. `versand_beauftragen` legt einen
Auftrag, sales-claw macht daraus hoechstens einen Entwurf. Was diese Tests
festhalten:

  * Eine Absage kommt FLACH zurueck (`ok: false` mit dem Grund), nicht als
    negatives Ergebnis in einer erfolgreichen Huelle. `ok: true` mit einem
    zweiten `ok: false` darin wird ueberlesen — und eine ueberlesene Absage
    ist hier der teuerste Fehler.
  * Die Pruefung selbst wird NICHT nachgebaut. Kanal, Verbotsliste und
    Wiederholung entscheidet die Datenbankfunktion; dieses Werkzeug reicht
    durch. Ein zweiter Prueforte waere genau die Doppelung, die der
    Entscheid abschafft.
"""
import json
import unittest
from unittest import mock

from spaces.marketing.claw import werkzeuge


class Rekorder:
    """Ersetzt den einzigen echten Netzgriff und gibt vorbereitete Antworten."""

    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.aufrufe = []

    def __call__(self, url, daten, kopfzeilen):
        self.aufrufe.append({"url": url, "daten": daten, "kopf": kopfzeilen})
        return self.antworten.pop(0)


def _antwort(ergebnis):
    return (200, json.dumps({"success": True, "message": "versandauftrag",
                             "data": ergebnis}))


class VersandBeauftragen(unittest.TestCase):

    def _lauf(self, rekorder, **kw):
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510"}):
            return werkzeuge.versand_beauftragen(**kw)

    def test_leere_nachricht_fragt_gar_nicht_erst(self):
        rekorder = Rekorder([])
        r = self._lauf(rekorder, kanal="email", nachricht="   ",
                       empfaenger="a@b.de")
        self.assertFalse(r["ok"])
        self.assertEqual(rekorder.aufrufe, [],
                         "eine leere Nachricht darf keinen Netzgriff kosten")

    def test_auftrag_geht_vollstaendig_an_die_richtige_route(self):
        rekorder = Rekorder([_antwort({"ok": True, "id": "a-1",
                                       "wiederholung": False, "grund": "liegt bereit"})])
        r = self._lauf(rekorder, kanal="email", nachricht="Hallo",
                       empfaenger="Anna@Firma.de", betreff="Betreff",
                       medien_datei="post.pdf", kampagne="Herbst",
                       quelle="broadcast_proposal:x")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["daten"]["auftrag_id"], "a-1")
        self.assertFalse(r["daten"]["wiederholung"])

        aufruf = rekorder.aufrufe[0]
        self.assertTrue(aufruf["url"].endswith("/api/versandauftraege"))
        gesendet = json.loads(aufruf["daten"].decode("utf-8"))
        self.assertEqual(gesendet, {
            "kanal": "email", "empfaenger": "Anna@Firma.de",
            "nachricht": "Hallo", "betreff": "Betreff",
            "medien_datei": "post.pdf", "kampagne": "Herbst",
            "quelle": "broadcast_proposal:x"})

    def test_absage_kommt_flach_zurueck_nicht_verschachtelt(self):
        grund = ("Dieser Empfaenger steht auf der gemeinsamen Verbotsliste "
                 "(marketing:unsubscribe: abgemeldet 2026-08-01).")
        rekorder = Rekorder([_antwort({"ok": False, "grund": grund})])
        r = self._lauf(rekorder, kanal="email", nachricht="Hallo",
                       empfaenger="a@b.de")
        self.assertFalse(r["ok"])
        self.assertEqual(r["fehler"], grund)
        self.assertNotIn("daten", r,
                         "eine Absage darf nicht wie ein Erfolg aussehen")

    def test_wiederholung_wird_als_solche_gemeldet(self):
        rekorder = Rekorder([_antwort({"ok": True, "id": "a-1", "wiederholung": True,
                                       "grund": "Derselbe Auftrag besteht schon"})])
        r = self._lauf(rekorder, kanal="email", nachricht="Hallo",
                       empfaenger="a@b.de")
        self.assertTrue(r["ok"])
        self.assertTrue(r["daten"]["wiederholung"])
        self.assertEqual(r["daten"]["auftrag_id"], "a-1")

    def test_unerwartete_antwort_wird_nicht_als_erfolg_gelesen(self):
        """Lieber „ich weiss es nicht" als ein stiller falscher Erfolg: wer
        hier faelschlich `ok` liest, schickt denselben Auftrag noch einmal."""
        rekorder = Rekorder([(200, json.dumps({"success": True, "data": "hae"}))])
        r = self._lauf(rekorder, kanal="email", nachricht="Hallo",
                       empfaenger="a@b.de")
        self.assertFalse(r["ok"])
        self.assertIn("versandauftraege_lesen", r["fehler"])

    def test_unerreichbare_api_bleibt_ein_fehler_mit_grund(self):
        rekorder = Rekorder([(500, "kaputt")])
        r = self._lauf(rekorder, kanal="email", nachricht="Hallo",
                       empfaenger="a@b.de")
        self.assertFalse(r["ok"])
        self.assertIn("500", r["fehler"])

    def test_kanal_wird_hier_nicht_nachgebaut(self):
        """Telegram weist die Datenbankfunktion ab, nicht dieses Werkzeug.
        Das ist Absicht: eine zweite Kanalliste im Agenten liefe der ersten
        davon."""
        rekorder = Rekorder([_antwort({"ok": False, "grund": "Unzulaessiger Kanal"})])
        r = self._lauf(rekorder, kanal="telegram", nachricht="Hallo",
                       empfaenger="a@b.de")
        self.assertFalse(r["ok"])
        self.assertEqual(len(rekorder.aufrufe), 1,
                         "die Entscheidung faellt drueben, also wird gefragt")


class VersandauftraegeLesen(unittest.TestCase):

    def test_liste_kommt_flach_und_filtert_ueber_die_route(self):
        zeilen = [{"id": "a-1", "status": "abgelehnt",
                   "grund": "Kein Kontakt in sales-claw"}]
        rekorder = Rekorder([(200, json.dumps({"success": True, "data": zeilen}))])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510"}):
            r = werkzeuge.versandauftraege_lesen(status="abgelehnt", anzahl=5)
        self.assertTrue(r["ok"])
        self.assertEqual(r["daten"], zeilen)
        url = rekorder.aufrufe[0]["url"]
        self.assertIn("limit=5", url)
        self.assertIn("status=abgelehnt", url)
        self.assertIsNone(rekorder.aufrufe[0]["daten"], "Lesen ist ein GET")


class Registrierung(unittest.TestCase):

    def test_beide_werkzeuge_sind_am_sidecar_angemeldet(self):
        from spaces.marketing.claw import server
        namen = {fn.__name__ for fn in server.WERKZEUGE}
        self.assertIn("versand_beauftragen", namen)
        self.assertIn("versandauftraege_lesen", namen)


if __name__ == "__main__":
    unittest.main()
