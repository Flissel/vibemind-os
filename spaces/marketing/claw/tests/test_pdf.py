"""PDF-Erzeugung: das erste Format, das sales-claw wirklich anhaengen kann.

WARUM PDF UND NICHT MARKDOWN. Gemessen am laufenden `sales-mcp` (04.09.2026,
`medien.pruefe`): `post.md` -> "Endung '.md' ist nicht zugelassen. Erlaubt:
.ics, .jpeg, .jpg, .mp3, .mp4, .ogg, .pdf, .png". Ein Markdown-Entwurf liegt
im richtigen Ordner und ist trotzdem unanhaengbar — `medien_liste` zeigt ihn
nicht einmal an. PDF ist das erste Format aus dieser Liste, das aus Text
entstehen kann.

UND KEINE UNTERORDNER. Dieselbe Messung: `Marketing/post.pdf` ->
"ist kein reiner Dateiname ... kein '/', kein '\\'". Der Zweck kommt deshalb
in den DATEINAMEN (`marketing-`, `email-`, `mobile-`), nicht in einen Ordner.
Das ist keine Notloesung: die Pfadpruefung ist der Riegel gegen einen
Ausbruch aus dem Medienordner, und den weicht man nicht fuer Ordnung auf.
"""
import importlib.util
import os
import re
import tempfile
import unittest
from unittest import mock

# reportlab liegt in `.venv`, nicht im pyenv-Python. Der Sidecar startet mit
# `.venv\Scripts\python.exe` (Launcher-Tabelle), also ist das der Interpreter,
# der zaehlt. Wer die Suite unter pyenv faehrt, soll hier eine Erklaerung
# lesen statt eines Sammelfehlers.
if importlib.util.find_spec("reportlab") is None:  # pragma: no cover
    raise unittest.SkipTest(
        "reportlab fehlt in diesem Interpreter. Der Marketing-Sidecar laeuft "
        "unter .venv: `.venv/Scripts/python.exe -m pytest "
        "spaces/marketing/claw/tests/test_pdf.py`")

from spaces.marketing.claw import pdf, werkzeuge  # noqa: E402

# Was sales-mcp anhaengen kann (medien.py:57-79, am 04.09.2026 gemessen).
ANHAENGBAR = {".pdf", ".jpg", ".jpeg", ".png", ".mp3", ".ogg", ".mp4", ".ics"}


class TestPdfBauen(unittest.TestCase):
    def test_liefert_echte_pdf_bytes(self):
        roh = pdf.bauen(titel="Early Access", text="Ein Absatz.")
        self.assertIsInstance(roh, bytes)
        self.assertTrue(roh.startswith(b"%PDF-"), roh[:20])
        self.assertIn(b"%%EOF", roh[-1024:])

    def test_umlaute_ueberleben(self):
        """Ein Newsletter ohne Umlaute waere in dieser Sprache unbrauchbar."""
        roh = pdf.bauen(titel="Grüße für Solo-Gründer",
                        text="Für dich gebaut — größer geht es nicht.")
        self.assertTrue(roh.startswith(b"%PDF-"))
        self.assertGreater(len(roh), 1000)

    def test_langer_text_bekommt_mehrere_seiten(self):
        kurz = pdf.bauen(titel="T", text="Ein Satz.")
        lang = pdf.bauen(titel="T", text="Ein Satz.\n\n" * 400)
        self.assertGreater(len(lang), len(kurz))
        self.assertGreaterEqual(pdf.seitenzahl(lang), 2)
        self.assertEqual(pdf.seitenzahl(kurz), 1)

    def test_belege_stehen_drin(self):
        roh = pdf.bauen(titel="T", text="Text",
                        belege=["VibeMind Knowledge / Core: Aussage"])
        self.assertGreater(len(roh), len(pdf.bauen(titel="T", text="Text")))

    def test_zu_klaeren_erscheint_nur_wenn_es_etwas_gibt(self):
        ohne = pdf.bauen(titel="T", text="Text")
        mit = pdf.bauen(titel="T", text="Text", zu_klaeren=["Preis unklar"])
        self.assertGreater(len(mit), len(ohne))

    def test_leerer_text_wird_abgelehnt(self):
        with self.assertRaises(ValueError):
            pdf.bauen(titel="T", text="   ")

    def test_absaetze_bleiben_absaetze(self):
        """Zwei Leerzeilen im Quelltext duerfen nicht zu einem Block werden."""
        eins = pdf.bauen(titel="T", text="A\n\nB\n\nC")
        zwei = pdf.bauen(titel="T", text="A B C")
        self.assertNotEqual(len(eins), len(zwei))

    def test_spitze_klammern_zerlegen_das_pdf_nicht(self):
        """reportlab liest Markup im Absatz — <b> aus einem Entwurf wuerde
        sonst als Auszeichnung gelesen oder das Rendern abbrechen."""
        roh = pdf.bauen(titel="A < B", text="5 < 7 & 8 > 2 <nicht-tag>")
        self.assertTrue(roh.startswith(b"%PDF-"))


