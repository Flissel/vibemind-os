"""Hub-Notizen (wie MOC im secondbrain): je Ordner die staerksten Dokumente.

Staerke = activation_strength, die der Gardener beim Begehen der Links erhoeht.
Was oft verbunden ist, steht oben - so entsteht die Struktur von selbst.

Hubs/*.md haben KEINEN YAML-Kopf (reine Uebersicht, kein Wissensdokument) -
Tresor.alle()/bekannte_namen() ignorieren den Ordner bereits von selbst, weil
"Hubs" nicht in ORDNER steht. Geschrieben wird wie im Tresor atomar (versteckte
.tmp-Datei + os.replace) und nur, wenn sich der Inhalt aendert - sonst wuerde
jeder 15-Minuten-Kurator-Volllauf Rowboats Datei-Watcher unnoetig ausloesen.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from core.knowledge.index import doc_external_id
from core.knowledge.schema import ORDNER, Dokument, dateiname


def hub_notizen(docs: List[Dokument], aktivierung: Dict[str, float], top: int = 20) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for typ, ordner in ORDNER.items():
        eigene = [d for d in docs if d.typ == typ]
        if not eigene:
            continue
        eigene.sort(key=lambda d: -aktivierung.get(doc_external_id(d), 0.0))
        zeilen = [f"# {ordner}", "", "Automatisch gepflegt vom Brain-Kurator. "
                  "Reihenfolge: wie stark das Dokument mit anderen verbunden ist.", ""]
        zeilen += [f"- [[{dateiname(d)}]]" for d in eigene[:top]]
        out[f"{ordner}.md"] = "\n".join(zeilen) + "\n"
    return out


def _schreiben_wenn_geaendert(pfad: Path, text: str) -> bool:
    """Wie Tresor.schreiben: versteckte .tmp-Datei + os.replace - aber nur,
    wenn sich der Inhalt tatsaechlich aendert. Gibt True zurueck, wenn
    geschrieben wurde."""
    if pfad.exists():
        try:
            if pfad.read_text(encoding="utf-8") == text:
                return False
        except OSError:
            pass
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.parent / f".{pfad.name}.tmp"
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, pfad)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return True


def schreiben(tresor, kg: Any) -> int:
    from qdrant_client.http.models import FieldCondition, Filter, MatchValue

    from core.qdrant_kg import COLLECTIONS
    docs = tresor.alle()
    akt: Dict[str, float] = {}
    qfilter = Filter(must=[FieldCondition(key="node_type", match=MatchValue(value="knowledge_doc"))])
    offset = None
    while True:
        pts, offset = kg.client.scroll(collection_name=COLLECTIONS["artifacts"], limit=256,
                                       offset=offset, with_payload=True, with_vectors=False,
                                       scroll_filter=qfilter)
        for p in pts:
            pl = p.payload or {}
            akt[f"doc::{pl.get('doc_typ')}::{pl.get('doc_id')}"] = float(pl.get("activation_strength", 0.0) or 0.0)
        if offset is None:
            break
    ziel = tresor.wurzel / "Hubs"
    notizen = hub_notizen(docs, akt)
    geschrieben = 0
    for name, text in notizen.items():
        if _schreiben_wenn_geaendert(ziel / name, text):
            geschrieben += 1
    return geschrieben
