"""Die Schoenheitspruefung — das Handwerk als Schritt, nicht als Ratschlag.

Betreiber-Auftrag (12.09.2026): „skills sollen fuer die schoenheit und
annehmbarkeit am kunden sorgen."

Die Fertigkeit `unterlage-gestalten` beschreibt das Handwerk in Prosa, und
ein Agent kann Prosa ueberlesen. Diese Pruefung LAEUFT — bei jedem
Vorlagen-Vorschlag und bei jeder Unterlage.

DER BEWEIS, DASS SIE NOETIG WAR: die Vorlage `warm-sand`, die der Agent am
12.09. vorgeschlagen und die ich dem Betreiber zur Durchsicht geschickt
habe, reisst VIER Kontrastschwellen (Akzent 3.6:1, Gold 4.2:1, Fusszeile
2.9:1, Handlungskasten 3.7:1). Niemand hat das gesehen — weder der Agent
noch ich. Eine Zahl sieht man nicht an, man muss sie rechnen.

ZWEI SCHWEREGRADE, und der Unterschied ist die ganze Zurueckhaltung: HART
blockiert (nachweislich unlesbar, Platzhalter beim Kunden), WEICH nennt nur
(ein Mensch kann gute Gruende haben). Eine Pruefung, die bei
Geschmacksfragen blockiert, wird umgangen.
"""
import unittest

from spaces.marketing.claw import schoenheit

# Die freigegebene Tafel — sie MUSS bestehen, sonst ist die Schwelle falsch
# gewaehlt und nicht die Tafel schlecht.
DUNKEL = {"grund": "#0f2422", "flaeche": "#1d3b39", "akzent": "#5eead4",
          "gold": "#fbbf24", "text": "#cfe3df", "text_hell": "#e9fbf6",
          "text_leise": "#8aa3a0", "handlung_text": "#0f2422"}


class Kontrast(unittest.TestCase):

    def test_die_extreme_stimmen(self):
        """Schwarz auf Weiss ist 21:1, gleiche Farbe ist 1:1. Wer die
        Rechnung verdreht, faellt hier auf."""
        self.assertAlmostEqual(schoenheit.kontrast("#000000", "#ffffff"), 21.0, places=1)
        self.assertAlmostEqual(schoenheit.kontrast("#ffffff", "#000000"), 21.0, places=1)
        self.assertAlmostEqual(schoenheit.kontrast("#123456", "#123456"), 1.0, places=3)

    def test_die_reihenfolge_ist_egal(self):
        self.assertAlmostEqual(schoenheit.kontrast("#0f2422", "#cfe3df"),
                               schoenheit.kontrast("#cfe3df", "#0f2422"), places=6)

    def test_kaputte_farbe_wirft_mit_klarem_satz(self):
        with self.assertRaises(ValueError) as f:
            schoenheit.leuchtdichte("blau")
        self.assertIn("#rrggbb", str(f.exception))


class GestaltPruefung(unittest.TestCase):

    def test_die_freigegebene_tafel_besteht(self):
        """Wenn `dunkel` durchfaellt, ist die Schwelle falsch - diese Tafel
        hat ein Mensch am 11.09.2026 an einem echten PDF beurteilt."""
        urteil = schoenheit.urteil(schoenheit.gestalt_pruefen(DUNKEL))
        self.assertTrue(urteil["bestanden"], urteil["hart"])

    def test_warm_sand_faellt_durch_und_nennt_die_zahlen(self):
        """Der Fall, der diese Pruefung ausgeloest hat."""
        warm_sand = {"grund": "#fbf1e4", "flaeche": "#4a2c17", "akzent": "#c1662f",
                     "gold": "#9c6b12", "text": "#3d2b1c", "text_hell": "#3a2314",
                     "text_leise": "#8a7660", "handlung_text": "#fdf3e7"}
        urteil = schoenheit.urteil(schoenheit.gestalt_pruefen(warm_sand))
        self.assertFalse(urteil["bestanden"])
        self.assertEqual(len(urteil["hart"]), 4)
        # Die Saetze muessen die GEMESSENE Zahl und die Schwelle nennen -
        # „zu wenig Kontrast" allein hilft niemandem beim Nachbessern.
        zusammen = " ".join(urteil["hart"])
        self.assertIn(":1", zusammen)
        self.assertIn("4.5:1", zusammen)

    def test_unsichtbarer_text_ist_hart(self):
        blind = dict(DUNKEL, text=DUNKEL["grund"])
        urteil = schoenheit.urteil(schoenheit.gestalt_pruefen(blind))
        self.assertFalse(urteil["bestanden"])

    def test_reines_schwarz_auf_weiss_ist_nur_WEICH(self):
        """Der Betreiber nannte harte Kontraste unangenehm (11.09.2026) -
        aber es gibt gute Gruende dafuer (Druck, Barrierefreiheit). Also
        nennen, nicht blockieren."""
        hart_gesetzt = {"grund": "#ffffff", "flaeche": "#ffffff", "akzent": "#000000",
                        "gold": "#000000", "text": "#000000", "text_hell": "#000000",
                        "text_leise": "#000000", "handlung_text": "#ffffff"}
        urteil = schoenheit.urteil(schoenheit.gestalt_pruefen(hart_gesetzt))
        self.assertTrue(urteil["bestanden"], urteil["hart"])
        self.assertTrue(urteil["weich"])
        self.assertIn("flimmert", " ".join(urteil["weich"]))

    def test_fehlende_farbe_wirft_nicht_sondern_meldet(self):
        urteil = schoenheit.urteil(schoenheit.gestalt_pruefen({"grund": "#ffffff"}))
        self.assertFalse(urteil["bestanden"])


