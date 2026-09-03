"""Schaufenster: nie ueberschreiben — zwei Laeufe am selben Tag mit demselben
Slug muessen BEIDE Artefakte hinterlassen (gemessen 03.09.2026: das
01-Uhr-Briefing wurde vom 13-Uhr-Lauf ueberschrieben, Beweis verloren).
"""
import os
import tempfile
import unittest
from unittest import mock

from spaces.marketing.claw import schaufenster


class TestNieUeberschreiben(unittest.TestCase):
    def test_gleicher_tag_gleicher_slug_beide_dateien_da(self):
        tmp = tempfile.mkdtemp()
        with mock.patch.dict("os.environ", {"SCHAUFENSTER_DIR": tmp}):
            p1 = schaufenster.ablegen("Early Access", "briefing.md", "erster Lauf")
            p2 = schaufenster.ablegen("Early Access", "briefing.md", "zweiter Lauf")
        self.assertNotEqual(p1, p2)
        self.assertEqual(open(p1, encoding="utf-8").read(), "erster Lauf")
        self.assertEqual(open(p2, encoding="utf-8").read(), "zweiter Lauf")

    def test_verschiedene_namen_bleiben_unangetastet(self):
        tmp = tempfile.mkdtemp()
        with mock.patch.dict("os.environ", {"SCHAUFENSTER_DIR": tmp}):
            p1 = schaufenster.ablegen("Thema", "ad-01.md", "a")
            p2 = schaufenster.ablegen("Thema", "ad-02.md", "b")
        self.assertTrue(os.path.exists(p1) and os.path.exists(p2))
        self.assertNotEqual(p1, p2)


if __name__ == "__main__":
    unittest.main()
