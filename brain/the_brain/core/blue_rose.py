"""Blue Rose als Personal Space, Stufe 1: Zahlen aus dem Entscheidungs-Graphen, Befinden von
Hand, Schalter, Loeschen. Ohne Modell.

Was hier gilt (Plan vom 28.11.2026, Freigabe des Betreibers "ueberall a"):

* Der Graph wird nur gelesen, und nur ohne Satztext (``DecisionGraph.entscheidungen_zahlen``).
  Aus ihm kommen Anzahlen: Saetze je Stunde und Bereich, "nicht verstanden", misslungen, Dauer.
* Das Befinden kommt nur aus der eigenen Antwort des Nutzers: eines von fuenf Woertern und
  eine Zahl 1-10. Kein freier Text, keine Deutung.
* Eigene Tabellen ``blue_rose_befinden`` und ``blue_rose_einstellungen`` (Migration
  20261128_blue_rose.sql), nur fuer die Service-Rolle. Der oeffentliche Schluessel der App
  sieht sie nicht. Fehlt der Service-Schluessel oder die Tabelle, sagt das die Antwort.
* Schalter ``analyse``: aus = der Graph wird nicht gelesen. Vorhandenes bleibt sichtbar und
  loeschbar. Ist der Schalter nicht lesbar, gilt er als aus.
* Protokolle nennen Anzahlen und Fehlerarten, nie Inhalte.

Die alten Flowzen-Tabellen (flowzen_checkins, flowzen_activity, flowzen_diary) werden nur
gezaehlt und auf Wunsch geleert; geschrieben wird dort nichts mehr.
"""
from __future__ import annotations

import logging
import os
import statistics
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional

logger = logging.getLogger("brain.blue_rose")

STIMMUNGEN = ("energized", "focused", "calm", "tired", "anxious")
TABELLE_BEFINDEN = "blue_rose_befinden"
TABELLE_EINSTELLUNGEN = "blue_rose_einstellungen"
# Art -> (Tabelle, Filter "alle Zeilen" fuer PostgREST; ein DELETE ohne Filter lehnt es ab)
ARTEN: Dict[str, tuple] = {
    "befinden": (TABELLE_BEFINDEN, {"id": "not.is.null"}),
    "alt_befinden": ("flowzen_checkins", {"id": "not.is.null"}),
    "alt_aktivitaet": ("flowzen_activity", {"id": "not.is.null"}),
    "alt_tagebuch": ("flowzen_diary", {"id": "not.is.null"}),
}
NICHT_VERSTANDEN = frozenset({"nicht-verstanden", "mehrdeutig"})
TAGE_MAX = 30
ZAEHL_GRENZE = 1000
EMPFEHLUNG_FRISCH_S = 6 * 3600

KATEGORIEN = {
    "deep_work": "Konzentrierte Arbeit",
    "creative": "Kreatives",
    "admin": "Verwaltung und Kleinkram",
    "social": "Austausch mit anderen",
    "rest": "Pause",
}


# ── Zahlen aus dem Graphen (reine Rechnung) ──────────────────────────


def _bereich(wert: Any) -> Optional[str]:
    if not isinstance(wert, str) or not wert:
        return None
    return wert.split(":", 1)[1] if wert.startswith("bereich:") else wert


