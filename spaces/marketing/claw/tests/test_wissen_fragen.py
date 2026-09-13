"""wissen_fragen: die ganze Wissensbasis befragen statt eine Quelle lesen.

WARUM NICHT UEBER ROWBOATS CHAT-ROUTE. Der Plan sah `POST /api/v1/{id}/chat`
vor. Am 04.09.2026 gemessen: die Route antwortet mit HTTP 200, der Turn geht
aber an den Agenten `memory_responder`, der die Wissensquellen NICHT liest —
die Antwort auf eine Produktfrage lautete woertlich, er kenne den Begriff
nicht. Rowboats MCP hat ausserdem gar keine Suche, nur Auflisten und Lesen
(6 Werkzeuge, gemessen). Also sammelt der Sidecar selbst, waehlt aus und
laesst das Modell antworten.

DIE QUELLENLISTE IST GRUNDWAHRHEIT, KEINE BEHAUPTUNG DES MODELLS: sie nennt
die Dokumente, die tatsaechlich im Prompt standen. Ein Modell, das eine
Quelle erfindet, kann diese Liste nicht faelschen.
"""
import json
import unittest
from unittest import mock

from spaces.marketing.claw import llm, werkzeuge, wissen
from spaces.marketing.claw.tests.test_werkzeuge import Rekorder


def _mcp_ok(daten):
    return (200, json.dumps({"jsonrpc": "2.0", "id": 1, "result": {
        "content": [{"type": "text", "text": json.dumps(daten)}],
        "isError": False}}))


def _basis():
    """Eine Mini-Wissensbasis: zwei Quellen, drei Dokumente."""
    return [
        _mcp_ok([{"id": "s1", "name": "VibeMind Knowledge"},
                 {"id": "s2", "name": "Bubbles/Index"}]),
        _mcp_ok([{"id": "d1", "name": "Ideaspace.md",
                  "content": "Der Ideaspace sammelt Ideen als Bubbles."},
                 {"id": "d2", "name": "Preise.md",
                  "content": "Early Access kostet nichts."}]),
        _mcp_ok([{"id": "d3", "name": "Klassifizierung.md",
                  "content": "Ideen bekommen eine Kategorie."}]),
    ]


class TestAuswahl(unittest.TestCase):
    """Die Auswahl ist rein — kein Netz, kein Modell, kein Zufall."""

    def setUp(self):
        self.stuecke = [
            {"quelle": "A", "dokument": "Ideaspace.md", "text": "Bubbles sammeln Ideen."},
            {"quelle": "A", "dokument": "Preise.md", "text": "Early Access ist gratis."},
            {"quelle": "B", "dokument": "Fremd.md", "text": "Voellig anderes Thema."},
        ]

    def test_treffer_stehen_vorn(self):
        gewaehlt = wissen.auswaehlen("Was ist der Ideaspace?", self.stuecke, budget=10_000)
        self.assertEqual(gewaehlt[0]["dokument"], "Ideaspace.md")

    def test_ohne_bezug_faellt_weg(self):
        gewaehlt = wissen.auswaehlen("Ideaspace", self.stuecke, budget=10_000)
        self.assertNotIn("Fremd.md", [s["dokument"] for s in gewaehlt])

    def test_budget_wird_eingehalten(self):
        gewaehlt = wissen.auswaehlen("Ideaspace Early Access", self.stuecke, budget=30)
        self.assertLessEqual(sum(len(s["text"]) for s in gewaehlt), 30)
        self.assertTrue(gewaehlt, "das beste Stueck muss trotz Budget durchkommen")

    def test_der_dokumentname_zaehlt_mit(self):
        """Ein Name wie 'Preise.md' ist ein Treffer, auch ohne das Wort im Text."""
        gewaehlt = wissen.auswaehlen("Preise", self.stuecke, budget=10_000)
        self.assertEqual(gewaehlt[0]["dokument"], "Preise.md")

    def test_kurze_woerter_erzeugen_keine_scheintreffer(self):
        self.assertEqual(wissen.auswaehlen("ist der was", self.stuecke, budget=10_000), [])

    def test_umlaute_und_grossschreibung_stoeren_nicht(self):
        stuecke = [{"quelle": "A", "dokument": "Konto.md", "text": "Guenstige Konditionen."}]
        self.assertTrue(wissen.auswaehlen("KONDITIONEN", stuecke, budget=10_000))


