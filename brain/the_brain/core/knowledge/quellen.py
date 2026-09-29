"""Faktenleser: Quelle -> Dokument ohne Deutung. Kein LLM.

Jeder Wert laeuft ueber nachfrage.abfragen/wert_von - derselbe Pfad, den die
Nachfrage-Probe spaeter nimmt. Faellt eine Quelle aus, liefert ihr Leser
nichts, und das bestehende Dokument bleibt, wie es ist (tresor schreibt nur
Neues).
"""
from __future__ import annotations

import logging
import os
from collections import Counter
from datetime import datetime
from typing import Any, List, Optional

from core.knowledge.nachfrage import abfragen, feld_lesen
from core.knowledge.schema import Beleg, Dokument, Fakt, dateiname

logger = logging.getLogger(__name__)
FEHLER: Counter = Counter()
PC_ZUSTAND_ID = "system"
PC_ZUSTAND_TITEL = "PC-Zustand"


class Sammler:
    def __init__(self, jetzt: datetime):
        self.jetzt = jetzt
        self.fakten: List[Fakt] = []
        self.belege: List[Beleg] = []

    def fakt(self, schluessel: str, quelle: str, ziel: str, feld: str, wert: Any) -> None:
        if wert is None:
            return
        nr = len(self.belege) + 1
        w = str(wert)
        self.belege.append(Beleg(nr=nr, quelle=quelle, ziel=ziel, feld=feld, wert=w,
                                 gemessen=self.jetzt))
        self.fakten.append(Fakt(schluessel=schluessel, wert=w, beleg=nr))

    def dokument(self, typ: str, id: str, titel: str, links: Optional[List[str]] = None) -> Dokument:
        return Dokument(typ=typ, id=id, titel=titel, stand=self.jetzt, fakten=self.fakten,
                        belege=self.belege, links=links or [])


def _pc_link() -> str:
    return f"{PC_ZUSTAND_TITEL} ({PC_ZUSTAND_ID[:6]})"


def bubbles(jetzt: datetime) -> List[Dokument]:
    liste = ("ideas?parent_id=is.null&select=id,title,status,score,updated_at,"
             "promoted_to_project_id&limit=1000")
    rows, _, _ = abfragen("supabase", liste)
    projekte = {}
    try:
        prow, _, _ = abfragen("supabase", "swe_design_runs?select=id,project_name,project_id&limit=1000")
        for p in prow or []:
            if p.get("project_id"):
                projekte[p["project_id"]] = p
    except RuntimeError:
        FEHLER["coding_projekte_fuer_links"] += 1
    out = []
    for r in rows or []:
        bid = r["id"]
        eins = f"ideas?id=eq.{bid}&select=status,score,updated_at"
        s = Sammler(jetzt)
        s.fakt("status", "supabase", eins, "status", r.get("status"))
        s.fakt("score", "supabase", eins, "score", r.get("score"))
        s.fakt("zuletzt_geaendert", "supabase", eins, "updated_at", r.get("updated_at"))
        zz = f"canvas_nodes?linked_idea_id=eq.{bid}&select=id&limit=1"
        _, gesamt, _ = abfragen("supabase", zz, mit_zaehlung=True)
        s.fakt("knoten", "supabase", zz, "#count", gesamt)
        links = []
        p = projekte.get(r.get("promoted_to_project_id"))
        if p:
            links.append(f"{p.get('project_name') or p['id']} ({str(p['id'])[:6]})")
        out.append(s.dokument("bubble", bid, r.get("title") or bid, links))
    return out


def coding_projekte(jetzt: datetime) -> List[Dokument]:
    rows, _, _ = abfragen("supabase", "swe_design_runs?select=id,project_name,status,"
                          "completed_stages,total_stages,gitea_repo,project_id,created_at"
                          "&order=created_at.desc&limit=200")
    out = []
    for r in rows or []:
        rid = str(r["id"])
        eins = f"swe_design_runs?id=eq.{rid}&select=status,completed_stages,total_stages,gitea_repo"
        s = Sammler(jetzt)
        s.fakt("status", "supabase", eins, "status", r.get("status"))
        s.fakt("stufen_fertig", "supabase", eins, "completed_stages", r.get("completed_stages"))
        s.fakt("stufen_gesamt", "supabase", eins, "total_stages", r.get("total_stages"))
        s.fakt("repo", "supabase", eins, "gitea_repo", r.get("gitea_repo"))
        # Herkunft: die Zeile existiert in swe_design_runs (Weg SWE-Design).
        s.fakt("weg_swe_design", "supabase", f"swe_design_runs?id=eq.{rid}&select=id", "id", rid)
        out.append(s.dokument("coding_projekt", rid, r.get("project_name") or rid))
    return out


