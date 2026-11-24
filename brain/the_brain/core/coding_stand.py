"""Feste Lesesaetze des Project Space (Coding): Stand, letzte Laeufe, Ergebnis.

Der Project Space hat einen eigenen Dienst (Port 8163) mit den Lesepfaden ``/api/stand``,
``/api/liste`` und ``/api/ergebnis``. Diese Saetze fragen ihn ohne Modell und ohne Agent und
machen aus seiner Antwort einen deutschen Satz. Geschrieben oder gestartet wird hier nichts.

Adresse: ``CODING_DIENST_URL``; ohne sie ``http://host.docker.internal:8163`` (das Gehirn laeuft
im Container, der Dienst auf dem Rechner des Betreibers). Laeuft der Dienst nicht, sagt jeder
Satz genau das. Das Modul ist eigenstaendig, weil im Container nur ``core/`` liegt.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Tuple

VORGABE_URL = "http://host.docker.internal:8163"
WARTEN_S = 5.0
GRENZE = 5

STUFE = {"annahme": "Annahme", "plan": "Plan", "bau": "Bau", "pruefung": "Prüfung", "abschluss": "Abschluss"}
ZUSTAND = {"wartet": "wartet", "laeuft": "läuft", "fertig": "fertig", "fehlgeschlagen": "gescheitert",
           "abgebrochen": "abgebrochen"}

NICHT_ERREICHBAR = "Der Dienst des Project Space antwortet nicht. Geändert habe ich nichts."
KEIN_LAUF = "Im Project Space gibt es noch keinen Lauf."


def _basis() -> str:
    return (os.environ.get("CODING_DIENST_URL") or VORGABE_URL).rstrip("/")


def _holen(pfad: str, **parameter: Any) -> Tuple[int, Optional[Dict[str, Any]]]:
    """(HTTP-Status, JSON) eines GET an den Dienst; (0, None) ohne Verbindung. Wirft nie."""
    frage = {k: v for k, v in parameter.items() if v is not None}
    url = _basis() + pfad + (("?" + urllib.parse.urlencode(frage)) if frage else "")
    try:
        with urllib.request.urlopen(url, timeout=WARTEN_S) as antwort:
            return antwort.status, json.loads(antwort.read().decode("utf-8"))
    except urllib.error.HTTPError as fehler:
        try:
            return fehler.code, json.loads(fehler.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return fehler.code, None
    except Exception:  # noqa: BLE001 - Verbindung, Zeit, kein JSON
        return 0, None


def _lesen(pfad: str, **parameter: Any) -> Dict[str, Any]:
    status, daten = _holen(pfad, **parameter)
    if status == 0 or daten is None and status != 404:
        return {"success": False, "error": "nicht_erreichbar"}
    if status == 404:
        return {"success": True, "kein_lauf": True}
    if status >= 400:
        return {"success": False, "error": "dienst_fehler", "status": status}
    return {"success": True, "daten": daten}


# ── Ziele (direct:core.coding_stand:...) ──────────────────────────────────────

def stand_lesen(value: Any = None, **_: Any) -> dict:
    """Ziel ``direct:core.coding_stand:stand_lesen``: der neueste Lauf (``/api/stand``)."""
    return _lesen("/api/stand")


def laeufe_lesen(value: Any = None, **_: Any) -> dict:
    """Ziel ``direct:core.coding_stand:laeufe_lesen``: die neuesten Laeufe (``/api/liste``)."""
    return _lesen("/api/liste", grenze=GRENZE)


def ergebnis_lesen(value: Any = None, **_: Any) -> dict:
    """Ziel ``direct:core.coding_stand:ergebnis_lesen``: neuester Lauf, bei ``fertig`` mit Ergebnis."""
    stand = _lesen("/api/stand")
    if stand.get("success") is not True or stand.get("kein_lauf"):
        return stand
    daten = stand.get("daten") or {}
    if daten.get("zustand") == "fertig" and daten.get("auftrag"):
        ergebnis = _lesen("/api/ergebnis", auftrag=daten["auftrag"])
        if ergebnis.get("success") is True and not ergebnis.get("kein_lauf"):
            stand["ergebnis"] = ergebnis.get("daten")
    return stand


# ── Saetze ─────────────────────────────────────────────────────────────────────

def dauer(sekunden: Any) -> str:
    """Dauer in Worten: 42 Sekunden, 3 Minuten, 1 Stunde 5 Minuten."""
    try:
        s = max(0, round(float(sekunden)))
    except (TypeError, ValueError):
        return ""
    if s < 60:
        return f"{s} Sekunden"
    m = s // 60
    if m < 60:
        return "1 Minute" if m == 1 else f"{m} Minuten"
    h, rest = divmod(m, 60)
    stunden = "1 Stunde" if h == 1 else f"{h} Stunden"
    return f"{stunden} {rest} Minuten" if rest else stunden


def _titel(lauf: Mapping[str, Any]) -> str:
    titel = " ".join(str(lauf.get("titel") or "").split())[:80] or "Ohne Titel"
    return f"„{titel}“"


def _wo(stand: Mapping[str, Any]) -> str:
    kurz = stand.get("kurz") or {}
    roh = kurz.get("stufe") or stand.get("stufe") or ""
    stufe = STUFE.get(roh, roh)
    if kurz.get("pakete_gesamt"):
        return f"{stufe}, Paket {kurz.get('paket_nr')} von {kurz['pakete_gesamt']}"
    return stufe


def stand_satz(stand: Mapping[str, Any]) -> str:
    """Ein Satz ueber einen Lauf, aus der Antwort von ``/api/stand``."""
    zustand = stand.get("zustand")
    titel = _titel(stand)
    if zustand == "laeuft":
        return f"{titel} läuft gerade: {_wo(stand)}."
    if zustand == "wartet":
        return f"{titel} ist angelegt und wartet auf den Start."
    if zustand == "fertig":
        zeit = dauer(stand.get("dauer_s"))
        return f"{titel} ist fertig{f' nach {zeit}' if zeit else ''}."
    if zustand == "fehlgeschlagen":
        grund = " ".join(str(stand.get("fehler") or "").split())[:300]
        return f"{titel} ist gescheitert. {grund}".strip()
    if zustand == "abgebrochen":
        return f"{titel} wurde abgebrochen."
    return f"{titel}: {ZUSTAND.get(zustand, zustand or 'unbekannt')}."


def liste_satz(liste: Mapping[str, Any]) -> str:
    """Ein Satz ueber die neuesten Laeufe, aus der Antwort von ``/api/liste``."""
    auftraege: List[Mapping[str, Any]] = [a for a in liste.get("auftraege") or [] if isinstance(a, Mapping)]
    if not auftraege:
        return KEIN_LAUF
    try:
        anzahl = int(liste.get("anzahl") or len(auftraege))
    except (TypeError, ValueError):
        anzahl = len(auftraege)
    teile = [f"{_titel(a)} ({ZUSTAND.get(a.get('zustand'), a.get('zustand') or 'unbekannt')})" for a in auftraege]
    kopf = "Im Project Space gibt es einen Lauf" if anzahl == 1 else f"Im Project Space gibt es {anzahl} Läufe"
    if anzahl > len(auftraege):
        return f"{kopf}, die neuesten: {', '.join(teile)}."
    return f"{kopf}: {', '.join(teile)}."


def ergebnis_satz(stand: Mapping[str, Any], ergebnis: Optional[Mapping[str, Any]]) -> str:
    """Satz zum neuesten Lauf; bei einem fertigen Lauf mit Paketen, Pruefung und Startbefehl."""
    satz = stand_satz(stand)
    if stand.get("zustand") != "fertig" or not isinstance(ergebnis, Mapping):
        return satz
    pakete = len(ergebnis.get("pakete") or [])
    dateien = len(ergebnis.get("dateien") or [])
    teile = [satz, f"Gebaut wurden {pakete} {'Paket' if pakete == 1 else 'Pakete'} mit "
                   f"{dateien} {'Datei' if dateien == 1 else 'Dateien'}."]
    pruefung = ergebnis.get("pruefung") or {}
    teile.append("Alle Prüfungen sind bestanden." if pruefung.get("bestanden")
                 else "Die Prüfung ist nicht bestanden.")
    if ergebnis.get("start"):
        teile.append(f"Gestartet wird die App mit „{ergebnis['start']}“ in ihrem Ordner.")
    return " ".join(teile)


def _ergebnis_aus(roh: Any) -> Optional[Mapping[str, Any]]:
    if isinstance(roh, Mapping) and isinstance(roh.get("result"), Mapping) and "success" not in roh:
        roh = roh["result"]
    if not isinstance(roh, Mapping) or roh.get("success") is not True:
        return None
    return roh


def antwort_stand(ergebnis: Any) -> Optional[str]:
    try:
        roh = _ergebnis_aus(ergebnis)
        if roh is None:
            return None
        if roh.get("kein_lauf"):
            return KEIN_LAUF
        return stand_satz(roh.get("daten") or {})
    except Exception:  # noqa: BLE001
        return None


def antwort_laeufe(ergebnis: Any) -> Optional[str]:
    try:
        roh = _ergebnis_aus(ergebnis)
        if roh is None:
            return None
        if roh.get("kein_lauf"):
            return KEIN_LAUF
        return liste_satz(roh.get("daten") or {})
    except Exception:  # noqa: BLE001
        return None


def antwort_ergebnis(ergebnis: Any) -> Optional[str]:
    try:
        roh = _ergebnis_aus(ergebnis)
        if roh is None:
            return None
        if roh.get("kein_lauf"):
            return KEIN_LAUF
        return ergebnis_satz(roh.get("daten") or {}, roh.get("ergebnis"))
    except Exception:  # noqa: BLE001
        return None


def fehlersatz(ergebnis: Any, fehler: Any = None) -> str:
    """Genauer Satz, wenn der Dienst nicht antwortet oder einen Fehler meldet."""
    if isinstance(ergebnis, Mapping) and isinstance(ergebnis.get("result"), Mapping) and "error" not in ergebnis:
        ergebnis = ergebnis["result"]
    art = ergebnis.get("error") if isinstance(ergebnis, Mapping) else None
    if art == "dienst_fehler":
        return "Der Dienst des Project Space meldet einen Fehler. Geändert habe ich nichts."
    return NICHT_ERREICHBAR
