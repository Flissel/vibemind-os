"""Prueffquote und MATCH-Anteil messen (Brain T1).

  python scripts/pruefquote.py                    # Registry + Qdrant localhost:6730
  python scripts/pruefquote.py --qdrant http://…  # anderer Qdrant

Gibt zwei Zahlen aus, die im Spec (Abschnitt 5) als Signale stehen:
  - Anteil der schreibenden Capabilities mit echter truth:-Pruefung
  - Verteilung MATCH/MISMATCH/UNVERIFIED im Ausfuehrungsprotokoll
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import requests
import yaml

DATA = Path(__file__).resolve().parents[1] / "data"


def registry_quote(caps: list, effects: dict) -> dict:
    eff = effects.get("effects") or {}
    write = [c for c in caps if eff.get(c["capability"]) == "write"]
    mit = [c for c in write
           if str((c.get("validator") or {}).get("kind", "")).startswith("truth:")]
    return {
        "write_gesamt": len(write),
        "write_mit_truth": len(mit),
        "quote_write": round(len(mit) / len(write), 3) if write else 0.0,
        "restliste": len(effects.get("restliste") or {}),
    }


def match_anteil(qdrant_url: str, collection: str = "brain-execution-log") -> dict:
    zaehler: Counter = Counter()
    offset = None
    while True:
        body = {"limit": 256, "with_payload": ["diff", "stage"], "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        r = requests.post(f"{qdrant_url.rstrip('/')}/collections/{collection}/points/scroll",
                          json=body, timeout=30)
        r.raise_for_status()
        res = r.json()["result"]
        for p in res["points"]:
            pl = p.get("payload") or {}
            if pl.get("stage") == "verify":
                zaehler[pl.get("diff", "?")] += 1
        offset = res.get("next_page_offset")
        if offset is None:
            return dict(zaehler)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qdrant", default="http://localhost:6730")
    a = ap.parse_args(argv)
    caps = yaml.safe_load((DATA / "capabilities.yaml").read_text(encoding="utf-8"))
    effects = yaml.safe_load((DATA / "capability_effects.yaml").read_text(encoding="utf-8"))
    out = {"registry": registry_quote(caps, effects)}
    try:
        out["ausfuehrungen_verify"] = match_anteil(a.qdrant)
    except Exception as e:  # Messung, kein Betrieb: Fehler sichtbar machen
        out["ausfuehrungen_verify"] = f"nicht messbar: {e}"
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
