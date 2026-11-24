"""Vergleichsbericht der Weiche im Schattenbetrieb (Routing-Papier 24.11.2026, Schritt 2).

    python scripts/weiche_vergleich.py                 # alles aus data/weiche_schatten.jsonl
    python scripts/weiche_vergleich.py --seit-h 24     # nur die letzten 24 Stunden
    python scripts/weiche_vergleich.py --json          # roh, wie GET /api/weiche/vergleich
    python scripts/weiche_vergleich.py --karten        # Karten gegen sich selbst prüfen

Der Bericht liest nur die Datei: kein Modell, kein Netz. ``--karten`` lädt dagegen das
lokale Einbettungsmodell (Speicher!), aber keinen Modellauftrag.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import weiche  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--seit-h", type=float, default=None)
    p.add_argument("--datei", type=Path, default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--karten", action="store_true")
    a = p.parse_args()
    if a.karten:
        daten = weiche.karten_pruefen(weiche.Weiche())
        print(json.dumps(daten, ensure_ascii=False, indent=2))
        return 0
    seit_ts = time.time() - a.seit_h * 3600 if a.seit_h else None
    daten = weiche.bericht(a.datei, seit_ts=seit_ts)
    print(json.dumps(daten, ensure_ascii=False, indent=2) if a.json else weiche.bericht_text(daten))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
