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
