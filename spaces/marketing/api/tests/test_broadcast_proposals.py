"""Leseroute fuer die KAMPAGNEN-Entwuerfe (marketing.broadcast_proposals).

WARUM ES DIESE ROUTE BRAUCHT, obwohl "/api/proposals" schon existiert:
die gibt es, aber sie liest `marketing.audience_proposals` — WEN man
anschreibt, nicht WAS man schreibt. Zwei aehnlich benannte Tabellen, und
die vorhandene Route sieht aus wie ein fertiges Feature, liefert fuer die
sieben Telegram-Entwuerfe aber `0 proposal(s)` (gemessen 04.09.2026). Die
Oberflaeche zeigte deshalb nichts, und der Marketing-Agent konnte seine
eigenen frueheren Entwuerfe nicht lesen — ohne die schreibt er zum sechsten
Mal dieselbe Aufzaehlung.

Der Name der neuen Route nennt die Tabelle, damit die Verwechslung nicht
wiederkehrt.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

PKG_ROOT = next(p.parent for p in Path(__file__).resolve().parents if p.name == "spaces")
sys.path.insert(0, str(PKG_ROOT))

from spaces.marketing.tools import marketing_tools as mt  # noqa: E402


_FAILS: list[str] = []


def _check(label: str, cond: bool, detail: str = ""):
    mark = "PASS" if cond else "FAIL"
    line = f"  [{mark}] {label}"
    if detail and not cond:
        line += f"  -- {detail}"
    print(line)
    if not cond:
        _FAILS.append(label)


_ZEILEN = [{"id": "1", "channel": "telegram", "status": "draft",
            "draft_subject": "Early Access", "draft_body_text": "Text",
            "created_at": "2026-09-03"}]


def test_liest_die_richtige_tabelle():
    gesehen = {}

    def merken(sql):
        gesehen["sql"] = sql
        return _ZEILEN

    with mock.patch.object(mt._db, "query_via_docker", merken):
        r = mt.list_broadcast_proposals()
    _check("liest_broadcast_proposals",
           "marketing.broadcast_proposals" in gesehen.get("sql", ""),
           gesehen.get("sql", "")[:120])
    _check("nicht_audience_proposals",
           "audience_proposals" not in gesehen.get("sql", ""))
    _check("gibt_die_zeilen_zurueck", r["success"] and r["data"] == _ZEILEN)


def test_ohne_status_kommen_alle():
    gesehen = {}

    def merken(sql):
        gesehen["sql"] = sql
        return _ZEILEN

    with mock.patch.object(mt._db, "query_via_docker", merken):
        mt.list_broadcast_proposals(status=None)
    _check("ohne_status_kein_where", "WHERE" not in gesehen.get("sql", "").upper())


def test_status_wird_als_literal_eingesetzt():
    """Kein String-Einbau von Hand — derselbe Weg wie die Nachbarabfragen."""
    gesehen = {}

    def merken(sql):
        gesehen["sql"] = sql
        return []

    with mock.patch.object(mt._db, "query_via_docker", merken):
        mt.list_broadcast_proposals(status="dr'aft")
    sql = gesehen.get("sql", "")
    _check("status_maskiert", "dr''aft" in sql or "dr\'aft" in sql, sql[:160])


def test_kanal_laesst_sich_eingrenzen():
    gesehen = {}

    def merken(sql):
        gesehen["sql"] = sql
        return []

    with mock.patch.object(mt._db, "query_via_docker", merken):
        mt.list_broadcast_proposals(channel="telegram")
    _check("kanal_im_where", "channel" in gesehen.get("sql", ""))


def test_route_haengt_am_server():
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        _check("route_via_testclient", False, "fastapi testclient fehlt")
        return
    from spaces.marketing.api import server as srv

    with mock.patch.object(mt, "list_broadcast_proposals",
                           return_value={"success": True, "message": "1",
                                         "data": _ZEILEN}):
        client = TestClient(srv.app)
        r = client.get("/api/broadcast_proposals?status=draft")
    _check("route_200", r.status_code == 200, f"got {r.status_code}")
    _check("route_liefert_daten",
           r.status_code == 200 and r.json().get("data") == _ZEILEN)


def main() -> int:
    print("test_broadcast_proposals")
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if _FAILS:
        print(f"\nFAILED: {len(_FAILS)} -> {_FAILS}")
        return 1
    print("\nalle gruen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
