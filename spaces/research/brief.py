"""Reine Bausteine der Research-Bubble-Kopplung: IDs, Zitate, Auftragsbau.

Kein Netz, keine Datenbank - damit der Rest der Kopplung gegen diese
Funktionen getestet werden kann, ohne einen Agenten zu starten.
"""

from __future__ import annotations

import re
import secrets
import time

# Crockford-Base32: ohne I, L, O, U. Deckungsgleich mit der Zeichenklasse der
# CHECK-Constraints in 20260817_research_report_artifacts.sql.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_URL_RE = re.compile(r"https?://[^\s<>()\]\[\"']+")

_EXPECTED_HEADINGS = (
    "ergebnis", "ergebnisse", "aufgabe", "aufgaben",
    "anforderungen", "deliverables", "ziel", "ziele",
)


def new_ulid(now_ms: int | None = None, rand: int | None = None) -> str:
    timestamp = int(time.time() * 1000) if now_ms is None else now_ms
    randomness = secrets.randbits(80) if rand is None else rand
    value = (timestamp << 80) | randomness
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_job_id() -> str:
    return f"job_v1_{new_ulid()}"


def new_artifact_ref() -> str:
    return f"artifact_v1_{new_ulid()}"


def count_citations(text: str) -> int:
    found = {url.rstrip(".,;:") for url in _URL_RE.findall(text or "")}
    return len(found)


def extract_expected(user_brief: str) -> str:
    """Hebt die Zeilen unter einer erkennbaren Ergebnis-Ueberschrift heraus.

    Bewusst mechanisch: kein Modellaufruf, weil dessen Ergebnis niemand
    pruefen wuerde. Findet sich keine Ueberschrift, fuellt der Mensch den
    Abschnitt im Vorschau-Gate.
    """
    lines = (user_brief or "").splitlines()
    collected: list[str] = []
    capturing = False
    for line in lines:
        stripped = line.strip()
        normalized = stripped.lower().rstrip(":").strip("# ").strip()
        if normalized in _EXPECTED_HEADINGS:
            capturing = True
            continue
        if capturing:
            if not stripped:
                if collected:
                    break
                continue
            if normalized in _EXPECTED_HEADINGS:
                break
            collected.append(stripped)
    return "\n".join(collected)


def compose_brief(
    *,
    bubble_title: str,
    bubble_nodes: list[dict],
    user_brief: str,
    output_path: str,
    depth: str,
    output_style: str,
    citation_style: str,
    language: str,
) -> str:
    context_lines = []
    for node in bubble_nodes:
        title = (node.get("title") or "").strip()
        content = (node.get("content") or "").strip()
        if not title and not content:
            continue
        context_lines.append(f"- {title}: {content}" if content else f"- {title}")
    context = "\n".join(context_lines) if context_lines else "- (keine Inhalte hinterlegt)"

    expected = extract_expected(user_brief)
    if not expected:
        expected = (
            "(Im Brief nicht erkennbar - bitte in der Vorschau selbst eintragen, "
            "damit klar ist, was herauskommen soll.)"
        )

    return f"""[Hintergrund aus Bubble "{bubble_title}"]
{context}

[Auftrag]
{user_brief.strip()}

[Erwartetes Ergebnis]
{expected}

[Vorgaben fuer diesen Lauf - sie ueberschreiben deine User Configuration]
- Tiefe: {depth}
- Ausgabestil: {output_style}
- Zitierweise: {citation_style}
- Sprache: {language}

[Pflicht]
1. Schreibe den vollstaendigen Report mit file_write unter EXAKT diesem
   absoluten Pfad: {output_path}
2. Der Report muss mindestens eine echte Quelle mit URL enthalten.
   Erfinde nichts. Fehlende Information wird als fehlend benannt.
3. Antworte am Ende mit genau diesem Pfad.
"""
