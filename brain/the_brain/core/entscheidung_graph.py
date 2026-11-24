"""Jeder Satz als Entscheidung im Neo4j-Graphen (Routing-Papier 24.11.2026, Schritt 1).

Bisher kamen nur Saetze in den Graphen, die einen Plan ausfuehrten (Plan/Hop am Ende von
``PlanExecutor.execute``). Feste Wege, Rueckfragen, Absagen und Hoeflichkeit hinterliessen
nichts. Hier bekommt jeder Satz von ``/api/multihop/execute``:

    (:Satz {id=trace_id, ts, reiter, laenge})            -- nie der Satztext
      -[:ENTSCHIEDEN_DURCH]->
    (:Entscheidung {id, weg, stufe, ok, dauer_ms, bereich_quelle, ts})
      -[:WAEHLT]-> (:Bereich {id})
      -[:FUEHRT_ZU]-> (:Plan {id})                         -- nur wenn ein Plan lief
    (:Satz)-[:IM_REITER]->(:Bereich)                       -- nur wenn die App einen Reiter schickt

``weg`` ist der Wert der Spur (scripts/brain_lage.py), ``stufe`` trennt darin die festen
Muster vom echten Planer (Vorsilbe der plan_id: shortcut_, ideen_, lesen_ ...).

Schreiben darf den Satz nie aufhalten: ``melden`` legt den Eintrag nur in eine begrenzte
Warteschlange; ein eigener Faden schreibt mit Zeitgrenze. Volle Warteschlange, Fehler und
Zeitueberschreitungen werden gezaehlt und geschluckt.

Schalter: ``ENTSCHEIDUNG_GRAPH=0`` schaltet alles ab. Zeitgrenze je Schreiben:
``ENTSCHEIDUNG_GRAPH_ZEIT_S`` (Vorgabe 2 s).
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from typing import Any, Dict, Mapping, Optional, Tuple

logger = logging.getLogger("brain.entscheidung_graph")

WARTESCHLANGE_MAX = 200


def an() -> bool:
    return os.environ.get("ENTSCHEIDUNG_GRAPH", "1") not in ("0", "false", "False")


def zeitgrenze_s() -> float:
    try:
        return max(0.1, float(os.environ.get("ENTSCHEIDUNG_GRAPH_ZEIT_S", "2")))
    except ValueError:
        return 2.0


# Vorsilbe der plan_id -> Stufe, die gegriffen hat (web/routers/introspection.py,
# core/recherche_bubble.py, core/desktop_*.py, core/plan_schema.Plan.make_id).
_STUFE_NACH_VORSILBE = (
    ("shortcut_", "abkuerzung-regex"),
    ("ideen_", "ideen-fest"),
    ("lesen_", "lesesatz-fest"),
    ("sales_", "sales-fest"),
    ("recherche_", "recherche-fest"),
    ("fenster-", "desktop-fest"),
    ("augen-", "desktop-fest"),
    ("plan_", "planer"),
)


def stufe_aus_plan(plan_id: Any) -> Optional[str]:
    """Die Stufe zu einer plan_id, ``"plan"`` fuer unbekannte Vorsilben, ``None`` ohne Plan."""
    if not isinstance(plan_id, str) or not plan_id:
        return None
    for vorsilbe, stufe in _STUFE_NACH_VORSILBE:
        if plan_id.startswith(vorsilbe):
            return stufe
    return "plan"


def _kennung(space: Any) -> Optional[str]:
    """Bereichskennung der App zu einem Namen aus Registry, Antwort oder Reiter."""
    try:
        from core.antwort_ziel import app_bereich
        from core.verstehen import _ALIASE

        kennung = app_bereich(space)
        if kennung is None:
            return None
        return _ALIASE.get(kennung, kennung)
    except Exception:  # noqa: BLE001
        return None


def _bereich_aller_schritte(executed: Any) -> Optional[str]:
    """Der eine Bereich aller ausgefuehrten Schritte (auch misslungener), sonst ``None``."""
    if not isinstance(executed, Mapping) or not executed:
        return None
    try:
        from core.antwort_ziel import _space_des_schritts
    except Exception:  # noqa: BLE001
        return None
    bereiche = set()
    for schritt in executed.values():
        if not isinstance(schritt, Mapping):
            continue
        kennung = _kennung(_space_des_schritts(schritt))
        if kennung is None:
            try:
                from core.verstehen import faehigkeit_space

                kennung = faehigkeit_space(schritt.get("capability"))
            except Exception:  # noqa: BLE001
                kennung = None
        if kennung is None:
            return None
        bereiche.add(kennung)
    return bereiche.pop() if len(bereiche) == 1 else None


def bereich_bestimmen(daten: Mapping[str, Any], reiter: Optional[str]) -> Tuple[Optional[str], str]:
    """(Bereich, Quelle). Reihenfolge: ziel der Antwort, space der Antwort, Schritte, Reiter."""
    ziel = daten.get("ziel")
    if isinstance(ziel, Mapping):
        kennung = _kennung(ziel.get("space"))
        if kennung:
            return kennung, "ziel"
    kennung = _kennung(daten.get("space"))
    if kennung:
        return kennung, "antwort"
    kennung = _bereich_aller_schritte(daten.get("executed"))
    if kennung:
        return kennung, "schritte"
    if reiter:
        return reiter, "reiter"
    return None, "keiner"


def _weg_der_antwort(daten: Mapping[str, Any], status_code: int, routed_via: Any) -> str:
    """Derselbe Weg wie in der Spur (introspection._spur_je_satz)."""
    weg = daten.get("weg")
    if isinstance(weg, str) and weg:
        return weg[:60]
    if isinstance(routed_via, str) and routed_via:
        return routed_via[:60]
    return ("rueckfrage" if daten.get("rueckfrage") else "hinweis" if daten.get("hinweis")
            else "angenommen" if daten.get("pending") else "fehler" if status_code >= 400
            else "ohne-weg")


def eintrag_bauen(daten: Any, *, reiter: Any = None, laenge: Any = None, dauer_ms: Any = None,
                  routed_via: Any = None, status_code: int = 200) -> Optional[Dict[str, Any]]:
    """Der Eintrag fuer einen Satz, oder ``None`` (keine trace_id). Ohne Satz- oder Antworttext."""
    try:
        if not isinstance(daten, Mapping):
            return None
        trace_id = daten.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id:
            return None
        reiter_kennung = _kennung(reiter) if isinstance(reiter, str) else None
        weg = _weg_der_antwort(daten, int(status_code or 200), routed_via)
        plan_id = daten.get("plan_id") if isinstance(daten.get("plan_id"), str) and daten.get("plan_id") else None
        bereich, quelle = bereich_bestimmen(daten, reiter_kennung)
        ok = daten.get("ok")
        return {
            "trace_id": trace_id,
            "ts": time.time(),
            "reiter": reiter_kennung,
            "laenge": int(laenge) if isinstance(laenge, int) and laenge >= 0 else None,
            "weg": weg,
            "stufe": stufe_aus_plan(plan_id) or weg,
            "ok": ok if isinstance(ok, bool) else None,
            "dauer_ms": int(dauer_ms) if isinstance(dauer_ms, (int, float)) and dauer_ms >= 0 else None,
            "bereich": bereich,
            "bereich_quelle": quelle,
            "plan_id": plan_id,
        }
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[entscheidung] Eintrag nicht gebaut: {type(e).__name__}: {e}")
        return None


class Schreiber:
    """Ein Faden, eine begrenzte Warteschlange. ``melden`` blockiert nie."""

    def __init__(self, graph: Any, maxsize: int = WARTESCHLANGE_MAX) -> None:
        self._graph = graph
        self._schlange: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=maxsize)
        self.zaehler = {"gemeldet": 0, "geschrieben": 0, "verworfen": 0, "fehler": 0}
        self._faden: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def _starten(self) -> None:
        with self._lock:
            if self._faden is None or not self._faden.is_alive():
                self._faden = threading.Thread(target=self._schleife, name="EntscheidungGraph", daemon=True)
                self._faden.start()

    def melden(self, eintrag: Dict[str, Any]) -> bool:
        """True = angenommen. Volle Schlange: verworfen und gezaehlt."""
        try:
            self._schlange.put_nowait(eintrag)
        except queue.Full:
            self.zaehler["verworfen"] += 1
            return False
        self.zaehler["gemeldet"] += 1
        self._starten()
        return True

    def einen_schreiben(self, eintrag: Dict[str, Any]) -> bool:
        try:
            self._graph.upsert_entscheidung(eintrag, timeout_s=zeitgrenze_s())
            self.zaehler["geschrieben"] += 1
            return True
        except Exception as e:  # noqa: BLE001
            self.zaehler["fehler"] += 1
            logger.debug(f"[entscheidung] Schreiben fehlgeschlagen: {type(e).__name__}: {e}")
            return False

    def _schleife(self) -> None:
        while True:
            eintrag = self._schlange.get()
            try:
                self.einen_schreiben(eintrag)
            finally:
                self._schlange.task_done()

    def warten(self, zeit_s: float = 2.0) -> bool:
        """Nur fuer Tests: wartet, bis die Schlange leer ist."""
        ende = time.time() + zeit_s
        while time.time() < ende:
            if self._schlange.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return False


_SCHREIBER: Dict[int, Schreiber] = {}
_SCHREIBER_LOCK = threading.Lock()


def schreiber_fuer(graph: Any) -> Schreiber:
    with _SCHREIBER_LOCK:
        schreiber = _SCHREIBER.get(id(graph))
        if schreiber is None or schreiber._graph is not graph:
            schreiber = Schreiber(graph)
            _SCHREIBER[id(graph)] = schreiber
        return schreiber


def melden(graph: Any, eintrag: Optional[Dict[str, Any]]) -> bool:
    """Meldet einen Satz an den Graphen. Wirft nie, blockiert nie."""
    try:
        if not an() or graph is None or eintrag is None or not hasattr(graph, "upsert_entscheidung"):
            return False
        return schreiber_fuer(graph).melden(eintrag)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[entscheidung] melden uebersprungen: {type(e).__name__}: {e}")
        return False


def zaehler(graph: Any) -> Dict[str, int]:
    """Zaehler des Schreibers (fuer /api/decision_graph/stats)."""
    try:
        with _SCHREIBER_LOCK:
            schreiber = _SCHREIBER.get(id(graph))
        return dict(schreiber.zaehler) if schreiber is not None else {}
    except Exception:  # noqa: BLE001
        return {}
