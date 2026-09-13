"""Der Riegel vor Marketings eigenen Versendern (Betreiber-Entscheid 12.09.2026).

Was hier festgehalten wird:

  * LIVE ist zu — in allen drei Versendern, und zwar VOR jedem Netzkontakt
    und vor jeder Konfiguration. Eine Absage, die erst kommt, wenn SMTP
    erreichbar ist, ist keine.
  * DRY_RUN und SHADOW bleiben offen. Sie stellen nichts zu; sie zu sperren
    haette Diagnose und Testlaeufe zerstoert, ohne irgendetwas sicherer zu
    machen.
  * Die Umgehung existiert und ist laut. Ein Schalter, der sich nicht
    umlegen laesst, wird auskommentiert — und dann weiss niemand mehr, dass
    es ihn gab.
"""
import logging
import unittest
from unittest import mock

from spaces.marketing.tools import versandsperre


class Riegel(unittest.TestCase):

    def test_ohne_umgehung_bricht_es_ab(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop(versandsperre.UMGEHUNG, None)
            with self.assertRaises(versandsperre.VersandGesperrt) as fehler:
                versandsperre.pruefen("_send_paranoid")
        text = str(fehler.exception)
        self.assertIn("_send_paranoid", text)
        self.assertIn("sales-claw", text)
        # Die Absage muss den WEG nennen, nicht nur das Verbot.
        self.assertIn("versand_beauftragen", text)
        self.assertIn(versandsperre.UMGEHUNG, text)

    def test_umgehung_laesst_durch_und_protokolliert(self):
        log = logging.getLogger("test-riegel")
        with mock.patch.dict("os.environ", {versandsperre.UMGEHUNG: "1"}), \
             mock.patch.object(log, "warning") as warnung:
            versandsperre.pruefen("_send_telegram", log)
        warnung.assert_called_once()
        self.assertIn("_send_telegram", str(warnung.call_args))

    def test_beliebiger_wert_oeffnet_nicht(self):
        with mock.patch.dict("os.environ", {versandsperre.UMGEHUNG: "vielleicht"}):
            with self.assertRaises(versandsperre.VersandGesperrt):
                versandsperre.pruefen("_send_openfang")


class ImLiveZweigVerdrahtet(unittest.TestCase):
    """Nicht die Existenz des Moduls beweist etwas, sondern dass die drei
    Versender es im LIVE-Zweig rufen — als ERSTES."""

    def _quelle(self, name):
        from pathlib import Path
        pfad = Path(versandsperre.__file__).with_name(name)
        return pfad.read_text(encoding="utf-8")

    def test_alle_drei_rufen_den_riegel_als_erstes_im_live_zweig(self):
        for name in ("_send_paranoid.py", "_send_telegram.py", "_send_openfang.py"):
            with self.subTest(name):
                zeilen = self._quelle(name).split("\n")
                start = next(i for i, z in enumerate(zeilen) if z.startswith("def run("))
                live = next(i for i in range(start, len(zeilen))
                            if zeilen[i].strip() == "if mode is SendMode.LIVE:")
                rumpf = [z.strip() for z in zeilen[live + 1:live + 8]
                         if z.strip() and not z.strip().startswith("#")]
                self.assertTrue(rumpf[0].startswith("versandsperre.pruefen("),
                                f"{name}: erste Anweisung im LIVE-Zweig ist "
                                f"{rumpf[0]!r}, nicht der Riegel")


class DryRunUndShadowBleibenOffen(unittest.TestCase):

    def test_dry_run_laeuft_am_riegel_vorbei(self):
        """Der Riegel haengt am LIVE-Zweig — ein DRY_RUN darf ihn nie sehen.
        Geprueft am echten run(), nicht an einer Nachbildung: eine spaeter
        verschobene Zeile wuerde hier auffallen."""
        from spaces.marketing.tools import _send_paranoid as sp
        with mock.patch.object(versandsperre, "pruefen",
                               side_effect=AssertionError("DRY_RUN hat den Riegel gesehen")), \
             mock.patch.object(sp, "_resolve_campaign",
                               side_effect=RuntimeError("bis hierhin und nicht weiter")):
            with self.assertRaises(RuntimeError):
                sp.run("c1", sp.SendMode.DRY_RUN)


if __name__ == "__main__":
    unittest.main()
