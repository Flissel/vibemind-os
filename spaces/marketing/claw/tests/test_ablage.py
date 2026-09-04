"""post_ablegen: erzeugte Beitraege landen dort, wo sales-claw sie findet.

DIE TRENNUNG DER ZWEI ORDNER IST ABSICHT und wird hier festgenagelt:
`/media` ist der Ordner des Menschen — bei sales-claw als `./media:/media:ro`
eingehaengt, also fuer jeden Dienst schreibgeschuetzt. Maschinell Erzeugtes
geht nach `/media-erzeugt` (`sales-mcp/medien.py:45-49`). Wer diese Grenze
aufweicht, laesst einen Agenten in die Ablage eines Menschen schreiben.

Die Namenspruefung folgt demselben Vorbild (`medien.py:100-113`): geprueft
wird VOR jedem Zugriff aufs Dateisystem, gegen beide Trennzeichen, den
Windows-Doppelpunkt und das NUL-Byte.
"""
import os
import tempfile
import unittest
from unittest import mock

from spaces.marketing.claw import ablage, werkzeuge


class TestAblage(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)
        self.env = mock.patch.dict(
            "os.environ", {"MEDIA_ERZEUGT_DIR": self.ordner.name,
                           "MEDIA_DIR": os.path.join(self.ordner.name, "menschen")})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_post_landet_in_erzeugt(self):
        ergebnis = werkzeuge.post_ablegen("early-access", "# Post\n\nText")
        self.assertTrue(ergebnis["ok"], ergebnis)
        pfad = ergebnis["daten"]["pfad"]
        self.assertTrue(os.path.exists(pfad))
        with open(pfad, encoding="utf-8") as datei:
            self.assertIn("# Post", datei.read())

    def test_niemals_in_den_menschen_ordner(self):
        """Der Kern der Trennung: MEDIA_DIR wird nie beschrieben."""
        menschen = os.environ["MEDIA_DIR"]
        os.makedirs(menschen, exist_ok=True)
        werkzeuge.post_ablegen("beitrag", "Text")
        self.assertEqual(os.listdir(menschen), [])

    def test_pfad_ausbruch_wird_abgelehnt(self):
        """Beide Schreibweisen. Der Riegel ist nicht das Trennzeichen — das
        wuerde weggeputzt — sondern der fuehrende Punkt, der nach dem
        Saeubern uebrigbleibt: aus `../../etc/passwd` wird `..-..-etc-passwd`,
        und ein Name, der mit einem Punkt beginnt, ist nicht erlaubt."""
        for boese in ("../../etc/passwd", "..\\..\\windows\\system32\\datei"):
            with self.subTest(name=boese):
                self.assertFalse(werkzeuge.post_ablegen(boese, "x")["ok"])
        self.assertEqual(os.listdir(self.ordner.name), [])

    def test_trennzeichen_ueberleben_den_titel_nicht(self):
        """`name` ist ein TITEL, kein Dateiname — "Early Access / Solo" soll
        einen brauchbaren Dateinamen ergeben, nicht abgelehnt werden. Der
        Vertrag ist deshalb nicht "wird zurueckgewiesen", sondern: kein
        Trennzeichen ueberlebt, und die Datei liegt DIREKT in der Wurzel,
        nie in einem angelegten Unterordner."""
        for titel in ("C:datei", "a/b", "Early Access / Solo-Gruender",
                      "Kampagne: Q4 \\ Nachfass"):
            with self.subTest(titel=titel):
                ergebnis = werkzeuge.post_ablegen(titel, "x")
                self.assertTrue(ergebnis["ok"], ergebnis)
                pfad = ergebnis["daten"]["pfad"]
                self.assertEqual(os.path.dirname(os.path.realpath(pfad)),
                                 os.path.realpath(self.ordner.name))
                name = os.path.basename(pfad)
                for zeichen in ("/", "\\", ":", "\x00"):
                    self.assertNotIn(zeichen, name)

    def test_kein_unterordner_wird_angelegt(self):
        werkzeuge.post_ablegen("a/b/c", "x")
        eintraege = os.listdir(self.ordner.name)
        self.assertTrue(all(not os.path.isdir(os.path.join(self.ordner.name, e))
                            for e in eintraege), eintraege)

    def test_nul_byte_wirft_nicht(self):
        ergebnis = werkzeuge.post_ablegen("da\x00tei", "x")
        self.assertTrue(ergebnis["ok"], ergebnis)
        self.assertNotIn("\x00", os.path.basename(ergebnis["daten"]["pfad"]))

    def test_versteckte_dateien_bleiben_draussen(self):
        self.assertFalse(werkzeuge.post_ablegen(".bashrc", "x")["ok"])
        self.assertFalse(werkzeuge.post_ablegen("..foo", "x")["ok"])

    def test_leerer_name_und_leerer_inhalt(self):
        self.assertFalse(werkzeuge.post_ablegen("   ", "Text")["ok"])
        self.assertFalse(werkzeuge.post_ablegen("beitrag", "   ")["ok"])

    def test_endung_wird_ergaenzt_aber_nicht_verdoppelt(self):
        eins = werkzeuge.post_ablegen("a", "x")["daten"]["pfad"]
        zwei = werkzeuge.post_ablegen("b.md", "x")["daten"]["pfad"]
        self.assertTrue(eins.endswith(".md"))
        self.assertTrue(zwei.endswith(".md") and not zwei.endswith(".md.md"))

    def test_art_bestimmt_die_endung(self):
        pfad = werkzeuge.post_ablegen("seite", "<p>x</p>", art="html")["daten"]["pfad"]
        self.assertTrue(pfad.endswith(".html"))

    def test_unbekannte_art_wird_abgelehnt(self):
        """Sonst schreibt ein Agent eine .ps1 oder .exe in einen Ordner,
        den ein Mensch spaeter oeffnet."""
        self.assertFalse(werkzeuge.post_ablegen("x", "inhalt", art="ps1")["ok"])

    def test_ordner_wird_angelegt(self):
        neu = os.path.join(self.ordner.name, "tiefer", "drin")
        with mock.patch.dict("os.environ", {"MEDIA_ERZEUGT_DIR": neu}):
            self.assertTrue(werkzeuge.post_ablegen("a", "x")["ok"])
        self.assertTrue(os.path.isdir(neu))

    def test_zweiter_beitrag_gleichen_namens_ueberschreibt_nicht(self):
        eins = werkzeuge.post_ablegen("gleich", "erster")["daten"]["pfad"]
        zwei = werkzeuge.post_ablegen("gleich", "zweiter")["daten"]["pfad"]
        self.assertNotEqual(eins, zwei)
        with open(eins, encoding="utf-8") as datei:
            self.assertEqual(datei.read(), "erster")

    def test_zeilenenden_sind_unix(self):
        pfad = werkzeuge.post_ablegen("a", "eins\nzwei")["daten"]["pfad"]
        with open(pfad, "rb") as datei:
            self.assertNotIn(b"\r\n", datei.read())


