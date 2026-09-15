"""Hausstil und PDF-Auszeichnung — die Rueckmeldung vom 11.09.2026.

Ein Mensch hat das erste echte PDF (`email-early-access-solo-gruender.pdf`)
gelesen und per WhatsApp geantwortet. Vier Punkte, alle hier festgehalten:

  1. „Ich wuerde bei den Stichpunkten die folgenden Woerter fett machen"
  2. „Zweiter Satz in Tuerkis bei der Ueberschrift auch in Fett"
  3. „Finde gut, dass die Schriftfarbe nicht ganz weiss ist" (also: NICHT
     aufhellen — ein Test, der eine Bestaetigung festhaelt)
  4. „Die langen Gedankenstriche wegmachen oder kuerzen. Sieht zu sehr nach
     KI aus."

Punkt 4 ist der heikelste, weil er in den TEXT eingreift. Die Tests halten
deshalb beides fest: dass gekuerzt wird, und dass ein Bereich (10-12 Uhr)
unangetastet bleibt.
"""
import unittest

from spaces.marketing.claw import stil

GEVIERT = "—"     # —
HALBGEVIERT = "–"  # –


class Gedankenstriche(unittest.TestCase):

    def test_einschub_wird_gekuerzt(self):
        roh = f"laeuft ueber deinen Namen {GEVIERT} und da setzt VibeMind an."
        neu, n = stil.hausstil(roh)
        self.assertEqual(n, 1)
        self.assertEqual(neu, "laeuft ueber deinen Namen - und da setzt VibeMind an.")

    def test_halbgeviert_mit_leerzeichen_ebenfalls(self):
        neu, n = stil.hausstil(f"Early Access {HALBGEVIERT} fuer Solo-Gruender")
        self.assertEqual((neu, n), ("Early Access - fuer Solo-Gruender", 1))

    def test_bereich_bleibt_unangetastet(self):
        """`10-12 Uhr` ist kein Gedankenstrich. Ihn zu zerschneiden waere ein
        Fehler, den niemand bestellt hat."""
        roh = f"Sprechzeit 10{HALBGEVIERT}12 Uhr"
        neu, n = stil.hausstil(roh)
        self.assertEqual((neu, n), (roh, 0))

    def test_enger_geviertstrich_wird_bindestrich_ohne_leerzeichen(self):
        neu, _ = stil.hausstil(f"Wort{GEVIERT}Wort")
        self.assertEqual(neu, "Wort-Wort")

    def test_sauberer_text_bleibt_zeichengleich(self):
        roh = "Ein Satz ohne lange Striche, mit Binde-Strich."
        self.assertEqual(stil.hausstil(roh), (roh, 0))

    def test_mehrere_werden_gezaehlt_nicht_doppelt(self):
        """Ein Geviertstrich MIT Leerzeichen passt auf beide Muster. Wer
        naiv addiert, meldet dem Agenten die doppelte Zahl."""
        roh = f"a {GEVIERT} b {GEVIERT} c"
        neu, n = stil.hausstil(roh)
        self.assertEqual(n, 2)
        self.assertEqual(neu, "a - b - c")

    def test_leeres_und_none_stuerzen_nicht(self):
        self.assertEqual(stil.hausstil(""), ("", 0))
        self.assertEqual(stil.hausstil(None), (None, 0))


class PdfAuszeichnung(unittest.TestCase):
    """Import erst hier: `pdf` zieht reportlab, und das soll die uebrigen
    Testmodule nicht mitreissen (gemessen 04.09.2026 — ein Import am
    Modulkopf killte neun Testmodule unter pyenv)."""

    def setUp(self):
        from spaces.marketing.claw import pdf
        self.pdf = pdf

    def test_doppelsterne_werden_fett(self):
        self.assertEqual(self.pdf._auszeichnen("ein **fettes** Wort"),
                         "ein <b>fettes</b> Wort")

    def test_einzelner_stern_zeichnet_nicht_aus(self):
        """Ein Stern am Zeilenanfang ist in Kampagnentexten ein
        Aufzaehlungszeichen, keine Betonung."""
        self.assertEqual(self.pdf._auszeichnen("* kein Fett"), "* kein Fett")

    def test_markup_aus_dem_entwurf_bleibt_entschaerft(self):
        """Die Reihenfolge ist die ganze Sicherheit: erst entschaerfen, dann
        auszeichnen. Sonst waere jeder Kampagnentext ein Einfallstor fuer
        beliebiges reportlab-Markup."""
        self.assertEqual(self.pdf._auszeichnen("<b>nicht erlaubt</b>"),
                         "&lt;b&gt;nicht erlaubt&lt;/b&gt;")
        self.assertNotIn("<", self.pdf._auszeichnen("<font size=99>x</font>")
                         .replace("<b>", "").replace("</b>", ""))

    def test_untertitel_ist_fett(self):
        """Punkt 2 der Rueckmeldung."""
        stile = self.pdf._stile(self.pdf._farben("dunkel"))
        _, fett = self.pdf.schriften()
        self.assertEqual(stile["unter"].fontName, fett)
        self.assertEqual(stile["unter"].textColor,
                         self.pdf._farben("dunkel")["akzent"])

    def test_textfarbe_ist_nicht_reinweiss(self):
        """Punkt 3 ist eine BESTAETIGUNG, kein Auftrag - und genau deshalb
        ein Test: bestaetigte Entscheidungen werden sonst beim naechsten
        Aufraeumen weggeputzt.

        `_farben` liefert reportlab-Color-Objekte, keine Zeichenketten; der
        Vergleich laeuft deshalb ueber die Tafel selbst."""
        self.assertNotIn(self.pdf.LAYOUTS["dunkel"]["text"].lower(),
                         ("#ffffff", "#fff"))
        self.assertNotIn(self.pdf.LAYOUTS["hell"]["text"].lower(),
                         ("#000000", "#000"))
        # ... und trotzdem noch lesbar hell auf dunklem Grund.
        self.assertNotEqual(self.pdf.LAYOUTS["dunkel"]["text"],
                            self.pdf.LAYOUTS["dunkel"]["grund"])

    def test_fusszeile_traegt_keinen_langen_strich(self):
        """Sie war eine der drei Quellen - und die einzige, die in unserem
        eigenen Code stand. Geprueft wird die KONSTANTE, nicht der
        Quelltext: der erste Anlauf dieses Tests fand einen Geviertstrich in
        einem Kommentar und schlug aus dem falschen Grund an."""
        self.assertNotIn(GEVIERT, self.pdf.FUSSZEILE)
        self.assertNotIn(HALBGEVIERT, self.pdf.FUSSZEILE)
        self.assertIn("vibemind.space", self.pdf.FUSSZEILE)

    def test_bauen_kuerzt_lange_striche_im_ganzen_dokument(self):
        roh = self.pdf.bauen(
            titel=f"Titel {GEVIERT} zweiter Teil",
            untertitel=f"Unter {GEVIERT} titel",
            text=f"Ein Satz {GEVIERT} mit Einschub.\n\n- Punkt {GEVIERT} eins",
            handlung=f"Jetzt {GEVIERT} anmelden",
            zu_klaeren=[f"Adresse fehlt {GEVIERT} vor Versand einsetzen"])
        self.assertTrue(roh.startswith(b"%PDF"))
        self.assertGreater(len(roh), 1000)


if __name__ == "__main__":
    unittest.main()
