"""Punkte einer Collection ins Archiv verschieben: erst kopieren, dann per
ID im Archiv bestaetigen, dann erst in der Quelle loeschen.

  python scripts/archivieren.py --von brain-episodic --nach brain-episodic-archive \
      --filter source=continuous_thinking            # Trockenlauf
  ... --ausfuehren                                    # wirklich
Beide Namen sind Aliasse; die Ziel-Collection muss existieren
(qdrant_kg.ensure_collections legt sie an).

Fix-Runde 1 (Finding 1): eine reine Vorher/Nachher-Zaehlung des Archivs ist
eine Falle - schlaegt ein Lauf mittendrin fehl (z.B. PUT einer spaeten Seite
wirft, nachdem fruehere Seiten schon durch sind), kopiert ein erneuter Lauf
dieselben IDs erneut. Der Upsert ist idempotent, das Archiv waechst beim
zweiten Versuch also NICHT mehr - die Zaehl-Differenz waere fuer immer 0 und
koennte nie wieder zur kopierten Anzahl passen, die Quelle bliebe ohne
manuellen Eingriff fuer immer ungeloescht. Deshalb wird nach dem Kopieren
stattdessen per ID direkt im Archiv nachgefragt (POST .../points mit
{"ids": [...]}), welche der kopierten IDs dort wirklich ankommen sind. Nur
diese bestaetigten IDs werden aus der Quelle geloescht. Das macht einen
erneuten Lauf nach einem Teilausfall idempotent: die Quelle enthaelt die
Punkte noch (nichts wurde geloescht), ein erneutes Kopieren ist ein
folgenloses Upsert, und die ID-Bestaetigung findet sie diesmal alle.
"""
from __future__ import annotations

import argparse
import sys

import requests


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qdrant", default="http://localhost:6730")
    ap.add_argument("--von", required=True)
    ap.add_argument("--nach", required=True)
    ap.add_argument("--filter", required=True, help="feld=wert")
    ap.add_argument("--ausfuehren", action="store_true")
    a = ap.parse_args(argv)
    feld, wert = a.filter.split("=", 1)
    q = a.qdrant.rstrip("/")
    flt = {"must": [{"key": feld, "match": {"value": wert}}]}
    anzahl = requests.post(f"{q}/collections/{a.von}/points/count",
                           json={"filter": flt, "exact": True}, timeout=60).json()["result"]["count"]
    print(f"# {anzahl} Punkte in {a.von} mit {a.filter}")
    if not a.ausfuehren:
        print("# Trockenlauf - nichts veraendert")
        return 0
    ids, offset = [], None
    while True:
        body = {"limit": 256, "with_payload": True, "with_vector": True, "filter": flt}
        if offset is not None:
            body["offset"] = offset
        res = requests.post(f"{q}/collections/{a.von}/points/scroll", json=body, timeout=120).json()["result"]
        pts = res["points"]
        if pts:
            requests.put(f"{q}/collections/{a.nach}/points?wait=true",
                         json={"points": [{"id": p["id"], "vector": p["vector"], "payload": p["payload"]}
                                          for p in pts]}, timeout=120).raise_for_status()
            ids += [p["id"] for p in pts]
        offset = res.get("next_page_offset")
        if offset is None:
            break
    if len(ids) != anzahl:
        print(f"# ABBRUCH: {len(ids)} kopiert, {anzahl} erwartet (Quelle hat sich "
              "waehrend des Laufs veraendert) - nichts geloescht")
        return 1
    # Bestaetigung per ID statt Vorher/Nachher-Zaehlung (Fix-Runde 1,
    # Finding 1): idempotent gegenueber einem erneuten Lauf nach Teilausfall.
    bestaetigt = []
    for i in range(0, len(ids), 500):
        stapel = ids[i:i + 500]
        res = requests.post(f"{q}/collections/{a.nach}/points",
                            json={"ids": stapel, "with_payload": False, "with_vector": False},
                            timeout=120).json()["result"]
        bestaetigt += [p["id"] for p in res]
    if len(bestaetigt) != len(ids):
        print(f"# ABBRUCH: {len(ids)} kopiert, {len(bestaetigt)} im Archiv bestaetigt - nichts geloescht")
        return 1
    for i in range(0, len(bestaetigt), 500):
        requests.post(f"{q}/collections/{a.von}/points/delete?wait=true",
                      json={"points": bestaetigt[i:i + 500]}, timeout=120).raise_for_status()
    print(f"# {len(bestaetigt)} verschoben nach {a.nach}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
