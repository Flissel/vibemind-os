"""Beleg: Ergebnis eines fertigen Auftrags gegen Rowboat zuruecklesen.

`beleg` liefert genau einen von `entfaellt`, `bestaetigt`, `nicht_bestaetigt`, `nicht_pruefbar`.
`leser(kanal, koerper)` ist z. B. `RowboatRpc.aufruf`; ohne Leser oder bei jedem Fehler des
Lesers lautet das Ergebnis `nicht_pruefbar` (nie ein Fehler nach aussen, nie ein Inhalt).
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from spaces.auftragsbuch.rowboat.text import kennung_in
from spaces.auftragsbuch.vertrag import Auftragsart

log = logging.getLogger("auftragsbuch")

ENTFAELLT = "entfaellt"
BESTAETIGT = "bestaetigt"
NICHT_BESTAETIGT = "nicht_bestaetigt"
NICHT_PRUEFBAR = "nicht_pruefbar"


def _items(bloecke: list, ziel: list[str]) -> None:
    for item in bloecke:
        ziel.append(item["text"])
        _items(item.get("children") or [], ziel)


def _kennungen_in_rowboat(leser: Callable[[str, object], object]) -> set[str]:
    todo = leser("todo:get", None)  # Rowboat: req ist z.null()
    if not isinstance(todo, dict):
        raise ValueError("unerwartete Form")
    texte: list[str] = [str(t) for t in todo.get("suggestions") or []]
    _items([b["item"] for b in todo["list"]["blocks"] if b.get("kind") == "item"], texte)
    return {k for k in map(kennung_in, texte) if k}


def _vorschlaege_anlegen(eingabe: dict, ergebnis: dict, leser: Callable[[str, object], object]) -> str:
    punkte = ergebnis["punkte"]
    if sorted(p["punkt_id"] for p in punkte) != sorted(p["punkt_id"] for p in eingabe["punkte"]):
        return NICHT_BESTAETIGT
    neu = [p["punkt_id"] for p in punkte if p["stand"] == "neu"]
    if not neu:
        return BESTAETIGT
    vorhanden = _kennungen_in_rowboat(leser)
    return BESTAETIGT if all(k in vorhanden for k in neu) else NICHT_BESTAETIGT


_PRUEFER = {("rowboat", "vorschlaege_anlegen"): _vorschlaege_anlegen}


def beleg(art: Auftragsart, eingabe: dict, ergebnis: dict,
          leser: Callable[[str, object], object] | None, auftrag: str = "") -> str:
    if art.nachpruefung == "form":
        return ENTFAELLT
    pruefer = _PRUEFER.get((art.space, art.art))
    if leser is None or pruefer is None:
        return NICHT_PRUEFBAR
    try:
        return pruefer(eingabe, ergebnis, leser)
    except Exception as e:  # noqa: BLE001 - jeder Fehler beim Lesen heisst: nicht pruefbar
        grund = getattr(e, "grund", None)  # fester Text von RowboatFehler, sonst nur der Typname
        log.info("auftrag=%s art=%s beleg=nicht_pruefbar grund=%s", auftrag[:8], art.art,
                 grund if isinstance(grund, str) else type(e).__name__)
        return NICHT_PRUEFBAR