class Platzhalter(unittest.TestCase):

    def test_die_ueblichen_werden_erkannt(self):
        for text, was in (
                ("Jetzt anmelden: [LINK EINFUEGEN]", "eckige Klammern"),
                ("Hallo {{first_name}}", "geschweifte"),
                ("Schreib an <deine Adresse>", "spitze"),
                ("Preis: TBD", "TBD"),
                ("Kontakt: info@example.com", "Beispiel-Domain"),
                ("Lorem ipsum dolor", "Blindtext")):
            with self.subTest(was):
                self.assertTrue(schoenheit.platzhalter(text), text)

    def test_gewoehnlicher_text_wird_nicht_verdaechtigt(self):
        """Ein falscher Treffer blockiert einen gueltigen Text und kostet
        mehr Vertrauen, als er einbringt. Die Muster sind absichtlich eng."""
        for text in ("Wir bauen VibeMind fuer Solo-Gruender.",
                     "Sprechzeit 10-12 Uhr",
                     "Schreib an felix@vibemind.space",
                     "Der Preis liegt bei 29 EUR/Monat",
                     "5 < 10 und 10 > 5",
                     "Anteil: 30% (gemessen)"):
            with self.subTest(text):
                self.assertEqual(schoenheit.platzhalter(text), [], text)


class Unterlage(unittest.TestCase):

    def _u(self, **kw):
        basis = dict(titel="VibeMind", untertitel="Fuer Solo-Gruender",
                     text="- **Marketing** laeuft mit\n- **Support** wartet auf dich",
                     handlung="Jetzt eintragen: vibemind.space/early",
                     belege=["Quelle / Dok: Aussage"], zu_klaeren=[], seiten=1)
        basis.update(kw)
        return schoenheit.urteil(schoenheit.unterlage_pruefen(**basis))

    def test_eine_gute_unterlage_besteht_ohne_anmerkung(self):
        urteil = self._u()
        self.assertTrue(urteil["bestanden"])
        self.assertEqual(urteil["weich"], [])

    def test_platzhalter_im_handlungskasten_ist_hart(self):
        """Der sichtbarste Platz der Seite."""
        urteil = self._u(handlung="Jetzt eintragen: [URL]")
        self.assertFalse(urteil["bestanden"])
        self.assertIn("Handlungskasten", " ".join(urteil["hart"]))
        self.assertIn("LEER", " ".join(urteil["hart"]))

    def test_platzhalter_im_text_ist_hart(self):
        urteil = self._u(text="Hallo {{first_name}}, schoen dass du da bist.")
        self.assertFalse(urteil["bestanden"])

    def test_stichpunkte_ohne_betonung_sind_WEICH(self):
        urteil = self._u(text="- Marketing laeuft mit\n- Support wartet auf dich")
        self.assertTrue(urteil["bestanden"], "das ist Geschmack, kein Fehler")
        self.assertIn("betontes Wort", " ".join(urteil["weich"]))

    def test_zu_viel_betonung_ist_WEICH(self):
        urteil = self._u(text=("- **alles** **hier** **ist** **fett**\n"
                               "- **und** **hier** **auch** **noch**"))
        self.assertTrue(urteil["bestanden"])
        self.assertIn("betont nichts", " ".join(urteil["weich"]))

    def test_zweite_seite_und_fehlende_belege_sind_WEICH(self):
        urteil = self._u(seiten=2, belege=[])
        self.assertTrue(urteil["bestanden"])
        zusammen = " ".join(urteil["weich"])
        self.assertIn("2 Seiten", zusammen)
        self.assertIn("ungeprueft", zusammen)

    def test_leerer_handlungskasten_ist_WEICH_nicht_hart(self):
        """Die Fertigkeit sagt ausdruecklich: lieber leer lassen als einen
        Platzhalter hineinschreiben. Dann darf leer nicht blockieren."""
        urteil = self._u(handlung="")
        self.assertTrue(urteil["bestanden"])
        self.assertIn("Kein Handlungskasten", " ".join(urteil["weich"]))


class ImWeg(unittest.TestCase):
    """Die Pruefung muss LAUFEN, nicht danebenstehen."""

    def test_pdf_erstellen_ruft_sie(self):
        import inspect
        from spaces.marketing.claw import werkzeuge
        for fn in (werkzeuge.pdf_erstellen, werkzeuge.pdf_aus_entwurf,
                   werkzeuge.vorlage_vorschlagen):
            with self.subTest(fn.__name__):
                quelle = inspect.getsource(fn)
                self.assertIn("schoenheit.", quelle,
                              f"{fn.__name__} prueft nicht")

    def test_es_gibt_kein_werkzeug_das_die_pruefung_abschaltet(self):
        from spaces.marketing.claw import server
        namen = {fn.__name__ for fn in server.WERKZEUGE}
        for verboten in ("pruefung_ueberspringen", "schoenheit_aus",
                         "trotzdem_setzen"):
            self.assertNotIn(verboten, namen)


if __name__ == "__main__":
    unittest.main()
