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


class TestVorschlagUndLesen(unittest.TestCase):
    def test_publikum_vorschlagen_trifft_proposals_tuer(self):
        rekorder = Rekorder([(200, json.dumps({"success": True, "proposal_id": "ap-1"}))])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510",
                                            "MARKETING_PROPOSAL_API_KEY": "pk1"}):
            r = werkzeuge.publikum_vorschlagen("KMU Nord", {"tag": "kmu"}, "Testlauf")
        self.assertTrue(r["ok"], r)
        self.assertEqual(rekorder.aufrufe[0]["url"], "http://x:5510/api/proposals")
        gesendet = json.loads(rekorder.aufrufe[0]["daten"])
        self.assertEqual(gesendet["name"], "KMU Nord")
        self.assertEqual(gesendet["filter_dsl"], {"tag": "kmu"})
        self.assertEqual(gesendet["api_key"], "pk1")

    def test_publikum_vorschlagen_verlangt_objekt(self):
        r = werkzeuge.publikum_vorschlagen("X", "kein-objekt")
        self.assertFalse(r["ok"])

    def test_posteingang_und_kampagnen_sind_gets(self):
        rekorder = Rekorder([(200, "[]"), (200, "[]")])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510"}):
            self.assertTrue(werkzeuge.posteingang_lesen()["ok"])
            self.assertTrue(werkzeuge.kampagnen_auflisten()["ok"])
        self.assertEqual([a["url"] for a in rekorder.aufrufe],
                         ["http://x:5510/api/inbox", "http://x:5510/api/campaigns"])
        self.assertTrue(all(a["daten"] is None for a in rekorder.aufrufe))


class TestPdfOhneReportlab(unittest.TestCase):
    """Eine fehlende Bibliothek darf EIN Werkzeug kosten, nicht alle.

    Gemessen 04.09.2026: ein `import pdf` am Modulkopf von `werkzeuge` riss
    unter dem pyenv-Python neun Testmodule mit — reportlab liegt nur in
    `.venv`. Seitdem wird `pdf` erst im Aufruf geladen.
    """

    def test_modulkopf_zieht_reportlab_nicht_nach(self):
        import ast
        import pathlib
        quelle = pathlib.Path(werkzeuge.__file__).read_text(encoding="utf-8")
        baum = ast.parse(quelle)
        oben = []
        for knoten in baum.body:  # nur die oberste Ebene
            if isinstance(knoten, ast.ImportFrom) and knoten.module:
                oben += [a.name for a in knoten.names]
            elif isinstance(knoten, ast.Import):
                oben += [a.name for a in knoten.names]
        self.assertNotIn("pdf", oben,
                         "pdf gehoert in den Funktionsrumpf, nicht an den Modulkopf")

    def test_fehlende_bibliothek_meldet_sich_verstaendlich(self):
        """Zwei Stellen muessen weg, sonst haengt der Test an der Reihenfolge.

        `from paket import modul` fragt ZUERST das Paket-Attribut ab und erst
        dann sys.modules. Hat ein frueher gelaufener Test (test_pdf.py) das
        Modul schon geladen, steht es als Attribut am Paket und ein blosses
        `sys.modules[...] = None` wird umgangen — der Test lief allein gruen
        und in der Suite rot. Beides entfernen, danach beides zurueck.
        """
        import sys
        import spaces.marketing.claw as paket

        gemerkt_modul = sys.modules.pop("spaces.marketing.claw.pdf", None)
        hatte_attribut = hasattr(paket, "pdf")
        gemerktes_attribut = getattr(paket, "pdf", None)
        if hatte_attribut:
            delattr(paket, "pdf")
        sys.modules["spaces.marketing.claw.pdf"] = None
        try:
            r = werkzeuge.pdf_erstellen("a", "T", "Text")
        finally:
            sys.modules.pop("spaces.marketing.claw.pdf", None)
            if gemerkt_modul is not None:
                sys.modules["spaces.marketing.claw.pdf"] = gemerkt_modul
            if hatte_attribut:
                setattr(paket, "pdf", gemerktes_attribut)

        self.assertFalse(r["ok"], r)
        self.assertIn("reportlab", r["fehler"])
        # Und der Rest lebt weiter — das ist der eigentliche Punkt.
        self.assertTrue(callable(werkzeuge.wissen_fragen))


