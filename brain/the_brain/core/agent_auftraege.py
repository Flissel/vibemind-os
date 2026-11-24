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
                frist_s: int = 600, status: str = "offen") -> str:
        """``status="laeuft"``: das Gehirn arbeitet selbst (Recherche); der Ausfuehrer am PC nimmt
        nur ``offen``."""
        if status not in ("offen", "laeuft"):
            raise ValueError("anlegen: status nur offen oder laeuft")
        jetzt = datetime.now(timezone.utc)
        frist = (jetzt + timedelta(seconds=frist_s)).isoformat()
        if status == "offen":
            auftrag = mit_neuanfang(auftrag)
        zeile = {
            "capability": capability, "agent": agent, "auftrag": auftrag, "trace_id": trace_id,
            "plan_id": plan_id, "hop_id": hop_id, "status": status, "frist": frist,
            "uebergabe": uebergabe, "antwortkanal": antwortkanal}
        if status == "laeuft":
            zeile["begonnen"] = jetzt.isoformat()
        zeilen = self._req("POST", rueckgabe=True, json=zeile)
        if not isinstance(zeilen, list) or not zeilen or not isinstance(zeilen[0], dict) or "id" not in zeilen[0]:
            raise RuntimeError("brain_agent_auftraege: Anlegen lieferte keine id")
        return str(zeilen[0]["id"])

    def uebergabe_setzen(self, auftrag_id: str, uebergabe: dict) -> None:
        self._req("PATCH", params={"id": f"eq.{auftrag_id}"}, json={"uebergabe": uebergabe})

    def zuletzt(self, limit: int = 20) -> List[dict]:
        """Die neuesten Auftraege, nur Faehigkeit, Agent, Stand und Zeiten (core/auftraege_lesen)."""
        return self._req("GET", params={"order": "angelegt.desc", "limit": str(limit),
                                        "select": "capability,agent,status,angelegt,begonnen,beendet"})

    def recherchen_laufend(self, limit: int = 20) -> List[dict]:
        """Laufende Recherchen des Gehirns (core/recherche_bubble)."""
        return self._req("GET", params={"status": "eq.laeuft", "uebergabe->recherche": "not.is.null",
                                        "order": "angelegt.asc", "limit": str(limit),
                                        "select": "id,trace_id,plan_id,angelegt,uebergabe"})

    def recherche_abschliessen(self, auftrag_id: str, status: str, pruefung: dict,
                               jetzt_iso: Optional[str] = None) -> bool:
        """Laufende Recherche auf fertig/fehler setzen; False, wenn sie nicht mehr laeuft."""
        if status not in ("fertig", "fehler"):
            raise ValueError("recherche_abschliessen: status nur fertig oder fehler")
        jetzt_iso = jetzt_iso or datetime.now(timezone.utc).isoformat()
        zeilen = self._req("PATCH", rueckgabe=True, params={"id": f"eq.{auftrag_id}", "status": "eq.laeuft"},
                           json={"status": status, "beendet": jetzt_iso, "pruefung": pruefung})
        return bool(zeilen)

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

    def pruefung_ergaenzen(self, auftrag_id: str, pruefung: dict) -> None:
        """Schreibt die volle Pruefung neu (PATCH nur nach id, ohne is.null-Schutz)."""
        self._req("PATCH", params={"id": f"eq.{auftrag_id}"}, json={"pruefung": pruefung})

    def dokument_abbrechen(self, bubble_id: Optional[str], jetzt_iso: Optional[str] = None) -> int:
        """White Paper anhalten (core/ideen_dokument): Zahl der angehaltenen Auftraege.

        Offene Teile werden 'abgelehnt' (mit Pruefung, damit nichts mehr gemeldet oder
        fortgesetzt wird); laufende Teile bekommen die Pruefung vorab (is.null-Schutz), so
        nimmt der Nachfasser ihr Ergebnis nicht mehr auf und legt keinen weiteren Teil an.
        """
        jetzt_iso = jetzt_iso or datetime.now(timezone.utc).isoformat()
        filter_ = {"uebergabe->rueckweg->>vertrag": "eq.abschnitt"}
        if bubble_id is not None:
            filter_["uebergabe->rueckweg->>bubble_id"] = f"eq.{bubble_id}"
        pruefung = {"verified": None, "reason": "abgebrochen", "kind": "ideen_dokument", "rueckweg": "abgebrochen",
                    "fortsetzung": "keine", "meldung": "Das White Paper wurde abgebrochen."}
        offen = self._req("PATCH", rueckgabe=True, params={**filter_, "status": "eq.offen"},
                          json={"status": "abgelehnt", "beendet": jetzt_iso, "pruefung": pruefung})
        laufend = self._req("PATCH", rueckgabe=True,
                            params={**filter_, "status": "in.(laeuft,fertig)", "pruefung": "is.null"},
                            json={"pruefung": pruefung})
        return len(offen or []) + len(laufend or [])

    def lesen(self, auftrag_id: str) -> Optional[dict]:
        """Eine Zeile fuer den Stand (core/auftrag_stand); ``None``, wenn es sie nicht gibt."""
        zeilen = self._req("GET", params={"id": f"eq.{auftrag_id}", "limit": "1",
                                          "select": "id,capability,status,angelegt,beendet,pruefung,uebergabe,"
                                                    "ergebnis"})
        return zeilen[0] if isinstance(zeilen, list) and zeilen and isinstance(zeilen[0], dict) else None

    def letzter_vorschlag(self, seit_iso: str, bubble_id: Optional[str] = None) -> Optional[dict]:
        """Der juengste offene Vorschlag (core/ideen_vorschlag), optional nur einer Bubble."""
        params = {"pruefung->>rueckweg": "eq.vorschlag", "pruefung->>vorschlag_erledigt": "is.null",
                  "beendet": f"gt.{seit_iso}", "order": "beendet.desc", "limit": "1",
                  "select": "id,capability,beendet,pruefung,uebergabe"}
        if bubble_id is not None:
            params["uebergabe->rueckweg->>bubble_id"] = f"eq.{bubble_id}"
        zeilen = self._req("GET", params=params)
        return zeilen[0] if isinstance(zeilen, list) and zeilen and isinstance(zeilen[0], dict) else None

    def letzter_erledigter_vorschlag(self, seit_iso: str, bubble_id: Optional[str] = None) -> Optional[dict]:
        """Der juengste schon erledigte Vorschlag (uebernommen oder verworfen), optional einer Bubble."""
        params = {"pruefung->>rueckweg": "eq.vorschlag", "pruefung->>vorschlag_erledigt": "not.is.null",
                  "beendet": f"gt.{seit_iso}", "order": "beendet.desc", "limit": "1",
                  "select": "id,capability,beendet,pruefung,uebergabe"}
        if bubble_id is not None:
            params["uebergabe->rueckweg->>bubble_id"] = f"eq.{bubble_id}"
        zeilen = self._req("GET", params=params)
        return zeilen[0] if isinstance(zeilen, list) and zeilen and isinstance(zeilen[0], dict) else None

    def vorschlag_erledigen(self, zeile: dict, wie: str) -> bool:
        """Vorschlag als erledigt markieren ("uebernommen"/"verworfen"); False, wenn schon erledigt."""
        pruefung = {**(zeile.get("pruefung") or {}), "vorschlag_erledigt": wie}
        zeilen = self._req("PATCH", rueckgabe=True,
                           params={"id": f"eq.{zeile['id']}", "pruefung->>vorschlag_erledigt": "is.null"},
                           json={"pruefung": pruefung})
        return bool(zeilen)

    def abgelaufene_markieren(self, jetzt_iso: Optional[str] = None) -> int:
        jetzt_iso = jetzt_iso or datetime.now(timezone.utc).isoformat()
        zeilen = self._req("PATCH", rueckgabe=True,
                           params={"status": "in.(offen,laeuft)", "frist": f"lt.{jetzt_iso}"},
                           json={"status": "abgelaufen", "beendet": jetzt_iso})
        return len(zeilen or [])


