"""E3 (task-brain-0019) — Tests für die Multi-Verb-Erkennung (core/multi_verb.py).

Architektur-Entscheidung E3 (architecture-decisions-2026-08-13-v1.md, User-
entschieden): Multi-Verb-Intents werden IMMER dekomponiert — ab zwei
unabhängigen Verben erzeugt Brain einen mehrstufigen Plan über den
hard/SoM-Pfad statt des 1-Hop-Capability-Shortcuts.

Definition „unabhängiges Verb" (siehe core/multi_verb.py Modul-Docstring):
ein Handlungsverb in Kommando-Position — am Äußerungsanfang oder unmittelbar
nach einem Koordinations-/Sequenz-Marker — mit eigener (ggf. elidierter)
Aktion. Explizite Sequenz-Marker („dann", „danach", „then", „after that")
nach einem Kommando-Verb zählen als eigener Schritt auch OHNE zweites
explizites Verb (Verb-Ellipse: „mach erst A, dann B").

NICHT multi-verb (Abgrenzung, je mit Testfällen unten):
  - Aufzählungen innerhalb EINES Verbs („lösche A, B und C")
  - Nomen-Koordination („eine Idee für APIs und Microservices")
  - Adverbial-Koordination („geh schnell und leise vor")
  - „und"/„and" ohne zweite Handlung
  - eingebettete Fragen mit „dann" („zeige mir was dann passiert")

Aufruf:
    python brain/the_brain/tests/test_multi_verb.py
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_BRAIN = Path(__file__).resolve().parents[1]   # brain/the_brain/
if str(_BRAIN) not in sys.path:
    sys.path.insert(0, str(_BRAIN))

_spec = importlib.util.spec_from_file_location(
    "multi_verb", _BRAIN / "core" / "multi_verb.py")
_mv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mv)

_passed: list[str] = []
_failed: list[str] = []


def check(name, cond):
    (_passed if cond else _failed).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)


# ── Positivfälle: ≥2 unabhängige Verben → multi ──────────────────────────────
_MULTI_CASES = [
    # cat0 0.6 — der Referenzfall der Testerwartung (Verb + Sequenz-Ellipse)
    "mach erst A, dann B und dann C",
    # zwei Kommando-Verben, koordiniert mit "und"
    "erstelle eine idee und füge sie der bubble hinzu",
    "öffne die datei und lösche die alte version",
    "analysiere die logs und schreibe eine zusammenfassung",
    # drei Verben ("baue", "leg ... ab"; "und" doppelt)
    "baue ein anschreiben und eine tabelle und leg sie ab",
    # Sequenz-Marker nach Verb (danach/anschließend)
    "zeige den status und starte danach den container",
    "speichere die datei und öffne anschließend den ordner",
    # Komma-Koordination zweier Verben
    "erstelle die bubble marketing, füge die idee kampagne hinzu",
    # Englisch
    "create a report and send it to anna",
    "search the logs, then delete the old entries",
    "first build the image and then deploy it",
    "open the dashboard and check the status",
    "scan the repo for secrets and write a summary",
    # Sequenz-Ellipse Englisch (Verb nur vorne, "then" trägt Schritt 2)
    "run the tests, then the linter",
    # Coding-Ketten (cat5 5.5 / 5.7 — E3: auch idiomatische Paare dekomponieren)
    "fix the bug in foo.py and then refactor utils.py",
    "commit and push my changes to github",
]

# ── Negativfälle: EIN Verb (oder keins) → single ─────────────────────────────
_SINGLE_CASES = [
    # cat0 0.3 — Nomen-Koordination unter EINEM Verb (semantisch trotzdem hard,
    # aber NICHT über die Multi-Verb-Regel)
    "erstelle mir eine Excel auf dem Desktop mit Spalten Name und Betrag",
    # Nomen-Koordination
    "erstelle eine idee für APIs und Microservices",
    "create a bubble called marketing and sales",
    # Aufzählung innerhalb EINES Verbs
    "lösche die ideen A, B und C",
    "delete the notes alpha, beta and gamma",
    # ein Verb, zwei Objekte
    "verbinde die idee X und die idee Y",
    "show me the report and the summary",
    # Adverbial-Koordination
    "geh schnell und leise vor",
    # kein Kommando-Verb
    "was ist der unterschied zwischen A und B",
    "wie gehts dir",
    "danke dir",
    # "dann" in eingebetteter Frage — kein Sequenz-Schritt
    "zeige mir was dann passiert",
    # Verb-Lexem im Nomen-Kontext, kein zweites Kommando
    "starte die suche nach dem letzten bericht",
    # ein Verb mit Präpositionalketten
    "öffne example.com im browser",
    # leere/degenerierte Eingaben
    "",
    "und",
]


def test_multi_cases():
    print("Test 1: Multi-Verb-Positivfälle (DE+EN) → is_multi_verb=True")
    for c in _MULTI_CASES:
        check(f"multi: «{c[:52]}»", _mv.is_multi_verb(c) is True)


def test_single_cases():
    print("Test 2: Abgrenzungsfälle (DE+EN) → is_multi_verb=False")
    for c in _SINGLE_CASES:
        check(f"single: «{c[:52] if c else '(leer)'}»", _mv.is_multi_verb(c) is False)


def test_explain_evidence():
    print("Test 3: explain() liefert nachvollziehbare Evidenz")
    out = _mv.explain("erstelle eine idee und füge sie der bubble hinzu")
    check("explain: is_multi_verb", out["is_multi_verb"] is True)
    check("explain: 2 Kommando-Verben", len(out["verbs"]) == 2)
    check("explain: 'erstelle' erkannt", "erstelle" in out["verbs"])
    check("explain: 'füge' erkannt", "füge" in out["verbs"] or "fuege" in out["verbs"])

    out2 = _mv.explain("mach erst A, dann B und dann C")
    check("explain: Sequenz-Ellipse ist multi", out2["is_multi_verb"] is True)
    check("explain: Sequenz-Marker belegt", len(out2["sequence_markers"]) >= 1)

    out3 = _mv.explain("lösche die ideen A, B und C")
    check("explain: Aufzählung single", out3["is_multi_verb"] is False)
    check("explain: genau 1 Kommando-Verb", len(out3["verbs"]) == 1)


def test_case_insensitive():
    print("Test 4: Groß-/Kleinschreibung irrelevant")
    check("upper multi", _mv.is_multi_verb("Erstelle eine Idee UND Füge sie hinzu") is True)
    check("upper single", _mv.is_multi_verb("Lösche die Ideen A, B und C") is False)


if __name__ == "__main__":
    test_multi_cases()
    test_single_cases()
    test_explain_evidence()
    test_case_insensitive()
    print()
    print(f"=== {len(_passed)} PASSED, {len(_failed)} FAILED ===")
    if _failed:
        print("FEHLGESCHLAGEN:", _failed)
        sys.exit(1)