class TestKanalPruefung(unittest.TestCase):
    """Ein Entwurf auf einem Kanal ohne Versandweg ist eine Falle.

    Gemessen 04.09. und noch einmal 12.09.2026 an `/api/channels`: `email`
    und `telegram` stehen auf `enabled: true, send_implemented: true`,
    `whatsapp` auf `false/false`. `kampagne_entwerfen` reichte den Kanal
    trotzdem durch — der Entwurf landete in broadcast_proposals, sah fertig
    aus und konnte nie rausgehen. Schlimmer noch: die Fertigkeit
    `whatsapp-nachricht` schickte den Agenten genau dorthin.

    WhatsApp GEHT in diesem Haus — nur nicht hier. sales-claw verschickt es
    ueber openwa (`dispatch.py`, Container laeuft), per KONTAKT statt per
    Rundnachricht, mit eigener Einwilligungspruefung. Der Fehlertext muss
    dorthin zeigen, sonst sucht der Agent an der falschen Stelle weiter.
    """

    def _kanaele(self, whatsapp_an=False):
        return (200, json.dumps({"success": True, "data": [
            {"channel": "email", "enabled": True, "send_implemented": True},
            {"channel": "telegram", "enabled": True, "send_implemented": True},
            {"channel": "whatsapp", "enabled": whatsapp_an,
             "send_implemented": whatsapp_an},
            {"channel": "linkedin", "enabled": False, "send_implemented": False},
        ]}))

    def test_kanal_ohne_versandweg_wird_abgelehnt(self):
        rekorder = Rekorder([self._kanaele()])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510"}):
            r = werkzeuge.kampagne_entwerfen("Ziel", "Zielgruppe", "whatsapp")
        self.assertFalse(r["ok"], r)
        self.assertEqual(len(rekorder.aufrufe), 1,
                         "nach der Absage darf nichts mehr geschrieben werden")

    def test_die_absage_nennt_den_weg_der_funktioniert(self):
        rekorder = Rekorder([self._kanaele()])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510"}):
            r = werkzeuge.kampagne_entwerfen("Ziel", "Zielgruppe", "whatsapp")
        self.assertIn("sales-claw", r["fehler"])
        self.assertIn("email", r["fehler"])
        self.assertIn("telegram", r["fehler"])

    def test_ein_kanal_mit_versandweg_geht_durch(self):
        rekorder = Rekorder([
            self._kanaele(),
            (200, json.dumps({"betreff": "B", "text": "T", "begruendung": "G"})),
            (200, json.dumps({"success": True, "data": {"id": "p1"}})),
        ])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.object(werkzeuge, "_llm_json",
                               return_value={"ok": True, "daten": {
                                   "betreff": "B", "text": "T", "begruendung": "G"}}), \
             mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510"}):
            r = werkzeuge.kampagne_entwerfen("Ziel", "Zielgruppe", "telegram")
        self.assertTrue(r["ok"], r)

    def test_whatsapp_geht_durch_sobald_der_versand_gebaut_ist(self):
        """Die Pruefung fragt den Dienst, sie haelt keine Liste im Kopf."""
        rekorder = Rekorder([
            self._kanaele(whatsapp_an=True),
            (200, json.dumps({"success": True, "data": {"id": "p1"}})),
        ])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.object(werkzeuge, "_llm_json",
                               return_value={"ok": True, "daten": {
                                   "betreff": "B", "text": "T", "begruendung": "G"}}), \
             mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510"}):
            r = werkzeuge.kampagne_entwerfen("Ziel", "Zielgruppe", "whatsapp")
        self.assertTrue(r["ok"], r)

    def test_unerreichbare_kanalliste_blockiert_nicht(self):
        """Ein Ausfall der Auskunft darf die Arbeit nicht anhalten — sonst
        haengt der Entwurf an der Verfuegbarkeit einer Nebenroute."""
        antworten = [(500, "kaputt"),
                     (200, json.dumps({"success": True, "data": {"id": "p1"}}))]
        rekorder = Rekorder(antworten)
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.object(werkzeuge, "_llm_json",
                               return_value={"ok": True, "daten": {
                                   "betreff": "B", "text": "T", "begruendung": "G"}}), \
             mock.patch.dict("os.environ", {"MARKETING_API_URL": "http://x:5510"}):
            r = werkzeuge.kampagne_entwerfen("Ziel", "Zielgruppe", "telegram")
        self.assertTrue(r["ok"], r)


if __name__ == "__main__":
    unittest.main()