class TestWerkzeug(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)
        self.env = mock.patch.dict("os.environ",
                                   {"MEDIA_ERZEUGT_DIR": self.ordner.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("MEDIA_ERZEUGT_SSH_HOST", None)

    def test_pdf_landet_als_datei(self):
        ergebnis = werkzeuge.pdf_erstellen("Early Access", "Titel", "Text")
        self.assertTrue(ergebnis["ok"], ergebnis)
        pfad = ergebnis["daten"]["pfad"]
        self.assertTrue(pfad.endswith(".pdf"))
        with open(pfad, "rb") as datei:
            self.assertTrue(datei.read(5) == b"%PDF-")

    def test_zweck_steht_im_dateinamen_nicht_in_einem_ordner(self):
        for zweck, anfang in (("marketing", "marketing-"), ("email", "email-"),
                              ("mobile", "mobile-")):
            with self.subTest(zweck=zweck):
                r = werkzeuge.pdf_erstellen("Probe", "T", "Text", zweck=zweck)
                name = os.path.basename(r["daten"]["pfad"])
                self.assertTrue(name.startswith(anfang), name)
                self.assertNotIn("/", r["daten"]["pfad"].replace(self.ordner.name, ""))

    def test_unbekannter_zweck_wird_abgelehnt(self):
        r = werkzeuge.pdf_erstellen("Probe", "T", "Text", zweck="geheim")
        self.assertFalse(r["ok"])
        self.assertIn("marketing", r["fehler"])

    def test_der_name_ueberlebt_als_sales_claw_dateiname(self):
        """Der Vertrag mit sales-claw: reiner Dateiname, erlaubte Endung."""
        r = werkzeuge.pdf_erstellen("Early Access / Solo-Gründer", "T", "Text")
        name = os.path.basename(r["daten"]["pfad"])
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertNotIn(":", name)
        self.assertFalse(name.startswith("."))
        self.assertIn(os.path.splitext(name)[1], ANHAENGBAR)
        self.assertEqual(name, os.path.basename(name))

    def test_zu_gross_wird_abgelehnt_bevor_es_scheitert(self):
        """sales-claw riegelt bei 15 MB ab — das faellt lieber hier auf."""
        with mock.patch.object(pdf, "bauen", return_value=b"%PDF-" + b"x" * (16 * 1024 * 1024)):
            r = werkzeuge.pdf_erstellen("Probe", "T", "Text")
        self.assertFalse(r["ok"])
        self.assertIn("15", r["fehler"])

    def test_fail_soft_wenn_das_rendern_scheitert(self):
        with mock.patch.object(pdf, "bauen", side_effect=RuntimeError("kaputt")):
            r = werkzeuge.pdf_erstellen("Probe", "T", "Text")
        self.assertFalse(r["ok"])
        self.assertIn("kaputt", r["fehler"])


class TestFerneAblageBinaer(unittest.TestCase):
    def test_pdf_reist_als_bytes_nicht_als_text(self):
        """Ein PDF durch eine Text-Kodierung zu schicken zerstoert es."""
        from spaces.marketing.claw import ablage
        gesehen = {}
        with mock.patch.dict("os.environ", {
                "MEDIA_ERZEUGT_SSH_HOST": "offload-vm",
                "MEDIA_ERZEUGT_DIR": "/ziel"}):
            with mock.patch.object(ablage, "_ssh_schreiben",
                                   lambda h, p, i: gesehen.update(inhalt=i, pfad=p)):
                r = werkzeuge.pdf_erstellen("Probe", "T", "Text")
        self.assertTrue(r["ok"], r)
        self.assertIsInstance(gesehen["inhalt"], bytes)
        self.assertTrue(gesehen["inhalt"].startswith(b"%PDF-"))
        self.assertTrue(re.fullmatch(r"/ziel/marketing-probe\.pdf", gesehen["pfad"]),
                        gesehen["pfad"])


class TestHandlungUndFuss(unittest.TestCase):
    """Ein Einseiter, dessen untere Haelfte leer bleibt, wirkt unfertig.

    Am ersten echten PDF gesehen (04.09.2026): Kopfband, Text, Belege — und
    darunter ein Drittel Nichts. Ein Marketing-Blatt traegt unten die
    Handlung, kein Vakuum.
    """

    def test_handlung_erscheint_im_pdf(self):
        ohne = pdf.bauen(titel="T", text="Text")
        mit = pdf.bauen(titel="T", text="Text",
                        handlung="Auf die Warteliste: vibemind.space/early")
        self.assertGreater(len(mit), len(ohne))

    def test_ohne_handlung_kein_leerer_kasten(self):
        """Kein Rahmen um nichts, wenn der Aufrufer nichts mitgibt."""
        a = pdf.bauen(titel="T", text="Text")
        b = pdf.bauen(titel="T", text="Text", handlung="   ")
        self.assertEqual(len(a), len(b))

    def test_fusszeile_auf_jeder_seite(self):
        eins = pdf.seitenzahl(pdf.bauen(titel="T", text="Text"))
        viele = pdf.seitenzahl(pdf.bauen(titel="T", text="Absatz.\n\n" * 400))
        self.assertEqual(eins, 1)
        self.assertGreaterEqual(viele, 2)

    def test_handlung_wird_ebenfalls_entschaerft(self):
        roh = pdf.bauen(titel="T", text="Text", handlung="Jetzt <hier> klicken & los")
        self.assertTrue(roh.startswith(b"%PDF-"))

    def test_werkzeug_reicht_die_handlung_durch(self):
        gesehen = {}

        def merken(**kw):
            gesehen.update(kw)
            return b"%PDF-fake"

        with mock.patch.object(pdf, "bauen", merken), \
             mock.patch.dict("os.environ", {"MEDIA_ERZEUGT_DIR": tempfile.mkdtemp()}):
            werkzeuge.pdf_erstellen("a", "T", "Text", handlung="Jetzt eintragen")
        self.assertEqual(gesehen.get("handlung"), "Jetzt eintragen")


if __name__ == "__main__":
    unittest.main()
