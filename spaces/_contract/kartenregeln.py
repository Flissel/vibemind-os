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


def ist_leer(karte: Mapping[str, Any]) -> bool:
    """Content under LEER_UNTER_WOERTERN words; the title does not count."""
    return len(woerter(karte.get("content"))) < LEER_UNTER_WOERTERN


def ist_duenn(karte: Mapping[str, Any]) -> bool:
    return len(" ".join(str(karte.get("content") or "").split())) < DUENN_UNTER_ZEICHEN


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