def zahlen(zeilen: Iterable[Mapping[str, Any]], *, jetzt: Optional[float] = None,
           tage: int = 14) -> Dict[str, Any]:
    """Anzahlen aus Zeilen je Satz. Die Zeilen tragen keinen Satztext; hier entsteht auch keiner.

    Gezaehlt werden nur Saetze des Nutzers (``laenge`` > 0): Seiten der App lesen ueber denselben
    Weg mit leerem Satz und sind keine Arbeit des Nutzers. ``stunden_utc`` ist in UTC; die App
    kennt die Ortszeit und legt die Tageszeiten selbst.
    """
    jetzt = time.time() if jetzt is None else float(jetzt)
    tage = max(1, min(int(tage), TAGE_MAX))
    seit = jetzt - tage * 86400
    gesehen = set()
    stunden = [0] * 24
    je_bereich: Dict[str, Dict[str, Any]] = {}
    dauern: List[float] = []
    tage_aktiv = set()
    summe = {"saetze": 0, "nicht_verstanden": 0, "misslungen": 0, "gesprochen": 0}
    for zeile in zeilen:
        if not isinstance(zeile, Mapping):
            continue
        ts = zeile.get("ts")
        laenge = zeile.get("laenge")
        if not isinstance(ts, (int, float)) or isinstance(ts, bool) or ts < seit or ts > jetzt + 60:
            continue
        if not isinstance(laenge, int) or isinstance(laenge, bool) or laenge <= 0:
            continue
        spur = zeile.get("trace_id")
        if spur is not None:
            if spur in gesehen:
                continue
            gesehen.add(spur)
        summe["saetze"] += 1
        stunden[int(ts // 3600) % 24] += 1
        tage_aktiv.add(int(ts // 86400))
        name = _bereich(zeile.get("bereich")) or _bereich(zeile.get("reiter")) or "ohne"
        feld = je_bereich.setdefault(name, {"id": name, "saetze": 0, "nicht_verstanden": 0,
                                           "misslungen": 0, "_dauern": []})
        feld["saetze"] += 1
        if zeile.get("weg") in NICHT_VERSTANDEN:
            summe["nicht_verstanden"] += 1
            feld["nicht_verstanden"] += 1
        elif zeile.get("ok") is False:
            summe["misslungen"] += 1
            feld["misslungen"] += 1
        if isinstance(spur, str) and spur.startswith("sprache-"):
            summe["gesprochen"] += 1
        dauer = zeile.get("dauer_ms")
        if isinstance(dauer, (int, float)) and not isinstance(dauer, bool) and dauer >= 0:
            dauern.append(float(dauer))
            feld["_dauern"].append(float(dauer))
    bereiche = []
    for feld in sorted(je_bereich.values(), key=lambda f: (-f["saetze"], f["id"])):
        werte = feld.pop("_dauern")
        feld["dauer_ms_median"] = int(statistics.median(werte)) if werte else None
        bereiche.append(feld)
    return {
        "tage": tage,
        **summe,
        "getippt": summe["saetze"] - summe["gesprochen"],
        "aktive_tage": len(tage_aktiv),
        "stunden_utc": stunden,
        "bereiche": bereiche,
        "dauer_ms_median": int(statistics.median(dauern)) if dauern else None,
    }


def zahlen_lesen(graph: Any, *, tage: int = 14, jetzt: Optional[float] = None) -> Dict[str, Any]:
    """Zahlen aus dem Graphen. ``graph`` ist der DecisionGraph des Gehirns oder ``None``."""
    jetzt = time.time() if jetzt is None else float(jetzt)
    tage = max(1, min(int(tage), TAGE_MAX))
    if graph is None or not hasattr(graph, "entscheidungen_zahlen"):
        return {"ok": False, "grund": "graph_fehlt"}
    try:
        roh = graph.entscheidungen_zahlen(jetzt - tage * 86400)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[blue-rose] Graph nicht lesbar: {type(e).__name__}")
        return {"ok": False, "grund": "graph_fehler"}
    out = zahlen(roh.get("zeilen") or [], jetzt=jetzt, tage=tage)
    return {"ok": True, "quelle": roh.get("quelle"), **out}


# ── Eigene Tabellen (nur Service-Rolle) ──────────────────────────────


def tabelle_aus_umgebung() -> Any:
    """Supabase-Zugang mit dem Service-Schluessel, oder ``None`` wenn er fehlt."""
    try:
        from core.config import get_secret

        schluessel = (get_secret("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    except Exception:  # noqa: BLE001
        schluessel = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    if not url or not schluessel:
        return None
    from core.supabase_ideas_client import SupabaseIdeasClient

    return SupabaseIdeasClient(url=url, anon_key=schluessel)


def _fehlgrund(client: Any) -> str:
    """Warum die letzte Anfrage scheiterte: Tabelle fehlt (Migration) oder nicht erreichbar."""
    letzter = str((getattr(client, "stats", None) or {}).get("last_error") or "")
    if "HTTP 404" in letzter or "PGRST205" in letzter or "42P01" in letzter:
        return "tabelle_fehlt"
    if "HTTP 401" in letzter or "HTTP 403" in letzter:
        return "kein_zugriff"
    return "nicht_erreichbar"


def stimmung_gueltig(wert: Any) -> bool:
    return isinstance(wert, str) and wert in STIMMUNGEN


def energie_gueltig(wert: Any) -> bool:
    return isinstance(wert, int) and not isinstance(wert, bool) and 1 <= wert <= 10


def stunde_gueltig(wert: Any) -> bool:
    return isinstance(wert, int) and not isinstance(wert, bool) and 0 <= wert <= 23


def tageszeit(stunde: int) -> str:
    """Wie bisher in Flowzen (voice/python/data/flowzen_repository.py)."""
    if 5 <= stunde < 8:
        return "early_morning"
    if 8 <= stunde < 12:
        return "morning"
    if 12 <= stunde < 14:
        return "midday"
    if 14 <= stunde < 17:
        return "afternoon"
    if 17 <= stunde < 21:
        return "evening"
    return "night"


_BEFINDEN_FELDER = "id,stimmung,energie,stunde,tageszeit,angelegt"


async def befinden_liste(client: Any, grenze: int = 60) -> Dict[str, Any]:
    zeilen = await client._request(
        "GET", f"/{TABELLE_BEFINDEN}",
        params={"select": _BEFINDEN_FELDER, "order": "angelegt.desc",
                "limit": str(max(1, min(int(grenze), 500)))},
    )
    if not isinstance(zeilen, list):
        return {"ok": False, "grund": _fehlgrund(client), "eintraege": []}
    return {"ok": True, "eintraege": zeilen}


async def befinden_eintragen(client: Any, stimmung: Any, energie: Any, stunde: Any) -> Dict[str, Any]:
    """Ein Eintrag aus der eigenen Antwort des Nutzers. Nachgelesen, bevor "ok" gemeldet wird."""
    if not (stimmung_gueltig(stimmung) and energie_gueltig(energie) and stunde_gueltig(stunde)):
        return {"ok": False, "grund": "eingabe_ungueltig"}
    zeile = {"stimmung": stimmung, "energie": energie, "stunde": stunde,
             "tageszeit": tageszeit(stunde)}
    neu = await client._request("POST", f"/{TABELLE_BEFINDEN}", json=zeile,
                                prefer="return=representation")
    if not isinstance(neu, list) or not neu or not neu[0].get("id"):
        return {"ok": False, "grund": _fehlgrund(client)}
    kennung = str(neu[0]["id"])
    gelesen = await client._request(
        "GET", f"/{TABELLE_BEFINDEN}",
        params={"select": _BEFINDEN_FELDER, "id": f"eq.{kennung}", "limit": "1"},
    )
    if not isinstance(gelesen, list) or not gelesen or gelesen[0].get("stimmung") != stimmung:
        return {"ok": False, "grund": "nicht_bestaetigt"}
    logger.info("[blue-rose] Befinden eingetragen (1 Zeile)")
    return {"ok": True, "eintrag": gelesen[0]}


async def anzahl(client: Any, tabelle: str) -> Optional[int]:
    """Zeilen einer Tabelle, hoechstens ``ZAEHL_GRENZE``; ``None`` = nicht lesbar."""
    zeilen = await client._request(
        "GET", f"/{tabelle}", params={"select": "id", "limit": str(ZAEHL_GRENZE)},
    )
    return len(zeilen) if isinstance(zeilen, list) else None


async def bestand(client: Any) -> Dict[str, Any]:
    """Was gespeichert ist: Anzahl je Art. Nie Inhalte."""
    out: Dict[str, Any] = {}
    for art, (tabelle, _) in ARTEN.items():
        out[art] = await anzahl(client, tabelle)
    return out


async def loeschen(client: Any, art: Any, kennung: Any = None) -> Dict[str, Any]:
    """Eine Art ganz loeschen, oder (nur ``befinden``) einen Eintrag. Danach wird nachgezaehlt."""
    if art == "alles":
        ergebnisse = {name: await loeschen(client, name) for name in ARTEN}
        return {"ok": all(e.get("ok") for e in ergebnisse.values()), "arten": ergebnisse}
    if art not in ARTEN:
        return {"ok": False, "grund": "art_unbekannt"}
    tabelle, alle = ARTEN[art]
    if kennung is not None:
        if art != "befinden" or not isinstance(kennung, str) or not kennung or len(kennung) > 64 \
                or not all(z.isalnum() or z == "-" for z in kennung):
            return {"ok": False, "grund": "kennung_ungueltig"}
        filter_ = {"id": f"eq.{kennung}"}
    else:
        filter_ = dict(alle)
    antwort = await client._request("DELETE", f"/{tabelle}", params=filter_)
    if antwort is None:
        return {"ok": False, "grund": _fehlgrund(client)}
    rest = await client._request(
        "GET", f"/{tabelle}", params={"select": "id", "limit": "1", **filter_},
    )
    if not isinstance(rest, list):
        return {"ok": False, "grund": "nicht_bestaetigt"}
    if rest:
        return {"ok": False, "grund": "nicht_geloescht"}
    logger.info(f"[blue-rose] geloescht: Art {art}" + (" (ein Eintrag)" if kennung else " (alle)"))
    return {"ok": True}


VORGABEN = {"analyse": True}


async def einstellungen_lesen(client: Any) -> Dict[str, Any]:
    """Schalter. Nicht lesbar = ``analyse`` aus (lieber nichts lesen als ungefragt)."""
    zeilen = await client._request(
        "GET", f"/{TABELLE_EINSTELLUNGEN}", params={"select": "schluessel,wert"},
    )
    if not isinstance(zeilen, list):
        return {"ok": False, "grund": _fehlgrund(client), "analyse": False}
    werte = dict(VORGABEN)
    for zeile in zeilen:
        name = zeile.get("schluessel")
        if name in VORGABEN and isinstance(zeile.get("wert"), bool):
            werte[name] = zeile["wert"]
    return {"ok": True, **werte}


async def einstellung_setzen(client: Any, name: Any, wert: Any) -> Dict[str, Any]:
    if name not in VORGABEN or not isinstance(wert, bool):
        return {"ok": False, "grund": "eingabe_ungueltig"}
    antwort = await client._request(
        "POST", f"/{TABELLE_EINSTELLUNGEN}",
        params={"on_conflict": "schluessel"},
        json={"schluessel": name, "wert": wert},
        prefer="resolution=merge-duplicates,return=representation",
    )
    if not isinstance(antwort, list) or not antwort:
        return {"ok": False, "grund": _fehlgrund(client)}
    stand = await einstellungen_lesen(client)
    if not stand.get("ok") or stand.get(name) is not wert:
        return {"ok": False, "grund": "nicht_bestaetigt"}
    return {"ok": True, name: wert}


def empfehlung(letzter: Optional[Mapping[str, Any]], stunde: int, *,
               alter_s: Optional[float]) -> Dict[str, Any]:
    """"Was als Naechstes?" aus der festen Tabelle (core/flowzen_ops), ohne Modell.

    Nur mit einem frischen eigenen Eintrag: eine alte Stimmung waere geraten.
    """
    if not letzter or alter_s is None or alter_s > EMPFEHLUNG_FRISCH_S or alter_s < -60:
        return {"ok": False, "grund": "befinden_fehlt"}
    from core.flowzen_ops import _category

    fenster = tageszeit(stunde)
    art = _category(str(letzter.get("stimmung") or ""), fenster)
    return {"ok": True, "kategorie": art, "name": KATEGORIEN.get(art, art), "tageszeit": fenster}
