import psycopg
import pytest

from conftest import MIGRATION, RUECKWEG, UEBERWACHT, als_agent, als_hand, eine, protokoll
from hilfen import bubble, idee, zurueck


def test_alle_zwoelf_tabellen_tragen_den_ausloeser(db):
    for tabelle in UEBERWACHT:
        n = eine(
            db,
            "SELECT count(*) AS n FROM pg_trigger WHERE tgrelid = %s::regclass AND tgname = 'zz_aenderung'",
            (tabelle,),
        )["n"]
        assert n == 1, tabelle


def test_migration_ist_zweimal_ausfuehrbar(db):
    with als_agent(db, "plan-1"):
        idee(db, "n1", None)
    db.execute(MIGRATION.read_text(encoding="utf-8"))
    assert len(protokoll(db)) == 1
    with als_agent(db, "plan-2"):
        idee(db, "n2", None)
    # ein Ausloeser je Tabelle, nicht zwei: genau eine neue Zeile
    assert len(protokoll(db)) == 2


def test_aufraeumen_loescht_nur_aeltere_zeilen(db):
    with als_agent(db, "alt"):
        idee(db, "n1", None)
    with als_agent(db, "neu"):
        idee(db, "n2", None)
    db.execute("UPDATE public.ideen_aenderungen SET erstellt_am = now() - interval '31 days' WHERE gruppe = 'alt'")
    assert db.execute("SELECT public.ideen_aenderungen_aufraeumen()").fetchone()[0] == 1
    assert [z["gruppe"] for z in protokoll(db)] == ["neu"]


def test_aufraeumen_lehnt_null_tage_ab(db):
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("SELECT public.ideen_aenderungen_aufraeumen(0)")


def test_anon_darf_nicht_aufraeumen(db):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with als_hand(db, rolle="anon"):
            db.execute("SELECT public.ideen_aenderungen_aufraeumen()")


def test_rueckweg_entfernt_alles_und_tabellen_arbeiten_weiter(db):
    with als_agent(db, "plan-1"):
        bubble(db, "b1")
    db.execute(RUECKWEG.read_text(encoding="utf-8"))
    assert eine(db, "SELECT to_regclass('public.ideen_aenderungen') AS t")["t"] is None
    assert eine(db, "SELECT count(*) AS n FROM pg_trigger WHERE tgname = 'zz_aenderung'")["n"] == 0
    assert eine(db, "SELECT count(*) AS n FROM pg_proc WHERE proname LIKE 'ideen\\_%%'")["n"] == 0
    with als_agent(db, "plan-2"):
        bubble(db, "b2")
        db.execute("UPDATE public.ideas SET title = 'geht' WHERE id = 'b2'")
        db.execute("DELETE FROM public.ideas WHERE id = 'b1'")
    assert eine(db, "SELECT title FROM public.ideas WHERE id = 'b2'")["title"] == "geht"
    # und wieder einspielbar
    db.execute(MIGRATION.read_text(encoding="utf-8"))
    with als_agent(db, "plan-3"):
        idee(db, "n1", None)
    assert zurueck(db)["gruppe"] == "plan-3"


def test_fehlende_tabelle_bricht_die_migration_ab(db_roh):
    db_roh.execute("DROP TABLE public.shuttles CASCADE")
    with pytest.raises(psycopg.errors.RaiseException, match="public.shuttles fehlt"):
        db_roh.execute(MIGRATION.read_text(encoding="utf-8"))
