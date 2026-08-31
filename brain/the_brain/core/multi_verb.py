"""E3 (task-brain-0019) — Multi-Verb-Erkennung für die Immer-Dekomposition.

Architektur-Entscheidung E3 (docs/governance/architecture-decisions-2026-08-13-v1.md
im Superproject, User-entschieden): Multi-Verb-Intents werden IMMER dekomponiert.
Ab zwei unabhängigen Verben erzeugt Brain einen mehrstufigen Plan über den
hard/SoM-Pfad; der 1-Hop-Capability-Shortcut ist für solche Intents tabu.

Definition „unabhängiges Verb":
Ein Handlungsverb in **Kommando-Position** — d.h. am Äußerungsanfang (nach
höchstens wenigen Füllwörtern wie „bitte", „erst", „first") oder unmittelbar
nach einem Koordinations-/Sequenz-Marker („und", „and", „sowie", Komma,
Semikolon, „dann", „danach", „anschließend", „then", „after that") — das eine
eigene ausführbare Aktion bezeichnet.

Zusatzregel Sequenz-Ellipse: Ein expliziter **Sequenz-Marker** („dann",
„danach", „anschließend", „then", „afterwards", „after that") NACH einem bereits
gesehenen Kommando-Verb eröffnet einen eigenen Schritt auch ohne zweites
explizites Verb — „mach erst A, dann B und dann C" ist dreischrittig, obwohl
nur „mach" als Verb auftaucht (Verb-Ellipse). Marker am Äußerungsende ohne
Fortsetzung zählen nicht („mach das dann").

Abgrenzung — NICHT multi-verb:
  - Aufzählungen innerhalb EINES Verbs: „lösche A, B und C" (ein Verb, drei
    Objekte; nach Komma/„und" folgt kein Verb).
  - Nomen-Koordination: „erstelle eine Idee für APIs und Microservices".
  - Adverbial-Koordination: „geh schnell und leise vor".
  - „und"/„and" ohne zweite Handlung generell: nach dem Koordinator muss ein
    Kommando-Verb stehen, sonst zählt der Slot nicht.
  - Eingebettete Fragen mit „dann": „zeige mir was dann passiert" — Marker
    unmittelbar nach Interrogativum („was", „wie", „what", „how") zählt nicht.
  - Verb-Lexeme in Nomen-Position: „starte die Suche" — „suche" steht nicht in
    Kommando-Position und zählt nicht.

Bekannte, bewusst akzeptierte Grenzen (deterministisch, kein LLM/Embedding):
  - Höfliche Modalkonstruktionen mit Infinitiv am Satzende („kannst du X
    erstellen und Y hinzufügen") werden nicht sicher erkannt — der semantische
    Difficulty-Router bleibt für solche Fälle zuständig.
  - Das Verb-Lexikon ist kuratiert (DE-Imperativ + EN-Grundform) und nicht
    vollständig; Erweiterungen gehören hierher, nicht in die Aufrufer.

Konsumenten:
  - core/difficulty_router.py  — hebt easy/medium auf hard (SoM-Pfad).
  - web/routers/introspection.py — Capability-Shortcut-Gate (_looks_like_multi_action).
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["is_multi_verb", "explain"]

# Kommando-Verben: DE-Imperativformen (2. Person Singular, inkl. Umlaut- und
# ASCII-Varianten) + EN-Grundformen. Ganze Tokens — keine Substring-Matches.
_COMMAND_VERBS = frozenset({
    # Deutsch (Imperativ)
    "erstelle", "erstell", "lege", "leg", "füge", "fuege", "fuge",
    "lösche", "loesche", "losche", "entferne", "zeige", "zeig", "liste",
    "verbinde", "trenne", "verlinke", "gehe", "geh", "betrete", "verlasse",
    "öffne", "oeffne", "offne", "schließe", "schliesse", "sende", "schicke",
    "schreibe", "schreib", "speichere", "bearbeite", "suche", "such", "finde",
    "starte", "stoppe", "beende", "baue", "bau", "mache", "mach", "wandle",
    "benenne", "umbenenne", "analysiere", "untersuche", "erkläre", "erklaere",
    "erweitere", "bewerte", "prüfe", "pruefe", "prufe", "plane", "exportiere",
    "importiere", "kopiere", "verschiebe", "notiere", "generiere",
    "aktualisiere", "installiere", "berechne", "zähle", "zaehle", "zahle",
    "fasse", "richte", "lies", "lade", "drucke", "teile", "ordne",
    # Englisch (Grundform)
    "create", "make", "build", "add", "delete", "remove", "update", "rename",
    "show", "list", "display", "open", "close", "send", "write", "save",
    "store", "edit", "search", "find", "fetch", "run", "start", "stop",
    "deploy", "export", "import", "copy", "move", "connect", "disconnect",
    "link", "analyze", "analyse", "check", "scan", "generate", "summarize",
    "summarise", "explain", "expand", "evaluate", "count", "read", "load",
    "print", "share", "sort", "convert", "install", "compute", "take",
    # Coding-Domäne (cat5: „fix … and then refactor", „commit and push")
    "fix", "refactor", "commit", "push", "pull", "merge", "revert", "rebase",
    "debug", "implement", "test", "compile", "rewrite", "patch", "review",
    # Deutsch Coding
    "behebe", "korrigiere", "repariere", "refaktoriere", "implementiere",
    "teste", "committe", "pushe", "merge", "debugge",
})

# Koordinatoren eröffnen einen neuen Kommando-Slot (dort MUSS ein Verb stehen,
# damit der Slot zählt).
_COORDINATORS = frozenset({"und", "and", "sowie", ",", ";"})

# Sequenz-Marker: explizite Reihenfolge-Semantik; nach einem gesehenen
# Kommando-Verb zählen sie als eigener Schritt (Verb-Ellipse erlaubt).
_SEQ_MARKERS = frozenset({
    "dann", "danach", "anschließend", "anschliessend",
    "then", "afterwards", "afterward",
})

# Interrogativa direkt VOR einem Sequenz-Marker neutralisieren ihn
# („was dann passiert" ist kein Folgeschritt).
_INTERROGATIVES = frozenset({"was", "wie", "wer", "wann", "what", "how", "who", "when"})

# Füllwörter, die zwischen Slot-Beginn und Verb stehen dürfen (max. Budget).
_FILLERS = frozenset({
    "bitte", "please", "mal", "mir", "mich", "uns", "jetzt", "now", "noch",
    "auch", "erst", "zuerst", "first", "kurz", "einfach", "just", "danach",
})

_MAX_FILLER_SKIP = 3

_TOKEN_RE = re.compile(r"[,;]|[^\s,;]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def explain(text: str) -> dict[str, Any]:
    """-> {is_multi_verb, verbs, sequence_markers, reason}.

    `verbs` sind die Kommando-Position-Verben in Auftretensreihenfolge
    (Slots, nicht distinkte Lexeme: „öffne A und öffne B" = 2).
    """
    tokens = _tokenize(text)
    verbs: list[str] = []
    seq_markers: list[str] = []

    slot_open = True          # Äußerungsanfang ist ein Kommando-Slot
    filler_budget = _MAX_FILLER_SKIP

    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]

        # Sequenz-Marker (inkl. Bigramme "after that"/"after this")
        is_marker = tok in _SEQ_MARKERS
        marker_len = 1
        if not is_marker and tok == "after" and i + 1 < n and tokens[i + 1] in ("that", "this"):
            is_marker = True
            marker_len = 2
        if is_marker:
            prev = tokens[i - 1] if i > 0 else ""
            has_continuation = i + marker_len < n
            if verbs and prev not in _INTERROGATIVES and has_continuation:
                seq_markers.append(" ".join(tokens[i:i + marker_len]))
            slot_open = True
            filler_budget = _MAX_FILLER_SKIP
            i += marker_len
            continue

        if tok in _COORDINATORS:
            slot_open = True
            filler_budget = _MAX_FILLER_SKIP
            i += 1
            continue

        if slot_open:
            if tok in _COMMAND_VERBS:
                verbs.append(tok)
                slot_open = False
            elif tok in _FILLERS and filler_budget > 0:
                filler_budget -= 1
            else:
                slot_open = False
        i += 1

    is_multi = len(verbs) >= 2 or (len(verbs) >= 1 and len(seq_markers) >= 1)

    if is_multi:
        reason = (
            f"{len(verbs)} Kommando-Verb(en) {verbs} "
            + (f"+ Sequenz-Marker {seq_markers}" if seq_markers else "(koordiniert)")
        )
    else:
        reason = f"{len(verbs)} Kommando-Verb(en) {verbs}, keine Sequenz-Fortsetzung"

    return {
        "is_multi_verb": is_multi,
        "verbs": verbs,
        "sequence_markers": seq_markers,
        "reason": reason,
    }


def is_multi_verb(text: str) -> bool:
    """True, wenn der Intent ≥2 unabhängige Verben enthält (E3: immer dekomponieren)."""
    return explain(text)["is_multi_verb"]
