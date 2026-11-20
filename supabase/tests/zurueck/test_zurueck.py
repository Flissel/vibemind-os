import psycopg
import pytest

from conftest import alle, als_agent, als_hand, eine, protokoll
from hilfen import bubble, erkundung, idee, kante, shuttle, zurueck


def _anzahl(db, tabelle: str) -> int:
    return eine(db, f"SELECT count(*) AS n FROM {tabelle}")["n"]


def test_zurueck_loescht_angelegte_idee(db):
    with als_agent(db, "plan-1"):
        idee(db, "n1", None, "Neu")
    ergebnis = zurueck(db)
    assert ergebnis["gruppe"] == "plan-1"
    assert ergebnis["anzahl"] == 1
    assert ergebnis["eintraege"][0]["titel"] == "Neu"
    assert _anzahl(db, "public.canvas_nodes") == 0


def test_zurueck_setzt_geaenderte_werte_zurueck(db):
    with als_hand(db):
        idee(db, "n1", None, "Alt", "alter Inhalt")
    with als_agent(db, "plan-1"):
        db.execute("UPDATE public.canvas_nodes SET title = 'Neu', content = 'neuer Inhalt', x = 5 WHERE id = 'n1'")
    zurueck(db)
    zeile = eine(db, "SELECT title, content, x FROM public.canvas_nodes WHERE id = 'n1'")
    assert (zeile["title"], zeile["content"], zeile["x"]) == ("Alt", "alter Inhalt", 0.0)


def test_zurueck_legt_geloeschte_idee_mit_kanten_wieder_an(db):
    with als_hand(db):
        idee(db, "n1", None, "Eins", "Inhalt eins")
        idee(db, "n2", None, "Zwei")
        kante(db, "k1", "n1", "n2")
        kante(db, "k2", "n2", "n1")
    with als_agent(db, "plan-1"):
        db.execute("DELETE FROM public.canvas_nodes WHERE id = 'n1'")
    assert _anzahl(db, "public.canvas_edges") == 0
    ergebnis = zurueck(db)
    assert ergebnis["anzahl"] == 3
    assert eine(db, "SELECT content FROM public.canvas_nodes WHERE id = 'n1'")["content"] == "Inhalt eins"
    kanten = alle(db, "SELECT id, from_node_id, to_node_id FROM public.canvas_edges ORDER BY id")
    assert [(k["id"], k["from_node_id"], k["to_node_id"]) for k in kanten] == [
        ("k1", "n1", "n2"),
        ("k2", "n2", "n1"),
    ]


def test_zurueck_stellt_bubble_mit_allem_was_daran_haengt_wieder_her(db):
    with als_hand(db):
        bubble(db, "b1", "Grosse Bubble")
        bubble(db, "b2", "Kind")
        db.execute("UPDATE public.ideas SET parent_id = 'b1' WHERE id = 'b2'")
        idee(db, "n1", "b1", "Idee an der Bubble")
        shuttle(db, "s1", "b1")
        erkundung(db, "e1", "b1")
        db.execute("INSERT INTO public.discovered_edges (id, from_idea_id, to_idea_id) VALUES ('d1', 'b1', 'b2')")
        db.execute("INSERT INTO public.projects (id, name, from_idea_id) VALUES ('p1', 'Projekt', 'b1')")
    with als_agent(db, "plan-1"):
        db.execute("DELETE FROM public.ideas WHERE id = 'b1'")
    assert _anzahl(db, "public.shuttles") == 0
    assert _anzahl(db, "public.exploration_sessions") == 0
    assert _anzahl(db, "public.discovered_edges") == 0

    zurueck(db)

    assert eine(db, "SELECT title FROM public.ideas WHERE id = 'b1'")["title"] == "Grosse Bubble"
    assert eine(db, "SELECT bubble_id FROM public.shuttles WHERE id = 's1'")["bubble_id"] == "b1"
    assert eine(db, "SELECT root_bubble_id FROM public.exploration_sessions WHERE id = 'e1'")["root_bubble_id"] == "b1"
    assert eine(db, "SELECT from_idea_id FROM public.discovered_edges WHERE id = 'd1'")["from_idea_id"] == "b1"
    assert eine(db, "SELECT linked_idea_id FROM public.canvas_nodes WHERE id = 'n1'")["linked_idea_id"] == "b1"
    assert eine(db, "SELECT parent_id FROM public.ideas WHERE id = 'b2'")["parent_id"] == "b1"
    assert eine(db, "SELECT from_idea_id FROM public.projects WHERE id = 'p1'")["from_idea_id"] == "b1"


