"""Altbestand in brain-decisions: Testlaeufe als is_test markieren.

  python scripts/decisions_testlaeufe_markieren.py            # Trockenlauf
  python scripts/decisions_testlaeufe_markieren.py --schreiben

Loescht nichts. Setzt nur `is_test: true` per set_payload auf Punkten, deren
intent ein Testmuster traegt (decision_outcome.is_test_run).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.decision_outcome import is_test_run  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qdrant", default="http://localhost:6730")
    ap.add_argument("--collection", default="brain-decisions")
    ap.add_argument("--schreiben", action="store_true")
    a = ap.parse_args(argv)
    base = f"{a.qdrant.rstrip('/')}/collections/{a.collection}"
    treffer, offset = [], None
    while True:
        body = {"limit": 256, "with_payload": ["intent", "is_test"], "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        res = requests.post(f"{base}/points/scroll", json=body, timeout=30).json()["result"]
        for p in res["points"]:
            pl = p.get("payload") or {}
            if not pl.get("is_test") and is_test_run(pl.get("intent", "")):
                treffer.append((p["id"], pl.get("intent", "")[:80]))
        offset = res.get("next_page_offset")
        if offset is None:
            break
    for pid, intent in treffer:
        print(f"{pid}  {intent}")
    print(f"# {len(treffer)} Testlaeufe {'markiert' if a.schreiben else 'gefunden (Trockenlauf)'}")
    if a.schreiben and treffer:
        r = requests.post(f"{base}/points/payload?wait=true",
                          json={"payload": {"is_test": True},
                                "points": [pid for pid, _ in treffer]}, timeout=30)
        r.raise_for_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
