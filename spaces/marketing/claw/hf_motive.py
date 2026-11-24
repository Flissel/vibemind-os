"""Erster Auftragstext fuer einen Higgsfield-Uebergang der Video-Werkbank (24.11.2026). Ohne Modell: eine feste
Sammlung von Motiven je Technologie ("der Uebergang tut, was die Technologie tut": Chunking = Papierschneider,
MCP = Stecker, Evals = Massband). Das Motiv wird ueber Stichwoerter im Titel des Videos und in den Kurztiteln der
beiden Szenen gewaehlt; passt keines, gilt das allgemeine. Der Text ist ein VORSCHLAG: der Betreiber sieht ihn auf
der Sales-Seite und kann ihn aendern, bevor bestellt wird. Er ist englisch, weil das Videomodell englische Texte am
genauesten umsetzt."""
from __future__ import annotations

import re

PROMPT_MAX = 1000        # wie Datenbank und API

# (Stichwoerter als Wortanfaenge, kleingeschrieben; Motiv)
MOTIVE: tuple[tuple[tuple[str, ...], str], ...] = (
    (("chunk", "schneid", "zerleg", "split"),
     "A paper cutter blade comes down and slices the slide into clean strips that slide apart"),
    (("mcp", "stecker", "schnittstelle", "anschluss", "plugin", "connector"),
     "A plug slides into a socket and a soft pulse of light travels along the cable across the frame"),
    (("eval", "mess", "bewert", "benchmark", "test"),
     "A measuring tape extends across the frame, locks, and snaps back"),
    (("embedding", "vektor", "vector"),
     "The slide breaks into small glowing dots that drift together into ordered clusters"),
    (("rag", "retriev", "such", "index", "search", "find"),
     "A magnifying glass sweeps over a row of index cards and lifts a single card out"),
    (("agent", "orchestr", "workflow", "automat"),
     "Small gears interlock and begin to turn, pushing the slide out of the frame"),
    (("gedächtnis", "gedaechtnis", "memory", "speicher", "cache"),
     "A drawer of a filing cabinet slides open, a card is filed, and the drawer closes"),
    (("prompt", "kontext", "context"),
     "Sheets of paper stack up neatly one on top of the other and the top sheet turns over"),
)
ALLGEMEIN = "The slide folds away like a sheet of paper and the next slide unfolds from behind it"
RAHMEN = ("Smooth transition between two presentation slides. {motiv}. The motion starts from the first frame and "
          "resolves exactly into the last frame. Matte 3D look, soft studio light, dark navy background with "
          "turquoise accents. No text, no letters, no logos, no people.")


def _woerter(*texte) -> list[str]:
    return re.findall(r"[a-zäöüß0-9]+", " ".join(str(t or "") for t in texte).lower())


def motiv(*texte) -> str:
    """Das erste Motiv, dessen Stichwort am Anfang eines Wortes der Texte steht; sonst das allgemeine."""
    woerter = _woerter(*texte)
    for stichwoerter, satz in MOTIVE:
        if any(w.startswith(s) for w in woerter for s in stichwoerter):
            return satz
    return ALLGEMEIN


def auftragstext(titel: str | None, thema: str | None, von_kurz: str | None, nach_kurz: str | None) -> str:
    """Vorschlag fuer den Auftragstext: einzeilig, ohne Steuerzeichen, hoechstens 1000 Zeichen. Die Texte des
    Auftrags dienen nur zur Wahl des Motivs und stehen nicht im Ergebnis."""
    return RAHMEN.format(motiv=motiv(titel, thema, von_kurz, nach_kurz))[:PROMPT_MAX]
