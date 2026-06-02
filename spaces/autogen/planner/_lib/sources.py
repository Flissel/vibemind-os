"""SoM Planner — Quellen-Index + Redaction (Daten-Kontext für den Planner).

Problem (verifiziert an run_0020): der Planner RÄT Pfade (z.B. ``Bewerbung/`` das
nicht existiert) und Capabilities, weil er die realen Quellen nicht kennt. Sogar
korrekt übernommene Pfade sind kaputt (``Felix Baumann``.md hat echte Backticks
im KB-Dateinamen). Lösung: vor dem Planen einen Index der ECHTEN verfügbaren
Quellen + nicht-sensible Inhalte in den Prompt geben.

Sicherheit (User-Entscheidung): Index aller Pfade; nicht-sensible Inhalte direkt;
SENSIBLE Werte (BG-Nr, IBAN, Beträge, Namen Dritter, Geburtsdatum) werden durch
strukturerhaltende Platzhalter ``<SENSIBEL:typ>`` ersetzt. So sieht der Planner
die Struktur (welche Felder es gibt), nie die Werte. Der Executor löst die
Platzhalter erst zur Laufzeit lokal auf (bestehende Deny-Liste greift weiter).

Eigenständig importierbar (kein AutoGen) — einzeln testbar.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# ── Scopes: wo der Index sucht. Pfade dürfen pro Maschine via env überschrieben
#    werden (Tests setzen SOM_SOURCE_* auf temp-Dirs).
_HOME = Path(os.path.expanduser("~"))


def _scopes() -> list[dict[str, Any]]:
    """Liste der durchsuchten Wurzeln mit Sensibilitäts-Klasse.

    sensitive=True  → nur Pfade, Inhalt NUR redaktiert (Platzhalter).
    sensitive=False → Pfade + Inhalt direkt (Token-budgetiert).
    """
    rowboat = Path(os.environ.get("SOM_SOURCE_ROWBOAT", str(_HOME / ".rowboat" / "knowledge")))
    buergergeld = Path(os.environ.get("SOM_SOURCE_BUERGERGELD", str(_HOME / "Documents" / "Buergergeld")))
    return [
        # Rowboat-KB: Topics/Projects/Notes sind nicht-sensibel; People/ ist sensibel
        # (echte Personen-Profile). Wir markieren People/ pro Datei (s.u.).
        {"root": rowboat, "label": "rowboat-knowledge", "default_sensitive": False,
         "sensitive_subdirs": ["People"]},
        # Bürgergeld: KOMPLETT sensibel (Sozialdaten). Nur Pfade + Redaction.
        {"root": buergergeld, "label": "buergergeld", "default_sensitive": True,
         "sensitive_subdirs": []},
    ]


# Dateien/Ordner die nie indexiert werden (Rauschen / Geheimnisse / Caches).
_SKIP_PARTS = {".backups", ".text-index", ".git", "__pycache__", "node_modules"}
_SKIP_SUFFIX = {".bak", ".tmp", ".lock"}
_INDEXABLE_SUFFIX = {".md", ".yaml", ".yml", ".txt", ".json", ".docx", ".pdf", ".xlsx"}


# ── Redaction ─────────────────────────────────────────────────────────────────
# Strukturerhaltend: Feldname bleibt, Wert wird Platzhalter. Reihenfolge zählt
# (spezifische Muster zuerst, sonst frisst € jede Zahl).
_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # IBAN (mit/ohne Leerzeichen)
    (re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?\d{4}){4,5}(?:[ ]?\d{1,2})?\b"), "<SENSIBEL:iban>"),
    # BG-Nummer  84308//0200188
    (re.compile(r"\b\d{3,6}//\d{5,9}\b"), "<SENSIBEL:bg_nummer>"),
    # Kundennummer  843E387738  (Ziffern+Buchstaben, >=8 Stellen, mind. 1 Buchstabe)
    (re.compile(r"\b(?=[0-9A-Z]*[A-Z])(?=[0-9A-Z]*[0-9])[0-9A-Z]{8,}\b"), "<SENSIBEL:kundennummer>"),
    # Geburtsdatum / ISO-Datum  1996-02-08
    (re.compile(r"\b(19|20)\d{2}-\d{2}-\d{2}\b"), "<SENSIBEL:datum>"),
    # Geldbeträge  850.00 / 7.800 / 1950  in Verbindung mit eur/€/_eur
    (re.compile(r"(?i)((?:eur|€|_eur:?)\s*)[\d.,]+"), r"\1<SENSIBEL:betrag>"),
    (re.compile(r"\b[\d.,]+\s*(?i:eur|€)\b"), "<SENSIBEL:betrag>"),
    # E-Mail
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<SENSIBEL:email>"),
    # Telefon (>=7 Ziffern am Stück, evtl. mit + / Leerzeichen)
    (re.compile(r"\+?\d[\d /-]{6,}\d"), "<SENSIBEL:telefon>"),
]


def redact(text: str) -> str:
    """Ersetzt erkannte sensible Werte durch ``<SENSIBEL:typ>``-Platzhalter.

    Strukturerhaltend — Feldnamen/Schlüssel bleiben, nur Werte werden maskiert.
    Bewusst konservativ (lieber zu viel maskieren als ein Leak)."""
    if not text:
        return text
    out = text
    for pattern, repl in _REDACT_PATTERNS:
        out = pattern.sub(repl, out)
    return out


# ── Index ─────────────────────────────────────────────────────────────────────
def _is_sensitive(rel_parts: tuple[str, ...], scope: dict[str, Any]) -> bool:
    if scope["default_sensitive"]:
        return True
    top = rel_parts[0] if rel_parts else ""
    return top in scope["sensitive_subdirs"]


def build_source_index() -> list[dict[str, Any]]:
    """Listet echte verfügbare Quellen über alle Scopes.

    Rückgabe: [{path, label, sensitive, kind}] — relativ zur Scope-Wurzel
    angezeigter Pfad (verbatim, inkl. eventueller Backticks im Namen, damit der
    Planner den ECHTEN Pfad sieht und nicht rät)."""
    out: list[dict[str, Any]] = []
    for scope in _scopes():
        root: Path = scope["root"]
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            if any(part in _SKIP_PARTS for part in p.parts):
                continue
            if p.suffix.lower() in _SKIP_SUFFIX:
                continue
            if p.suffix.lower() not in _INDEXABLE_SUFFIX:
                continue
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            out.append({
                "path": rel.as_posix(),
                "label": scope["label"],
                "sensitive": _is_sensitive(rel.parts, scope),
                "kind": p.suffix.lower().lstrip("."),
            })
    return out


# Nur diese Endungen liefern direkt Text-Inhalt (docx/pdf/xlsx nur als Pfad).
_TEXT_SUFFIX = {".md", ".yaml", ".yml", ".txt", ".json"}


def _relevance(intent: str, path: str) -> int:
    """Billige Token-Überlappung zwischen Intent und Dateipfad (Ranking)."""
    q = set(re.findall(r"\w+", intent.lower()))
    hay = set(re.findall(r"\w+", path.lower()))
    return len(q & hay)


def context_block(intent: str, max_chars: int = 3500, max_inline: int = 5,
                  max_index: int = 60) -> str:
    """Baut den Planner-Prompt-Block: Quellen-Index (intent-relevanteste Pfade) +
    redaktierte Inhalte der relevantesten nicht-sensiblen Text-Dateien.

    - SENSIBLE Dateien: nur Pfad + [SENSIBEL]-Marker, NIE Inhalt.
    - Nicht-sensible Text-Dateien: Inhalt direkt, aber durch redact() gejagt
      (Defense-in-depth, falls doch mal ein Wert drinsteht).
    - Binär (docx/pdf/xlsx): nur Pfad (kein Inline-Inhalt).

    Der Index wird auf max_index intent-relevanteste Quellen begrenzt — die KB
    hat hunderte Dateien, das würde sonst jeden Prompt sprengen. Sensible Quellen
    werden bevorzugt mit aufgenommen (der Planner soll WISSEN dass es sie gibt),
    aber nur als Pfad."""
    index = build_source_index()
    if not index:
        return ""

    # Index nach Intent-Relevanz ranken; sensible Quellen bekommen einen kleinen
    # Bonus, damit der Planner ihre Existenz sieht (z.B. Buergergeld/stammdaten).
    def _rank(e: dict[str, Any]) -> int:
        return _relevance(intent, e["path"]) + (1 if e["sensitive"] else 0)

    ranked = sorted(index, key=_rank, reverse=True)
    shown = ranked[:max_index]
    omitted = len(index) - len(shown)

    lines: list[str] = ["VERFÜGBARE QUELLEN (real indexiert — nutze EXAKT diese Pfade, rate keine):"]
    for e in shown:
        mark = "  [SENSIBEL — Inhalt nur lokal zur Laufzeit]" if e["sensitive"] else ""
        lines.append(f"  - {e['label']}/{e['path']}{mark}")
    if omitted > 0:
        lines.append(f"  …(+{omitted} weitere Quellen — bei Bedarf gezielt per Tool suchen)")

    # Inline-Inhalte: nicht-sensible Text-Dateien, nach Intent-Relevanz sortiert.
    candidates = [
        e for e in index
        if not e["sensitive"] and Path(e["path"]).suffix.lower() in _TEXT_SUFFIX
    ]
    candidates.sort(key=lambda e: _relevance(intent, e["path"]), reverse=True)

    inline: list[str] = []
    used = sum(len(line) + 1 for line in lines)
    for e in candidates[: max_inline * 3]:
        if len([x for x in inline if x.startswith("### ")]) >= max_inline:
            break
        root = next((s["root"] for s in _scopes() if s["label"] == e["label"]), None)
        if root is None:
            continue
        fp = root / e["path"]
        try:
            raw = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        if not raw.strip():
            continue
        snippet = redact(raw).strip()
        budget = max_chars - used
        if budget < 200:
            break
        if len(snippet) > budget:
            snippet = snippet[:budget].rstrip() + "\n…(gekürzt)"
        chunk = f"### {e['label']}/{e['path']}\n{snippet}"
        inline.append(chunk)
        used += len(chunk) + 1

    if inline:
        lines.append("\nINHALT relevanter nicht-sensibler Quellen (sensible Werte sind maskiert):")
        lines.extend(inline)

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    try:
        sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    # Redaction-Selbsttest (kein echter Datei-Zugriff nötig)
    sample = (
        "bg_nummer: 84308//0200188\n"
        "iban: DE75 7016 9388 0000 6259 30\n"
        "kundennummer: 843E387738\n"
        "geburtsdatum: 1996-02-08\n"
        "gesamt_miete_eur: 850.00\n"
        "email: unit-y-ai@gmx.de\n"
    )
    red = redact(sample)
    assert "84308//0200188" not in red, red
    assert "DE75" not in red, red
    assert "843E387738" not in red, red
    assert "1996-02-08" not in red, red
    assert "850.00" not in red, red
    assert "unit-y-ai@gmx.de" not in red, red
    assert "bg_nummer:" in red and "iban:" in red, "Struktur muss erhalten bleiben"
    print("redact() selftest OK:")
    print(red)

    idx = build_source_index()
    print(f"\nbuild_source_index: {len(idx)} Quellen")
    sens = [e for e in idx if e["sensitive"]]
    print(f"  davon sensibel: {len(sens)}")
    cb = context_block("Plane wie ich meine Bewerbungsunterlagen aktualisiere")
    print(f"\ncontext_block: {len(cb)} Zeichen")
    # Kein sensibler Wert darf im Block stehen
    assert "84308//0200188" not in cb
    assert "DE75 7016" not in cb
    print("sources.py selftest OK (keine sensiblen Werte im context_block)")
