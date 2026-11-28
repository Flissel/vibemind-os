"""Stufen-Protokoll je Satz (Etagen-Ansicht, Plan 28.11.2026, Baustufe B).

Bisher trug eine Entscheidung genau eine Stufe: die, die entschieden hat. Hier kommt dazu,
welche Etagen der Satz beruehrt hat und wie lange jede brauchte:

    stufen_etage   ["eingang", "gespraech", "muster", "ausfuehrung", "pruefung", "ergebnis"]
    stufen_station ["ideas",   "kein-treffer", "ideen-fest", "idea.create", "validator-ok", "ideas"]
    stufen_ms      [1,         3,           7,           840,          -1,             2]

Drei gleich lange Listen an der Entscheidung (core/entscheidung_graph.py). -1 heisst "nicht
gemessen". Nur Kennungen und Zahlen: kein Regex-Text, kein Treffer, kein Satzteil. Jede Station
laeuft durch ``kennung``; was nicht wie eine Kennung aussieht, wird zu "?".

Messen darf den Satz nie aufhalten und nie stoeren:
  * ``marke`` ist ein Listeneintrag und ein Uhrenwert, mehr nicht.
  * Jede Funktion faengt alles ab. Ohne begonnenes Protokoll tun ``marke`` und ``station`` nichts.
  * Gebaut werden die Listen erst nach der Antwort (``listen``), geschrieben im eigenen Faden.

Das Protokoll haengt am laufenden Aufruf (contextvars): gleichzeitige Saetze vermischen sich
nicht. Schalter: ``STUFEN_PROTOKOLL=0`` schaltet alles ab.
"""
from __future__ import annotations

import contextvars
import os
import re
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

ETAGEN = ("eingang", "gespraech", "muster", "einordnung", "planung", "ausfuehrung", "pruefung",
          "ergebnis")
KEIN_TREFFER = "kein-treffer"
NICHT_GEMESSEN = -1
MAX_EINTRAEGE = 16

_KENNUNG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,47}$")
_PRUEFENDE = ("gespraech", "muster", "einordnung")   # Etagen, die ohne Treffer durchlaufen werden

_AKTUELL: contextvars.ContextVar[Optional[List[List[Any]]]] = contextvars.ContextVar(
    "stufen_protokoll", default=None)


def an() -> bool:
    return os.environ.get("STUFEN_PROTOKOLL", "1") not in ("0", "false", "False")


def kennung(wert: Any) -> str:
    """Der Wert, wenn er wie eine Kennung aussieht (Buchstaben, Ziffern, ``_ . : -``, hoechstens
    48 Zeichen), sonst "?". So kann kein Satzteil und kein Muster-Text in den Graphen geraten."""
    text = str(wert) if isinstance(wert, (str, int)) and not isinstance(wert, bool) else ""
    return text if _KENNUNG.match(text) else "?"


def beginnen() -> None:
    """Neues Protokoll fuer diesen Aufruf; die Uhr laeuft ab hier (Etage "eingang")."""
    try:
        _AKTUELL.set([["eingang", "", time.perf_counter()]] if an() else None)
    except Exception:  # noqa: BLE001
        pass


def marke(etage: str, station: str = "") -> None:
    """Ab jetzt laeuft ``etage``. Dieselbe Etage zweimal hintereinander bleibt ein Eintrag."""
    try:
        protokoll = _AKTUELL.get()
        if protokoll is None or etage not in ETAGEN or len(protokoll) >= MAX_EINTRAEGE:
            return
        if protokoll[-1][0] == etage:
            if station and not protokoll[-1][1]:
                protokoll[-1][1] = station
            return
        protokoll.append([etage, station, time.perf_counter()])
    except Exception:  # noqa: BLE001
        pass


def station(etage: str, name: Any) -> None:
    """Benennt die Station der zuletzt begonnenen ``etage`` (z. B. die Klasse der Einordnung)."""
    try:
        protokoll = _AKTUELL.get()
        if protokoll is None or not name:
            return
        for eintrag in reversed(protokoll):
            if eintrag[0] == etage:
                eintrag[1] = str(name)
                return
    except Exception:  # noqa: BLE001
        pass


def abholen() -> Optional[List[Tuple[str, str, int]]]:
    """Beendet das Protokoll und gibt (Etage, Station, ms) je Eintrag zurueck, sonst ``None``."""
    try:
        protokoll = _AKTUELL.get()
        _AKTUELL.set(None)
        if not protokoll:
            return None
        ende = time.perf_counter()
        grenzen = [e[2] for e in protokoll[1:]] + [ende]
        return [(e[0], e[1], max(0, int(round((bis - e[2]) * 1000))))
                for e, bis in zip(protokoll, grenzen)]
    except Exception:  # noqa: BLE001
        return None


