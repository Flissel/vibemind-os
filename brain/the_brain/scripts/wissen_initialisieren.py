"""Ersteinrichtung: alle Wissensdokumente aus den Quellen anlegen, Index bauen.

  python scripts/wissen_initialisieren.py [--ohne-deutung]
Laeuft im brain-loops-Container (dort ist der Wissensordner beschreibbar).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.knowledge import index, kurator, quellen  # noqa: E402
from core.knowledge.tresor import Tresor  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ohne-deutung", action="store_true")
    a = ap.parse_args()
    kg = None
    try:
        from core.qdrant_kg import QdrantKG
        kg = QdrantKG()
        kg.ensure_collections()
    except Exception as e:
        print(f"# Qdrant nicht verfuegbar, nur Dateien: {e}")
    t = Tresor()
    k = kurator.Kurator(t, kg=None, deuten=None if a.ohne_deutung else kurator.llm_deuten)
    print(json.dumps({"kurator": k.voll_durchlauf(), "quellenfehler": dict(quellen.FEHLER)}))
    if kg is not None:
        print(json.dumps({"index": index.neu_aufbauen(kg, t)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
