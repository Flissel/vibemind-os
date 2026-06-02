"""SoM Planner — Frage-Aufbereitung für den Nutzer (Telegram/Voice).

Problem (User-Feedback 2026-06-02): die rohen `benoetigte_daten_fehlen` (Executor)
+ `approval_gates` (Validator) sind Validator-/Tech-Jargon, nicht menschlich:
  "zielstelle-klaeren: Zielstelle, URL/Text, Ausgabeformat fehlen — Nutzer-Input
   zwingend erforderlich bevor Schritte 3–5 starten können."
  "cv-fakten-updaten: Capability 'knowledge_query' ist read-only … umgeleitet auf
   openfang:openclaude-coder …"
Davon ist nur das ERSTE eine echte Nutzer-Frage; das zweite ist eine interne
Routing-Notiz die das System selbst löst.

Dieses Modul macht aus den rohen Einträgen SAUBERE, menschliche Fragen:
1. KLASSIFIZIEREN: echte Nutzer-Entscheidung vs. interne Planungs-Notiz (Notizen
   raus — die löst das System, sie gehören nicht aufs Handy).
2. HUMANISIEREN: step-id-Präfix weg, Tech-Tokens (openfang:…, knowledge_query,
   desktop_skill) weg, sensible Pfade (`Datei`.md) weg, "bevor Schritt 3-5"-Jargon
   weg. Kern-Frage extrahieren.
3. DEDUPEN: mehrere Einträge zum selben Thema (z.B. 3× Zielstelle) zusammenfassen.

Reine Funktionen, kein LLM — deterministisch + testbar.
"""

from __future__ import annotations

import re

# ── interne Notizen, die KEINE Nutzer-Frage sind (Routing/Mechanik) ──────────
# Wenn ein roher Eintrag eines dieser Muster enthält UND keine echte Frage stellt,
# wird er verworfen (das System löst es selbst / der Validator hat schon umgeleitet).
_INTERNAL_NOTE_PATTERNS = [
    r"read-only",
    r"umgeleitet|umgelenkt|ausgewichen",
    r"capability\s+['\"]?\w+['\"]?\s+(ist|passt|liefert)",
    r"als\s+zusatzschritt\s+ergänzt|kein\s+eigener\s+plan-schritt",
    r"falls\s+\w+\s+(gewünscht|nötig)",         # optionale Erweiterungen
    r"zur\s+laufzeit\s+(aus|gelesen)",          # Laufzeit-Lese-Hinweis
    r"openfang:|desktop_skill|knowledge_query|rowboat-knowledge",  # reiner Tech-Hinweis
]

# Marker, dass es DOCH eine echte Frage/Entscheidung ist (überstimmt Notiz-Filter)
_REAL_QUESTION_MARKERS = [
    r"\?",
    r"\bwelche[rs]?\b|\bwofür\b|\bwozu\b|\bwohin\b",
    r"\bbestätig|\bfreigabe|\bgenehmig|\berlaub",
    r"\bunbekannt\b|\bfehlt\b|\bnenne[n]?\b|\bangeben\b|\bbeantworten\b",
    r"\bzielstelle|\bziel\b|\bzielprofil|\bstelle\b",
]

# Tech-Tokens / sensibles, das aus dem angezeigten Text entfernt wird
_STRIP_PATTERNS = [
    (re.compile(r"`[^`]*`"), ""),                                  # `Datei`.md backticks-Inhalt
    (re.compile(r"\bopenfang:[\w-]+"), "das System"),
    (re.compile(r"\b(knowledge_query|desktop_skill|rowboat-knowledge|openclaude-coder)\b"), "das System"),
    (re.compile(r"\bschritt[e]?\s*\d+(\s*[–-]\s*\d+)?\b", re.IGNORECASE), "den nächsten Schritten"),
    (re.compile(r"\bcapability\b", re.IGNORECASE), "Funktion"),
    (re.compile(r"\.md\b|\.docx\b|\.yaml\b"), ""),
    (re.compile(r"\s{2,}"), " "),
]

# step-id-Präfix wie "zielstelle-klaeren:" oder "cv-datei-erstellen:" abschneiden
_STEPID_PREFIX = re.compile(r"^\s*[a-z0-9]+(?:-[a-z0-9]+){1,}\s*:\s*", re.IGNORECASE)


def _is_internal_note(text: str) -> bool:
    low = text.lower()
    if any(re.search(p, low) for p in _REAL_QUESTION_MARKERS):
        # echte Frage-Marker → behalten, AUSSER es ist rein technisch (openfang: etc.)
        if re.search(r"read-only|umgeleitet|als\s+zusatzschritt", low):
            return True
        return False
    return any(re.search(p, low) for p in _INTERNAL_NOTE_PATTERNS)


def _humanize(text: str) -> str:
    t = _STEPID_PREFIX.sub("", text or "").strip()
    for pat, repl in _STRIP_PATTERNS:
        t = pat.sub(repl, t)
    t = t.strip(" .,;—-")
    # "X, Y, Z fehlt — Nutzer-Input zwingend …" → "X, Y, Z"
    t = re.sub(r"\s*[—-]\s*nutzer-?input.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*\bist\s+(unbekannt|unklar|offen)\b.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\bfehlt|fehlen\b", "", t, flags=re.IGNORECASE).strip(" .,;—-")
    # Aufzählung "A, B, C" → freundliche Frage "Welche Angaben brauchst du: A, B, C?"
    if "," in t and "?" not in t and not re.match(r"(?i)^(welche|was|wie|wofür|darf)", t):
        t = f"Welche Angaben hast du dazu? — {t}"
    elif t and "?" not in t and not re.match(r"(?i)^(welche|was|wie|wofür|darf|freigabe)", t):
        t = t + "?"
    return t


