"""Punkte einer Collection ins Archiv verschieben: erst kopieren, zaehlen, dann loeschen.

  python scripts/archivieren.py --von brain-episodic --nach brain-episodic-archive \
      --filter source=continuous_thinking            # Trockenlauf
  ... --ausfuehren                                    # wirklich
Beide Namen sind Aliasse; die Ziel-Collection muss existieren
(qdrant_kg.ensure_collections legt sie an).
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
    vorher = requests.post(f"{q}/collections/{a.nach}/points/count", json={"exact": True},
                           timeout=60).json()["result"]["count"]
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
    nachher = requests.post(f"{q}/collections/{a.nach}/points/count", json={"exact": True},
                            timeout=60).json()["result"]["count"]
    if nachher - vorher != len(ids) or len(ids) != anzahl:
        print(f"# ABBRUCH: kopiert {len(ids)}, erwartet {anzahl}, Archiv +{nachher - vorher} - nichts geloescht")
        return 1
    for i in range(0, len(ids), 500):
        requests.post(f"{q}/collections/{a.von}/points/delete?wait=true",
                      json={"points": ids[i:i + 500]}, timeout=120).raise_for_status()
    print(f"# {len(ids)} verschoben nach {a.nach}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
