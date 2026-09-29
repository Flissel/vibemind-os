"""Kurator: fuehrt Wissensdokumente nach, wenn etwas passiert ist.

Fix-Runde 1 (Task-7-Review, Findings 1-3+5): Der Leerlauf-Denker
(ContinuousThinkingEngine) laeuft nicht ueberall, wo Ereignisse entstehen -
brain-core faehrt mit BRAIN_BACKGROUND_LOOPS=0 (kein CTE-Tick dort), aber
genau dort meldet der PlanExecutor plan_completed/action_verified/
action_refuted per record_event(). Der Worker-CTE tickt zwar, sieht aber nur
seine eigenen Ereignisse (self_steer_dispatch/no_match_cluster). Ein
synchroner CTE->Kurator-Aufruf aus _think_tick griff deshalb in Produktion so
gut wie nie, und wo er griff, lief er ungebremst auf dem CTE-Thread (Leser +
~120 Tresor-Schreibvorgaenge je Volllauf-Bereich) und konnte sich mit dem
periodischen Kurator-Thread ueberschneiden (zwei Schreiber auf denselben
Tresor-Pfad -> spurious "abgelehnt").

Neuer Weg: record_event() schreibt zusaetzlich (wenn KURATOR_EREIGNIS_DATEI
gesetzt ist) eine JSON-Zeile ueber ereignis_melden() - das funktioniert in
JEDEM Prozess, der record_event() aufruft, egal ob dessen CTE tickt oder
nicht. Der EINZIGE Aufrufer des Kurators ist der Kurator-Takt-Thread im
brain-loops-Worker: er arbeitet neue Zeilen ueber
Kurator.ereignisse_abarbeiten() ab (Byte-Offset in einer .offset-Datei,
robust gegen Rotation/Kuerzung) und faehrt daneben periodisch
Kurator.voll_durchlauf(). Genau ein Schreiber -> keine parallelen
Tresor-Schreibversuche mehr.

Fakten kommen aus quellen (kein LLM); die Deutung optional vom LLM - verworfen,
wenn sie die Belegpflicht nicht erfuellt. Das Dokument wird dann ohne neue
Deutung geschrieben.

Controller-Ruling (Task 7): self.deuten(dok) wird nur gerufen, wenn deuten
gesetzt ist UND die im Tresor gespeicherte Deutung nicht mehr gueltig waere
(Tresor.deutung_noch_gueltig). Das spart LLM-Aufrufe und schuetzt von Hand
editierte Deutungen davor, bei jedem Lauf neu ueberschrieben zu werden.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Set, Tuple

from core.knowledge import quellen as _quellen
from core.knowledge.schema import Dokument, dateiname, pruefen

logger = logging.getLogger(__name__)

KURATOR_ENABLED = os.environ.get("KURATOR_ENABLED", "0").lower() in ("1", "true", "yes")
KURATOR_DEUTUNG = os.environ.get("KURATOR_DEUTUNG", "0").lower() in ("1", "true", "yes")
KURATOR_INTERVAL_S = float(os.environ.get("KURATOR_INTERVAL_S", "900"))
# Fix-Runde 1: Cross-Prozess-Ereignisdatei (leer = Feature aus). Der Stack
# setzt denselben Pfad in brain-core UND brain-loops (gemeinsames Volume,
# siehe Task 11).
KURATOR_EREIGNIS_DATEI = os.environ.get("KURATOR_EREIGNIS_DATEI", "")
# Takt, in dem der Worker die Ereignisdatei abarbeitet (unabhaengig vom
# selteneren Volllauf-Takt KURATOR_INTERVAL_S).
KURATOR_EREIGNIS_TAKT_S = float(os.environ.get("KURATOR_EREIGNIS_TAKT_S", "30"))

# Ereignis (kind oder kind:<Praefix der capability>) -> Leser
EREIGNIS_BEREICHE: Dict[str, Tuple[str, ...]] = {
    "action_verified:bubble": ("bubbles",),
    "action_verified:idea": ("bubbles",),
    "action_verified:coding": ("coding_projekte",),
    "action_verified:code": ("coding_projekte",),
    "plan_completed": ("agents", "pc_zustand"),
    "action_refuted": ("agents", "pc_zustand"),
    # "user_message" entfernt (Fix-Runde 1, Finding f): wird nirgends
    # emittiert. Das User-Dokument wird vom Volllauf aufgefrischt.
}

_DEUTEN_PROMPT = """Du schreibst den Abschnitt "Deutung" eines Wissensdokuments.
Regeln: hoechstens 4 Saetze, Deutsch. JEDER Satz endet mit mindestens einer
Belegnummer wie [B1], die unten existiert. Nichts erfinden, was die Belege nicht
tragen. Keine Aufzaehlung, kein Titel.

