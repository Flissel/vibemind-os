"""Kontext fuer den Video-Agenten (11.11.2026): Markenwissen der Firma plus Rowboat-Dokumente, nach Bezug zur
Frage ausgewaehlt, als Liste benannter Dokumente. Die Namen sind die Quellenangaben, auf die sich Gliederung und
Belege spaeter beziehen (`video_texte.szene_pruefen` vergleicht Zahlen woertlich gegen genau diese Texte).

DATENSCHUTZ. Keine personenbezogenen Kundendaten im Kontext: Ordner und Dateien, die nach Kontakten, Agent-Notizen
oder Verlauf heissen, bleiben draussen (Markenwissen wie Rowboat); ein Dokument ohne Namen oder Text kann nicht
eingeordnet werden und faellt ebenfalls weg. Kundennamen darf ein Video nur nennen, wenn sie im Auftrag stehen.

`holen` wirft nie. Rowboat ist fail-soft: ist es nicht erreichbar, steht das in `hinweise`.

ANGENOMMENE FELDNAMEN der Rowboat-Antworten (aus werkzeuge._rowboat, test_wissensbasis.py und
workers/bubble_rowboat_export.py; die Antwortform von `dokumente(id, True)` ist nicht live geprueft): Quellen =
Liste von {id, name}; Dokumente = Liste von {id, name, typ, content}. Tolerant gelesen werden zusaetzlich
sourceId/_id, title, inhalt/text/body und ein Umschlag {quellen|sources|items|dokumente|documents: [...]}.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from spaces.marketing.claw import markenwissen, werkzeuge, wissen

BUDGET_VORGABE = 60_000
DOKUMENT_MAX = 20_000            # ein einzelnes Dokument sprengt nie allein das Budget
_VERMERK = " … (gekürzt)"
# Ordner-/Dateiteile, die nie in den Kontext kommen (Kontakte, Agent-Notizen, Sicherungen/Verlauf)
_GESPERRT = ("kontakt", "notiz", "verlauf")
_ID = ("id", "_id", "sourceId")
_NAME = ("name", "title", "titel", "filename")
_TEXT = ("content", "inhalt", "text", "body")
_LISTEN = ("quellen", "sources", "items", "dokumente", "documents", "daten")


@dataclass
class Dokument:
    name: str
    text: str
    herkunft: str      # "marke" | "rowboat"


@dataclass
class Kontext:
    dokumente: list[Dokument] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)


def _gesperrt(pfad: str) -> bool:
    """Enthaelt ein Teil des Pfads (Ordner oder Datei) Kontakte, Notizen oder Verlauf?"""
    return any(w in teil.casefold() for teil in pfad.replace("\\", "/").split("/") for w in _GESPERRT)


def _erstes(eintrag: dict, schluessel: tuple[str, ...]) -> str:
    for s in schluessel:
        wert = eintrag.get(s)
        if isinstance(wert, str) and wert.strip():
            return wert.strip()
        if isinstance(wert, int) and not isinstance(wert, bool) and s in _ID:
            return str(wert)
    return ""


def _liste(daten: object) -> list[dict]:
    """Eintraege einer Rowboat-Antwort: Liste oder Umschlag-Objekt mit einer Liste."""
    if isinstance(daten, dict):
        daten = next((daten[k] for k in _LISTEN if isinstance(daten.get(k), list)), [])
    return [e for e in daten if isinstance(e, dict)] if isinstance(daten, list) else []


def _kappen(text: str, grenze: int) -> str:
    return text if len(text) <= grenze else text[:grenze] + _VERMERK


# --- Markenwissen -----------------------------------------------------------------------------

def _marke(mandant: str, hinweise: list[str]) -> list[Dokument]:
    ordner = markenwissen.ordner_finden(markenwissen.wurzel(), mandant, mandant)
    if ordner is None:
        hinweise.append(f"Kein Markenwissen für {mandant} gefunden")
        return []
    intern: list[str] = []      # Hinweise von markenwissen: Verknuepfungen, Dateien zu gross, nicht lesbar
    dokumente: list[Dokument] = []
    weg = 0
    for rel, pfad in markenwissen._dateien(ordner, intern):
        if _gesperrt(rel):
            weg += 1
            continue
        text = markenwissen._lesen(rel, pfad, intern)
        if text is None or markenwissen.ist_leer(text):
            continue
        grenze = markenwissen.MARKE_MAX if rel.casefold() == "marke.md" else DOKUMENT_MAX
        dokumente.append(Dokument(rel, _kappen(text, grenze), "marke"))
    hinweise += intern
    if weg:
        hinweise.append(f"{weg} Datei(en) mit Kontakten, Notizen oder Verlauf nicht gelesen")
    return dokumente


# --- Rowboat ----------------------------------------------------------------------------------

def _rowboat(quellen, dokumente, hinweise: list[str]) -> list[Dokument]:
    r = quellen()
    if not isinstance(r, dict) or not r.get("ok"):
        hinweise.append("Rowboat nicht erreichbar, es gilt nur das Markenwissen")
        return []
    ergebnis: list[Dokument] = []
    unlesbar = 0
    for quelle in _liste(r.get("daten")):
        qid, qname = _erstes(quelle, _ID), _erstes(quelle, _NAME)
        if not qid or not qname or _gesperrt(qname):
            continue
        d = dokumente(qid, True)
        if not isinstance(d, dict) or not d.get("ok"):
            unlesbar += 1
            continue
        for eintrag in _liste(d.get("daten")):
            dname, text = _erstes(eintrag, _NAME), _erstes(eintrag, _TEXT)
            if not dname or _gesperrt(dname) or not text or markenwissen.ist_leer(text):
                continue   # ohne Namen oder Inhalt nicht einzuordnen: bleibt draussen
            ergebnis.append(Dokument(f"{qname}/{dname}", _kappen(text, DOKUMENT_MAX), "rowboat"))
    if unlesbar:
        hinweise.append(f"{unlesbar} Rowboat-Quelle(n) nicht lesbar")
    return ergebnis


# --- Auswahl ----------------------------------------------------------------------------------

def holen(mandant: str, frage: str, budget: int = BUDGET_VORGABE,
          quellen=werkzeuge.wissensquellen, dokumente=werkzeuge.dokumente) -> Kontext:
    """Kontext zur Frage: Marke.md immer, alles andere nach Bezug zur Frage bis `budget` Zeichen. Wirft nie."""
    hinweise: list[str] = []
    alle: list[Dokument] = []
    try:
        alle += _marke(mandant, hinweise)
    except Exception:  # noqa: BLE001 - ohne Markenwissen geht es weiter
        hinweise.append("Markenwissen nicht lesbar")
    try:
        alle += _rowboat(quellen, dokumente, hinweise)
    except Exception:  # noqa: BLE001 - Rowboat darf nie den Auftrag kippen
        hinweise.append("Rowboat nicht erreichbar, es gilt nur das Markenwissen")

    gesehen: set[str] = set()
    einzig = []
    for d in alle:               # Rowboat legt gleichnamige Dokumente doppelt an: der Name zaehlt einmal
        if d.name not in gesehen:
            gesehen.add(d.name)
            einzig.append(d)

    marke = [d for d in einzig if d.herkunft == "marke" and d.name.casefold() == "marke.md"]
    rest = [d for d in einzig if d not in marke]
    stuecke = [{"quelle": mandant if d.herkunft == "marke" else d.name.split("/", 1)[0],
                "dokument": d.name, "text": d.text, "_d": d} for d in rest]
    platz = budget - sum(len(d.text) for d in marke)
    try:
        gewaehlt = [s["_d"] for s in wissen.auswaehlen(frage, stuecke, budget=max(platz, 0))] if stuecke else []
    except Exception:  # noqa: BLE001
        gewaehlt = []
        hinweise.append("Auswahl des Materials fehlgeschlagen")
    if stuecke and not gewaehlt:
        hinweise.append("Kein Material mit Bezug zum Thema gefunden")
    return Kontext(marke + gewaehlt, hinweise)
