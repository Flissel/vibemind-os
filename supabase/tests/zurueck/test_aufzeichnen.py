import psycopg
import pytest

from conftest import als_agent, als_hand, protokoll
from hilfen import bubble, idee, shuttle


def test_anlegen_wird_aufgezeichnet(db):
    with als_agent(db, "plan-1"):
        idee(db, "n1", None, "Erste Idee")
    (zeile,) = protokoll(db)
    assert zeile["vorgang"] == "INSERT"
    assert zeile["tabelle"] == "public.canvas_nodes"
    assert zeile["zeilen_id"] == "n1"
    assert zeile["vorher"] is None
    assert zeile["nachher"]["title"] == "Erste Idee"


def test_aendern_haelt_alten_und_neuen_stand(db):
    with als_hand(db):
        idee(db, "n1", None, "Alt")
    with als_agent(db, "plan-1"):
        db.execute("UPDATE public.canvas_nodes SET title = 'Neu' WHERE id = 'n1'")
    (zeile,) = protokoll(db, "vorgang = 'UPDATE'")
    assert zeile["vorher"]["title"] == "Alt"
    assert zeile["nachher"]["title"] == "Neu"


def test_loeschen_haelt_alten_stand(db):
    with als_hand(db):
        idee(db, "n1", None, "Bleibt im Protokoll", "Inhalt")
    with als_agent(db, "plan-1"):
        db.execute("DELETE FROM public.canvas_nodes WHERE id = 'n1'")
    (zeile,) = protokoll(db, "vorgang = 'DELETE'")
    assert zeile["vorher"]["content"] == "Inhalt"
    assert zeile["nachher"] is None


def test_kopf_macht_agent_und_gruppe(db):
    with als_agent(db, "plan-1"):
        bubble(db, "b1")
    (zeile,) = protokoll(db)
    assert (zeile["quelle"], zeile["gruppe"]) == ("agent", "plan-1")


def test_ohne_kopf_ist_hand_mit_eigener_gruppe(db):
    with als_hand(db):
        bubble(db, "b1")
    with als_hand(db):
        bubble(db, "b2")
    erste, zweite = protokoll(db)
    assert erste["quelle"] == zweite["quelle"] == "hand"
    assert erste["gruppe"] != zweite["gruppe"]


def test_kaputter_kopf_gilt_als_hand_und_bricht_nichts(db):
    with db.transaction():
        db.execute("SELECT set_config('request.headers', 'kein json', true)")
        bubble(db, "b1")
    (zeile,) = protokoll(db)
    assert zeile["quelle"] == "hand"


def test_nur_technische_spalten_wird_nicht_aufgezeichnet(db):
    with als_hand(db):
        idee(db, "n1", None)
        bubble(db, "b1")
    vorher = len(protokoll(db))
    with als_agent(db, "abgleich"):
        db.execute("UPDATE public.canvas_nodes SET last_synced_at = now() WHERE id = 'n1'")
        db.execute("UPDATE public.ideas SET last_synced_at = now(), embedding_hash = 'x' WHERE id = 'b1'")
    assert len(protokoll(db)) == vorher


def test_loeschweitergabe_elternteil_steht_vor_den_kindern(db):
    # Gemessenes Verhalten von Postgres, auf das sich ideen_zurueck einstellt:
    # die Ausloeser der mitgeloeschten Kinder laufen nach denen des Elternteils.
    with als_hand(db):
        bubble(db, "b1")
        shuttle(db, "s1", "b1")
    with als_agent(db, "plan-1"):
        db.execute("DELETE FROM public.ideas WHERE id = 'b1'")
    zeilen = protokoll(db, "gruppe = 'plan-1'")
    nach_tabelle = {z["tabelle"]: z for z in zeilen}
    assert nach_tabelle["public.shuttles"]["vorgang"] == "DELETE"
    assert nach_tabelle["public.ideas"]["id"] < nach_tabelle["public.shuttles"]["id"]


def test_verweis_auf_leer_wird_als_aenderung_aufgezeichnet(db):
    with als_hand(db):
        bubble(db, "b1")
        idee(db, "n1", "b1")
    with als_agent(db, "plan-1"):
        db.execute("DELETE FROM public.ideas WHERE id = 'b1'")
    (zeile,) = protokoll(db, "gruppe = 'plan-1' AND tabelle = 'public.canvas_nodes'")
    assert zeile["vorgang"] == "UPDATE"
    assert zeile["vorher"]["linked_idea_id"] == "b1"
    assert zeile["nachher"]["linked_idea_id"] is None


def test_anon_schreibt_fachtabelle_und_wird_aufgezeichnet(db):
    with als_agent(db, "plan-anon", rolle="anon"):
        bubble(db, "b1")
    assert len(protokoll(db, "gruppe = 'plan-anon'")) == 1


@pytest.mark.parametrize("rolle", ["anon", "authenticated"])
def test_anon_darf_protokoll_weder_lesen_noch_schreiben(db, rolle):
    with als_hand(db):
        bubble(db, "b1")
    for sql in (
        "SELECT * FROM public.ideen_aenderungen",
        "DELETE FROM public.ideen_aenderungen",
        "UPDATE public.ideen_aenderungen SET quelle = 'hand'",
        "INSERT INTO public.ideen_aenderungen (gruppe, quelle, tabelle, vorgang, zeilen_id) "
        "VALUES ('x', 'agent', 'public.ideas', 'INSERT', 'x')",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with als_hand(db, rolle=rolle):
                db.execute(sql)