class TestOhneKonfiguration(unittest.TestCase):
    def test_vorgabe_ist_der_erzeugt_ordner(self):
        """Ohne Umgebungsvariable gilt /media-erzeugt — nie /media."""
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("MEDIA_ERZEUGT_DIR", None)
            self.assertEqual(os.path.basename(ablage.wurzel()), "media-erzeugt")


class TestFerneAblage(unittest.TestCase):
    """sales-claw laeuft auf der VM, der Sidecar auf dem Host.

    Ein Beitrag, der nur auf dem Windows-Rechner landet, erreicht den
    Vertrieb nie — gemessen 04.09.2026: `sales-mcp` und die Versender
    laufen auf `offload-vm`, ihr `media-erzeugt` liegt unter
    `/home/debian/sales-claw/media-erzeugt` und traegt dort schon echte
    Kalenderdateien.

    Der Weg dorthin folgt exakt `sync/_db.py`: eine Umgebungsvariable
    schaltet den fernen Modus ein, der Inhalt reist auf stdin und NIE auf
    einer Kommandozeile, und das Umschalten ist durch Loeschen der
    Variable vollstaendig rueckgaengig zu machen.
    """

    def test_ohne_variable_bleibt_alles_lokal(self):
        with tempfile.TemporaryDirectory() as ordner:
            with mock.patch.dict("os.environ",
                                 {"MEDIA_ERZEUGT_DIR": ordner}, clear=False):
                os.environ.pop("MEDIA_ERZEUGT_SSH_HOST", None)
                with mock.patch.object(ablage, "_ssh_schreiben") as fern:
                    self.assertTrue(werkzeuge.post_ablegen("a", "x")["ok"])
                fern.assert_not_called()

    def test_mit_variable_geht_es_ueber_ssh(self):
        with mock.patch.dict("os.environ", {
                "MEDIA_ERZEUGT_SSH_HOST": "offload-vm",
                "MEDIA_ERZEUGT_DIR": "/home/debian/sales-claw/media-erzeugt"}):
            with mock.patch.object(ablage, "_ssh_schreiben",
                                   return_value=None) as fern:
                ergebnis = werkzeuge.post_ablegen("early-access", "Text")
        self.assertTrue(ergebnis["ok"], ergebnis)
        self.assertEqual(
            ergebnis["daten"]["pfad"],
            "offload-vm:/home/debian/sales-claw/media-erzeugt/early-access.md")
        fern.assert_called_once()

    def test_inhalt_reist_auf_stdin_nie_in_der_kommandozeile(self):
        gesehen = {}

        def merken(host, pfad, inhalt):
            gesehen.update(host=host, pfad=pfad, inhalt=inhalt)

        geheim = "GEHEIMER-INHALT-42"
        with mock.patch.dict("os.environ", {
                "MEDIA_ERZEUGT_SSH_HOST": "offload-vm",
                "MEDIA_ERZEUGT_DIR": "/ziel"}):
            with mock.patch.object(ablage, "_ssh_schreiben", merken):
                werkzeuge.post_ablegen("a", geheim)
        self.assertEqual(gesehen["inhalt"], geheim)
        self.assertNotIn(geheim, gesehen["pfad"])

    def test_ferner_fehler_ist_fail_soft(self):
        def kaputt(host, pfad, inhalt):
            raise OSError("ssh weg")
        with mock.patch.dict("os.environ", {
                "MEDIA_ERZEUGT_SSH_HOST": "offload-vm",
                "MEDIA_ERZEUGT_DIR": "/ziel"}):
            with mock.patch.object(ablage, "_ssh_schreiben", kaputt):
                ergebnis = werkzeuge.post_ablegen("a", "x")
        self.assertFalse(ergebnis["ok"])
        self.assertIn("ssh weg", ergebnis["fehler"])

    def test_ferner_pfad_bleibt_ein_einziges_segment(self):
        """Der Name ist nach dem Saeubern ein Segment — nichts darf ihn
        wieder zu einem Pfad machen, auch nicht in der Ferne."""
        gesehen = {}
        with mock.patch.dict("os.environ", {
                "MEDIA_ERZEUGT_SSH_HOST": "offload-vm",
                "MEDIA_ERZEUGT_DIR": "/ziel"}):
            with mock.patch.object(ablage, "_ssh_schreiben",
                                   lambda h, p, i: gesehen.update(pfad=p)):
                werkzeuge.post_ablegen("a/b/c", "x")
        self.assertEqual(gesehen["pfad"], "/ziel/a-b-c.md")


if __name__ == "__main__":
    unittest.main()
