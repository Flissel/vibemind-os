"""Auswertung der Schatten-Datei des Schritt-Waechters.

  python scripts/schritt_waechter_bericht.py <pfad zur schritt_waechter.jsonl>

Liest nur die uebergebene Datei, schreibt nichts und druckt den Bericht als JSON.
Unlesbare Zeilen werden uebersprungen und als "defekt" gezaehlt (nur in der Ausgabe).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

STICHPROBE_MAX = 40


def bericht(zeilen: list[dict]) -> dict:
    """Zaehlt Schritte, Auffaelliges und Unbeurteiltes und zieht eine Stichprobe."""
    je_quelle: dict[str, int] = {}
    je_faehigkeit: dict[str, list[int]] = {}
    auffaellige: dict[str, list[dict]] = {}
    auffaellig = 0
    unbeurteilt = 0
    for z in zeilen:
        quelle = z.get("quelle")
        je_quelle[quelle] = je_quelle.get(quelle, 0) + 1
        fae = z.get("capability") or "?"
        zaehler = je_faehigkeit.setdefault(fae, [0, 0])
        zaehler[0] += 1
        wert = z.get("auffaellig")
        if wert is None:
            unbeurteilt += 1
        elif wert:
            auffaellig += 1
            zaehler[1] += 1
            auffaellige.setdefault(fae, []).append(z)
    # Reihum je Faehigkeit eine Zeile, Faehigkeiten in Reihenfolge ihres ersten Auftretens.
    stichprobe: list[dict] = []
    runde = 0
    while len(stichprobe) < STICHPROBE_MAX:
        neu = [liste[runde] for liste in auffaellige.values() if len(liste) > runde]
        if not neu:
            break
        stichprobe.extend(neu[:STICHPROBE_MAX - len(stichprobe)])
        runde += 1
    return {"schritte": len(zeilen), "auffaellig": auffaellig, "unbeurteilt": unbeurteilt,
            "je_quelle": je_quelle, "je_faehigkeit": je_faehigkeit, "stichprobe": stichprobe}


def lese(pfad: Path) -> tuple[list[dict], int]:
    """Liest die JSONL-Datei; liefert die lesbaren Zeilen und die Zahl der defekten."""
    zeilen: list[dict] = []
    defekt = 0
    with open(pfad, encoding="utf-8", errors="replace") as f:
        for roh in f:
            if not roh.strip():
                continue
            try:
                z = json.loads(roh)
            except ValueError:
                defekt += 1
                continue
            if isinstance(z, dict):
                zeilen.append(z)
            else:
                defekt += 1
    return zeilen, defekt


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Aufruf: python scripts/schritt_waechter_bericht.py <pfad zur schritt_waechter.jsonl>",
              file=sys.stderr)
        return 2
    zeilen, defekt = lese(Path(argv[1]))
    ausgabe = bericht(zeilen)
    ausgabe["defekt"] = defekt
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(ausgabe, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
