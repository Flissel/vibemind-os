"""Index: Wissensdokumente -> Qdrant. Aus den Dateien jederzeit neu baubar.

rowboat-artifacts bekommt das Dokument (node_type knowledge_doc), brain-semantic
einen Begriff pro Dokument (node_type concept). Die Wikilinks stehen im Payload;
der Gardener (mcmp_gardener) laeuft darueber.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from core.knowledge.schema import ORDNER, Dokument, dateiname


def doc_external_id(dok: Dokument) -> str:
    return f"doc::{dok.typ}::{dok.id}"


def _text(dok: Dokument) -> str:
    fakten = "; ".join(f"{f.schluessel}={f.wert}" for f in dok.fakten)
    return f"{dok.titel} ({dok.typ}). {fakten}. {dok.deutung}"[:2000]


def eintragen(kg: Any, dok: Dokument) -> Optional[str]:
    from core.qdrant_kg import NT_CONCEPT, NT_KNOWLEDGE_DOC
    payload = {
        "doc_typ": dok.typ, "doc_id": dok.id, "titel": dok.titel,
        "datei": f"{ORDNER[dok.typ]}/{dateiname(dok)}.md",
        "wikilinks": list(dok.links), "stand": dok.stand.isoformat(),
        "source": "knowledge",
    }
    pid = kg._upsert_point(external_id=doc_external_id(dok), node_type=NT_KNOWLEDGE_DOC,
                           text=_text(dok), payload_extra=payload)
    kg._upsert_point(external_id=f"concept::{dok.typ}::{dok.id}", node_type=NT_CONCEPT,
                     text=f"{dok.titel} ({dok.typ})",
                     payload_extra={"doc_external_id": doc_external_id(dok), "source": "knowledge"})
    return pid


def neu_aufbauen(kg: Any, tresor) -> Dict[str, int]:
    docs = tresor.alle()
    geschrieben = fehler = 0
    for d in docs:
        if eintragen(kg, d):
            geschrieben += 1
        else:
            fehler += 1
    return {"dokumente": len(docs), "geschrieben": geschrieben, "fehler": fehler}


class Kanten(int):
    """Anzahl gesetzter Kanten; `neu_indexiert` = Dokumente, deren Punkt fehlte
    und die dabei neu eingetragen wurden (Selbstheilung, N1)."""
    neu_indexiert: int = 0


def verknuepfen(kg: Any, tresor) -> Kanten:
    """Wikilinks -> payload.linked.ideas (external_ids), damit der Gardener sie begeht.

    Fix-Runde 1 (Finding 1): Qdrants set_payload ersetzt nur TOP-LEVEL-Keys -
    ein blindes {"linked": {"ideas": [...]}} wuerde also linked.bubbles/
    thoughts/spaces/... loeschen, die _build_edges (qdrant_kg.py) bereits
    geschrieben hat. Deshalb read-merge-write wie dort: bestehenden linked-
    Dict lesen, NUR linked.ideas ersetzen (auch auf leer, wenn ein Wikilink
    entfernt wurde), alles andere unangetastet lassen. Existiert der Punkt
    noch nicht in Qdrant (Qdrant war bei einem Lauf aus, Collection neu
    angelegt), wird das Dokument per eintragen() neu indexiert (Selbstheilung,
    N1) und danach normal verlinkt. Scheitert das, wird es uebersprungen.
    """
    from core.qdrant_kg import COLLECTIONS, _empty_linked, _point_id
    docs = tresor.alle()
    nach_name = {dateiname(d): doc_external_id(d) for d in docs}
    coll = COLLECTIONS["artifacts"]
    kanten = 0
    neu = 0
    for d in docs:
        ziele = [nach_name[l] for l in d.links if l in nach_name]
        pid = _point_id(doc_external_id(d))
        try:
            rec = kg.client.retrieve(collection_name=coll, ids=[pid], with_payload=True)
        except Exception:
            continue
        if not rec:
            try:
                if not eintragen(kg, d):
                    continue
            except Exception:
                continue
            neu += 1
            basis = None
        else:
            basis = (rec[0].payload or {}).get("linked")
        linked = dict(basis or _empty_linked())
        linked["ideas"] = ziele
        kg.client.set_payload(collection_name=coll, payload={"linked": linked}, points=[pid])
        kanten += len(ziele)
    erg = Kanten(kanten)
    erg.neu_indexiert = neu
    return erg
