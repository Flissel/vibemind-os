"""Schwelle VERSTEHEN_MIN_COS messen: reiner Embedding-Lauf, kein Planer, kein Agent.

Bettet echte Saetze (16 aus dem Rundgang, feste Beispielsaetze) und Unsinn-/Fremdsaetze ein,
klassifiziert sie mit dem echten DifficultyRouter (echte Anker) und schlaegt die kleinste
Schwelle vor, bei der kein Unsinn-Satz den Planer erreicht.

Aufruf (im Brain-Ordner, mit gesetztem EMBEDDING_SERVICE_URL; die Adresse wird nie ausgegeben):

    python scripts/verstehen_schwelle_messen.py [--markdown]
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.disable(logging.WARNING)

ECHT = [
    # Rundgang 23.11.2026
    "Welche Bubbles gibt es?", "Wie ist der Code-Status?", "Welche Fenster sind gerade auf dem Desktop offen?",
    "Wie ist der Stand im SWE Design?", "Welche Agenten gibt es in Rowboat?",
    "Welche geplanten Aufgaben gibt es im Dashboard?", "Welche Videoprojekte gibt es?",
    "Welche Teams laufen gerade in der Agent Farm?", "Woran denkt das Gehirn gerade?",
    "Was steht in meinem Journal?", "Welche Simulationen gibt es in MiroFish?",
    "Wie viele Kontakte habe ich im Sales?", "Wie laufen meine Marketing-Kampagnen?", "Welche Kurse gibt es?",
    "Welche Events stehen als nächstes an?", "Zeig meine Aufträge",
    # feste Beispielsaetze
    "Zeig die Notizen dieser Bubble", "Halt fest: Preise im Januar prüfen", "Ordne die Notizen im Raster",
    "Welche geplanten Aufgaben gibt es?", "Mach einen Screenshot", "Öffne example.com im Browser",
]
UNSINN = [
    "asdf jklö qwertz", "Blau schmeckt lauter als Dienstag", "Der Kühlschrank singt Opern",
    "Wie viele Zähne hat ein Regenbogen?", "lorem ipsum dolor sit amet", "Kartoffel Kartoffel Kartoffel",
    "Wann hat der Mond Geburtstag?", "Trompete gegen Wolke drei zu null", "hmm", "ok", "...",
    "Erzähl mir vom Wetter auf dem Mars im Jahr 3000", "Ist die Zahl sieben eifersüchtig?",
    "Wie riecht die Farbe Grün?", "Bring mir bei, wie man Nebel faltet", "xyz 123 abc",
    "Warum bellen Fische nicht?", "Der Stuhl hat Heimweh", "Mach die Nacht heller als den Tag",
    "Welche Farbe hat ein Furz?",
]


class _Datei:
    """Vektoren aus einer JSON-Datei {text: vektor} (vorher beim Embedding-Dienst geholt)."""

    def __init__(self, pfad: str) -> None:
        import json

        self.vektoren = json.loads(Path(pfad).read_text(encoding="utf-8"))

    def encode(self, text: str):
        return self.vektoren[text]

    def encode_batch(self, texte):
        return [self.vektoren[t] for t in texte]


def alle_texte() -> list:
    """Alle Texte, die eingebettet werden muessen: Anker und Messsaetze."""
    from core.difficulty_router import DEFAULT_ANCHORS

    texte = [t for lvl in DEFAULT_ANCHORS.values() for t in lvl]
    return list(dict.fromkeys(texte + ECHT + UNSINN))


def main() -> int:
    if "--texte" in sys.argv:
        import json

        print(json.dumps(alle_texte(), ensure_ascii=False))
        return 0
    from core.difficulty_router import DifficultyRouter

    if "--vektoren" in sys.argv:
        embedder = _Datei(sys.argv[sys.argv.index("--vektoren") + 1])
    elif os.environ.get("EMBEDDING_SERVICE_URL"):
        from core.qdrant_kg import Embedder

        embedder = Embedder.get()
    else:
        print("EMBEDDING_SERVICE_URL oder --vektoren fehlt; ohne Vektoren keine Messung.")
        return 2
    router = DifficultyRouter(embedder=embedder)
    zeilen = []
    for art, saetze in (("echt", ECHT), ("unsinn", UNSINN)):
        for satz in saetze:
            cls = router._classify_base(satz)
            zeilen.append((art, satz, cls.get("level"), float(cls.get("score") or 0.0),
                           cls.get("method"), str(cls.get("anchor") or "")))
    ohne_cos = [z for z in zeilen if z[4] not in ("qwen-cosine", "qwen-cosine-lowconf")]
    if ohne_cos:
        # Faellt die Einbettung aus (z. B. BRAIN_KG_SEMANTIC_DIM passt nicht zum Dienst), rechnet
        # der Router still mit der Laengen-Heuristik weiter. Das ist keine Messung.
        print(f"ABBRUCH: {len(ohne_cos)} Saetze ohne Cosinus (Methode {ohne_cos[0][4]!r}). "
              "Einbettung pruefen, z. B. BRAIN_KG_SEMANTIC_DIM passend zum Dienst setzen.")
        return 3
    zum_planer = [z for z in zeilen if z[4] in ("qwen-cosine", "qwen-cosine-lowconf")
                  and z[2] in ("medium", "hard", "insane")]
    max_unsinn = max((z[3] for z in zum_planer if z[0] == "unsinn"), default=0.0)
    vorschlag = round(max_unsinn + 0.01, 2)
    echte_abgewiesen = [z for z in zum_planer if z[0] == "echt" and z[3] < vorschlag]

    md = "--markdown" in sys.argv
    if md:
        print("| Art | Satz | Stufe | cos | naechster Anker |\n|---|---|---|---|---|")
    for art, satz, level, score, methode, anker in sorted(zeilen, key=lambda z: -z[3]):
        if md:
            print(f"| {art} | {satz} | {level} | {score:.3f} | {anker[:50]} |")
        else:
            print(f"{art:6} {score:.3f} {level:7} {methode:22} {satz!r} <- {anker[:50]!r}")
    print(f"\nhoechster Unsinn-cos auf Planer-Stufen: {max_unsinn:.3f}")
    print(f"vorgeschlagene Schwelle VERSTEHEN_MIN_COS={vorschlag}")
    print(f"echte Saetze dadurch 'nicht verstanden': {len(echte_abgewiesen)} von {len(ECHT)}")
    for z in echte_abgewiesen:
        print(f"  - {z[1]!r} (cos {z[3]:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
