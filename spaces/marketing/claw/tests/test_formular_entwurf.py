"""Foto oder Beschreibung -> Formular-Gestalt, ueber `claude -p` auf dem Abo."""
import json
import os
import pathlib
import unittest

from spaces.marketing.claw import formular_entwurf as fe

GUT = {"seite": {"breite_mm": 148, "hoehe_mm": 105}, "felder": [
    {"name": "kunde", "beschriftung": "Kunde", "art": "text", "quelle": "kunde.name",
     "platz": {"x": 8, "y": 20, "breite": 60, "hoehe": 12}}]}


def _lauf(stdout, rc=0):
    def lauf(argv, **kw):
        lauf.argv, lauf.kw = argv, kw
        return type("E", (), {"returncode": rc, "stdout": stdout, "stderr": "kaputt"})()
    return lauf


def _cli(obj):
    return json.dumps({"result": json.dumps(obj)})


class Entwurf(unittest.TestCase):
    def test_foto_wird_als_datei_gelesen_nicht_als_text_geschickt(self):
        lauf = _lauf(_cli(GUT))
        auftrag = {"bild_b64": "iVBORw0KGgo=", "bild_typ": "image/png", "beschreibung": "",
                   "anmerkung": "", "rueckmeldungen": [], "runde": 1}
        gestalt, fehler = fe.entwerfen(auftrag, lauf)
        self.assertEqual(fehler, "")
        self.assertEqual(gestalt, GUT)
        self.assertIn("--allowedTools", lauf.argv)
        self.assertIn("Read", lauf.argv)
        self.assertNotIn("iVBORw0KGgo=", " ".join(lauf.argv))

    def test_rueckmeldungen_aller_runden_stehen_im_auftrag_an_das_modell(self):
        lauf = _lauf(_cli(GUT))
        fe.entwerfen({"bild_b64": None, "bild_typ": None, "beschreibung": "Kunde, Datum",
                      "anmerkung": "", "runde": 3, "rueckmeldungen": [
                          {"runde": 1, "anmerkung": "Datum groesser"},
                          {"runde": 2, "anmerkung": "Logo fehlt"}]}, lauf)
        prompt = " ".join(lauf.argv)
        self.assertIn("Datum groesser", prompt)
        self.assertIn("Logo fehlt", prompt)

    def test_kaputtes_json_ist_ein_fehler_keine_halbe_vorlage(self):
        gestalt, fehler = fe.entwerfen({"bild_b64": None, "bild_typ": None,
                                        "beschreibung": "x", "anmerkung": "",
                                        "runde": 1, "rueckmeldungen": []},
                                       _lauf(json.dumps({"result": "{nicht json"})))
        self.assertIsNone(gestalt)
        self.assertIn("JSON", fehler)

    def test_ein_fehlschlag_der_cli_wird_gemeldet(self):
        gestalt, fehler = fe.entwerfen({"bild_b64": None, "bild_typ": None,
                                        "beschreibung": "x", "anmerkung": "",
                                        "runde": 1, "rueckmeldungen": []}, _lauf("", rc=1))
        self.assertIsNone(gestalt)
        self.assertIn("kaputt", fehler)

    def test_der_katalog_im_prompt_ist_der_von_sales(self):
        """Drift-Waechter: Marketing schlaegt nur Quellen vor, die Sales kennt.

        Liest SALES_CLAW_DIR, falls gesetzt (der Live-Checkout hat den
        aktuellen Katalog; der Submodul-Pin dieses Worktrees kann alt sein).
        Fehlt die Datei oder steht kein KATALOG darin, ist das ein FEHLER,
        kein Skip - ein still uebersprungener Waechter waere keiner.
        """
        sales_dir = os.environ.get("SALES_CLAW_DIR")
        basis = (pathlib.Path(sales_dir) if sales_dir
                 else pathlib.Path(__file__).resolve().parents[3] / "sales-claw")
        pfad = basis / "sales-mcp" / "terminkarte.py"
        sales = pfad.read_text(encoding="utf-8") if pfad.exists() else ""
        if "KATALOG" not in sales:
            self.fail("sales-claw zu alt oder nicht gefunden - SALES_CLAW_DIR setzen")
        for quelle in fe.QUELLEN:
            self.assertIn(f'"{quelle}"', sales, quelle)


if __name__ == "__main__":
    unittest.main()
