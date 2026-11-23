"""Abgleich je punkt_id: was ist in Rowboat schon bekannt, was muss neu eingestellt werden (rein)."""
from __future__ import annotations

from spaces.auftragsbuch.rowboat.text import kennung_in

SCHON_VORHANDEN = "schon_vorhanden"
FRUEHER_ABGELEHNT = "frueher_abgelehnt"
NEU = "neu"
_ABGELEHNT = {"dismissed", "taught"}


def _item_texte(items: object, ziel: list[str], tiefe: int = 0) -> None:
    if not isinstance(items, list) or tiefe > 2:
        raise ValueError("unerwartete Form")
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError("unerwartete Form")
        ziel.append(item["text"])
        _item_texte(item.get("children") or [], ziel, tiefe + 1)


def todo_texte(todo: object) -> tuple[list[str], list[str]]:
    """(offene Vorschlaege, Listeneintraege samt Unterpunkten) aus der Antwort von `todo:get`.

    Form nach Rowboat (`packages/shared/src/ipc.ts`, `todo.ts`): `suggestions: string[]`,
    `list.blocks[]` mit `{kind: 'item', item: {text, children[]}}` oder `{kind: 'raw', ...}`.
    Jede andere Form: ValueError (ohne Inhalt).
    """
    if not isinstance(todo, dict):
        raise ValueError("unerwartete Form")
    offen = todo.get("suggestions")
    liste = todo.get("list")
    if not isinstance(offen, list) or not all(isinstance(t, str) for t in offen) or not isinstance(liste, dict):
        raise ValueError("unerwartete Form")
    bloecke = liste.get("blocks")
    if not isinstance(bloecke, list) or not all(isinstance(b, dict) for b in bloecke):
        raise ValueError("unerwartete Form")
    texte: list[str] = []
    _item_texte([b.get("item") for b in bloecke if b.get("kind") == "item"], texte)
    return list(offen), texte


def signale_aus(antwort: object) -> list[dict]:
    """`signals` aus der Antwort von `todo:plannerSignals`; jede andere Form: ValueError."""
    signale = antwort.get("signals") if isinstance(antwort, dict) else None
    if not isinstance(signale, list):
        raise ValueError("unerwartete Form")
    return signale


def _kennungen(texte: list[str]) -> set[str]:
    return {k for k in map(kennung_in, texte) if k}


def _letzte_urteile(signale: list[dict]) -> dict[str, str]:
    """Letzte Signalart je Kennung. Reihenfolge = Listenreihenfolge (aelteste zuerst)."""
    urteil: dict[str, str] = {}
    for s in signale:
        if not isinstance(s, dict):
            continue
        k = kennung_in(s.get("key"))
        if k and isinstance(s.get("kind"), str):
            urteil[k] = s["kind"]
    return urteil


def einordnen(punkte: list[dict], offen: list[str], liste: list[str], signale: list[dict],
              bekannt: set[str]) -> tuple[dict[str, str], list[dict]]:
    """Stand je Kennung fuer alles schon Bekannte plus die neu einzustellenden Punkte.

    `offen`: Texte der offenen Vorschlaege (`todo:get` -> suggestions); `liste`: Texte der
    Listeneintraege (inkl. Kinder). Beide enthalten die Kennung am Ende des Textes.
    `signale`: Antwort `signals` von Rowboat `todo:plannerSignals`, je Eintrag
    `{kind, key, at}` (key = normalisierter, kleingeschriebener Text, Kennung am Ende; at = ISO-Zeit).
    Gelesen werden nur `kind` und `key`. Das Protokoll ist anhaengend: Rowboat selbst
    (`stickyDismissedKeys`) laesst das spaeter eingetragene Signal gewinnen, nach Listenreihenfolge
    und nicht nach `at`; genauso hier. `dismissed` und `taught` gelten als abgelehnt.
    `bekannt`: Kennungen aus dem Auftragsbuch (auch Archiviertes).

    Reihenfolge der Regeln: offen/Liste -> schon_vorhanden; letztes Signal dismissed/taught ->
    frueher_abgelehnt; bekannt -> schon_vorhanden; sonst neu. Doppelte Kennung: die erste zaehlt.
    """
    da = _kennungen(offen) | _kennungen(liste)
    urteil = _letzte_urteile(signale)
    stand: dict[str, str] = {}
    neu: list[dict] = []
    gesehen: set[str] = set()
    for p in punkte:
        pid = p["punkt_id"]
        if pid in gesehen:
            continue
        gesehen.add(pid)
        if pid in da:
            stand[pid] = SCHON_VORHANDEN
        elif urteil.get(pid) in _ABGELEHNT:
            stand[pid] = FRUEHER_ABGELEHNT
        elif pid in bekannt:
            stand[pid] = SCHON_VORHANDEN
        else:
            neu.append(p)
    return stand, neu


def nach_einstellen(neu: list[dict], hinzugefuegt: list[str]) -> tuple[dict[str, str], list[str]]:
    """Stand `neu` je Kennung, die in `hinzugefuegt` steht; dazu die Kennungen aus `neu`, die fehlen.

    `hinzugefuegt` sind die Texte, die Rowboat (`addSuggestions`) zurueckgibt (normalisiert:
    Leerraum zusammengefasst, getrimmt), nicht die Kennungen. Abgeglichen wird ueber die
    Kennung am Textende (`kennung_in`); ein Eintrag ohne Kennung zaehlt als Rohwert.
    """
    da = {kennung_in(t) or t for t in hinzugefuegt}
    stand: dict[str, str] = {}
    fehlen: list[str] = []
    for p in neu:
        pid = p["punkt_id"]
        if pid in da:
            stand[pid] = NEU
        elif pid not in fehlen:
            fehlen.append(pid)
    return stand, fehlen