def pruefung_aus(executed: Any) -> Optional[str]:
    """Station der Etage Pruefung aus den ausgefuehrten Schritten, ``None`` ohne Pruefung.

    Validator vor Freigabe; ein Stopp schlaegt ein Ja. Nur feste Namen, nie Inhalt.
    """
    if not isinstance(executed, Mapping):
        return None
    urteile, freigaben = [], []
    for schritt in executed.values():
        if not isinstance(schritt, Mapping):
            continue
        verdikt = schritt.get("validator_verdict")
        if isinstance(verdikt, Mapping) and "verified" in verdikt:
            urteile.append(verdikt.get("verified"))
        for aufruf in schritt.get("tool_calls") or []:
            status = aufruf.get("approval_status") if isinstance(aufruf, Mapping) else None
            if isinstance(status, str) and status and status != "none":
                freigaben.append(status)
    if urteile:
        if any(u is False for u in urteile):
            return "validator-stopp"
        return "validator-ok" if all(u is True for u in urteile) else "validator-offen"
    if freigaben:
        if any(f in ("denied", "deny") for f in freigaben):
            return "freigabe-nein"
        return "freigabe-offen" if any(f == "requested" for f in freigaben) else "freigabe-ok"
    return None


def _ausfuehrung_ms(executed: Any) -> Optional[int]:
    """Summe der Schritt-Dauern in ms, ``None`` ohne Angabe."""
    if not isinstance(executed, Mapping) or not executed:
        return None
    summe, gefunden = 0.0, False
    for schritt in executed.values():
        dauer = schritt.get("elapsed_s") if isinstance(schritt, Mapping) else None
        if isinstance(dauer, (int, float)) and not isinstance(dauer, bool) and dauer >= 0:
            summe += float(dauer)
            gefunden = True
    return int(round(summe * 1000)) if gefunden else None


def listen(protokoll: Any, *, entschied: str, stufe: Any, reiter: Any = None, bereich: Any = None,
           ausgang: Any = None, faehigkeit: Any = None, schritte: Any = None,
           executed: Any = None) -> Dict[str, List[Any]]:
    """Die drei Listen fuer die Entscheidung. Leere Listen, wenn nichts gemessen wurde.

    ``entschied`` ist die Etage, die entschieden hat (entscheidung_graph.etage_aus); ihre Station
    ist ``stufe``. Pruefende Etagen davor ohne eigene Station heissen "kein-treffer".
    Lief die Ausfuehrung innerhalb der entscheidenden Etage (Wege, die selbst ausfuehren), wird
    ihre Dauer aus den Schritten herausgerechnet und als eigene Etage gezeigt.
    """
    leer: Dict[str, List[Any]] = {"stufen_etage": [], "stufen_station": [], "stufen_ms": []}
    try:
        if not protokoll:
            return leer
        zeilen: List[List[Any]] = []
        for etage, name, ms in protokoll:
            if etage not in ETAGEN:
                continue
            if etage == "eingang":
                name = reiter or "ohne-reiter"
            elif etage == entschied:
                # Die Stufe der Entscheidung; bei der Einordnung dahinter die gemessene Klasse.
                klasse = name if etage == "einordnung" and name and name != stufe else ""
                name = f"{stufe}:{klasse}" if klasse and stufe else (stufe or name)
            elif etage in _PRUEFENDE and not name:
                name = KEIN_TREFFER
            elif etage == "ausfuehrung":
                name = name or faehigkeit
                if isinstance(schritte, int) and schritte > 1:
                    name = f"{schritte}-schritte"
            elif etage == "ergebnis":
                name = bereich or ausgang
            zeilen.append([etage, kennung(name), int(ms)])

        etagen = [z[0] for z in zeilen]
        if entschied in ETAGEN and entschied not in etagen:
            # Entschieden wurde, bevor die Etage begann (z. B. eine Absage mitten in den Mustern):
            # die letzte laufende Etage vor dem Ergebnis traegt die Stufe.
            for z in reversed(zeilen):
                if z[0] in _PRUEFENDE or z[0] == "planung":
                    z[1] = kennung(stufe)
                    break
        hat_schritte = isinstance(executed, Mapping) and bool(executed)
        if hat_schritte and "ausfuehrung" not in etagen and entschied in etagen:
            # Der Weg hat selbst ausgefuehrt: die Schritt-Dauer aus seiner Etage herausnehmen.
            stelle = etagen.index(entschied)
            dauer = _ausfuehrung_ms(executed)
            name = f"{schritte}-schritte" if isinstance(schritte, int) and schritte > 1 else faehigkeit
            if dauer is not None:
                dauer = min(dauer, zeilen[stelle][2])
                zeilen[stelle][2] -= dauer
            zeilen.insert(stelle + 1, ["ausfuehrung", kennung(name),
                                       NICHT_GEMESSEN if dauer is None else dauer])
            etagen.insert(stelle + 1, "ausfuehrung")
        pruefung = pruefung_aus(executed)
        if pruefung and "ausfuehrung" in etagen:
            stelle = etagen.index("ausfuehrung") + 1
            zeilen.insert(stelle, ["pruefung", pruefung, NICHT_GEMESSEN])
        return {"stufen_etage": [z[0] for z in zeilen],
                "stufen_station": [z[1] for z in zeilen],
                "stufen_ms": [z[2] for z in zeilen]}
    except Exception:  # noqa: BLE001
        return leer
