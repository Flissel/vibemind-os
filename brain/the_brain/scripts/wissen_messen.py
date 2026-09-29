"""Signale der Wissensschicht messen (Spec §5): Nachfrage-Probe, Beleg-Quote, Frische.

  python scripts/wissen_messen.py [--wurzel <knowledge-dir>]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.knowledge.nachfrage import nachfragen as _nachfragen  # noqa: E402
from core.knowledge.schema import deutung_saetze  # noqa: E402
from core.knowledge.tresor import Tresor  # noqa: E402


def messen(tresor, nachfragen=_nachfragen) -> dict:
    docs = tresor.alle()
    belege = [b for d in docs for b in d.belege]
    ergebnisse = [nachfragen(b) for b in belege]
    pruefbar = [e for e in ergebnisse if e is not None]
    saetze = [s for d in docs for s in deutung_saetze(d.deutung)]
    mit_beleg = [s for s in saetze if "[B" in s]
    jetzt = datetime.now(timezone.utc)
    alter = max(((jetzt - b.gemessen).total_seconds() / 3600 for b in belege), default=0.0)
    return {
        "dokumente": len(docs),
        "belege": len(belege),
        "belege_stimmen": sum(1 for e in pruefbar if e),
        "nicht_pruefbar": len(ergebnisse) - len(pruefbar),
        "nachfrage_quote": round(sum(1 for e in pruefbar if e) / len(pruefbar), 3) if pruefbar else 0.0,
        "beleg_quote": round(len(mit_beleg) / len(saetze), 3) if saetze else 1.0,
        "aeltester_beleg_h": round(alter, 1),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--wurzel")
    a = ap.parse_args()
    json.dump(messen(Tresor(Path(a.wurzel) if a.wurzel else None)), sys.stdout, indent=2)
    print()
