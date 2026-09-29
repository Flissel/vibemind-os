"""Kurator: fuehrt Wissensdokumente nach, wenn etwas passiert ist.

Aufgerufen vom Leerlauf-Denker (ContinuousThinkingEngine._think_event) und
periodisch vom brain-loops-Worker (voll_durchlauf). Fakten kommen aus quellen
(kein LLM); die Deutung optional vom LLM - verworfen, wenn sie die Belegpflicht
nicht erfuellt. Das Dokument wird dann ohne neue Deutung geschrieben.

Controller-Ruling (Task 7): self.deuten(dok) wird nur gerufen, wenn deuten
gesetzt ist UND die im Tresor gespeicherte Deutung nicht mehr gueltig waere
(Tresor.deutung_noch_gueltig). Das spart LLM-Aufrufe und schuetzt von Hand
editierte Deutungen davor, bei jedem Lauf neu ueberschrieben zu werden.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from core.knowledge import quellen as _quellen
from core.knowledge.schema import Dokument, pruefen

logger = logging.getLogger(__name__)

KURATOR_ENABLED = os.environ.get("KURATOR_ENABLED", "0").lower() in ("1", "true", "yes")
KURATOR_DEUTUNG = os.environ.get("KURATOR_DEUTUNG", "0").lower() in ("1", "true", "yes")
KURATOR_INTERVAL_S = float(os.environ.get("KURATOR_INTERVAL_S", "900"))

# Ereignis (kind oder kind:<Praefix der capability>) -> Leser
EREIGNIS_BEREICHE: Dict[str, Tuple[str, ...]] = {
    "action_verified:bubble": ("bubbles",),
    "action_verified:idea": ("bubbles",),
    "action_verified:coding": ("coding_projekte",),
    "action_verified:code": ("coding_projekte",),
    "plan_completed": ("agents", "pc_zustand"),
    "action_refuted": ("agents", "pc_zustand"),
    "user_message": ("user",),
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


class Kurator:
    def __init__(self, tresor, kg: Any = None,
                 deuten: Optional[Callable[[Dokument], str]] = None, leser=_quellen):
        self.tresor = tresor
        self.kg = kg
        self.deuten = deuten
        self.leser = leser
        self.stats = {"geschrieben": 0, "abgelehnt": 0, "quellenfehler": 0,
                      "deutung_verworfen": 0}

    def _bereiche(self, kind: str, payload: Dict[str, Any]) -> Tuple[str, ...]:
        cap = str((payload or {}).get("capability") or "")
        praefix = cap.split("_", 1)[0].split(".", 1)[0]
        return EREIGNIS_BEREICHE.get(f"{kind}:{praefix}") or EREIGNIS_BEREICHE.get(kind) or ()

    def _pflegen(self, dok: Dokument) -> None:
        # Controller-Ruling: LLM nur rufen, wenn die gespeicherte Deutung
        # ohnehin nicht mehr fuehrend bliebe (spart Aufrufe, schuetzt
        # von Hand gepflegte Deutungen).
        if self.deuten is not None and not self.tresor.deutung_noch_gueltig(dok):
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
        ok, probleme = self.tresor.schreiben(dok)
        if not ok:
            self.stats["abgelehnt"] += 1
            logger.warning("[kurator] %s abgelehnt: %s", dok.titel, probleme[:3])
            return
        self.stats["geschrieben"] += 1
        if self.kg is not None:
            try:
                from core.knowledge.index import eintragen
                eintragen(self.kg, self.tresor.lesen_von(dok) or dok)
            except Exception as e:
                logger.info("[kurator] Index fehlgeschlagen: %s", e)

    def _lauf(self, namen) -> Dict[str, int]:
        jetzt = datetime.now(timezone.utc)
        vorher = dict(self.stats)
        for name in namen:
            try:
                ergebnis = getattr(self.leser, name)(jetzt)
            except RuntimeError as e:
                self.stats["quellenfehler"] += 1
                logger.warning("[kurator] Quelle %s ausgefallen: %s", name, e)
                continue
            for dok in ([ergebnis] if isinstance(ergebnis, Dokument) else (ergebnis or [])):
                self._pflegen(dok)
        return {k: self.stats[k] - vorher[k] for k in self.stats}

    def bei_ereignis(self, kind: str, payload: Dict[str, Any]) -> Dict[str, int]:
        return self._lauf(self._bereiche(kind, payload))

    def voll_durchlauf(self) -> Dict[str, int]:
        return self._lauf(("bubbles", "coding_projekte", "agents", "pc_zustand", "user"))
