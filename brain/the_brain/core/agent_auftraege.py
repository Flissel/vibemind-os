"""Ausfuehrungskette K1: Zugriff des Brains auf die Auftragstabelle.

Das Brain legt Agenten-Auftraege an (status 'offen'), speichert den Plan-Rest,
liest fertige Auftraege zur Pruefung und laesst ueberfaellige ablaufen. Den
Status 'laeuft/fertig/fehler/abgelehnt' setzt nur der Ausfuehrer am PC.
Spec: docs/superpowers/specs/2026-10-08-ausfuehrungskette-k1-auftraege-design.md
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from core.config import get_secret

AGENT_AUFTRAEGE_ENABLED = os.environ.get("AGENT_AUFTRAEGE_ENABLED", "0").lower() in ("1", "true", "yes")
TABELLE = "brain_agent_auftraege"
TIMEOUT_S = 10


class AuftragsTabelle:
    def __init__(self, basis_url: str, schluessel: str, *, http: Any = None) -> None:
        self._url = basis_url.rstrip("/") + "/rest/v1/" + TABELLE
        self._schluessel = schluessel
        self._http = http or requests

    def _header(self, rueckgabe: bool = False) -> Dict[str, str]:
        h = {"apikey": self._schluessel, "Authorization": f"Bearer {self._schluessel}",
             "Content-Type": "application/json"}
        if rueckgabe:
            h["Prefer"] = "return=representation"
        return h

    def _req(self, methode: str, *, params=None, json=None, rueckgabe=False):
        r = self._http.request(methode, self._url, headers=self._header(rueckgabe), params=params,
                               json=json, timeout=TIMEOUT_S)
        r.raise_for_status()
        return r.json() if rueckgabe or methode == "GET" else None

    def anlegen(self, *, capability: str, agent: str, auftrag: str, trace_id: str, plan_id: str,
                hop_id: str, uebergabe: Optional[dict] = None, antwortkanal: Optional[dict] = None,
                frist_s: int = 600) -> str:
        frist = (datetime.now(timezone.utc) + timedelta(seconds=frist_s)).isoformat()
        zeilen = self._req("POST", rueckgabe=True, json={
            "capability": capability, "agent": agent, "auftrag": auftrag, "trace_id": trace_id,
            "plan_id": plan_id, "hop_id": hop_id, "status": "offen", "frist": frist,
            "uebergabe": uebergabe, "antwortkanal": antwortkanal})
        if not isinstance(zeilen, list) or not zeilen or not isinstance(zeilen[0], dict) or "id" not in zeilen[0]:
            raise RuntimeError("brain_agent_auftraege: Anlegen lieferte keine id")
        return str(zeilen[0]["id"])

    def plan_rest_setzen(self, auftrag_id: str, plan_rest: dict) -> None:
        self._req("PATCH", params={"id": f"eq.{auftrag_id}"}, json={"plan_rest": plan_rest})

    def zu_pruefen(self, limit: int = 20) -> List[dict]:
        return self._req("GET", params={"status": "eq.fertig", "pruefung": "is.null",
                                        "order": "beendet.asc", "limit": str(limit)})

    def beendete_ohne_meldung(self, limit: int = 20) -> List[dict]:
        return self._req("GET", params={"status": "in.(fehler,abgelehnt,abgelaufen)", "pruefung": "is.null",
                                        "order": "beendet.asc", "limit": str(limit)})

    def pruefung_setzen(self, auftrag_id: str, pruefung: dict) -> bool:
        """True, wenn gesetzt; False, wenn schon gesetzt oder id unbekannt."""
        zeilen = self._req("PATCH", rueckgabe=True,
                           params={"id": f"eq.{auftrag_id}", "pruefung": "is.null"},
                           json={"pruefung": pruefung})
        return bool(zeilen)

    def abgelaufene_markieren(self, jetzt_iso: Optional[str] = None) -> int:
        jetzt_iso = jetzt_iso or datetime.now(timezone.utc).isoformat()
        zeilen = self._req("PATCH", rueckgabe=True,
                           params={"status": "in.(offen,laeuft)", "frist": f"lt.{jetzt_iso}"},
                           json={"status": "abgelaufen", "beendet": jetzt_iso})
        return len(zeilen or [])


def tabelle_aus_umgebung() -> AuftragsTabelle:
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    schluessel = (get_secret("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not schluessel:
        raise RuntimeError("kein Service-Schluessel oder keine SUPABASE_URL fuer brain_agent_auftraege")
    return AuftragsTabelle(url, schluessel)
