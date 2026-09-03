"""F2: die Quelle sales-claw ist eine bekannte Vorschlagsquelle — sonst
normalisiert propose_audience sie zu hand:unknown, und die UI verliert die
Herkunft (Spec E5)."""
import unittest

from spaces.marketing.tools import marketing_tools as mt


class TestQuelleSalesClaw(unittest.TestCase):
    def test_sales_claw_ist_erlaubte_quelle(self):
        self.assertIn("sales-claw", mt._ALLOWED_HAND_SOURCES)

    def test_bisherige_quellen_bleiben(self):
        for q in ("lead-hand", "researcher-hand", "collector-hand",
                  "browser-hand", "predictor-hand", "manual"):
            self.assertIn(q, mt._ALLOWED_HAND_SOURCES)


if __name__ == "__main__":
    unittest.main()
