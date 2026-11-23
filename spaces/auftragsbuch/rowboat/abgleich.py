"""Abgleich je punkt_id: was ist in Rowboat schon bekannt, was muss neu eingestellt werden (rein)."""
from __future__ import annotations

from spaces.auftragsbuch.rowboat.text import kennung_in

SCHON_VORHANDEN = "schon_vorhanden"
FRUEHER_ABGELEHNT = "frueher_abgelehnt"
NEU = "neu"
_ABGELEHNT = {"dismissed", "taught"}


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
    """Stand `neu` je Kennung aus `hinzugefuegt`; dazu die Kennungen aus `neu`, die fehlen."""
    da = set(hinzugefuegt)
    stand: dict[str, str] = {}
    fehlen: list[str] = []
    for p in neu:
        pid = p["punkt_id"]
        if pid in da:
            stand[pid] = NEU
        elif pid not in fehlen:
            fehlen.append(pid)
    return stand, fehlen
