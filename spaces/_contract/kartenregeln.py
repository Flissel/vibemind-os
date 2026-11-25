"""Card rules and the promotion gate, shared by the ideas space and the SWE-Design space.

One file, standard library only, no network, no imports from either space.  Both
load it by path (``importlib.util.spec_from_file_location``), so the rules are
the same in the gate before promoting (ideas) and in the view of the promoted
project (SWE-Design).  Agreed in befoerdern-uebergabe.md (F2-F4, 24.11.2026).

Input rows as in Supabase: cards = ``canvas_nodes`` (id, node_type, title,
content, metadata), edges = ``canvas_edges`` (from_node_id, to_node_id).
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

LEER_UNTER_WOERTERN = 3
DUENN_UNTER_ZEICHEN = 40
WHITE_PAPER_PFLICHT = True  # F3: default until the operator decides otherwise

_WORT = re.compile(r"[a-zäöüß0-9]+", re.IGNORECASE)


def woerter(text: Any) -> list[str]:
    return _WORT.findall(str(text or "").lower())


def _meta(karte: Mapping[str, Any]) -> Mapping[str, Any]:
    meta = karte.get("metadata")
    return meta if isinstance(meta, Mapping) else {}


def ist_dokument(karte: Mapping[str, Any]) -> bool:
    return karte.get("node_type") == "dokument"


def ist_white_paper(karte: Mapping[str, Any]) -> bool:
    return ist_dokument(karte) and _meta(karte).get("art") == "white_paper"


def ist_gruppenkopf(karte: Mapping[str, Any]) -> bool:
    """Header card made by idea_group: neither empty nor loose, not counted (F4)."""
    return _meta(karte).get("gruppen_kopf") is True


def ist_notiz(karte: Mapping[str, Any]) -> bool:
    return not ist_dokument(karte) and not ist_gruppenkopf(karte)


# ── Text der Karte ───────────────────────────────────────────────────────
# Formatierte Karten tragen ihren Inhalt in ``content_json`` (Specs, SWOT, Pro/Contra,
# User Stories ...); ``content`` wiederholt dort oft nur den Titel. Die alten
# Formatier-Agenten haben ausserdem Vorlagen und Absagen hinterlassen, die wie Inhalt
# aussehen, aber nichts sagen. Beides steht hier einmal fuer Ideen, Gehirn und SWE-Design.

# Vorlagentext: ganze Zeichenketten, die nichts sagen (Vergleich ohne Gross-/Kleinschreibung).
VORLAGEN = frozenset(t.lower() for t in {
    "Konkrete Anforderung", "Betroffene Komponente", "Was analysiert wird", "Technische Spezifikation",
    "Gesamtbewertung der aktuellen Situation", "Gesamtbewertung", "SWOT Analyse", "SWOT-Analyse", "Mind Map",
    "User Stories", "Gliederung", "Notiz-Titel", "Nicht angegeben", "Pro-Contra Analyse", "Empfehlung",
    "Loesungsansatz", "Lösungsansatz", "Aktion 1", "Aktion 2", "Risiko", "Absicherung", "Staerke", "Stärke",
    "Begruendung", "Begründung", "Schwaeche", "Schwäche", "Gegenmaassnahme", "Gegenmaßnahme", "Chance",
    "Naechster Schritt", "Nächster Schritt", "Performance", "Security", "Scalability", "Usability",
    "Compliance", "must_have", "should_have", "nice_to_have", "high", "medium", "low",
    "Wie wird es getestet?", "Wie wird es sichergestellt?", "Wie wird die Sicherheit gewährleistet?",
    "Wie skaliert das System?", "Wie skaliert es?", "Wie wird es skaliert?", "Wie wird es benutzt?",
    "Wie benutzerfreundlich ist es?", "Wie benutzerfreundlich ist das System?", "Wie benutzerfreundlich ist?",
    "Hinweise zur Implementierung", "Implementierungshinweise",
})
# Vorlagen mit wechselndem Ende, z. B. "Konkrete Anforderung für Sicherheit".
VORLAGE_MUSTER = re.compile(r"konkrete anforderung(?: f(?:ü|ue)r [\w-]+)?", re.IGNORECASE)
# Absagen der Formatier-Agenten: ganze Saetze, die nur sagen, dass nichts da ist.
ABSAGE = re.compile(
    r"\bkeine?\b[^.!?]{0,90}\b(anforderungen|spezifikationen|details|implementierungshinweise|angaben)\b"
    r"[^.!?]{0,160}\b(gefunden|enthalten|vorhanden|identifiziert|angegeben|extrahier\w*|verfügbar)"
    r"|\b(inhalt|quellinhalt)\b[^.!?]{0,40}\bnicht ausreichend"
    r"|\bbitte (stellen|ergänzen) sie\b"
    r"|\b(lediglich|ausschließlich|nur) den (titel|komponentennamen)\b"
    r"|\bbeschränkt sich auf (den|die wiederholung des) (titel|titels|komponentennamen)\b"
    r"|\benthält keine\b[^.!?]{0,40}\b(spezifikationen|anforderungen|details)\b"
    r"|\bes liegen keine\b[^.!?]{0,120}\bvor\b"
    r"|\b(weitere|zusätzliche) (informationen|details|angaben)\b[^.!?]{0,120}\b(erforderlich|nötig|notwendig|benötigt)\b"
    r"|\bfür eine (vollständige|detaillierte) (extraktion|spezifikation)\b"
    r"|\bmüssen aus\b[^.!?]{0,60}\bextrahiert werden\b"
    r"|\bkein extrahierbarer inhalt\b",
    re.IGNORECASE)
# Feldnamen in content_json, deren Werte nichts zum Lesen beitragen.
OHNE_INHALT = frozenset({
    "type", "id", "color", "colour", "status", "priority", "icon", "x", "y", "style", "shape", "source",
    "target", "from", "to", "url", "href", "created_at", "updated_at", "metadata", "category", "weight",
    "likelihood", "impact", "story_points", "formatted_by", "original_format",
})
_SATZ = re.compile(r"(?<=[.!?])\s+")
_NAME = re.compile(r"[\s_\-–]+")


def _gleich_titel(text: str, titel: str) -> bool:
    return bool(titel) and _NAME.sub("", text).lower() == _NAME.sub("", titel).lower()


def _sagt_etwas(text: str, titel: str) -> bool:
    """True when ``text`` has a sentence that is neither the title, a template nor a refusal."""
    for satz in _SATZ.split(" ".join(text.split())):
        satz = satz.strip()
        if (satz and not _gleich_titel(satz, titel) and satz.lower() not in VORLAGEN
                and not VORLAGE_MUSTER.fullmatch(satz) and not ABSAGE.search(satz)):
            return True
    return False


def _flach(wert: Any, aus: list[str], titel: str, tiefe: int = 0) -> None:
    if tiefe > 8 or len(aus) > 400:
        return
    if isinstance(wert, str):
        text = " ".join(wert.split())
        if text and _sagt_etwas(text, titel):
            aus.append(text)
    elif isinstance(wert, Mapping):
        for name, teil in wert.items():
            if name not in OHNE_INHALT:
                _flach(teil, aus, titel, tiefe + 1)
    elif isinstance(wert, list):
        for teil in wert:
            _flach(teil, aus, titel, tiefe + 1)


def _struktur(wert: Any) -> Any:
    if isinstance(wert, str) and wert.strip()[:1] in ("{", "["):
        try:
            import json
            return json.loads(wert)
        except ValueError:
            return None
    return wert


def kartentext(karte: Mapping[str, Any]) -> str:
    """What a card says: ``content``, else ``summary``, else the text values of ``content_json``.

    A text that only repeats the title, is template text or a refusal of the old formatting
    agents says nothing; then the next source is tried.  ``content`` and ``summary`` come back
    unchanged when they say something; from ``content_json`` only the telling values, one per
    line.  Empty string when the card says nothing.
    """
    titel = str(karte.get("title") or "").strip()
    for feld in ("content", "summary"):
        wert = karte.get(feld)
        if isinstance(wert, str) and _sagt_etwas(wert, titel):
            return wert.strip()
    teile: list[str] = []
    _flach(_struktur(karte.get("content_json")), teile, titel)
    if not teile and not isinstance(karte.get("summary"), str):
        _flach(_struktur(karte.get("summary")), teile, titel)
    return "\n".join(dict.fromkeys(teile))


def ist_leer(karte: Mapping[str, Any]) -> bool:
    """Card text (``kartentext``) under LEER_UNTER_WOERTERN words; the title does not count."""
    return len(woerter(kartentext(karte))) < LEER_UNTER_WOERTERN


def ist_duenn(karte: Mapping[str, Any]) -> bool:
    return len(" ".join(kartentext(karte).split())) < DUENN_UNTER_ZEICHEN


def lose_karten(karten: Sequence[Mapping[str, Any]], kanten: Sequence[Mapping[str, Any]]) -> list:
    """Note cards without an edge to another card of the same bubble (header cards count as partners)."""
    ids = {k.get("id") for k in karten}
    verbunden: set = set()
    for e in kanten:
        a, b = e.get("from_node_id"), e.get("to_node_id")
        if a in ids and b in ids and a != b:
            verbunden.update((a, b))
    return [k.get("id") for k in karten if ist_notiz(k) and k.get("id") not in verbunden]


def hat_bewertung(bewertung: Any) -> bool:
    """F2: only an empty field means no rating; a score of 0 is a rating."""
    if bewertung is None:
        return False
    if isinstance(bewertung, Mapping):
        return any(v is not None for v in bewertung.values())
    return True


def bewertung_der_bubble(bubble: Mapping[str, Any]) -> dict[str, Any] | None:
    """The rating of a bubble row (``ideas``), or None when it has none (F2b, 24.11.2026).

    ``ideas.score`` defaults to 0.0, so a bare 0 without any reason cannot be told from
    "never rated": rated means a score other than 0, or at least one reason in
    ``metadata.bewertungen``.
    """
    meta = _meta(bubble)
    gruende = meta.get("bewertungen") if isinstance(meta.get("bewertungen"), list) else []
    score = bubble.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        score = None
    if (score is None or score == 0) and not gruende:
        return None
    return {"score": score, "begruendungen": len(gruende)}


def white_paper_abdeckung(karten: Sequence[Mapping[str, Any]]) -> float | None:
    """Share of note cards the newest white paper names as source; None without white paper or notes."""
    papiere = [k for k in karten if ist_white_paper(k)]
    notizen = [k.get("id") for k in karten if ist_notiz(k)]
    if not papiere or not notizen:
        return None
    neuestes = max(papiere, key=lambda k: str(k.get("updated_at") or ""))
    quellen = set(_meta(neuestes).get("quellen") or [])
    return round(sum(1 for n in notizen if n in quellen) / len(notizen), 3)


def sinnpruefung(karten: Sequence[Mapping[str, Any]], kanten: Sequence[Mapping[str, Any]], bewertung: Any,
                 *, white_paper_pflicht: bool = WHITE_PAPER_PFLICHT) -> dict[str, Any]:
    """Promotion gate.  Blocks on: no cards, empty cards, loose cards, no rating, no white paper (switch).

    Thin cards are a hint only.  Returns numbers and card ids, no sentences.
    """
    notizen = [k for k in karten if ist_notiz(k)]
    leer = [k.get("id") for k in notizen if ist_leer(k)]
    duenn = [k.get("id") for k in notizen if ist_duenn(k) and k.get("id") not in leer]
    lose = lose_karten(karten, kanten)
    papier = any(ist_white_paper(k) for k in karten)
    gruende: list[str] = []
    if not notizen:
        gruende.append("keine_karten")
    if leer:
        gruende.append(f"leere_karten:{len(leer)}")
    if lose:
        gruende.append(f"lose_karten:{len(lose)}")
    if not hat_bewertung(bewertung):
        gruende.append("keine_bewertung")
    if white_paper_pflicht and not papier:
        gruende.append("kein_white_paper")
    hinweise: list[str] = []
    if duenn:
        hinweise.append(f"duenne_karten:{len(duenn)}")
    if not white_paper_pflicht and not papier:
        hinweise.append("kein_white_paper")
    return {
        "bestanden": not gruende,
        "gruende": gruende,
        "hinweise": hinweise,
        "zahlen": {"notizen": len(notizen), "kanten": len(kanten), "leer": len(leer), "lose": len(lose),
                   "duenn": len(duenn), "anteil_im_white_paper": white_paper_abdeckung(karten)},
        "leer": leer,
        "lose": lose,
        "duenn": duenn,
    }


# ── Bewertungslauf (Stufe 1, 25.11.2026) ─────────────────────────────────
# Ein Lauf bewertet die Bubble aus fuenf Sichten zu je 0-25 Punkten; die Summe ist der Score.
# Gespeichert in ``ideas.metadata.bewertung_lauf`` (Werkzeug bubble_bewertung des Ideen-Servers),
# gelesen vom Gehirn (Befoerdern) und vom Panel. "rowboat" heisst seit Stufe 1 "Wissenslage":
# Vorwissen, Doku, vorhandene Muster, Wiederverwendbares, belegte Annahmen und Begriffe.

SICHTEN = ("coding", "swe_design", "research", "rowboat", "ideas")
SICHT_NAMEN = {"coding": "Coding", "swe_design": "SWE Design", "research": "Research",
               "rowboat": "Wissenslage", "ideas": "Ideas"}
SICHT_MAX = 25
TODO_ARTEN = ("karte_ergaenzen", "karte_fuellen", "verbindung", "white_paper", "entscheidung")
TODOS_JE_SICHT = 5
TODO_TITEL_ZEICHEN = 120
TODO_FEHLT_ZEICHEN = 400
TODO_KARTEN_MAX = 20
URTEIL_ZEICHEN = 600
BEFOERDERN_SCHWELLE = 95   # Vorgabe; das Gehirn liest die Einstellung VIBEMIND_BEFOERDERN_SCHWELLE


def kartenstand(karten: Sequence[Mapping[str, Any]], kanten: Sequence[Mapping[str, Any]]) -> str:
    """Pruefsumme ueber Inhalt der Karten und die Verbindungen der Bubble.

    Zaehlt: Kennung, Art, Titel, ``content``, ``summary``, ``content_json`` jeder Karte und
    (von, nach, Art) jeder Verbindung zwischen zwei Karten der Bubble. Lage und Zeitstempel
    zaehlen nicht: Verschieben macht eine Bewertung nicht veraltet.
    """
    import hashlib
    import json

    ids = {k.get("id") for k in karten}
    kanten = [e for e in kanten if e.get("to_node_id") in ids]
    teile = sorted(json.dumps([k.get("id"), k.get("node_type"), k.get("title"), k.get("content"),
                               k.get("summary"), k.get("content_json")],
                              sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
                   for k in karten)
    verbindungen = sorted({(str(e.get("from_node_id")), str(e.get("to_node_id")), str(e.get("edge_type") or ""))
                           for e in kanten if e.get("from_node_id") in ids})
    text = "\n".join(teile) + "\n--\n" + "\n".join("|".join(v) for v in verbindungen)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ganz(wert: Any, von: int, bis: int) -> bool:
    return isinstance(wert, int) and not isinstance(wert, bool) and von <= wert <= bis


def _text(wert: Any, grenze: int, leer: bool = False) -> bool:
    return (isinstance(wert, str) and len(wert) <= grenze and (leer or bool(wert.strip()))
            and not any(ord(c) < 32 and c not in "\n\t" for c in wert))


def lauf_fehler(lauf: Any) -> str:
    """"" wenn ``lauf`` die Form eines neuen Bewertungslaufs hat (ohne zeit/kartenstand), sonst der Grund."""
    if not isinstance(lauf, Mapping):
        return "lauf"
    if set(lauf) - {"sichten", "todos", "gekuerzt"}:
        return "lauf"
    sichten = lauf.get("sichten")
    if not isinstance(sichten, Mapping) or set(sichten) != set(SICHTEN):
        return "sichten"
    for name in SICHTEN:
        sicht = sichten[name]
        if (not isinstance(sicht, Mapping) or set(sicht) != {"punkte", "urteil"}
                or not _ganz(sicht["punkte"], 0, SICHT_MAX) or not _text(sicht["urteil"], URTEIL_ZEICHEN, True)):
            return f"sichten.{name}"
    todos = lauf.get("todos")
    if not isinstance(todos, list) or len(todos) > TODOS_JE_SICHT * len(SICHTEN):
        return "todos"
    je_sicht: dict[str, int] = {}
    for todo in todos:
        if (not isinstance(todo, Mapping)
                or set(todo) != {"sicht", "titel", "fehlt", "karten", "punkte", "art"}
                or todo["sicht"] not in SICHTEN or todo["art"] not in TODO_ARTEN
                or not _text(todo["titel"], TODO_TITEL_ZEICHEN) or not _text(todo["fehlt"], TODO_FEHLT_ZEICHEN)
                or not _ganz(todo["punkte"], 1, SICHT_MAX)
                or not isinstance(todo["karten"], list) or len(todo["karten"]) > TODO_KARTEN_MAX
                or not all(isinstance(k, str) and 0 < len(k) <= 128 for k in todo["karten"])):
            return "todos"
        je_sicht[todo["sicht"]] = je_sicht.get(todo["sicht"], 0) + 1
        if je_sicht[todo["sicht"]] > TODOS_JE_SICHT:
            return "todos"
    if "gekuerzt" in lauf and not isinstance(lauf["gekuerzt"], bool):
        return "gekuerzt"
    return ""


def lauf_score(lauf: Mapping[str, Any]) -> int:
    """Summe der fuenf Sichten (0-100)."""
    return sum(int(lauf["sichten"][name]["punkte"]) for name in SICHTEN)


def bewertung_lauf(bubble: Mapping[str, Any]) -> dict[str, Any] | None:
    """Der letzte gespeicherte Lauf der Bubble, oder None (auch bei kaputter Form)."""
    lauf = _meta(bubble).get("bewertung_lauf")
    if (not isinstance(lauf, Mapping) or not _ganz(lauf.get("score"), 0, 100)
            or not isinstance(lauf.get("kartenstand"), str) or not isinstance(lauf.get("todos"), list)):
        return None
    return dict(lauf)


def offene_todos(lauf: Mapping[str, Any], anzahl: int | None = None) -> list[dict[str, Any]]:
    """Offene To-dos, die mit dem groessten Punktgewinn zuerst (bei Gleichstand nach Nummer)."""
    offen = [dict(t) for t in lauf.get("todos") or [] if isinstance(t, Mapping) and not t.get("erledigt")]
    offen.sort(key=lambda t: (-int(t.get("punkte") or 0), int(t.get("nr") or 0)))
    return offen[:anzahl] if anzahl is not None else offen


def schwelle_pruefen(bubble: Mapping[str, Any], karten: Sequence[Mapping[str, Any]],
                     kanten: Sequence[Mapping[str, Any]], schwelle: int = BEFOERDERN_SCHWELLE) -> dict[str, Any]:
    """Befoerdern nur ab der Schwelle: letzter echter Lauf, nicht veraltet. Ein Handwert zaehlt nicht.

    Rueckgabe: bestanden, grund (kein_lauf | unter_schwelle | veraltet | ""), score, schwelle, top (3 To-dos).
    """
    lauf = bewertung_lauf(bubble)
    if lauf is None:
        return {"bestanden": False, "grund": "kein_lauf", "score": None, "schwelle": schwelle, "top": []}
    top = offene_todos(lauf, 3)
    aus = {"score": lauf["score"], "schwelle": schwelle, "top": top}
    if lauf["kartenstand"] != kartenstand(karten, kanten):
        return {"bestanden": False, "grund": "veraltet", **aus}
    if lauf["score"] < schwelle:
        return {"bestanden": False, "grund": "unter_schwelle", **aus}
    return {"bestanden": True, "grund": "", **aus}
