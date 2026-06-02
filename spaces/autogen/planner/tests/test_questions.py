"""Regression-Tests für die Frage-Aufbereitung (questions.clean_questions).

User-Feedback 2026-06-02: Telegram zeigte Validator-Jargon/Tech-Leaks statt
menschlicher Fragen. clean_questions muss:
- interne Notizen (read-only, Agent-Umleitung, optionale Schritte) VERWERFEN
- Tech-Tokens (openfang:, knowledge_query, desktop_skill) + sensible Pfade
  (`Datei`.md) ENTFERNEN
- step-id-Präfixe + 'bevor Schritt 3-5'-Jargon abschneiden
- gleiches Thema DEDUPEN
- approval-Fragen IMMER behalten (menschlich formuliert)

Aufruf:
    python spaces/autogen/planner/tests/test_questions.py
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_PLANNER = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("som_questions_test", _PLANNER / "_lib" / "questions.py")
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)

_passed, _failed = [], []


def check(name, cond):
    (_passed if cond else _failed).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)


# Die ECHTEN run_0022-Fragen (6 Jargon-Einträge)
RAW = [
    {"id": "daten_1", "typ": "daten", "frage": "zielstelle-klaeren: Zielstelle, Stellenausschreibung-URL/Text, Ausgabeformat und Anschreiben-Wunsch fehlen — Nutzer-Input zwingend erforderlich bevor Schritte 3–5 starten können."},
    {"id": "daten_2", "typ": "daten", "frage": "cv-fakten-updaten: Capability 'knowledge_query' ist read-only (rowboat-knowledge-Agent); tatsächliche Datei-Editierung wurde auf openfang:openclaude-coder umgeleitet."},
    {"id": "daten_3", "typ": "daten", "frage": "cv-fakten-updaten: Aktueller Semesterstand muss zur Laufzeit aus rowboat-knowledge/People/`Felix Baumann`.md gelesen werden."},
    {"id": "daten_4", "typ": "daten", "frage": "cv-datei-erstellen: Konkreter Unternehmensname für Zielpfad erst nach zielstelle-klaeren bekannt."},
    {"id": "daten_5", "typ": "daten", "frage": "cv-datei-erstellen: Falls PDF-Export gewünscht, muss desktop_skill als Zusatzschritt ergänzt werden."},
    {"id": "gate_1", "typ": "approval", "frage": "Freigabe für 'cv-datei-erstellen': Persistenter Schreibvorgang auf privatem Dokument (Felix Baumann.md)."},
]


def test_run0022_cleanup():
    print("Test 1: run_0022 — 6 Jargon-Einträge → wenige saubere Fragen")
    cleaned = q.clean_questions(RAW)
    blob = " ".join(c["frage"] for c in cleaned).lower()
    check("deutlich reduziert (<=3 Fragen statt 6)", len(cleaned) <= 3)
    check("mind. 1 Frage übrig", len(cleaned) >= 1)
    check("approval-Frage erhalten + menschlich", any(c["typ"] == "approval" and "darf ich" in c["frage"].lower() for c in cleaned))
    check("kein 'openfang:' Leak", "openfang:" not in blob)
    check("kein 'knowledge_query' Leak", "knowledge_query" not in blob)
    check("kein 'desktop_skill' Leak", "desktop_skill" not in blob)
    check("kein Backtick-Pfad Leak", "`felix" not in blob and ".md" not in blob)
    check("kein 'schritte 3' Jargon", "schritte 3" not in blob and "schritt 3" not in blob)
    check("kein step-id-Präfix (zielstelle-klaeren:)", "zielstelle-klaeren:" not in blob)


def test_internal_notes_dropped():
    print("Test 2: reine interne Notizen werden verworfen")
    notes = [
        {"id": "d1", "typ": "daten", "frage": "x-step: Capability 'knowledge_query' ist read-only, umgeleitet auf openfang:writer."},
        {"id": "d2", "typ": "daten", "frage": "y-step: Falls PDF gewünscht, desktop_skill als Zusatzschritt ergänzen."},
    ]
    cleaned = q.clean_questions(notes)
    check("beide internen Notizen verworfen", len(cleaned) == 0)


def test_real_question_kept():
    print("Test 3: echte Frage bleibt, wird humanisiert")
    real = [{"id": "d1", "typ": "daten", "frage": "ziel-klaeren: Für welche Stelle bewirbst du dich?"}]
    cleaned = q.clean_questions(real)
    check("echte Frage behalten", len(cleaned) == 1)
    check("step-id-Präfix entfernt", not cleaned[0]["frage"].lower().startswith("ziel-klaeren"))
    check("Fragezeichen erhalten", "?" in cleaned[0]["frage"])


def test_dedupe():
    print("Test 4: gleiches Thema (Zielstelle) wird dedupliziert")
    dupes = [
        {"id": "d1", "typ": "daten", "frage": "a-step: Zielstelle ist unbekannt."},
        {"id": "d2", "typ": "daten", "frage": "b-step: Zielstelle muss vor Schritt 3 vorliegen."},
        {"id": "d3", "typ": "daten", "frage": "c-step: Zielstelle/Branche fehlt noch."},
    ]
    cleaned = q.clean_questions(dupes)
    check("3× Zielstelle → 1 Frage", len(cleaned) == 1)


def test_approval_humanized():
    print("Test 5: Approval-Typen menschlich formuliert")
    checks = [
        ("Freigabe für 'x': Persistenter Schreibvorgang Dokument speichern.", "speicher"),
        ("Freigabe für 'y': Versand der E-Mail ans Jobcenter.", "absenden"),
        ("Freigabe für 'z': Löschen der alten Datei.", "löschen"),
    ]
    for raw_text, marker in checks:
        out = q.clean_questions([{"id": "g", "typ": "approval", "frage": raw_text}])
        check(f"'{marker}' erkannt", any(marker in c["frage"].lower() for c in out))


if __name__ == "__main__":
    test_run0022_cleanup()
    test_internal_notes_dropped()
    test_real_question_kept()
    test_dedupe()
    test_approval_humanized()
    print()
    print(f"=== {len(_passed)} PASSED, {len(_failed)} FAILED ===")
    if _failed:
        print("FEHLGESCHLAGEN:", _failed)
        sys.exit(1)
