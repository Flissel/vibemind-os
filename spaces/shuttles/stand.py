"""The fixed snapshot of a promoted project: read it, check it, map it to card rows.

When a bubble is promoted, the ideas space writes the snapshot to
``projects.metadata["uebergabe"]`` (format version 1, agreed in befoerdern-uebergabe.md).
This module only reads that dict; it does no I/O and calls no model.

- ``pruefsumme_stand``: recompute the sha256 over canonical JSON (without ``am`` and
  ``pruefsumme``) and compare it with the stored one.
- ``karten_und_kanten``: map the snapshot's cards (``art`` notiz | dokument | white_paper |
  gruppe) back to card rows, so the shared rules count them as for a live bubble.
- ``stand_lesen``: all of it in one result, including the gate check recomputed with
  ``spaces/_contract/kartenregeln.py``.  A stored "passed" never vouches for itself.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping

VERSION = 1


def _lade_kartenregeln():
    pfad = Path(__file__).resolve().parents[1] / "_contract" / "kartenregeln.py"
    spec = importlib.util.spec_from_file_location("spaces_contract_kartenregeln", pfad)
    if spec is None or spec.loader is None:
        raise RuntimeError("spaces/_contract/kartenregeln.py fehlt")
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


regeln = _lade_kartenregeln()


def _text(wert: Any) -> str:
    return wert if isinstance(wert, str) else ""


def pruefsumme_stand(stand: Mapping[str, Any]) -> str:
    """'stimmt', 'falsch' or 'fehlt'."""
    gespeichert = stand.get("pruefsumme")
    if not isinstance(gespeichert, str) or not gespeichert.startswith("sha256:"):
        return "fehlt"
    koerper = {k: v for k, v in stand.items() if k not in ("am", "pruefsumme")}
    kanonisch = json.dumps(koerper, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    erwartet = "sha256:" + hashlib.sha256(kanonisch).hexdigest()
    return "stimmt" if hmac.compare_digest(gespeichert, erwartet) else "falsch"


def karten_und_kanten(stand: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Card and edge rows as in Supabase (canvas_nodes / canvas_edges) for the shared rules.

    The chosen white paper (``white_paper``) is added as a document card carrying its sources,
    so the coverage counts exactly as for a live bubble.
    """
    karten: list[dict[str, Any]] = []
    for k in stand.get("karten") or []:
        if not isinstance(k, Mapping) or not k.get("id"):
            continue
        art = k.get("art")
        if art == "gruppe":
            meta: dict[str, Any] = {"gruppen_kopf": True}
        elif art == "white_paper":
            meta = {"art": "white_paper"}
        else:
            meta = {}
        karten.append({
            "id": str(k["id"]),
            "node_type": "dokument" if art in ("dokument", "white_paper") else "note",
            "title": _text(k.get("titel")),
            "content": _text(k.get("text")),
            "metadata": meta,
            "gruppe": k.get("gruppe") if isinstance(k.get("gruppe"), str) else None,
        })
    wp = stand.get("white_paper")
    if isinstance(wp, Mapping):
        karten.append({
            "id": str(wp.get("node_id") or "white_paper"),
            "node_type": "dokument",
            "title": _text(wp.get("titel")),
            "content": _text(wp.get("text")),
            "metadata": {"art": "white_paper", "quellen": [str(q) for q in (wp.get("quellen") or []) if q]},
            "updated_at": _text(wp.get("geaendert_am")) or "9999",  # the chosen one is the newest
            "gruppe": None,
        })
    kanten = [{"from_node_id": str(e.get("von")), "to_node_id": str(e.get("nach")), "edge_type": _text(e.get("art"))}
              for e in (stand.get("kanten") or []) if isinstance(e, Mapping)]
    return karten, kanten


def stand_lesen(metadata: Any) -> dict[str, Any]:
    """Read ``projects.metadata`` and return the checked snapshot.

    ``ok`` False with ``fehler`` ``kein_stand`` (no snapshot) or ``format_unbekannt`` (version not 1).
    """
    stand = metadata.get("uebergabe") if isinstance(metadata, Mapping) else None
    if not isinstance(stand, Mapping):
        return {"ok": False, "fehler": "kein_stand"}
    if stand.get("version") != VERSION:
        return {"ok": False, "fehler": "format_unbekannt", "version": stand.get("version")}
    karten, kanten = karten_und_kanten(stand)
    notizen = [k for k in karten if regeln.ist_notiz(k)]
    wp = stand.get("white_paper") if isinstance(stand.get("white_paper"), Mapping) else None
    return {
        "ok": True,
        "fehler": None,
        "version": VERSION,
        "bubble_id": stand.get("bubble_id"),
        "titel": _text(stand.get("titel")),
        "am": stand.get("am"),
        "pruefsumme": pruefsumme_stand(stand),
        "bewertung": stand.get("bewertung"),
        "white_paper": {"node_id": wp.get("node_id"), "titel": _text(wp.get("titel")), "text": _text(wp.get("text"))}
        if wp else None,
        # Derselbe Kartentext wie in der Sinnpruefung (kartenregeln.kartentext).
        "notizen": [{"id": k["id"], "titel": k["title"], "text": regeln.kartentext(k), "gruppe": k["gruppe"]}
                    for k in notizen],
        "kanten": kanten,
        "pruefung": regeln.sinnpruefung(karten, kanten, stand.get("bewertung")),
    }
