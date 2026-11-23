"""Beleg: Ergebnis eines fertigen Auftrags gegen Rowboat zuruecklesen.

`beleg` liefert genau einen von `entfaellt`, `bestaetigt`, `nicht_bestaetigt`, `nicht_pruefbar`.
`leser(kanal, koerper)` ist z. B. `RowboatRpc.aufruf`; ohne Leser oder bei jedem Fehler des
Lesers lautet das Ergebnis `nicht_pruefbar` (nie ein Fehler nach aussen, nie ein Inhalt).
"""
from __future__ import annotations

import re
from collections.abc import Callable

from spaces.auftragsbuch.vertrag import Auftragsart

ENTFAELLT = "entfaellt"
BESTAETIGT = "bestaetigt"
NICHT_BESTAETIGT = "nicht_bestaetigt"
NICHT_PRUEFBAR = "nicht_pruefbar"

_KENNUNG_AM_ENDE = re.compile(r"#([a-f0-9]{16})$")


def _kennung_in(text: object) -> str | None:
    """Wie rowboat/text.kennung_in (Task 7): Leerraum zusammenfassen, klein schreiben, `#<16 hex>` am Ende."""
    if not isinstance(text, str):
        return None
    treffer = _KENNUNG_AM_ENDE.search(re.sub(r"\s+", " ", text).strip().lower())
    return treffer.group(1) if treffer else None


def _items(bloecke: list, ziel: list[str]) -> None:
    for item in bloecke:
        ziel.append(item["text"])
        _items(item.get("children") or [], ziel)


def _kennungen_in_rowboat(leser: Callable[[str, object], object]) -> set[str]:
    todo = leser("todo:get", {})
    if not isinstance(todo, dict):
        raise ValueError("unerwartete Form")
    texte: list[str] = [str(t) for t in todo.get("suggestions") or []]
    _items([b["item"] for b in todo["list"]["blocks"] if b.get("kind") == "item"], texte)
    return {k for k in map(_kennung_in, texte) if k}


def _vorschlaege_anlegen(ergebnis: dict, leser: Callable[[str, object], object]) -> str:
    punkte = ergebnis["punkte"]
    neu = [p["punkt_id"] for p in punkte if p["stand"] == "neu"]
    if not neu:
        return BESTAETIGT
    vorhanden = _kennungen_in_rowboat(leser)
    return BESTAETIGT if all(k in vorhanden for k in neu) else NICHT_BESTAETIGT


_PRUEFER = {("rowboat", "vorschlaege_anlegen"): _vorschlaege_anlegen}


def beleg(art: Auftragsart, eingabe: dict, ergebnis: dict,
          leser: Callable[[str, object], object] | None) -> str:
    if art.nachpruefung == "form":
        return ENTFAELLT
    pruefer = _PRUEFER.get((art.space, art.art))
    if leser is None or pruefer is None:
        return NICHT_PRUEFBAR
    try:
        return pruefer(ergebnis, leser)
    except Exception:  # noqa: BLE001 - jeder Fehler beim Lesen heisst: nicht pruefbar
        return NICHT_PRUEFBAR