def test_plan_mit_drei_schritten_ist_eine_aenderung(db):
    with als_hand(db):
        bubble(db, "b1")
    # drei Anfragen, also drei Transaktionen, mit derselben Plan-Kennung
    with als_agent(db, "plan-1"):
        idee(db, "n1", "b1", "Eins")
    with als_agent(db, "plan-1"):
        idee(db, "n2", "b1", "Zwei")
    with als_agent(db, "plan-1"):
        kante(db, "k1", "n1", "n2")
    ergebnis = zurueck(db)
    assert ergebnis["anzahl"] == 3
    assert _anzahl(db, "public.canvas_nodes") == 0
    assert _anzahl(db, "public.canvas_edges") == 0
    assert _anzahl(db, "public.ideas") == 1


def test_zweimal_zurueck_nimmt_zwei_gruppen(db):
    with als_agent(db, "plan-1"):
        idee(db, "n1", None, "Eins")
    with als_agent(db, "plan-2"):
        idee(db, "n2", None, "Zwei")
    assert zurueck(db)["gruppe"] == "plan-2"
    assert zurueck(db)["gruppe"] == "plan-1"
    assert _anzahl(db, "public.canvas_nodes") == 0


def test_ohne_offene_gruppe_kommt_null_zurueck(db):
    with als_agent(db, "plan-1"):
        idee(db, "n1", None)
    zurueck(db)
    ergebnis = zurueck(db)
    assert ergebnis["gruppe"] is None
    assert ergebnis["anzahl"] == 0


def test_zurueck_laesst_handaenderungen_stehen(db):
    with als_agent(db, "plan-1"):
        idee(db, "n1", None, "vom Agenten")
    with als_hand(db):
        idee(db, "n2", None, "von Hand")
    assert zurueck(db)["gruppe"] == "plan-1"
    assert [z["id"] for z in alle(db, "SELECT id FROM public.canvas_nodes")] == ["n2"]
    assert zurueck(db)["gruppe"] is None


def test_zurueck_mit_gruppe_nimmt_auch_handaenderung(db):
    with als_hand(db):
        idee(db, "n1", None, "von Hand")
    gruppe = protokoll(db)[0]["gruppe"]
    assert zurueck(db, gruppe)["gruppe"] == gruppe
    assert _anzahl(db, "public.canvas_nodes") == 0


def test_unbekannte_gruppe_wird_abgelehnt(db):
    with pytest.raises(psycopg.errors.RaiseException, match="aenderung_unbekannt"):
        zurueck(db, "gibt-es-nicht")


def test_nachgeschriebenes_last_synced_at_ist_kein_konflikt(db):
    with als_hand(db):
        idee(db, "n1", None, "Alt")
    with als_agent(db, "plan-1"):
        db.execute("UPDATE public.canvas_nodes SET title = 'Neu' WHERE id = 'n1'")
    # der Abgleich mit Rowboat schreibt seinen Zeitstempel nach
    with als_hand(db):
        db.execute("UPDATE public.canvas_nodes SET last_synced_at = now() WHERE id = 'n1'")
    zurueck(db)
    assert eine(db, "SELECT title FROM public.canvas_nodes WHERE id = 'n1'")["title"] == "Alt"


def test_zweitaenderung_bricht_ganz_ab(db):
    with als_hand(db):
        idee(db, "n1", None, "Alt eins")
        idee(db, "n2", None, "Alt zwei")
    with als_agent(db, "plan-1"):
        db.execute("UPDATE public.canvas_nodes SET title = 'Neu eins' WHERE id = 'n1'")
        db.execute("UPDATE public.canvas_nodes SET title = 'Neu zwei' WHERE id = 'n2'")
    with als_hand(db):
        db.execute("UPDATE public.canvas_nodes SET title = 'von Hand' WHERE id = 'n1'")
    vorher = protokoll(db)
    with pytest.raises(psycopg.errors.RaiseException, match="aenderung_konflikt: public.canvas_nodes n1"):
        zurueck(db)
    # nichts halb zurueckgenommen: n2 steht noch auf dem neuen Stand, Protokoll unveraendert
    assert eine(db, "SELECT title FROM public.canvas_nodes WHERE id = 'n2'")["title"] == "Neu zwei"
    assert eine(db, "SELECT title FROM public.canvas_nodes WHERE id = 'n1'")["title"] == "von Hand"
    assert protokoll(db) == vorher


def test_geloeschte_zeile_die_wieder_da_ist_bricht_ab(db):
    with als_hand(db):
        idee(db, "n1", None, "Alt")
    with als_agent(db, "plan-1"):
        db.execute("DELETE FROM public.canvas_nodes WHERE id = 'n1'")
    with als_hand(db):
        idee(db, "n1", None, "neu von Hand")
    with pytest.raises(psycopg.errors.RaiseException, match="aenderung_konflikt"):
        zurueck(db)
    assert eine(db, "SELECT title FROM public.canvas_nodes WHERE id = 'n1'")["title"] == "neu von Hand"


