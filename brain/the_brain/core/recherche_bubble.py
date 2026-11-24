"""Recherche zu einer Bubble: Vorschau, Rueckfrage, Start, Nachfassen (Koordination 23.11.2026).

Befund: Der Pruefweg ``research:*`` (spaces/research/execution_target.py) lief im Container des
Gehirns und diktierte dem Agenten einen Container-Pfad; der Agent laeuft auf dem Rechner. Schreiber
und Pruefer sahen nie dieselbe Platte ("report file was never written").

Dieser Weg nutzt den Recherche-Server ``spaces-research`` (spaces/research/mcp_server.py). Er
laeuft auf dem Rechner, schreibt und prueft den Bericht auf seiner eigenen Platte und legt die
Recherche-Karte in der Bubble selbst an. Das Gehirn ruft ihn nur ueber feste Aufrufe
(``McpExecutor``, kein Modell):

1. "Recherchiere zu ..." -> ``research_start`` OHNE ``confirm``: nur die Vorschau (kein Lauf, keine
   Kosten). Die Antwort ist eine Rueckfrage; die Vorschau gilt ``GILT_S`` Sekunden, einmal.
2. "Ja, recherchieren" -> erst eine Zeile in der Auftragstabelle (status ``laeuft``, damit der
   Ausfuehrer am PC sie nicht als Agentenauftrag startet; Frist ``FRIST_S``), dann
   ``research_start`` mit ``confirm`` und dem Brief der Vorschau. Antwort: angenommen.
3. Der Nachfasser fragt je Runde ``research_status``: ``pending`` -> weiter warten; ``failed`` ->
   Fehler mit Grund; fertig -> die Karte am Zustand pruefen (Typ ``research`` in genau dieser
   Bubble), dann ``fertig``. Nach der Frist markiert die Tabelle die Zeile als abgelaufen.

Jeder andere Satz verwirft die offene Vorschau. Der Plan-Ausfuehrer startet diese Ereignisse nie
(``NUR_GEHIRN``): ein Planer-Plan kann keinen Lauf ausloesen.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from typing import Any, Callable, Dict, Mapping, Optional

logger = logging.getLogger("brain.recherche_bubble")

# Rechtetraeger ohne Modell-Schleife (Registry-Block "recherche"). Nie brain-researcher: der hat
# ein Modell, und mit research_start in seinen Werkzeugen koennte es die Rueckfrage umgehen.
AGENT = "brain-recherche"
SERVER = "spaces-research"
EREIGNIS_START = "recherche.start"    # Werkzeug research_start (Vorschau und Start)
EREIGNIS_STATUS = "recherche.status"  # Werkzeug research_status
NUR_GEHIRN = frozenset({EREIGNIS_START, EREIGNIS_STATUS})


def _name_wie_openfang(wert: str) -> str:
    # Wie McpExecutor._tool_namespace_component: OpenFang sieht "Spaces_Research" und
    # "spaces-research" als denselben Server.
    return wert.strip().lower().replace("-", "_")


def ist_recherche_ziel(ziel: Any) -> bool:
    """True fuer jedes Ziel, das beim Recherche-Server landen kann, egal in welcher Schreibweise.

    OpenFang sucht das Werkzeug unter ``mcp_<server>_<tool>``; dabei verschwimmt die Grenze
    zwischen Server und Werkzeug ("spaces" + "research_research_start" ergibt denselben Namen).
    Darum absichtlich streng: das ganze Ziel ohne Leerraum, Doppelpunkt wie Unterstrich, und
    der Servername darf nirgends darin vorkommen.
    """
    if not isinstance(ziel, str):
        return False
    flach = _name_wie_openfang(re.sub(r"\s+", "", ziel)).replace(":", "_")
    return _name_wie_openfang(SERVER) in flach

GILT_S = 120
FRIST_S = 90 * 60          # wie die Zeitgrenze des Kindprozesses im Recherche-Server (5400 s)
TIEFE = "thorough"
SPRACHE = "german"

_JA = re.compile(r"^\s*ja\s*[,!.]?\s*(?:bitte\s+)?(?:recherchier(?:e|en)?|starte?\s+die\s+recherche)"
                 r"(?:\s+es)?\s*[.!]?\s*$", re.IGNORECASE)
_JOB = re.compile(r"^job_v1_[0-9A-HJKMNPQRSTVWXYZ]{26}$")

OHNE_FRAGE = ("Es ist gerade keine Recherche zur Bestätigung offen. Sag „Recherchiere zu dieser Bubble“, "
              "dann zeige ich zuerst, was ich suchen würde.")
NICHT_ERREICHBAR = "Der Recherche-Dienst ist gerade nicht erreichbar; gestartet habe ich nichts."
OHNE_GRENZE = "Der Recherche-Dienst nennt keine Kostengrenze; gestartet habe ich nichts."
TABELLE_FEHLT ="Die Auftragstabelle ist nicht erreichbar; ohne sie starte ich keine Recherche."

_sperre = threading.Lock()
_offen: Dict[str, Any] = {}


class RechercheFehler(Exception):
    """Deutscher Satz fuer den Menschen (nie Rohtext)."""


def _jetzt() -> float:
    return time.time()


# ── Aufrufe am Recherche-Server ─────────────────────────────────────────────

def _laufzeit_kontext(werkzeug: str, plan_id: str, schritt: str, korrelation: str) -> Any:
    from .capability_targets import find_registered_mcp_authority
    from .openfang_runtime_authority import RuntimeInvocationContext, runtime_invocation_id

    autoritaet = find_registered_mcp_authority(AGENT, SERVER, werkzeug)
    if autoritaet is None or not autoritaet.space_id:
        raise RuntimeError(f"nicht_gebunden: {werkzeug} ist im Verzeichnis nicht gebunden")
    return RuntimeInvocationContext(
        correlation_id=korrelation or plan_id, plan_id=plan_id, plan_revision=1, step_id=schritt,
        space_id=autoritaet.space_id, agent_name=autoritaet.agent,
        invocation_id=runtime_invocation_id(plan_id, 1, schritt),
    )


def _inhalt(ergebnis: Any) -> Dict[str, Any]:
    """Die JSON-Antwort des Werkzeugs aus ``{"content": [{"type": "text", "text": ...}]}``."""
    if isinstance(ergebnis, dict) and "status" in ergebnis:
        return ergebnis
    if isinstance(ergebnis, dict):
        for teil in ergebnis.get("content") or []:
            if isinstance(teil, dict) and teil.get("type") == "text":
                try:
                    wert = json.loads(teil.get("text") or "")
                except ValueError:
                    continue
                if isinstance(wert, dict):
                    return wert
    raise RuntimeError("Recherche-Werkzeug: keine lesbare Antwort")


def aufruf(werkzeug: str, argumente: Dict[str, Any], *, plan_id: str, schritt: str,
           korrelation: str = "") -> Dict[str, Any]:
    """Ein fester Aufruf am Recherche-Server ueber OpenFang (wie der Plan-Ausfuehrer fuer mcp:)."""
    from .capability_targets import McpExecutor

    kontext = _laufzeit_kontext(werkzeug, plan_id, schritt, korrelation)
    antwort = McpExecutor(f"mcp:{AGENT}:{SERVER}:{werkzeug}").call_with_arg(
        dict(argumente), extra_params={"_runtime_authority": kontext})
    if antwort.get("pending") is True:
        raise RuntimeError("freigabe_ausstehend")
    if not antwort.get("ok"):
        raise RuntimeError(str(antwort.get("error") or "Recherche-Werkzeug gescheitert")[:300])
    return _inhalt(antwort.get("result"))


# ── 1. Vorschau ────────────────────────────────────────────────────────────

def _titel(argumente: Mapping[str, Any]) -> str:
    return str(argumente.get("bubble_titel") or "").strip() or "ohne Titel"


def vorschau(argumente: Mapping[str, Any], *, rufe: Callable[..., Dict[str, Any]] = aufruf) -> str:
    """Vorschau holen (kein Lauf) und als Rueckfrage formulieren; merkt sie fuer "Ja, recherchieren"."""
    verwerfen()
    plan_id = f"recherche_{int(_jetzt() * 1000)}"
    # Einmaliges Zeichen: der Server merkt sich nur seinen Hash zur Vorschau; starten kann nur,
    # wer es vorlegt, genau einmal und nur fuer diese Bubble (spaces/research, start_token).
    zeichen = secrets.token_urlsafe(32)
    try:
        antwort = rufe("research_start", {"bubble_id": argumente["bubble_id"], "brief": argumente["thema"],
                                          "depth": TIEFE, "language": SPRACHE, "start_token": zeichen},
                       plan_id=plan_id, schritt="vorschau")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[recherche] Vorschau nicht moeglich: {type(e).__name__}")
        return NICHT_ERREICHBAR
    brief = antwort.get("brief")
    if antwort.get("status") != "preview" or not isinstance(brief, str) or not brief.strip():
        return NICHT_ERREICHBAR
    grenze = _grenzen(antwort.get("grenzen"))
    if grenze is None:
        # Ohne genannte Kostengrenze keine Rueckfrage, die zum Start fuehren koennte (24.11.2026).
        return OHNE_GRENZE
    seiten, laeufe = grenze
    with _sperre:
        _offen.update({"argumente": dict(argumente), "brief": brief, "zeichen": zeichen, "bis": _jetzt() + GILT_S})
    return (f"Ich würde zu „{_titel(argumente)}“ recherchieren: {argumente['thema']}. Gründlich, mit Quellen, "
            "auf Deutsch; die Notizen der Bubble gehen als Hintergrund mit. Das kostet höchstens "
            f"{laeufe} Modell-Läufe über das Claude-Abo und höchstens {seiten} geholte Seiten und dauert "
            "einige Minuten. Sag „Ja, recherchieren“, dann starte ich.")


def _grenzen(roh: Any) -> Optional[tuple]:
    """(Seiten, Modell-Laeufe) aus der Vorschau des Servers; ``None``, wenn nicht sauber genannt."""
    if not isinstance(roh, Mapping):
        return None
    seiten, laeufe = roh.get("seiten"), roh.get("modell_laeufe")
    for wert in (seiten, laeufe):
        if isinstance(wert, bool) or not isinstance(wert, int) or wert < 1:
            return None
    if roh.get("budget_waechter") is not True:
        return None
    return seiten, laeufe


def verwerfen() -> None:
    with _sperre:
        _offen.clear()


def ist_bestaetigung(satz: Any) -> bool:
    return isinstance(satz, str) and bool(_JA.match(satz))


def satz_pruefen(satz: Any) -> Optional[Dict[str, Any]]:
    """Fuer jeden Satz vor allem anderen aufrufen.

    ``None``: keine Bestaetigung; eine offene Vorschau verfaellt. ``{"satz": ...}``: "Ja,
    recherchieren" ohne gueltige Vorschau. ``{"offen": {...}}``: bestaetigt, genau einmal.
    """
    jetzt = _jetzt()
    with _sperre:
        offen = dict(_offen)
        _offen.clear()
    if not ist_bestaetigung(satz):
        return None
    if not offen or offen.get("bis", 0) < jetzt:
        return {"satz": OHNE_FRAGE}
    return {"offen": offen}


# ── 2. Start ───────────────────────────────────────────────────────────────

def starten(offen: Mapping[str, Any], *, tabelle: Any, trace_id: str,
            rufe: Callable[..., Dict[str, Any]] = aufruf) -> Dict[str, Any]:
    """Zeile anlegen, dann den Lauf starten. Rueckgabe: Felder der Antwort. Wirft ``RechercheFehler``."""
    argumente = dict(offen["argumente"])
    plan_id = f"recherche_{int(_jetzt() * 1000)}"
    uebergabe = {"recherche": {"bubble_id": argumente["bubble_id"], "bubble_titel": argumente.get("bubble_titel"),
                               "thema": argumente["thema"]}}
    try:
        auftrag_id = tabelle.anlegen(
            capability=EREIGNIS_START, agent=AGENT, auftrag=f"Recherche zu „{_titel(argumente)}“: {argumente['thema']}",
            trace_id=trace_id, plan_id=plan_id, hop_id="s1", uebergabe=uebergabe, antwortkanal={"art": "api"},
            frist_s=FRIST_S, status="laeuft")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[recherche] Zeile nicht angelegt: {type(e).__name__}")
        raise RechercheFehler(TABELLE_FEHLT) from e
    try:
        antwort = rufe("research_start", {"bubble_id": argumente["bubble_id"], "brief": argumente["thema"],
                                          "depth": TIEFE, "language": SPRACHE, "confirm": True,
                                          "final_brief": offen["brief"], "start_token": offen["zeichen"]},
                       plan_id=plan_id, schritt="start", korrelation=trace_id)
        job_id = antwort.get("job_id")
        if antwort.get("status") != "started" or not isinstance(job_id, str) or not _JOB.match(job_id):
            raise RuntimeError("Recherche-Werkzeug: kein Start bestaetigt")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[recherche] Start gescheitert: {type(e).__name__}")
        _schliessen(tabelle, auftrag_id, "fehler", False, "Die Recherche ließ sich nicht starten; geändert habe "
                                                          "ich nichts.", "start: " + type(e).__name__)
        raise RechercheFehler("Die Recherche ließ sich nicht starten; geändert habe ich nichts.") from e
    try:
        tabelle.uebergabe_setzen(auftrag_id, {"recherche": {**uebergabe["recherche"], "job_id": job_id}})
    except Exception as e:  # noqa: BLE001 - der Lauf laeuft; ohne Kennung schliesst der Nachfasser ehrlich
        logger.warning(f"[recherche] Kennung nicht gespeichert: {type(e).__name__}")
    return {
        "ok": True, "pending": True, "status": "angenommen", "fertig": False, "space": "ideas", "executed": {},
        "auftraege": [{"auftrag_id": auftrag_id, "agent": AGENT, "capability": EREIGNIS_START}],
        "final_text": (f"Recherche gestartet, Nr. {str(auftrag_id)[:8]}. Das dauert einige Minuten; die Karte "
                       f"„Research: …“ erscheint in „{_titel(argumente)}“, sobald der Bericht mit Quellen da ist."),
    }


def _schliessen(tabelle: Any, auftrag_id: str, status: str, verified: Optional[bool], meldung: str,
                grund: str, **mehr: Any) -> bool:
    pruefung = {"verified": verified, "reason": grund, "kind": "recherche",
                "rueckweg": "geschrieben" if status == "fertig" else "fehler", "meldung": meldung,
                "fortsetzung": "fertig" if status == "fertig" else "fehler", **mehr}
    if status != "fertig":
        pruefung["fortsetzung_fehler"] = meldung
    try:
        return bool(tabelle.recherche_abschliessen(auftrag_id, status, pruefung))
    except Exception:  # noqa: BLE001
        logger.exception("Recherche-Zeile %s nicht abgeschlossen", auftrag_id)
        return False


# ── 3. Nachfassen ──────────────────────────────────────────────────────────

_GRUENDE = (
    (re.compile(r"agent call failed", re.I), "Der Recherche-Agent hat keinen Bericht geschrieben"),
    (re.compile(r"no citation", re.I), "Der Bericht nennt keine Quelle, darum lege ich keine Karte an"),
)


def _grund(fehler: Any) -> str:
    text = str(fehler or "")
    for muster, satz in _GRUENDE:
        if muster.search(text):
            return satz
    return "Die Recherche ist gescheitert"


def nachfassen(zeile: Mapping[str, Any], *, status_abfragen: Callable[[str], Dict[str, Any]],
               karte_lesen: Callable[[str], Optional[Mapping[str, Any]]]) -> Optional[tuple]:
    """Eine laufende Recherche-Zeile: ``None`` (laeuft weiter) oder ``(status, verified, meldung, grund, mehr)``.

    Wirft bei Transportfehlern (dann bleibt die Zeile, die naechste Runde fragt erneut).
    """
    daten = ((zeile.get("uebergabe") or {}).get("recherche") or {}) if isinstance(zeile.get("uebergabe"), dict) else {}
    titel = str(daten.get("bubble_titel") or "").strip() or "ohne Titel"
    job_id = daten.get("job_id")
    if not isinstance(job_id, str) or not _JOB.match(job_id):
        return ("fehler", None, "Die Recherche wurde gestartet, aber ihr Stand lässt sich nicht verfolgen; "
                                "eine Karte lege ich nicht an.", "ohne job_id", {})
    antwort = status_abfragen(job_id)
    status = antwort.get("status")
    if status == "pending":
        return None
    if status == "failed":
        return ("fehler", False, f"{_grund(antwort.get('error'))}. Eine Karte habe ich nicht angelegt.",
                "failed", {})
    if status != "done":
        raise RuntimeError("Recherche-Status unbekannt")
    node_id = antwort.get("node_id")
    karte = karte_lesen(node_id) if isinstance(node_id, str) and node_id else None
    if (not isinstance(karte, Mapping) or karte.get("node_type") != "research"
            or str(karte.get("linked_idea_id")) != str(daten.get("bubble_id"))):
        return ("fehler", False, "Der Bericht ist da, aber die Recherche-Karte finde ich in der Bubble nicht.",
                "karte fehlt", {})
    quellen = antwort.get("citation_count")
    anzahl = f" mit {quellen} Quelle{'n' if quellen != 1 else ''}" if isinstance(quellen, int) else ""
    return ("fertig", True, f"Die Recherche ist fertig: „{str(karte.get('title') or 'Research')[:120]}“{anzahl} "
                            f"liegt in „{titel}“.", "karte geprueft", {"karte_id": node_id})


def runde(tabelle: Any, *, status_abfragen: Callable[[str], Dict[str, Any]],
          karte_lesen: Callable[[str], Optional[Mapping[str, Any]]]) -> Dict[str, int]:
    """Alle laufenden Recherchen einmal nachfassen (Nachfasser-Takt). Wirft nie."""
    z = {"recherche_fertig": 0, "recherche_fehler": 0, "recherche_wartet": 0, "fehler": 0}
    try:
        zeilen = tabelle.recherchen_laufend()
    except Exception:  # noqa: BLE001
        logger.exception("recherchen_laufend fehlgeschlagen")
        z["fehler"] += 1
        return z
    for zeile in zeilen if isinstance(zeilen, list) else []:
        if not isinstance(zeile, Mapping):
            continue
        try:
            aus = nachfassen(zeile, status_abfragen=status_abfragen, karte_lesen=karte_lesen)
        except Exception as e:  # noqa: BLE001 - naechste Runde fragt erneut
            logger.warning(f"[recherche] Status nicht lesbar: {type(e).__name__}")
            z["fehler"] += 1
            continue
        if aus is None:
            z["recherche_wartet"] += 1
            continue
        status, verified, meldung, grund, mehr = aus
        if _schliessen(tabelle, str(zeile.get("id")), status, verified, meldung, grund, **mehr):
            z["recherche_fertig" if status == "fertig" else "recherche_fehler"] += 1
    return z


def anschluss() -> Dict[str, Callable[..., Any]]:
    """Die echten Bindungen fuer ``runde``: Status am Recherche-Server, Karte aus der Datenbank."""
    def status_abfragen(job_id: str) -> Dict[str, Any]:
        schritt = f"status/{int(_jetzt() * 1000)}"
        return aufruf("research_status", {"job_id": job_id}, plan_id=f"recherche_{job_id}", schritt=schritt)

    def karte_lesen(node_id: str) -> Optional[Mapping[str, Any]]:
        from .ideen_rueckweg import _lesen
        zeilen = _lesen("canvas_nodes", {"select": "id,title,node_type,linked_idea_id", "id": f"eq.{node_id}",
                                         "limit": "1"})
        if zeilen is None:
            raise RuntimeError("canvas_nodes nicht lesbar")  # naechste Runde, nicht "Karte fehlt"
        return zeilen[0] if isinstance(zeilen, list) and zeilen and isinstance(zeilen[0], dict) else None

    return {"status_abfragen": status_abfragen, "karte_lesen": karte_lesen}
