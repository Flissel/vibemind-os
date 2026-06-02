"""Regression-Tests für das Antwort-Parsing des som-resume-Wrappers.

Deckt die im echten OpenFang-Volltest (2026-06-02) gefundenen Bugs ab:
- OpenFang prependet '[User]\\n' → muss abgeschnitten werden
- OpenFang (builtin:chat) liefert den GANZEN Konversations-Verlauf →
  nur die LETZTE [User]-Eingabe zählt
- 'antwort:'-Trigger + optionales 'run_XXXX:'-Präfix werden entfernt
- 'x' / 'antwort: x' = Abbruch (None)

Aufruf:
    python spaces/autogen/planner/tests/test_resume_parse.py
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Wrapper liegt im Repo-root scripts/ (zwei Ebenen über planner/ + ../../..)
_PLANNER = Path(__file__).resolve().parents[1]
_REPO = _PLANNER.parents[3]   # Vibemind_V1/
_WRAPPER = _REPO / "scripts" / "openfang_som_resume_wrapper.py"

spec = importlib.util.spec_from_file_location("som_resume_wrap_test", _WRAPPER)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

_passed, _failed = [], []


def check(name, cond):
    (_passed if cond else _failed).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)


CASES = [
    # (input, expected parse_answers output)
    ("antwort: AI", {"1": "AI"}),
    ("1: AI 2: ja", {"1": "AI", "2": "ja"}),
    ("Werkstudent Startup", {"1": "Werkstudent Startup"}),
    ("x", None),
    ("abbrechen", None),
    # OpenFang prependet [User]\n
    ("[User]\nantwort: AI-Engineer Vollzeit", {"1": "AI-Engineer Vollzeit"}),
    ("[User]\nx", None),
    ("[User]\nantwort: x", None),
    # Telegram-Listener: [Replying to …]\n
    ("[Replying to Bot: Frage?]\nWerkstudent", {"1": "Werkstudent"}),
    # builtin:chat liefert GANZEN Verlauf → nur letzte [User]-Eingabe zählt
    ("[User]\nantwort: ALT\n\n[Assistant]\nDanke…\n\n[User]\nantwort: NEU",
     {"1": "NEU"}),
    ("[User]\n1: A\n\n[Assistant]\n…\n\n[User]\nx", None),
    # optionales run-Präfix für gezielten Run
    ("[User]\nrun_0021: antwort: Konzern", {"1": "Konzern"}),
]


def test_parse():
    print("parse_answers — Channel-Präfix + Transcript + Trigger")
    for inp, exp in CASES:
        got = w.parse_answers(inp)
        check(f"{inp[:45]!r} -> {exp}", got == exp)


def test_explicit_run_id():
    print("_explicit_run_id — auch mit [User]-Präfix")
    check("'[User]\\nrun_0019: x' -> run_0019",
          w._explicit_run_id("[User]\nrun_0019: doch Konzern") == "run_0019")
    check("'antwort: x' -> None (kein run-id)",
          w._explicit_run_id("antwort: AI") is None)


if __name__ == "__main__":
    test_parse()
    test_explicit_run_id()
    print()
    print(f"=== {len(_passed)} PASSED, {len(_failed)} FAILED ===")
    if _failed:
        print("FEHLGESCHLAGEN:", _failed)
        sys.exit(1)