class TestWissenFragen(unittest.TestCase):
    def setUp(self):
        werkzeuge._wissen_zwischenspeicher.clear()
        self.addCleanup(werkzeuge._wissen_zwischenspeicher.clear)
        self.env = mock.patch.dict("os.environ", {
            "ROWBOAT_URL": "http://vm:3100",
            "ROWBOAT_PROJECT_ID": "proj-1",
            "ROWBOAT_API_KEY": "geheim-999",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_antwort_und_quellen(self):
        rekorder = Rekorder(_basis())
        antwort = {"ok": True, "text": "Der Ideaspace sammelt Ideen."}
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.object(llm, "frage", return_value=antwort):
            ergebnis = werkzeuge.wissen_fragen("Was ist der Ideaspace?")
        self.assertTrue(ergebnis["ok"], ergebnis)
        self.assertIn("Ideaspace", ergebnis["daten"]["antwort"])
        self.assertIn("VibeMind Knowledge — Ideaspace.md", ergebnis["daten"]["quellen"])

    def test_quellen_sind_grundwahrheit_nicht_modellbehauptung(self):
        rekorder = Rekorder(_basis())
        gelogen = {"ok": True, "text": "Laut Quelle 'Geheimakte.pdf' kostet es 500 Euro."}
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.object(llm, "frage", return_value=gelogen):
            ergebnis = werkzeuge.wissen_fragen("Ideaspace")
        self.assertNotIn("Geheimakte.pdf", ergebnis["daten"]["quellen"])

    def test_belegpflicht_steht_im_auftrag(self):
        rekorder = Rekorder(_basis())
        gesehen = {}
        def merken(system, nutzer):
            gesehen["system"] = system
            gesehen["nutzer"] = nutzer
            return {"ok": True, "text": "x"}
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.object(llm, "frage", merken):
            werkzeuge.wissen_fragen("Ideaspace")
        self.assertIn("Zu klaeren", gesehen["system"])
        self.assertIn("Ideaspace.md", gesehen["nutzer"])

    def test_ohne_treffer_ehrlich_statt_erfunden(self):
        rekorder = Rekorder(_basis())
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.object(llm, "frage") as modell:
            ergebnis = werkzeuge.wissen_fragen("Quantencomputer Zertifizierung")
        self.assertTrue(ergebnis["ok"], ergebnis)
        self.assertEqual(ergebnis["daten"]["quellen"], [])
        self.assertIn("nichts", ergebnis["daten"]["antwort"].lower())
        modell.assert_not_called()  # kein Modellaufruf ohne Rohstoff

    def test_zweite_frage_holt_die_basis_nicht_neu(self):
        rekorder = Rekorder(_basis())
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.object(llm, "frage", return_value={"ok": True, "text": "x"}):
            werkzeuge.wissen_fragen("Ideaspace")
            werkzeuge.wissen_fragen("Early Access")
        self.assertEqual(len(rekorder.aufrufe), 3, "3 Abrufe: 1 Quellenliste + 2 Quellen")

    def test_fail_soft_wenn_rowboat_aus_ist(self):
        def kaputt(url, daten, kopfzeilen):
            raise OSError("Connection refused")
        with mock.patch.object(werkzeuge, "_roh_anfrage", kaputt):
            ergebnis = werkzeuge.wissen_fragen("egal")
        self.assertFalse(ergebnis["ok"])

    def test_leere_frage_fragt_gar_nicht_erst(self):
        rekorder = Rekorder([])
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder):
            ergebnis = werkzeuge.wissen_fragen("   ")
        self.assertFalse(ergebnis["ok"])
        self.assertEqual(rekorder.aufrufe, [])

    def test_totes_modell_kippt_nicht_die_quellen(self):
        rekorder = Rekorder(_basis())
        with mock.patch.object(werkzeuge, "_roh_anfrage", rekorder), \
             mock.patch.object(llm, "frage",
                               return_value={"ok": False, "fehler": "Shim nicht nutzbar"}):
            ergebnis = werkzeuge.wissen_fragen("Ideaspace")
        self.assertFalse(ergebnis["ok"])
        self.assertIn("Ideaspace.md", json.dumps(ergebnis, ensure_ascii=False))

    def test_kein_schluessel_im_fehlertext(self):
        def kaputt(url, daten, kopfzeilen):
            raise OSError("Bearer geheim-999 abgelehnt")
        with mock.patch.object(werkzeuge, "_roh_anfrage", kaputt):
            ergebnis = werkzeuge.wissen_fragen("egal")
        self.assertNotIn("geheim-999", json.dumps(ergebnis))


class TestAllerweltsbegriffe(unittest.TestCase):
    """Ein Wort, das fast ueberall steht, unterscheidet nichts.

    Gemessen am 04.09.2026: die Frage "Was ist der Ideaspace in VibeMind?"
    zog acht Dokumente aus der Quelle "VibeMind - E-Ticketing_DE" herein —
    einem KUNDENPROJEKT ohne Bezug zum Ideaspace. Grund: alle vierzehn
    Quellen heissen "VibeMind ...", der Begriff traf also jede von ihnen und
    wog im Namen dreifach. Eine Kampagne haette sich auf Fremdmaterial
    berufen. Deshalb faellt raus, was fast ueberall vorkommt.
    """

    def _basis(self, n=10):
        stuecke = [{"quelle": "VibeMind - Projekt %d" % i, "dokument": "Doc%d.md" % i,
                    "text": "VibeMind Allgemeines ueber Rechnungen."} for i in range(n)]
        stuecke.append({"quelle": "VibeMind Knowledge", "dokument": "Ideaspace.md",
                        "text": "Der Ideaspace zeigt Bubbles in drei Dimensionen."})
        return stuecke

    def test_haeufiger_begriff_zieht_nicht_alles_herein(self):
        gewaehlt = wissen.auswaehlen("Ideaspace in VibeMind", self._basis(), budget=10_000)
        self.assertEqual([s["dokument"] for s in gewaehlt], ["Ideaspace.md"])

    def test_wenn_nur_haeufiges_gefragt_ist_wird_trotzdem_geantwortet(self):
        """Sonst waere 'Erzaehl mir von VibeMind' eine Fehlanzeige."""
        gewaehlt = wissen.auswaehlen("VibeMind", self._basis(), budget=10_000)
        self.assertTrue(gewaehlt, "alle Begriffe haeufig: dann eben die haeufigen nehmen")

    def test_in_kleiner_basis_bleibt_alles_erlaubt(self):
        """Bei drei Dokumenten ist 'kommt in zweien vor' kein Allerweltswort."""
        stuecke = [{"quelle": "A", "dokument": "Eins.md", "text": "Bubbles und Ideen."},
                   {"quelle": "A", "dokument": "Zwei.md", "text": "Bubbles und Kaffee."}]
        self.assertEqual(len(wissen.auswaehlen("Bubbles", stuecke, budget=10_000)), 2)


class TestZweiteRunde(unittest.TestCase):
    """Praezision zuerst — aber lieber ein grober Treffer als gar keiner.

    Gemessen 04.09.2026: mit dem Allerwelts-Filter allein fiel die Frage
    "Was ist der Ideaspace in VibeMind?" auf NULL Dokumente. "vibemind" war
    als Allerweltswort raus, "ideaspace" stand woertlich nirgends — die
    Dokumente schreiben "ideas.space". Erst gar nichts zu finden ist
    schlechter als zu viel zu finden.
    """

    def test_zweite_runde_wenn_die_praezise_nichts_findet(self):
        stuecke = [{"quelle": "VibeMind A", "dokument": "Doc%d.md" % i,
                    "text": "VibeMind macht Dinge."} for i in range(6)]
        gewaehlt = wissen.auswaehlen("Zauberstab VibeMind", stuecke, budget=10_000)
        self.assertTrue(gewaehlt, "wenn der genaue Begriff nichts trifft, zaehlt der grobe")

    def test_zusammengesetztes_wort_findet_seinen_bestandteil(self):
        stuecke = [{"quelle": "A", "dokument": "Spaces.md",
                    "text": "ideas.space sammelt Ideen als Bubbles."},
                   {"quelle": "A", "dokument": "Fremd.md", "text": "Etwas anderes."}]
        gewaehlt = wissen.auswaehlen("Ideaspace", stuecke, budget=10_000)
        self.assertEqual([s["dokument"] for s in gewaehlt], ["Spaces.md"])

    def test_praefix_erst_ab_fuenf_zeichen(self):
        """Sonst macht 'Idee' jedes Dokument mit 'Ideen' zum Treffer — und
        vier Zeichen sind zu wenig, um eine Absicht zu erkennen."""
        stuecke = [{"quelle": "A", "dokument": "D.md", "text": "Ideenmanagement heute."}]
        self.assertEqual(wissen.auswaehlen("Idee", stuecke, budget=10_000), [])
        self.assertTrue(wissen.auswaehlen("Ideen", stuecke, budget=10_000))

    def test_praefix_faengt_keine_zufaelligen_wortanfaenge(self):
        stuecke = [{"quelle": "A", "dokument": "D.md", "text": "Kontinent und Kontakt."}]
        self.assertEqual(wissen.auswaehlen("Konto", stuecke, budget=10_000), [])


class TestSeltenheitZaehlt(unittest.TestCase):
    """Nicht jeder Treffer ist gleich viel wert.

    Gemessen 04.09.2026 am echten Bestand: die Frage "Was bietet VibeMind
    Solo-Gruendern?" holte neun von siebzehn Dokumenten aus dem Kundenprojekt
    E-Ticketing_DE (HAFAS_Wrapper, FlixBus_Tickets, Klarna_BNPL). Der
    Allerwelts-Filter griff nicht: "bietet" steht zwar in vielen Dokumenten,
    aber nicht in mehr als der Haelfte — und zaehlte deshalb genauso viel wie
    "Gruender". Ein Wort ist so viel wert, wie es selten ist.
    """

    def _bestand(self):
        breit = [{"quelle": "Fremd", "dokument": "F%d.md" % i,
                  "text": "Dieses Dokument bietet Zahlungsabwicklung."} for i in range(8)]
        breit.append({"quelle": "Produkt", "dokument": "Zielgruppe.md",
                      "text": "Das Angebot bietet Solo-Gruendern einen eigenen Betrieb."})
        return breit

    def test_das_seltene_wort_entscheidet(self):
        gewaehlt = wissen.auswaehlen("Was bietet es Solo-Gruendern?",
                                     self._bestand(), budget=10_000)
        self.assertEqual(gewaehlt[0]["dokument"], "Zielgruppe.md")

    def test_ein_allerweltswort_allein_reisst_nicht_alles_herein(self):
        """'bietet' allein darf die neun Dokumente nicht vor das eine ziehen."""
        gewaehlt = wissen.auswaehlen("Gruendern bietet", self._bestand(), budget=10_000)
        self.assertEqual(gewaehlt[0]["dokument"], "Zielgruppe.md")

    def test_gewicht_faellt_mit_der_haeufigkeit(self):
        selten = wissen.gewicht(df=1, anzahl=100)
        haeufig = wissen.gewicht(df=90, anzahl=100)
        self.assertGreater(selten, haeufig)
        self.assertGreaterEqual(haeufig, 0.0, "kein negatives Gewicht")

    def test_unbekanntes_wort_gilt_als_sehr_spezifisch(self):
        """'Ideaspace' steht in keinem Dokument woertlich — genau deshalb ist
        es die schaerfste Angabe, die die Frage macht."""
        self.assertGreater(wissen.gewicht(df=0, anzahl=100),
                           wissen.gewicht(df=5, anzahl=100))

    def test_schwache_treffer_kommen_gar_nicht_erst_mit(self):
        """Der eigentliche Fehler war nicht die Reihenfolge, sondern die
        Aufnahme: die neun Fremd-Dokumente standen zwar hinten, aber sie
        standen im Auftrag und faerbten die Antwort."""
        gewaehlt = wissen.auswaehlen("Was bietet es Solo-Gruendern?",
                                     self._bestand(), budget=100_000)
        self.assertEqual([s["dokument"] for s in gewaehlt], ["Zielgruppe.md"])


class TestFunktionswoerter(unittest.TestCase):
    """Ein Funktionswort traegt keine Absicht — auch wenn es selten ist.

    Der harte Beleg, gemessen 04.09.2026 an den 134 echten Dokumenten: das
    Wort "bietet" steht in genau 8 davon. Damit ist es statistisch SELTEN,
    die IDF gab ihm volles Gewicht (2,77 — so viel wie "solo") — und diese
    acht Dokumente gehoerten alle zum Kundenprojekt E-Ticketing_DE. Auf die
    Frage "Was bietet VibeMind Solo-Gruendern?" kamen so HAFAS_Wrapper,
    FlixBus_Tickets und Klarna_BNPL in den Auftrag. Keine Korpus-Statistik
    kann das erkennen: dass "bietet" nichts ueber das Thema sagt, ist eine
    Eigenschaft der Sprache, nicht dieses Bestandes. Also eine Liste.
    """

    def _bestand(self):
        """20 Dokumente. Nur zwei enthalten "bietet" — es ist hier selten."""
        stuecke = [{"quelle": "Fremd", "dokument": "F%d.md" % i,
                    "text": "Zahlungsabwicklung und Fahrplanauskunft."} for i in range(17)]
        stuecke.append({"quelle": "Fremd", "dokument": "Bezahlen.md",
                        "text": "Der Dienst bietet Kartenzahlung."})
        stuecke.append({"quelle": "Fremd", "dokument": "Fahrplan.md",
                        "text": "Die Auskunft bietet Verbindungen."})
        stuecke.append({"quelle": "Produkt", "dokument": "Zielgruppe.md",
                        "text": "Gedacht fuer Solo-Gruendern und kleine Teams."})
        return stuecke

    def test_seltenes_funktionswort_zieht_nichts_herein(self):
        gewaehlt = wissen.auswaehlen("Was bietet es Solo-Gruendern?",
                                     self._bestand(), budget=100_000)
        self.assertEqual([s["dokument"] for s in gewaehlt], ["Zielgruppe.md"])

    def test_eine_frage_nur_aus_funktionswoertern_ist_eine_fehlanzeige(self):
        self.assertEqual(wissen.auswaehlen("Was kannst du eigentlich machen?",
                                           self._bestand(), budget=100_000), [])

    def test_funktionswoerter_im_dokument_stoeren_nicht(self):
        """Gefiltert wird die FRAGE, nicht der Bestand — ein Dokument bleibt
        ueber seine Inhaltswoerter auffindbar."""
        gewaehlt = wissen.auswaehlen("Kartenzahlung", self._bestand(), budget=100_000)
        self.assertEqual([s["dokument"] for s in gewaehlt], ["Bezahlen.md"])


if __name__ == "__main__":
    unittest.main()