def test_zurueck_reisst_nichts_spaeter_angelegtes_mit(db):
    # Der Agent legt eine Bubble an. Danach haengt jemand von Hand ein Shuttle daran.
    # Die Bubble zu loeschen wuerde das Shuttle mitloeschen: also abbrechen.
    with als_agent(db, "plan-1"):
        bubble(db, "b1")
    with als_hand(db):
        shuttle(db, "s1", "b1")
    with pytest.raises(psycopg.errors.RaiseException, match="aenderung_konflikt: public.shuttles s1"):
        zurueck(db)
    assert _anzahl(db, "public.ideas") == 1
    assert _anzahl(db, "public.shuttles") == 1


def test_zuruecknahme_wird_aufgezeichnet_und_nimmt_sich_nie_selbst_zurueck(db):
    with als_agent(db, "plan-1"):
        idee(db, "n1", None, "Eins")
    zurueck(db)
    spur = protokoll(db, "quelle = 'zurueck'")
    assert [(z["gruppe"], z["vorgang"]) for z in spur] == [("zurueck:plan-1", "DELETE")]
    assert protokoll(db, "gruppe = 'plan-1'")[0]["zurueckgenommen_am"] is not None
    # noch einmal "zurueck" fasst die Zuruecknahme nicht an
    assert zurueck(db)["gruppe"] is None
    assert _anzahl(db, "public.canvas_nodes") == 0


def test_nach_zurueck_zeichnet_derselbe_vorgang_wieder_normal_auf(db):
    with als_agent(db, "plan-1"):
        idee(db, "n1", None)
    with db.transaction():
        zurueck(db)
        idee(db, "n2", None, "gleiche Transaktion, nach der Zuruecknahme")
    (zeile,) = protokoll(db, "zeilen_id = 'n2'")
    assert zeile["quelle"] == "hand"


def test_embedding_spalten_kommen_mit_dem_alten_stand_zurueck(db):
    with als_hand(db):
        bubble(db, "b1", "Alt")
        db.execute("UPDATE public.ideas SET embedding_hash = 'h-alt', embedding_vector = '[1,2]' WHERE id = 'b1'")
    with als_agent(db, "plan-1"):
        db.execute("UPDATE public.ideas SET title = 'Neu', embedding_hash = 'h-neu', embedding_vector = '[3,4]' WHERE id = 'b1'")
    zurueck(db)
    zeile = eine(db, "SELECT title, embedding_hash, embedding_vector FROM public.ideas WHERE id = 'b1'")
    assert (zeile["title"], zeile["embedding_hash"], zeile["embedding_vector"]) == ("Alt", "h-alt", "[1,2]")


@pytest.mark.parametrize("rolle", ["anon", "authenticated"])
def test_anon_darf_ideen_zurueck_nicht_aufrufen(db, rolle):
    with als_agent(db, "plan-1"):
        idee(db, "n1", None)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with als_hand(db, rolle=rolle):
            db.execute("SELECT public.ideen_zurueck()")
    assert _anzahl(db, "public.canvas_nodes") == 1


def test_service_role_darf_ideen_zurueck_aufrufen(db):
    with als_agent(db, "plan-1"):
        idee(db, "n1", None)
    with als_hand(db, rolle="service_role"):
        db.execute("SELECT public.ideen_zurueck()")
    assert _anzahl(db, "public.canvas_nodes") == 0


def test_idee_im_selben_plan_geaendert_und_bubble_geloescht(db):
    # Dieselbe Zeile zweimal in einer Gruppe: erst vom Agenten geaendert, dann durch
    # das Loeschen der Bubble auf "ohne Bubble" gesetzt. Beides muss zurueck.
    with als_hand(db):
        bubble(db, "b1")
        idee(db, "n1", "b1", "Alt")
    with als_agent(db, "plan-1"):
        db.execute("UPDATE public.canvas_nodes SET title = 'Neu' WHERE id = 'n1'")
        db.execute("DELETE FROM public.ideas WHERE id = 'b1'")
    zurueck(db)
    zeile = eine(db, "SELECT title, linked_idea_id FROM public.canvas_nodes WHERE id = 'n1'")
    assert (zeile["title"], zeile["linked_idea_id"]) == ("Alt", "b1")


def test_verweis_ins_leere_bricht_ab_statt_endlos_zu_laufen(db):
    # Das Shuttle wurde geloescht; seine Bubble ist danach von Hand entfernt worden.
    with als_hand(db):
        bubble(db, "b1")
        shuttle(db, "s1", "b1")
    with als_agent(db, "plan-1"):
        db.execute("DELETE FROM public.shuttles WHERE id = 's1'")
    with als_hand(db):
        db.execute("DELETE FROM public.ideas WHERE id = 'b1'")
    with pytest.raises(psycopg.errors.RaiseException, match="aenderung_konflikt: public.shuttles s1"):
        zurueck(db)
