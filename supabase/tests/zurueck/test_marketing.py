"""Marketing-Tabellen: Zurueck stellt dort nur den Verweis auf die Bubble wieder her.

Im Betrieb haengen an ``status`` Ausloeser, die bei ``approved`` den Versand anstossen.
Eine Zuruecknahme darf deshalb nie einen Status zurueckschreiben.
"""
import psycopg
import pytest

from conftest import als_agent, als_hand, eine
from hilfen import bubble, zurueck


def _vorschlag(db, status: str = "draft", bubble_id: str | None = None) -> str:
    cur = db.execute(
        "INSERT INTO marketing.broadcast_proposals (channel, status, draft_body_text, created_by, "
        "approved_at, approved_by, bubble_id) VALUES ('telegram', %s, 'Text', 'test', "
        "CASE WHEN %s = 'approved' THEN now() END, CASE WHEN %s = 'approved' THEN 'felix' END, %s) "
        "RETURNING id::text",
        (status, status, status, bubble_id),
    )
    return cur.fetchone()[0]


def _status_waechter(db) -> None:
    """Stellvertreter fuer die Versand-Ausloeser des Betriebs: zaehlt jedes UPDATE,
    das die Spalte status ueberhaupt nennt."""
    db.execute("CREATE TABLE marketing.status_beruehrt (n int)")
    db.execute(
        "CREATE FUNCTION marketing.status_merken() RETURNS trigger LANGUAGE plpgsql AS "
        "$f$ BEGIN INSERT INTO marketing.status_beruehrt VALUES (1); RETURN NEW; END $f$"
    )
    db.execute(
        "CREATE TRIGGER status_merken AFTER UPDATE OF status ON marketing.broadcast_proposals "
        "FOR EACH ROW EXECUTE FUNCTION marketing.status_merken()"
    )


def _beruehrt(db) -> int:
    return eine(db, "SELECT count(*) AS n FROM marketing.status_beruehrt")["n"]


def test_verweis_kommt_zurueck_ohne_den_status_anzufassen(db):
    _status_waechter(db)
    with als_hand(db):
        bubble(db, "b1")
        vid = _vorschlag(db, "approved", "b1")
    with als_agent(db, "plan-1"):
        db.execute("DELETE FROM public.ideas WHERE id = 'b1'")
    assert eine(db, "SELECT bubble_id FROM marketing.broadcast_proposals WHERE id = %s", (vid,))["bubble_id"] is None

    zurueck(db)

    zeile = eine(db, "SELECT bubble_id, status FROM marketing.broadcast_proposals WHERE id = %s", (vid,))
    assert (zeile["bubble_id"], zeile["status"]) == ("b1", "approved")
    assert _beruehrt(db) == 0


def test_verweis_kommt_zurueck_auch_wenn_der_status_inzwischen_weiter_ist(db):
    with als_hand(db):
        bubble(db, "b1")
        vid = _vorschlag(db, "draft", "b1")
    with als_agent(db, "plan-1"):
        db.execute("DELETE FROM public.ideas WHERE id = 'b1'")
    with als_hand(db):
        db.execute("UPDATE marketing.broadcast_proposals SET status = 'rejected', rejected_at = now(), "
                   "rejected_by = 'felix' WHERE id = %s", (vid,))

    zurueck(db)

    zeile = eine(db, "SELECT bubble_id, status FROM marketing.broadcast_proposals WHERE id = %s", (vid,))
    assert (zeile["bubble_id"], zeile["status"]) == ("b1", "rejected")


def test_verweis_inzwischen_neu_gesetzt_bricht_ab(db):
    with als_hand(db):
        bubble(db, "b1")
        bubble(db, "b2")
        vid = _vorschlag(db, "draft", "b1")
    with als_agent(db, "plan-1"):
        db.execute("DELETE FROM public.ideas WHERE id = 'b1'")
    with als_hand(db):
        db.execute("UPDATE marketing.broadcast_proposals SET bubble_id = 'b2' WHERE id = %s", (vid,))
    with pytest.raises(psycopg.errors.RaiseException, match="aenderung_konflikt"):
        zurueck(db)
    assert eine(db, "SELECT count(*) AS n FROM public.ideas WHERE id = 'b1'")["n"] == 0


def test_statusaenderung_eines_agenten_ist_nicht_zuruecknehmbar(db):
    _status_waechter(db)
    with als_hand(db):
        vid = _vorschlag(db, "approved")
    with als_agent(db, "plan-1"):
        db.execute("UPDATE marketing.broadcast_proposals SET status = 'rejected', rejected_at = now(), "
                   "rejected_by = 'agent' WHERE id = %s", (vid,))
    vorher = _beruehrt(db)
    with pytest.raises(psycopg.errors.RaiseException, match="aenderung_nicht_zuruecknehmbar"):
        zurueck(db)
    assert eine(db, "SELECT status FROM marketing.broadcast_proposals WHERE id = %s", (vid,))["status"] == "rejected"
    assert _beruehrt(db) == vorher


def test_angelegter_vorschlag_ist_nicht_zuruecknehmbar_und_der_rest_bleibt_stehen(db):
    with als_agent(db, "plan-1"):
        bubble(db, "b1")
        vid = _vorschlag(db, "draft", "b1")
    with pytest.raises(psycopg.errors.RaiseException, match="aenderung_nicht_zuruecknehmbar"):
        zurueck(db)
    assert eine(db, "SELECT count(*) AS n FROM public.ideas WHERE id = 'b1'")["n"] == 1
    assert eine(db, "SELECT count(*) AS n FROM marketing.broadcast_proposals WHERE id = %s", (vid,))["n"] == 1


def test_geloeschter_vorschlag_ist_nicht_zuruecknehmbar(db):
    with als_hand(db):
        vid = _vorschlag(db, "approved")
    with als_agent(db, "plan-1"):
        db.execute("DELETE FROM marketing.broadcast_proposals WHERE id = %s", (vid,))
    with pytest.raises(psycopg.errors.RaiseException, match="aenderung_nicht_zuruecknehmbar"):
        zurueck(db)


def test_sammelauftrag_bekommt_den_verweis_ebenfalls_zurueck(db):
    with als_hand(db):
        bubble(db, "b1")
        sid = db.execute(
            "INSERT INTO marketing.crowdfunding_batches (bubble_id, channel, created_by) "
            "VALUES ('b1', 'telegram', 'test') RETURNING id::text"
        ).fetchone()[0]
    with als_agent(db, "plan-1"):
        db.execute("DELETE FROM public.ideas WHERE id = 'b1'")
    zurueck(db)
    assert eine(db, "SELECT bubble_id FROM marketing.crowdfunding_batches WHERE id = %s", (sid,))["bubble_id"] == "b1"
