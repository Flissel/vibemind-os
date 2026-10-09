"""Nachfasser (Ausfuehrungskette K1).

Prueft fertige Agenten-Auftraege mit dem Validator der Capability, protokolliert
die Pruefung, setzt den gespeicherten Rest des Plans fort, gibt fehlgeschlagene,
abgelehnte und abgelaufene Auftraege zur Meldung frei und markiert ueberfaellige
Auftraege als abgelaufen. Laeuft als Thread in brain-loops.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Dict, Optional

from core.agent_auftraege import AGENT_AUFTRAEGE_ENABLED

logger = logging.getLogger("brain.nachfasser")

NACHFASSER_ENABLED = AGENT_AUFTRAEGE_ENABLED
NACHFASSER_TAKT_S = float(os.environ.get("NACHFASSER_TAKT_S", "5"))


class Nachfasser:
    def __init__(self, tabelle: Any, *, validator: Any, router: Any, plan_executor: Any,
                 exec_log: Any = None) -> None:
        self.tabelle = tabelle
        self.validator = validator
        self.router = router
        self.plan_executor = plan_executor
        self.exec_log = exec_log

    def runde(self) -> Dict[str, int]:
        z = {"geprueft": 0, "bestaetigt": 0, "abgelehnt_pruefung": 0, "fortgesetzt": 0,
             "abgelaufen": 0, "gemeldet": 0, "fehler": 0}
        try:
            fertige = self.tabelle.zu_pruefen()
        except Exception:  # noqa: BLE001
            logger.exception("zu_pruefen fehlgeschlagen")
            fertige = []
            z["fehler"] += 1
        for a in fertige:
            try:
                self._pruefen(a, z)
            except Exception:  # noqa: BLE001 - ein Auftrag darf die Runde nicht stoppen
                logger.exception("Pruefung von Auftrag %s fehlgeschlagen", a.get("id"))
                z["fehler"] += 1
        try:
            beendete = self.tabelle.beendete_ohne_meldung()
        except Exception:  # noqa: BLE001
            logger.exception("beendete_ohne_meldung fehlgeschlagen")
            beendete = []
            z["fehler"] += 1
        for b in beendete:
            try:
                # Pruefung leer setzen, damit der Ausfuehrer die Meldung zustellen kann.
                if self.tabelle.pruefung_setzen(b["id"], {"verified": None, "reason": b.get("status")}):
                    z["gemeldet"] += 1
            except Exception:  # noqa: BLE001
                logger.exception("Meldung fuer Auftrag %s fehlgeschlagen", b.get("id"))
                z["fehler"] += 1
        try:
            z["abgelaufen"] = int(self.tabelle.abgelaufene_markieren() or 0)
        except Exception:  # noqa: BLE001
            logger.exception("abgelaufene_markieren fehlgeschlagen")
            z["fehler"] += 1
        return z

    def _pruefen(self, a: dict, z: Dict[str, int]) -> None:
        cap = a.get("capability") or ""
        ergebnis = a.get("ergebnis")
        cfg = (self.router.get_capability(cap) or {}).get("validator")
        kind = cfg.get("kind") if isinstance(cfg, dict) else None
        if not cfg:
            pruefung = {"verified": None, "reason": "kein Pruefer", "kind": None}
            verified: Optional[bool] = None
        elif isinstance(kind, str) and kind.startswith("agent:"):
            # agent-Pruefer sind zu teuer/langsam fuer den Takt: nicht aufrufen.
            pruefung = {"verified": None, "reason": "agent-Pruefer nicht im Takt", "kind": kind}
            verified = None
        else:
            verdict = self.validator.validate(
                cfg, intent=a.get("auftrag"), arg=a.get("auftrag"), raw_result={"response": ergebnis}) or {}
            verified = verdict.get("verified")
            on_fail = verdict.get("on_fail") or (cfg.get("on_fail") if isinstance(cfg, dict) else None)
            if verdict.get("valid") is False and on_fail == "block":
                verified = False
            pruefung = {"verified": verified, "reason": verdict.get("reason"), "kind": verdict.get("kind")}
        z["geprueft"] += 1
        # Nur wer die Pruefung wirklich gesetzt hat, darf fortsetzen (kein Doppellauf bei Ueberlappung).
        if not self.tabelle.pruefung_setzen(a["id"], pruefung):
            return
        if verified is True:
            z["bestaetigt"] += 1
        elif verified is False:
            z["abgelehnt_pruefung"] += 1
        self._protokollieren(a, cap, verified, pruefung.get("reason"))
        rest = a.get("plan_rest")
        if verified is not False and rest:
            try:
                from core.plan_schema import Plan
                state = dict(rest.get("state") or {})
                if rest.get("output_var"):
                    state[rest["output_var"]] = ergebnis
                res = self.plan_executor.execute(Plan.from_dict(rest["plan"]), start_state=state,
                                                 antwortkanal=rest.get("antwortkanal"))
                if isinstance(res, dict) and res.get("ok") is False and not res.get("pending"):
                    raise RuntimeError(f"Fortsetzung nicht ok: {str(res.get('error') or res)[:200]}")
            except Exception as e:  # noqa: BLE001 - Fehlschlag sichtbar machen, nicht verschlucken
                meldung = f"{type(e).__name__}: {e}"[:300]
                logger.warning("Fortsetzung von Auftrag %s fehlgeschlagen: %s", a.get("id"), meldung)
                z["fehler"] += 1
                try:
                    self.tabelle.pruefung_ergaenzen(a["id"], {**pruefung, "fortsetzung_fehler": meldung})
                except Exception:  # noqa: BLE001
                    logger.exception("pruefung_ergaenzen fuer Auftrag %s fehlgeschlagen", a.get("id"))
                return
            z["fortgesetzt"] += 1

    def _protokollieren(self, a: dict, cap: str, verified: Optional[bool], reason: Any) -> None:
        if self.exec_log is None:
            return
        try:
            self.exec_log.record_step(
                plan_id=a.get("plan_id") or "", hop_k=None, intent=a.get("auftrag") or "",
                stage="verify", capability=cap, source="nachfasser", claimed_ok=True,
                verified=verified, reason=str(reason or ""))
        except Exception:  # noqa: BLE001 - Protokoll-Fehler nur loggen
            logger.warning("Ausfuehrungsprotokoll fehlgeschlagen", exc_info=True)


def takt_schleife(n: Nachfasser, *, takt_s: float, schlafen: Callable[[float], Any] = time.sleep,
                  runden: Optional[int] = None) -> None:
    i = 0
    while runden is None or i < runden:
        try:
            n.runde()
        except Exception:  # noqa: BLE001
            logger.exception("Nachfasser-Runde fehlgeschlagen")
        i += 1
        schlafen(takt_s)