def _topic_key(text: str) -> str:
    """Grober Themen-Schlüssel zum Dedupen (gleiches Thema → eine Frage)."""
    low = text.lower()
    for key in ("zielstelle", "ziel", "stelle", "semester", "freigabe", "speicher",
                "unternehmen", "firma", "pdf", "format"):
        if key in low:
            return key
    return low[:24]


def clean_questions(raw_questions: list[dict]) -> list[dict]:
    """Nimmt die rohen [{id, frage, typ}] (aus _derive_questions) und gibt
    saubere, menschliche, deduplizierte Nutzer-Fragen zurück.

    approval-Fragen (typ='approval') bleiben IMMER (Freigaben sind echte
    Nutzer-Entscheidungen) — werden nur humanisiert. daten-Fragen werden
    gefiltert (interne Notizen raus) + dedupliziert.
    """
    out: list[dict] = []
    seen_topics: set[str] = set()

    for q in raw_questions or []:
        raw = q.get("frage", "") or ""
        typ = q.get("typ", "daten")

        if typ == "approval":
            human = _humanize_approval(raw)
            out.append({"id": q.get("id"), "frage": human, "typ": "approval"})
            continue

        # daten: interne Notizen verwerfen
        if _is_internal_note(raw):
            continue
        human = _humanize(raw)
        if len(human) < 4:
            continue
        topic = _topic_key(raw)
        if topic in seen_topics:
            continue  # gleiches Thema schon gefragt
        seen_topics.add(topic)
        out.append({"id": q.get("id"), "frage": human, "typ": "daten"})

    return out


def _humanize_approval(raw: str) -> str:
    """'Freigabe für 'cv-datei-erstellen': Persistenter Schreibvorgang …'
    → 'Darf ich den fertigen CV speichern?'"""
    low = raw.lower()
    # spezifischere/gefährlichere Aktionen ZUERST (lösch/versand/zahlung vor
    # dem breiten "datei/dokument"-Match, sonst frisst Speichern alles)
    if "lösch" in low or "entfern" in low:
        return "Darf ich das löschen? (ja / nein)"
    if "versand" in low or "senden" in low or "mail" in low:
        return "Darf ich das absenden? (ja / nein — erst prüfen)"
    if "zahlung" in low or "überweis" in low:
        return "Darf ich die Zahlung auslösen? (ja / nein)"
    if "speicher" in low or "schreib" in low or "datei" in low or "dokument" in low:
        return "Darf ich das Dokument speichern? (ja / erst zeigen)"
    # generisch: step-id raus, kurzer Freigabe-Text
    t = _STEPID_PREFIX.sub("", re.sub(r"^\s*freigabe\s+für\s+'[^']*'\s*:?\s*", "", raw, flags=re.IGNORECASE))
    for pat, repl in _STRIP_PATTERNS:
        t = pat.sub(repl, t)
    return ("Freigabe nötig: " + t.strip(" .,;—-")[:120]) if t.strip() else "Freigabe nötig."


if __name__ == "__main__":
    import sys
    try:
        sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    # Selbsttest mit den ECHTEN run_0022-Fragen
    raw = [
        {"id": "daten_1", "typ": "daten", "frage": "zielstelle-klaeren: Zielstelle, Stellenausschreibung-URL/Text, Ausgabeformat und Anschreiben-Wunsch fehlen — Nutzer-Input zwingend erforderlich bevor Schritte 3–5 starten können."},
        {"id": "daten_2", "typ": "daten", "frage": "cv-fakten-updaten: Capability 'knowledge_query' ist read-only (rowboat-knowledge-Agent); tatsächliche Datei-Editierung wurde auf openfang:openclaude-coder umgeleitet — falls das stört."},
        {"id": "daten_3", "typ": "daten", "frage": "cv-fakten-updaten: Aktueller Semesterstand von Felix Baumann muss zur Laufzeit aus rowboat-knowledge/People/`Felix Baumann`.md gelesen werden."},
        {"id": "daten_4", "typ": "daten", "frage": "cv-datei-erstellen: Konkreter Unternehmensname für Zielpfad erst nach zielstelle-klaeren bekannt."},
        {"id": "daten_5", "typ": "daten", "frage": "cv-datei-erstellen: Falls PDF-Export gewünscht, muss desktop_skill als Zusatzschritt ergänzt werden."},
        {"id": "gate_1", "typ": "approval", "frage": "Freigabe für 'cv-datei-erstellen': Persistenter Schreibvorgang auf privatem Dokument (Felix Baumann.md) im Wissens-System."},
    ]
    cleaned = clean_questions(raw)
    print(f"=== {len(raw)} roh → {len(cleaned)} sauber ===")
    for c in cleaned:
        print(f"  [{c['typ']}] {c['frage']}")
    # keine Tech-Tokens / sensible Pfade mehr
    blob = " ".join(c["frage"] for c in cleaned).lower()
    for bad in ("openfang:", "knowledge_query", "desktop_skill", "`felix", ".md", "schritte 3"):
        assert bad not in blob, f"LEAK: {bad}"
    print("OK — kein Tech-Jargon / sensibler Pfad in den sauberen Fragen")