Dokument: {titel} ({typ})
Belege:
{belege}
"""


def llm_deuten(dok: Dokument) -> str:
    from core.multi_llm_router import MultiLLMRouter
    belege = "\n".join(f"[B{b.nr}] {f.schluessel} = {f.wert}"
                       for f in dok.fakten for b in dok.belege if b.nr == f.beleg)
    return MultiLLMRouter()._complete_sync(
        "brain_planning", _DEUTEN_PROMPT.format(titel=dok.titel, typ=dok.typ, belege=belege),
        300, 0.2).strip()


def bereiche_fuer(kind: str, payload: Optional[Dict[str, Any]]) -> Tuple[str, ...]:
    """Ereignis -> Tupel betroffener Leser-Namen (Bereiche). Gemeinsame
    Nachschlagelogik fuer Kurator._bereiche, ereignis_melden und
    ereignisse_abarbeiten."""
    cap = str((payload or {}).get("capability") or "")
    praefix = cap.split("_", 1)[0].split(".", 1)[0]
    return EREIGNIS_BEREICHE.get(f"{kind}:{praefix}") or EREIGNIS_BEREICHE.get(kind) or ()


def ereignis_melden(kind: str, payload: Dict[str, Any]) -> None:
    """Fix-Runde 1 (Finding 1): Cross-Prozess-Ereignismeldung. Haengt EINE
    JSON-Zeile an KURATOR_EREIGNIS_DATEI an - nur wenn die Datei konfiguriert
    ist UND das Ereignis auf mindestens einen Bereich abbildet (sonst waere
    die Zeile fuer den Kurator wertlos). Nur kind/capability/ts landen in der
    Datei - keine Intents/Freitexte (Datenschutz). Wirft nie."""
    try:
        if not KURATOR_EREIGNIS_DATEI:
            return
        if not bereiche_fuer(kind, payload):
            return
        zeile = json.dumps({
            "kind": kind,
            "capability": str((payload or {}).get("capability") or ""),
            "ts": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False)
        with open(KURATOR_EREIGNIS_DATEI, "a", encoding="utf-8") as f:
            f.write(zeile + "\n")
    except Exception as e:
        logger.debug("[kurator] ereignis_melden fehlgeschlagen: %s", e)


class Kurator:
    def __init__(self, tresor, kg: Any = None,
                 deuten: Optional[Callable[[Dokument], str]] = None, leser=_quellen):
        self.tresor = tresor
        self.kg = kg
        self.deuten = deuten
        self.leser = leser
        self.stats = {"geschrieben": 0, "abgelehnt": 0, "quellenfehler": 0,
                      "deutung_verworfen": 0, "unveraendert": 0, "unlesbar": 0}

    def _bereiche(self, kind: str, payload: Dict[str, Any]) -> Tuple[str, ...]:
        return bereiche_fuer(kind, payload)

    def _pflegen(self, dok: Dokument, bekannte: Optional[Set[str]] = None) -> None:
        # Schlusspruefung I1: Datei EINMAL lesen, Ergebnis an
        # deutung_noch_gueltig und schreiben weitergeben.
        geladen = self.tresor.laden(dok)
        # Controller-Ruling: LLM nur rufen, wenn die gespeicherte Deutung
        # ohnehin nicht mehr fuehrend bliebe (spart Aufrufe, schuetzt
        # von Hand gepflegte Deutungen).
        if self.deuten is not None and not self.tresor.deutung_noch_gueltig(dok, geladen=geladen):
            try:
                text = self.deuten(dok)
                kandidat = dok.model_copy(update={"deutung": text})
                if pruefen(kandidat) == []:
                    dok = kandidat
                else:
                    self.stats["deutung_verworfen"] += 1
            except Exception as e:
                self.stats["deutung_verworfen"] += 1
                logger.info("[kurator] Deutung fehlgeschlagen: %s", e)
        ok, probleme = self.tresor.schreiben(dok, bekannte=bekannte, geladen=geladen)
        status = getattr(self.tresor, "letzter_status", "")
        if not ok:
            if status == "unlesbar":
                self.stats["unlesbar"] += 1
                logger.warning("[kurator] %s nicht ueberschrieben: %s", dok.titel, probleme[:1])
                return
            self.stats["abgelehnt"] += 1
            logger.warning("[kurator] %s abgelehnt: %s", dok.titel, probleme[:3])
            return
        if bekannte is not None:
            bekannte.add(dateiname(dok))
        if status == "unveraendert":
            # Schlusspruefung I1: nichts geschrieben -> auch nicht neu indexieren.
            self.stats["unveraendert"] += 1
            return
        self.stats["geschrieben"] += 1
        if self.kg is not None:
            try:
                from core.knowledge.index import eintragen
                eintragen(self.kg, getattr(self.tresor, "zuletzt_geschrieben", None) or dok)
            except Exception as e:
                logger.info("[kurator] Index fehlgeschlagen: %s", e)

    def _lauf(self, namen) -> Dict[str, int]:
        jetzt = datetime.now(timezone.utc)
        vorher = dict(self.stats)
        # Schlusspruefung I1: bekannte Namen EINMAL pro Lauf, nicht pro Dokument.
        bekannte = self.tresor.bekannte_namen() if namen else set()
        for name in namen:
            try:
                ergebnis = getattr(self.leser, name)(jetzt)
            except RuntimeError as e:
                self.stats["quellenfehler"] += 1
                logger.warning("[kurator] Quelle %s ausgefallen: %s", name, e)
                continue
            for dok in ([ergebnis] if isinstance(ergebnis, Dokument) else (ergebnis or [])):
                self._pflegen(dok, bekannte)
        return {k: self.stats[k] - vorher[k] for k in self.stats}

    def bei_ereignis(self, kind: str, payload: Dict[str, Any]) -> Dict[str, int]:
        return self._lauf(self._bereiche(kind, payload))

    def voll_durchlauf(self) -> Dict[str, int]:
        ergebnis = self._lauf(("bubbles", "coding_projekte", "agents", "pc_zustand", "user"))
        if self.kg is not None:
            try:
                from core.knowledge import hubs, index
                ergebnis["kanten"] = index.verknuepfen(self.kg, self.tresor)
                ergebnis["hubs"] = hubs.schreiben(self.tresor, self.kg)
            except Exception as e:
                logger.info("[kurator] Verbindungen fehlgeschlagen: %s", e)
        return ergebnis

    def ereignisse_abarbeiten(self, pfad: str, offset_pfad: str) -> Dict[str, int]:
        """Fix-Runde 1 (Finding 1+2): arbeitet die seit dem letzten Lauf an
        `pfad` angehaengten Zeilen ab. Liest ab dem in `offset_pfad`
        gespeicherten Byte-Offset (0, wenn nicht vorhanden), ueberspringt
        kaputte Zeilen, vereinigt die Bereiche aller neuen Ereignisse und
        ruft `_lauf` GENAU EINMAL (kein Aufruf, wenn die Menge leer ist -
        z.B. keine neuen Zeilen oder nur ungemappte Ereignisse). Der neue
        Offset wird erst NACH dem Lauf geschrieben, damit ein Absturz
        mittendrin dieselben Zeilen beim naechsten Mal erneut abarbeitet statt
        sie zu verlieren. Ist die Datei kuerzer als der gespeicherte Offset
        (rotiert/gekuerzt), wird bei 0 neu begonnen."""
        leer: Dict[str, int] = {k: 0 for k in self.stats}
        leer["ereignisse"] = 0

        try:
            with open(offset_pfad, "r", encoding="utf-8") as f:
                offset = int((f.read() or "0").strip() or "0")
        except (OSError, ValueError):
            offset = 0

        try:
            groesse = os.path.getsize(pfad)
        except OSError:
            return dict(leer)
        if groesse < offset:
            offset = 0

        bereiche: set = set()
        anzahl = 0
        neu_offset = offset
        try:
            with open(pfad, "rb") as f:
                f.seek(offset)
                while True:
                    zeile = f.readline()
                    if not zeile:
                        break
                    neu_offset = f.tell()
                    try:
                        ereignis = json.loads(zeile.decode("utf-8").strip())
                    except (ValueError, TypeError, UnicodeDecodeError):
                        continue
                    if not isinstance(ereignis, dict):
                        continue
                    anzahl += 1
                    bereiche.update(bereiche_fuer(
                        ereignis.get("kind", ""),
                        {"capability": ereignis.get("capability")}))
        except OSError as e:
            logger.warning("[kurator] Ereignisdatei %s nicht lesbar: %s", pfad, e)
            return dict(leer)

        ergebnis = self._lauf(tuple(bereiche)) if bereiche else {k: 0 for k in self.stats}

        try:
            with open(offset_pfad, "w", encoding="utf-8") as f:
                f.write(str(neu_offset))
        except OSError as e:
            logger.warning("[kurator] Offset %s nicht schreibbar: %s", offset_pfad, e)

        ergebnis = dict(ergebnis)
        ergebnis["ereignisse"] = anzahl
        return ergebnis