# Lauf 8: Der Ausfuehrer am PC schickt jeden Auftrag in die eine Sitzung des Agenten
# (POST /api/agents/{id}/message ohne eigene Sitzung). Zwei Urteilsfragen bekamen so das
# Gruppier-Ergebnis des vorigen Auftrags zurueck. Der Vorspann ueberstimmt die Vorgeschichte;
# die Pruefung des Ergebnisses bleibt die eigentliche Sicherung.
NEUANFANG = ("NEUER AUFTRAG, fuer sich allein. Alles, was vorher in dieser Unterhaltung steht, gehoert zu "
             "anderen, abgeschlossenen Auftraegen: nicht wiederholen, nicht fortsetzen, nicht darauf beziehen. "
             "Antworte nur auf den Auftrag nach dieser Zeile.\n---\n")


def mit_neuanfang(auftrag: str) -> str:
    """Auftragstext mit dem Vorspann (genau einmal)."""
    return auftrag if auftrag.startswith(NEUANFANG) else NEUANFANG + auftrag


def offene_auftraege(exec_result: Any) -> List[dict]:
    """Wartende Agenten-Auftraege aus einem PlanExecutor-Ergebnis (pending Hops mit auftrag_id)."""
    if not isinstance(exec_result, dict) or not exec_result.get("pending"):
        return []
    aus: List[dict] = []
    for _sid, hop in (exec_result.get("executed") or {}).items():
        res = hop.get("result") if isinstance(hop, dict) else None
        if isinstance(hop, dict) and hop.get("pending") and isinstance(res, dict) and res.get("auftrag_id"):
            aus.append({"auftrag_id": str(res["auftrag_id"]), "agent": res.get("agent"),
                        "capability": hop.get("capability")})
    return aus


def quittung(auftraege: List[dict]) -> str:
    """Sofort-Antwort fuer einen angenommenen Auftrag (Route und brain_chat).

    Ohne agent faellt "zustaendig" auf den Capability-Namen zurueck (nie "None").
    """
    a = auftraege[0]
    zustaendig = a.get("agent") or a.get("capability") or "unbekannt"
    return (f"Auftrag angenommen, Nr. {str(a['auftrag_id'])[:8]} – zuständig: {zustaendig}. "
            "Das Ergebnis kommt, sobald es geprüft ist.")


def tabelle_aus_umgebung() -> AuftragsTabelle:
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    schluessel = (get_secret("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not schluessel:
        raise RuntimeError("kein Service-Schluessel oder keine SUPABASE_URL fuer brain_agent_auftraege")
    return AuftragsTabelle(url, schluessel)
