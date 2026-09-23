"""Reine Bausteine der Research-Bubble-Kopplung."""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "brief.py"


def load_brief():
    spec = importlib.util.spec_from_file_location("spaces_research_brief", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("spaces/research/brief.py must be importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


JOB_RE = re.compile(r"^job_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$")
ARTIFACT_RE = re.compile(r"^artifact_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$")


class UlidTests(unittest.TestCase):
    def test_job_id_matches_the_migration_check_constraint(self) -> None:
        brief = load_brief()
        for _ in range(50):
            self.assertRegex(brief.new_job_id(), JOB_RE)

    def test_artifact_ref_matches_the_migration_check_constraint(self) -> None:
        brief = load_brief()
        for _ in range(50):
            self.assertRegex(brief.new_artifact_ref(), ARTIFACT_RE)

    def test_ulid_excludes_the_ambiguous_crockford_letters(self) -> None:
        brief = load_brief()
        produced = "".join(brief.new_ulid() for _ in range(200))
        for forbidden in "ILOU":
            self.assertNotIn(forbidden, produced)

    def test_ulid_is_deterministic_for_fixed_inputs(self) -> None:
        brief = load_brief()
        first = brief.new_ulid(now_ms=1_726_000_000_000, rand=12345)
        second = brief.new_ulid(now_ms=1_726_000_000_000, rand=12345)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 26)


class CitationTests(unittest.TestCase):
    def test_counts_unique_urls_only(self) -> None:
        brief = load_brief()
        text = (
            "Siehe https://example.test/a und https://example.test/b\n"
            "nochmals https://example.test/a\n"
        )
        self.assertEqual(brief.count_citations(text), 2)

    def test_report_without_sources_counts_zero(self) -> None:
        brief = load_brief()
        self.assertEqual(brief.count_citations("Kein einziger Beleg."), 0)

    def test_trailing_punctuation_is_stripped(self) -> None:
        brief = load_brief()
        self.assertEqual(brief.count_citations("von https://example.test/x."), 1)


class ExpectedTests(unittest.TestCase):
    def test_lifts_lines_under_a_recognised_heading(self) -> None:
        brief = load_brief()
        text = "Rolle\nBlabla\n\nErgebnis\nDrei USPs nennen.\nJede Aussage belegen.\n"
        expected = brief.extract_expected(text)
        self.assertIn("Drei USPs nennen.", expected)
        self.assertIn("Jede Aussage belegen.", expected)
        self.assertNotIn("Blabla", expected)

    def test_returns_empty_when_no_heading_matches(self) -> None:
        brief = load_brief()
        self.assertEqual(brief.extract_expected("Nur Fliesstext ohne Gliederung."), "")


class ComposeTests(unittest.TestCase):
    def _compose(self, **overrides):
        brief = load_brief()
        kwargs = dict(
            bubble_title="Sheerlay",
            bubble_nodes=[{"title": "Kernidee", "content": "OCR-Etikettenscan"}],
            user_brief="Aufgabe\nWettbewerbsanalyse durchfuehren.",
            output_path="/tmp/research_job_v1_X.md",
            depth="thorough",
            output_style="detailed",
            citation_style="academic_apa",
            language="german",
        )
        kwargs.update(overrides)
        return brief.compose_brief(**kwargs)

    def test_carries_bubble_context_and_user_brief(self) -> None:
        text = self._compose()
        self.assertIn("Sheerlay", text)
        self.assertIn("OCR-Etikettenscan", text)
        self.assertIn("Wettbewerbsanalyse durchfuehren.", text)

    def test_states_the_absolute_output_path_verbatim(self) -> None:
        text = self._compose(output_path="/tmp/research_job_v1_ABC.md")
        self.assertIn("/tmp/research_job_v1_ABC.md", text)

    def test_carries_the_per_run_overrides(self) -> None:
        text = self._compose()
        for token in ("thorough", "detailed", "academic_apa", "german"):
            self.assertIn(token, text)

    def test_names_the_missing_expected_section_when_brief_has_none(self) -> None:
        text = self._compose(user_brief="Nur Fliesstext.")
        self.assertIn("Erwartetes Ergebnis", text)
        self.assertIn("in der Vorschau", text)

    def test_empty_bubble_still_produces_a_usable_brief(self) -> None:
        text = self._compose(bubble_nodes=[])
        self.assertIn("Wettbewerbsanalyse durchfuehren.", text)


class JobIdFromBriefTests(unittest.TestCase):
    def test_reads_the_job_id_out_of_the_dictated_output_path(self) -> None:
        brief = load_brief()
        text = (
            "[Pflicht]\n"
            "1. Schreibe den Report unter EXAKT diesem Pfad: "
            "C:\\Users\\X\\.openfang\\research-artifacts\\research_job_v1_01M2N5VPP9T2PX2GJ7NG3F9XHN.md\n"
        )
        self.assertEqual(
            brief.job_id_from_brief(text), "job_v1_01M2N5VPP9T2PX2GJ7NG3F9XHN"
        )

    def test_reads_it_from_a_posix_path_too(self) -> None:
        brief = load_brief()
        text = "Pfad: /home/u/.openfang/research-artifacts/research_job_v1_0000000000000000000000000A.md"
        self.assertEqual(
            brief.job_id_from_brief(text), "job_v1_0000000000000000000000000A"
        )

    def test_returns_none_when_the_brief_carries_no_path(self) -> None:
        brief = load_brief()
        self.assertIsNone(brief.job_id_from_brief("Nur Fliesstext ohne Pfad."))

    def test_ignores_a_malformed_job_id(self) -> None:
        brief = load_brief()
        self.assertIsNone(brief.job_id_from_brief("research_job_v1_zzz.md"))

    def test_takes_the_first_when_several_appear(self) -> None:
        brief = load_brief()
        text = (
            "research_job_v1_0000000000000000000000000A.md und spaeter "
            "research_job_v1_0000000000000000000000000B.md"
        )
        self.assertEqual(
            brief.job_id_from_brief(text), "job_v1_0000000000000000000000000A"
        )


if __name__ == "__main__":
    unittest.main()