def agents(jetzt: datetime, audit_n: int = 500) -> List[Dokument]:
    liste, _, _ = abfragen("openfang", "/api/agents")
    audit_ziel = f"/api/audit/recent?n={audit_n}"
    audit, _, _ = abfragen("openfang", audit_ziel)
    eintraege = (audit or {}).get("entries", [])
    out = []
    for a in liste or []:
        name, aid = a.get("name"), a.get("id")
        if not name or not aid:
            continue
        sel = f"[name={name}]"
        s = Sammler(jetzt)
        s.fakt("zustand", "openfang", "/api/agents", f"{sel}.state", a.get("state"))
        s.fakt("bereit", "openfang", "/api/agents", f"{sel}.ready", a.get("ready"))
        s.fakt("modell", "openfang", "/api/agents", f"{sel}.model_name", a.get("model_name"))
        s.fakt("zuletzt_aktiv", "openfang", "/api/agents", f"{sel}.last_active", a.get("last_active"))
        passt = [e for e in eintraege if e.get("agent_id") == aid]
        s.fakt("aktionen_ok", "openfang", audit_ziel, f"#agent:{aid}:ok",
               sum(1 for e in passt if e.get("outcome") == "ok"))
        s.fakt("aktionen_fehler", "openfang", audit_ziel, f"#agent:{aid}:fehler",
               sum(1 for e in passt if str(e.get("outcome", "")).startswith("error")))
        out.append(s.dokument("agent", aid, name, [_pc_link()]))
    return out


def _dienste():
    e = os.environ
    return [
        ("qdrant", (e.get("QDRANT_URL") or "").rstrip("/") + "/healthz"),
        ("embedding", (e.get("EMBEDDING_SERVICE_URL") or "http://embedding-service:8080").rstrip("/") + "/health"),
        ("openfang", (e.get("OPENFANG_URL") or "").rstrip("/") + "/api/health"),
        ("supabase", (e.get("SUPABASE_URL") or "").rstrip("/") + "/auth/v1/health"),
    ]


def pc_zustand(jetzt: datetime) -> Dokument:
    s = Sammler(jetzt)
    for name, url in _dienste():
        if not url.startswith("http"):
            continue
        try:
            _, _, status = abfragen("http", url)
            s.fakt(f"dienst_{name}", "http", url, "#status", status)
        except RuntimeError:
            FEHLER[f"dienst_{name}"] += 1
    liste, _, _ = abfragen("openfang", "/api/agents")
    laufend = sum(1 for a in liste or [] if a.get("state") == "Running")
    # Zaehlung als Beleg: Nachfrage zaehlt dieselbe Liste neu (feld #running).
    s.fakt("agents_laufend", "openfang", "/api/agents", "#running", laufend)
    return s.dokument("pc_zustand", PC_ZUSTAND_ID, PC_ZUSTAND_TITEL)


def user(jetzt: datetime) -> Dokument:
    s = Sammler(jetzt)
    ck = "flowzen_checkins?select=mood,energy,created_at&order=created_at.desc&limit=1"
    rows, _, _ = abfragen("supabase", ck)
    if rows:
        s.fakt("stimmung", "supabase", ck, "mood", rows[0].get("mood"))
        s.fakt("energie", "supabase", ck, "energy", rows[0].get("energy"))
        s.fakt("checkin_zeit", "supabase", ck, "created_at", rows[0].get("created_at"))
    ch = "conversation_history?select=id&limit=1"
    _, gesamt, _ = abfragen("supabase", ch, mit_zaehlung=True)
    s.fakt("gespraechszeilen", "supabase", ch, "#count", gesamt)
    neu, _, _ = abfragen("supabase", "ideas?parent_id=is.null&select=id,title"
                         "&order=updated_at.desc&limit=5")
    links = [f"{r.get('title') or r['id']} ({r['id'][:6]})" for r in neu or []]
    return s.dokument("user", "user", "User", links)


def alle(jetzt: datetime) -> List[Dokument]:
    out: List[Dokument] = []
    for name, leser in (("bubbles", bubbles), ("coding_projekte", coding_projekte),
                        ("agents", agents)):
        try:
            out += leser(jetzt)
        except RuntimeError as e:
            FEHLER[name] += 1
            logger.warning("[wissen] Quelle %s ausgefallen: %s", name, e)
    for name, leser in (("pc_zustand", pc_zustand), ("user", user)):
        try:
            d = leser(jetzt)
            if d is not None:
                out.append(d)
        except RuntimeError as e:
            FEHLER[name] += 1
            logger.warning("[wissen] Quelle %s ausgefallen: %s", name, e)
    return out
