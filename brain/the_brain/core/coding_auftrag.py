"""Bauauftrag an den Project Space: Rueckfrage, Start, Nachfassen.

Der Project Space hat einen eigenen Dienst (Port 8163, ``spaces/coding/dienst.py``). Er nimmt mit
``POST /api/auftrag`` einen Satz an, laesst die Coding-Engine daraus JavaScript auf Node.js bauen
und meldet den Stand unter ``/api/stand``. Dieser Weg verbindet das Gehirn damit, ohne Modell und
ohne Planer:

1. "Bau mir ..." -> nur eine Rueckfrage (kein Lauf, keine Kosten). Sie gilt ``GILT_S`` Sekunden, einmal.
2. "Ja, bauen" -> erst eine Zeile in der Auftragstabelle (status ``laeuft``, damit der Ausfuehrer am
   PC sie nicht als Agentenauftrag startet; Frist ``FRIST_S``), dann ``POST /api/auftrag`` mit dem
   Schreibschluessel aus ``CODING_DIENST_SCHLUESSEL``. Die Kennung des Dienstes (``c-...``) steht in
   ``uebergabe.coding``; die App verfolgt die Zeile ueber ``/api/agent-auftraege/{id}/stand``.
3. Der Nachfasser fragt je Runde ``/api/stand``: wartet/laeuft -> weiter warten; gescheitert oder
   abgebrochen -> Fehler mit Grund; fertig -> das Ergebnis lesen und als ``ungeprueft`` abschliessen.
   Die Tests meldet die Engine selbst; unabhaengig geprueft ist das nicht, und das sagt der Satz.

Es gibt keine Faehigkeit dafuer: ein Planer-Plan kann keinen Bau starten.

Absicherung (K8, 26.11.2026). Port 5000 ist nicht nur vom eigenen Rechner erreichbar, und das Gehirn
antwortet CORS fuer jeden Ursprung. Darum:

* Riegel: Rueckfrage und Bestaetigung nur mit lokalem Host-Kopf und ohne ``Origin`` und
  ``Sec-Fetch-Site`` (also nie aus einer Webseite), wie ``POST /api/desktop/hand-klick``.
* Jede Rueckfrage hat eine eigene ``frage_id``; nur wer sie bekommen hat, kann sie bestaetigen.
  Andere Saetze verwerfen sie nicht mehr.
* "Ja, bauen" gilt nur mit ``context.frage_id`` und dem Zeichen der App im Kopf ``X-Vibemind-Eingabe``:
  ``eingabe_zeichen(VIBEMIND_EINGABE_SCHLUESSEL, "coding-bauen <frage_id>", <unix-sekunden>)``,
  60 s gueltig, jedes Zeichen einmal. Ohne Schluessel im Gehirn startet nie ein Bau (fail-closed).
* Schluessel lassen sich als Docker-Secret uebergeben: ``<NAME>_FILE`` (Pfad) geht vor ``<NAME>``.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from .geheimnis import geheimnis

logger = logging.getLogger("brain.coding_auftrag")

AGENT = "brain-coder"            # die Engine ruft das Modell als brain-coder (Budget dort)
EREIGNIS_START = "coding.auftrag"
SPACE = "projects"
VORGABE_URL = "http://host.docker.internal:8163"
KOPF_SCHLUESSEL = "X-Coding-Schluessel"
WARTEN_S = 10.0
GILT_S = 300
FRIST_S = 45 * 60                # Zeitgrenze des Dienstes (30 min) plus Luft fuer Warten und Pruefen
SATZ_MAX = 2000
OFFEN_MAX = 20                   # hoechstens so viele offene Rueckfragen zugleich (aelteste fliegt)
BELEG_PRAEFIX = "coding-bauen"   # die App zeichnet "coding-bauen <frage_id>"
_FRAGE_ID = re.compile(r"^[0-9a-f]{32}$")
_LOKALE_HOSTS = ("localhost", "127.0.0.1", "[::1]")

_KENNUNG = re.compile(r"^c-\d{8}-[0-9a-f]{8}$")

# "Bau mir ...": Verb am Anfang, danach (innerhalb weniger Woerter) etwas, das man programmiert.
_VERB = r"^\s*(?:bitte\s+)?(?:bau|baue|programmier|programmiere|entwickle|schreib|schreibe|erstell|erstelle)\s+"
_DING = (r"(?:funktion|programm|app|anwendung|web-?app|webseite|skript|script|cli|kommandozeilen\w*|api|"
         r"rest-?api|server|bibliothek|modul|klasse|rechner|zaehler|zähler|spiel|tool|werkzeug)")
_BAU = re.compile(_VERB + r"(?:\S+\s+){0,4}?" + _DING + r"\b", re.IGNORECASE)
# Im Project Space genuegt "Bau mir ..." (der Reiter sagt, worum es geht).
_BAU_IM_SPACE = re.compile(r"^\s*(?:bitte\s+)?(?:bau|baue|programmier|programmiere|entwickle)\s+mir\b", re.IGNORECASE)
# Der Dienst baut nur JavaScript auf Node.js: andere Sprachen bleiben beim bisherigen Weg.
_ANDERE_SPRACHE = re.compile(r"\b(?:python|java(?!script)|c\+\+|c#|rust|golang|go-|kotlin|swift|php|ruby|"
                             r"typescript|\.py|\.java|\.rs|\.go)\b", re.IGNORECASE)

# Saetze anderer Spaces ("Erstelle eine Bubble zur App-Idee", "Schreib eine Mail zum Programm").
# Gilt nicht im Project Space selbst (dort ist "Bau mir eine App fuer Ideen" ein Bauauftrag).
_ANDERER_SPACE = re.compile(r"\b(?:idee\w*|bubble\w*|karte\w*|white\s*-?paper|dokument\w*|text\w*|e?-?mail\w*|"
                            r"brief\w*|post\w*|beitrag\w*|angebot\w*|rechnung\w*)\b", re.IGNORECASE)

# Pruefung 25.11.2026: kopiert als  Ja, erledigen“.  ging die feste Form nicht und lief 16 s ins
# Modell. Bestaetigungen gelten auch mit umgebenden Anfuehrungszeichen und Satzzeichen.
_RAND_VORN = r"""^[\s"'„“”‚‘’«»]*"""
_RAND_HINTEN = r"""[\s.!?…"'„“”‚‘’«»]*$"""
_JA = re.compile(_RAND_VORN + r"ja\s*[,!.]?\s*(?:bitte\s+)?(?:bau(?:e|en)?(?:\s+es)?|starte?\s+den\s+bau)"
                 + _RAND_HINTEN, re.IGNORECASE)

OHNE_FRAGE = ("Es ist gerade kein Bauauftrag zur Bestätigung offen. Sag zum Beispiel „Bau mir eine Funktion …“, "
              "dann frage ich zuerst nach.")
NICHT_ERREICHBAR = "Der Dienst des Project Space antwortet nicht; gestartet habe ich nichts."
NIMMT_NICHTS = "Der Project Space nimmt gerade keine Bauaufträge an; gestartet habe ich nichts."
OHNE_SCHLUESSEL = "Mir fehlt der Schreibschlüssel für den Project Space; gestartet habe ich nichts."
TABELLE_FEHLT = "Die Auftragstabelle ist nicht erreichbar; ohne sie starte ich keinen Bau."
NICHT_GESTARTET = "Der Project Space hat den Bau nicht angenommen; geändert habe ich nichts."
BESCHAEFTIGT = "Der Project Space baut gerade etwas anderes. Versuch es, wenn der Lauf fertig ist."
NUR_AUS_DER_APP = ("Bauaufträge nehme ich nur aus der VibeMind-App auf diesem Rechner an; "
                   "gestartet habe ich nichts.")
NICHT_BELEGT = ("Die Bestätigung kam ohne gültiges Zeichen der App; gestartet habe ich nichts. "
                "Bestätige den Bau bitte in der VibeMind-App.")

_sperre = threading.Lock()
_offen: Dict[str, Dict[str, Any]] = {}       # frage_id -> {"satz", "bis"}
_verbraucht: Dict[str, float] = {}           # Zeichen -> Zeit der Verwendung (jedes Zeichen einmal)


class CodingFehler(Exception):
    """Deutscher Satz fuer den Menschen (nie Rohtext)."""


def _jetzt() -> float:
    return time.time()


# ── Dienst ─────────────────────────────────────────────────────────────────

def _basis() -> str:
    return (os.environ.get("CODING_DIENST_URL") or VORGABE_URL).rstrip("/")


def _anfrage(methode: str, pfad: str, koerper: Optional[Dict[str, Any]] = None,
             kopf: Optional[Dict[str, str]] = None) -> Tuple[int, Optional[Dict[str, Any]]]:
    """(HTTP-Status, JSON) am Dienst; (0, None) ohne Verbindung. Wirft nie."""
    daten = json.dumps(koerper).encode("utf-8") if koerper is not None else None
    frage = urllib.request.Request(_basis() + pfad, data=daten, method=methode,
                                   headers={"Content-Type": "application/json", **(kopf or {})})
    try:
        with urllib.request.urlopen(frage, timeout=WARTEN_S) as antwort:
            return antwort.status, json.loads(antwort.read().decode("utf-8"))
    except urllib.error.HTTPError as fehler:
        try:
            return fehler.code, json.loads(fehler.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return fehler.code, None
    except Exception:  # noqa: BLE001 - Verbindung, Zeit, kein JSON
        return 0, None


def gesundheit() -> Tuple[int, Optional[Dict[str, Any]]]:
    return _anfrage("GET", "/health")


def auftrag_senden(satz: str) -> Tuple[int, Optional[Dict[str, Any]]]:
    schluessel = geheimnis("CODING_DIENST_SCHLUESSEL")
    if not schluessel:
        raise CodingFehler(OHNE_SCHLUESSEL)
    return _anfrage("POST", "/api/auftrag", {"aufgabe": satz, "ohne_npm": True}, {KOPF_SCHLUESSEL: schluessel})


def stand_abfragen(kennung: str) -> Tuple[int, Optional[Dict[str, Any]]]:
    return _anfrage("GET", "/api/stand?" + urllib.parse.urlencode({"auftrag": kennung}))


def ergebnis_abfragen(kennung: str) -> Tuple[int, Optional[Dict[str, Any]]]:
    return _anfrage("GET", "/api/ergebnis?" + urllib.parse.urlencode({"auftrag": kennung}))


# ── 1. Erkennen und Rueckfrage ──────────────────────────────────────────────

def ist_bauauftrag(satz: Any, space: Optional[str] = None) -> bool:
    """Ein Satz, der etwas gebaut haben will, das der Project Space bauen kann."""
    if not isinstance(satz, str) or not satz.strip() or len(satz) > SATZ_MAX:
        return False
    if _ANDERE_SPRACHE.search(satz) or (space != SPACE and _ANDERER_SPACE.search(satz)):
        return False
    if _BAU.search(satz):
        return True
    return space == SPACE and bool(_BAU_IM_SPACE.search(satz))


def _kurz(satz: str, grenze: int = 160) -> str:
    flach = " ".join(satz.split())
    return flach if len(flach) <= grenze else flach[: grenze - 1].rstrip() + "…"


def riegel_offen(kopf: Mapping[str, Any], umgebung: Optional[Mapping[str, str]] = None) -> bool:
    """Nur vom eigenen Rechner und nie aus einer Webseite (wie ``/api/desktop/hand-klick``).

    Host-Kopf localhost/127.0.0.1/[::1] (weitere Namen: ``DECISION_GRAPH_HOSTS``) und weder
    ``Origin`` noch ``Sec-Fetch-Site``: Browser schicken bei fetch immer ``Sec-Fetch-Site`` und bei
    POST immer ``Origin``. Allein kein Schutz gegen Programme im Netz; der traegt erst das Zeichen.
    """
    def wert(name: str) -> str:
        roh = kopf.get(name)
        if roh is None:
            roh = kopf.get(name.lower())
        return str(roh or "").strip().lower()

    host = wert("Host")
    name = host.split("]", 1)[0] + "]" if host.startswith("[") else (host.rsplit(":", 1)[0] if ":" in host else host)
    u = os.environ if umgebung is None else umgebung
    weitere = tuple(h.strip().lower() for h in (u.get("DECISION_GRAPH_HOSTS") or "").split(",") if h.strip())
    if not name or name not in _LOKALE_HOSTS + weitere:
        return False
    return not wert("Origin") and not wert("Sec-Fetch-Site")


def _aufraeumen(jetzt: float) -> None:
    """Verfallene Rueckfragen und alte Zeichen vergessen (unter ``_sperre`` aufrufen)."""
    for fid in [f for f, o in _offen.items() if float(o.get("bis", 0)) < jetzt]:
        del _offen[fid]
    for z in [z for z, t in _verbraucht.items() if jetzt - t > 4 * 60]:
        del _verbraucht[z]


def rueckfrage(satz: str, *,
               gesund: Optional[Callable[[], Tuple[int, Optional[Dict[str, Any]]]]] = None) -> Dict[str, Any]:
    """Rueckfrage formulieren (kein Lauf). ``{"satz": ..., "frage_id": ...}``; ``frage_id`` nur,
    wenn wirklich eine Rueckfrage offen ist (sonst ``None`` und der Satz sagt, warum nicht)."""
    status, daten = (gesund or gesundheit)()
    if status != 200 or not isinstance(daten, dict) or daten.get("service") != "coding-dienst":
        return {"satz": NICHT_ERREICHBAR, "frage_id": None}
    if daten.get("schreiben") is not True:
        return {"satz": NIMMT_NICHTS, "frage_id": None}
    if not geheimnis("CODING_DIENST_SCHLUESSEL"):
        return {"satz": OHNE_SCHLUESSEL, "frage_id": None}
    frage_id = secrets.token_hex(16)
    jetzt = _jetzt()
    with _sperre:
        _aufraeumen(jetzt)
        while len(_offen) >= OFFEN_MAX:
            del _offen[min(_offen, key=lambda f: float(_offen[f].get("bis", 0)))]
        _offen[frage_id] = {"satz": satz.strip(), "bis": jetzt + GILT_S}
    text = (f"Soll der Project Space das bauen: „{_kurz(satz)}“? Er baut JavaScript auf Node.js mit Tests, "
            "die Coding-Engine ruft dafür ein Modell auf (begrenzte Zahl je Auftrag), und es dauert einige "
            "Minuten bis eine halbe Stunde. Sag „Ja, bauen“, dann starte ich.")
    return {"satz": text, "frage_id": frage_id}


def vorschau(satz: str, *, gesund: Optional[Callable[[], Tuple[int, Optional[Dict[str, Any]]]]] = None) -> str:
    """Nur der Satz der Rueckfrage (siehe ``rueckfrage``)."""
    return rueckfrage(satz, gesund=gesund)["satz"]


def verwerfen() -> None:
    with _sperre:
        _offen.clear()
        _verbraucht.clear()


def etwas_offen(jetzt: Optional[float] = None) -> bool:
    """Wartet eine Rueckfrage auf "Ja, bauen"? Nur lesen."""
    grenze = _jetzt() if jetzt is None else jetzt
    with _sperre:
        return any(float(o.get("bis", 0)) >= grenze for o in _offen.values())


def ist_bestaetigung(satz: Any) -> bool:
    return isinstance(satz, str) and bool(_JA.match(satz))


def beleg_satz(frage_id: str) -> str:
    """Was die App fuer "Ja, bauen" zeichnet (``desktop_tagesrest.eingabe_zeichen``)."""
    return f"{BELEG_PRAEFIX} {frage_id}"


def _zeichen_gueltig(frage_id: str, zeichen: Any, jetzt: float, umgebung: Optional[Mapping[str, str]]) -> bool:
    """Zeichen ueber "coding-bauen <frage_id>" mit dem gemeinsamen Schluessel, im Zeitfenster.

    Verbraucht das Zeichen nicht (das macht ``satz_pruefen`` erst, wenn alles passt)."""
    from .desktop_tagesrest import (
        SCHLUESSEL_MINDESTLAENGE, SCHLUESSEL_NAME, ZEICHEN_FENSTER_S, eingabe_zeichen,
    )

    schluessel = geheimnis(SCHLUESSEL_NAME, umgebung)
    if len(schluessel) < SCHLUESSEL_MINDESTLAENGE or not isinstance(zeichen, str):
        return False
    zeit_text, _, _ = zeichen.partition(".")
    if not zeit_text.isdigit() or len(zeit_text) > 12:
        return False
    if abs(jetzt - int(zeit_text)) > ZEICHEN_FENSTER_S:
        return False
    return hmac.compare_digest(zeichen, eingabe_zeichen(schluessel, beleg_satz(frage_id), int(zeit_text)))


def satz_pruefen(satz: Any, *, frage_id: Any = None, zeichen: Any = None, riegel: bool = False,
                 umgebung: Optional[Mapping[str, str]] = None) -> Optional[Dict[str, Any]]:
    """Fuer jeden Satz vor allem anderen aufrufen.

    ``None``: keine Bestaetigung (eine offene Rueckfrage bleibt, sie gehoert dem, der sie bekam).
    ``{"satz": ...}``: "Ja, bauen", das nichts startet (keine gueltige Rueckfrage, nicht vom eigenen
    Rechner oder ohne gueltiges Zeichen; dann zusaetzlich ``"grund"``). ``{"offen": {...}}``:
    bestaetigt, genau einmal. ``riegel``: Ergebnis von ``riegel_offen`` fuer diese Anfrage.
    """
    if not ist_bestaetigung(satz):
        return None
    if not riegel:
        return {"satz": NUR_AUS_DER_APP, "grund": "nur_aus_der_app"}
    jetzt = _jetzt()
    fid = frage_id if isinstance(frage_id, str) and _FRAGE_ID.match(frage_id) else None
    with _sperre:
        _aufraeumen(jetzt)
        offen = _offen.get(fid) if fid else None
        if offen is None:
            return {"satz": OHNE_FRAGE}
        if not _zeichen_gueltig(fid, zeichen, jetzt, umgebung):
            return {"satz": NICHT_BELEGT, "grund": "nicht_belegt"}
        if zeichen in _verbraucht:
            return {"satz": NICHT_BELEGT, "grund": "nicht_belegt"}
        _verbraucht[zeichen] = jetzt
        del _offen[fid]
    return {"offen": dict(offen), "frage_id": fid}


# ── 2. Start ───────────────────────────────────────────────────────────────

def starten(offen: Mapping[str, Any], *, tabelle: Any, trace_id: str,
            senden: Optional[Callable[[str], Tuple[int, Optional[Dict[str, Any]]]]] = None) -> Dict[str, Any]:
    """Zeile anlegen, dann den Bau beim Dienst starten. Wirft ``CodingFehler``."""
    satz = str(offen["satz"])
    plan_id = f"coding_{int(_jetzt() * 1000)}"
    uebergabe = {"coding": {"satz": satz}}
    try:
        auftrag_id = tabelle.anlegen(
            capability=EREIGNIS_START, agent=AGENT, auftrag=f"Project Space baut: {_kurz(satz, 300)}",
            trace_id=trace_id, plan_id=plan_id, hop_id="s1", uebergabe=uebergabe, antwortkanal={"art": "api"},
            frist_s=FRIST_S, status="laeuft")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[coding] Zeile nicht angelegt: {type(e).__name__}")
        raise CodingFehler(TABELLE_FEHLT) from e
    try:
        status, antwort = (senden or auftrag_senden)(satz)
    except CodingFehler as e:
        _schliessen(tabelle, auftrag_id, "fehler", False, str(e), "ohne schluessel")
        raise
    roh = antwort.get("auftrag") if isinstance(antwort, dict) else None
    kennung = roh if isinstance(roh, str) and _KENNUNG.match(roh) else None
    if status != 200 or kennung is None or not antwort.get("angestossen"):
        # Angenommen, aber nicht angestossen: der Dienst baut gerade einen anderen Auftrag.
        satz_fehler = BESCHAEFTIGT if status == 200 and kennung is not None else NICHT_GESTARTET
        logger.warning(f"[coding] Start abgelehnt: HTTP {status}")
        _schliessen(tabelle, auftrag_id, "fehler", False, satz_fehler, f"start: HTTP {status}",
                    coding_auftrag=kennung)
        raise CodingFehler(satz_fehler)
    try:
        tabelle.uebergabe_setzen(auftrag_id, {"coding": {"satz": satz, "auftrag": kennung}})
    except Exception as e:  # noqa: BLE001 - der Bau laeuft; ohne Kennung schliesst der Nachfasser ehrlich
        logger.warning(f"[coding] Kennung nicht gespeichert: {type(e).__name__}")
    hoechstens = antwort.get("modellaufrufe_hoechstens")
    grenze = f" Höchstens {hoechstens} Modellaufrufe." if isinstance(hoechstens, int) and hoechstens > 0 else ""
    return {
        "ok": True, "pending": True, "status": "angenommen", "fertig": False, "space": SPACE, "executed": {},
        "auftraege": [{"auftrag_id": auftrag_id, "agent": AGENT, "capability": EREIGNIS_START}],
        "final_text": (f"Bau gestartet, Nr. {str(auftrag_id)[:8]}. Den Fortschritt zeigt der Project Space; "
                       f"ich melde mich, wenn er fertig ist.{grenze}"),
    }


def _schliessen(tabelle: Any, auftrag_id: str, status: str, verified: Optional[bool], meldung: str,
                grund: str, **mehr: Any) -> bool:
    """Drei Staende wie im Dienst: ``fertig`` nur mit gruener Abnahme (verified True, Rueckweg
    ``abgenommen``), gebaut ohne Pruefer der Aufgabe ``ungeprueft``, sonst ``fehler``."""
    if status == "fertig":
        rueckweg = "abgenommen" if verified is True else "ungeprueft"
    else:
        rueckweg = "fehler"
    pruefung = {"verified": verified, "reason": grund, "kind": "coding",
                "rueckweg": rueckweg, "meldung": meldung,
                "fortsetzung": "fertig" if status == "fertig" else "fehler", **mehr}
    if status != "fertig":
        pruefung["fortsetzung_fehler"] = meldung
    try:
        return bool(tabelle.recherche_abschliessen(auftrag_id, status, pruefung))
    except Exception:  # noqa: BLE001
        logger.exception("Coding-Zeile %s nicht abgeschlossen", auftrag_id)
        return False


# ── 3. Nachfassen ──────────────────────────────────────────────────────────

def _fertig_satz(sicht: Mapping[str, Any], ergebnis: Optional[Mapping[str, Any]]) -> str:
    """Satz zu einem gebauten Lauf; ``fertig`` (Abnahme gruen) oder ``ungeprueft`` (ohne Pruefer der Aufgabe)."""
    titel = str(sicht.get("titel") or "ohne Titel").strip()[:120]
    abnahme = sicht.get("abnahme") if isinstance(sicht.get("abnahme"), Mapping) else {}
    if sicht.get("zustand") == "fertig":
        kopf = f"„{titel}“ ist fertig: die unabhängige Abnahme ist bestanden (Tests und Prüfer der Aufgabe grün)."
    else:
        warum = "; ".join(str(g) for g in abnahme.get("gruende") or []) or "es gibt keinen Prüfer für diese Aufgabe"
        kopf = (f"„{titel}“ ist gebaut, aber ungeprüft: {warum[:300]}. "
                "Allgemein geprüft ist nur, dass die Tests laufen und grün sind.")
    if not isinstance(ergebnis, Mapping):
        return kopf
    dateien = ergebnis.get("dateien") if isinstance(ergebnis.get("dateien"), list) else []
    start = ergebnis.get("start")
    start_satz = f" Start mit „{start}“ im Ordner der App." if isinstance(start, str) and start else ""
    return f"{kopf} {len(dateien)} Datei(en).{start_satz}"


def nachfassen(zeile: Mapping[str, Any], *,
               stand: Optional[Callable[[str], Tuple[int, Optional[Dict[str, Any]]]]] = None,
               ergebnis: Optional[Callable[[str], Tuple[int, Optional[Dict[str, Any]]]]] = None) -> Optional[tuple]:
    """Eine laufende Coding-Zeile: ``None`` (laeuft weiter) oder ``(status, verified, meldung, grund, mehr)``.

    Wirft bei Transportfehlern (dann bleibt die Zeile, die naechste Runde fragt erneut).
    """
    uebergabe = zeile.get("uebergabe") if isinstance(zeile.get("uebergabe"), dict) else {}
    daten = uebergabe.get("coding") if isinstance(uebergabe.get("coding"), dict) else {}
    kennung = daten.get("auftrag")
    if not isinstance(kennung, str) or not _KENNUNG.match(kennung):
        return ("fehler", None, "Der Bau wurde gestartet, aber sein Stand lässt sich nicht verfolgen. "
                                "Im Project Space steht, wie weit er ist.", "ohne kennung", {})
    status, sicht = (stand or stand_abfragen)(kennung)
    if status == 0 or (status >= 500) or (status == 200 and not isinstance(sicht, dict)):
        raise RuntimeError("Project Space nicht erreichbar")
    if status == 404:
        return ("fehler", False, "Der Project Space kennt diesen Bau nicht mehr.", "unbekannt",
                {"coding_auftrag": kennung})
    if status != 200:
        raise RuntimeError(f"Project Space: HTTP {status}")
    zustand = sicht.get("zustand")
    if zustand in ("wartet", "laeuft"):
        return None
    if zustand == "abgebrochen":
        return ("fehler", False, "Der Bau wurde abgebrochen.", "abgebrochen", {"coding_auftrag": kennung})
    if zustand == "fehlgeschlagen":
        fehler = str(sicht.get("fehler") or "").strip()
        satz = f"Der Bau ist gescheitert: {fehler[:300]}" if fehler else "Der Bau ist gescheitert."
        return ("fehler", False, satz, "fehlgeschlagen", {"coding_auftrag": kennung})
    if zustand not in ("fertig", "ungeprueft"):
        raise RuntimeError("Project Space: Zustand unbekannt")
    # Die Selbstauskunft der Engine zaehlt nicht: "fertig" setzt im Dienst nur die gruene Abnahme.
    e_status, e_daten = (ergebnis or ergebnis_abfragen)(kennung)
    satz = _fertig_satz(sicht, e_daten if e_status == 200 else None)
    if zustand == "fertig":
        return ("fertig", True, satz, "abnahme", {"coding_auftrag": kennung})
    return ("fertig", None, satz, "ungeprueft", {"coding_auftrag": kennung})


def runde(tabelle: Any, *, stand: Optional[Callable[[str], Tuple[int, Optional[Dict[str, Any]]]]] = None,
          ergebnis: Optional[Callable[[str], Tuple[int, Optional[Dict[str, Any]]]]] = None) -> Dict[str, int]:
    """Alle laufenden Bauauftraege einmal nachfassen (Nachfasser-Takt). Wirft nie."""
    z = {"coding_fertig": 0, "coding_fehler": 0, "coding_wartet": 0, "fehler": 0}
    try:
        zeilen = tabelle.coding_laufend()
    except Exception:  # noqa: BLE001
        logger.exception("coding_laufend fehlgeschlagen")
        z["fehler"] += 1
        return z
    for zeile in zeilen if isinstance(zeilen, list) else []:
        if not isinstance(zeile, Mapping):
            continue
        try:
            aus = nachfassen(zeile, stand=stand, ergebnis=ergebnis)
        except Exception as e:  # noqa: BLE001 - naechste Runde fragt erneut
            logger.warning(f"[coding] Stand nicht lesbar: {type(e).__name__}")
            z["fehler"] += 1
            continue
        if aus is None:
            z["coding_wartet"] += 1
            continue
        status, verified, meldung, grund, mehr = aus
        if _schliessen(tabelle, str(zeile.get("id")), status, verified, meldung, grund, **mehr):
            z["coding_fertig" if status == "fertig" else "coding_fehler"] += 1
    return z
