"""Tests fuer Migrationslauf und Schema `event` (gegen den Wegwerf-Cluster)."""
from pathlib import Path

import psycopg
import pytest

from spaces.event_management.db import anwenden


def test_migrationen_sortiert_und_eindeutig(tmp_path: Path):
    liste = anwenden.migrationen()
    nummern = [nr for nr, _, _ in liste]
    assert nummern == sorted(nummern)
    assert len(nummern) == len(set(nummern))
    assert liste[0][:2] == (1, "001_schema.sql")

    (tmp_path / "001_a.sql").write_text("select 1;", encoding="utf-8")
    (tmp_path / "001_b.sql").write_text("select 1;", encoding="utf-8")
    with pytest.raises(ValueError):
        anwenden.migrationen(tmp_path)


def test_anwenden_ist_wiederholbar(db_admin):
    # db_admin hat bereits alle Migrationen angewendet.
    assert anwenden.anwenden(db_admin) == []
    zeilen = db_admin.execute("select nr, name from event.migration order by nr").fetchall()
    assert (1, "001_schema.sql") in [(z["nr"], z["name"]) for z in zeilen]


def _firma(conn, kurz="beispiel"):
    return conn.execute(
        "insert into event.firma (name, kurz) values (%s, %s) returning id",
        ("Beispiel GmbH", kurz),
    ).fetchone()["id"]


def test_ende_vor_beginn_abgelehnt(db_admin):
    firma_id = _firma(db_admin)
    with pytest.raises(psycopg.errors.CheckViolation):
        db_admin.execute(
            "insert into event.event (firma_id, kategorie, name, beginn, ende)"
            " values (%s, 'fest', 'Sommerfest', '2026-07-02 18:00+02', '2026-07-02 17:00+02')",
            (firma_id,),
        )


def test_firmenkuerzel_format(db_admin):
    with pytest.raises(psycopg.errors.CheckViolation):
        _firma(db_admin, kurz="Beispiel GmbH")


def test_mail_eindeutig_ohne_gross_klein(db_admin):
    db_admin.execute(
        "insert into event.person (mail, name) values ('A@example.org', 'Anna Beispiel')"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        db_admin.execute(
            "insert into event.person (mail, name) values ('a@example.org', 'Anton Beispiel')"
        )


def test_baustein_nur_mit_event_und_kontakt_derselben_firma(db_admin):
    firma_a = _firma(db_admin, "firma-a")
    firma_b = _firma(db_admin, "firma-b")
    event_a = db_admin.execute(
        "insert into event.event (firma_id, kategorie, name, beginn, ende)"
        " values (%s, 'fest', 'Sommerfest', now(), now()) returning id",
        (firma_a,),
    ).fetchone()["id"]
    kontakt_b = db_admin.execute(
        "insert into event.kontakt (firma_id, name) values (%s, 'Bea Beispiel') returning id",
        (firma_b,),
    ).fetchone()["id"]
    # Kontakt aus Firma B an Event aus Firma A: egal mit welcher firma_id.
    for firma_id in (firma_a, firma_b):
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            db_admin.execute(
                "insert into event.orga_team (event_id, firma_id, kontakt_id)"
                " values (%s, %s, %s)",
                (event_a, firma_id, kontakt_b),
            )


def test_db_app_setzt_nur_uebergebenen_kontext(db_app):
    def kontext(conn):
        return conn.execute(
            "select current_user as wer,"
            " pg_has_role('event_app', 'member') as mitglied,"
            " current_setting('event.firmen', true) as firmen,"
            " current_setting('event.betreiber', true) as betreiber,"
            " current_setting('event.person', true) as person,"
            " current_setting('event.event', true) as event"
        ).fetchone()

    leer = kontext(db_app())
    assert leer["wer"] == "event_app_test" and leer["mitglied"] is True
    assert [leer[k] for k in ("firmen", "betreiber", "person", "event")] == [None] * 4

    voll = kontext(db_app(firmen=["f1", "f2"], betreiber=True, person="p1", event="e1"))
    assert (voll["firmen"], voll["betreiber"], voll["person"], voll["event"]) == (
        "f1,f2", "an", "p1", "e1",
    )


def test_cli_wendet_an_und_zeigt_keinen_dsn(leere_db_dsn, tmp_path, monkeypatch, capsys):
    dsn_datei = tmp_path / "dsn.txt"
    dsn_datei.write_text(leere_db_dsn + "\r\n", encoding="utf-8")
    monkeypatch.setenv("EVENT_ADMIN_DSN_FILE", str(dsn_datei))
    monkeypatch.delenv("EVENT_ADMIN_DSN", raising=False)

    assert anwenden.main() == 0
    ausgabe = capsys.readouterr()
    assert ausgabe.out.split() == [name for _, name, _ in anwenden.migrationen()]
    assert anwenden.main() == 0
    zweite = capsys.readouterr()
    assert zweite.out == ""
    assert "127.0.0.1" not in ausgabe.out + ausgabe.err + zweite.err


def _event(conn, firma_id, name="Sommerfest"):
    return conn.execute(
        "insert into event.event (firma_id, kategorie, name, beginn, ende)"
        " values (%s, 'fest', %s, now(), now()) returning id",
        (firma_id, name),
    ).fetchone()["id"]


def _kategorie(conn, event_id, firma_id, name="Technik"):
    return conn.execute(
        "insert into event.aufgabe_kategorie (event_id, firma_id, name)"
        " values (%s, %s, %s) returning id",
        (event_id, firma_id, name),
    ).fetchone()["id"]


def _aufgabe(conn, event_id, firma_id, kategorie_id):
    return conn.execute(
        "insert into event.aufgabe (event_id, firma_id, titel, kategorie_id)"
        " values (%s, %s, 'Buehne aufbauen', %s) returning id",
        (event_id, firma_id, kategorie_id),
    ).fetchone()["id"]


def test_aufgabe_nur_mit_kategorie_desselben_events(db_admin):
    firma_a = _firma(db_admin, "firma-a")
    firma_b = _firma(db_admin, "firma-b")
    e1 = _event(db_admin, firma_a, "Sommerfest")
    e2 = _event(db_admin, firma_a, "Winterfest")
    e3 = _event(db_admin, firma_b, "Hoffest")
    kat_e2 = _kategorie(db_admin, e2, firma_a)
    kat_e3 = _kategorie(db_admin, e3, firma_b)

    # Kategorie eines anderen Events derselben Firma und einer fremden Firma.
    for fremde in (kat_e2, kat_e3):
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _aufgabe(db_admin, e1, firma_a, fremde)

    # Positivkontrolle: Kategorie desselben Events.
    kat_e1 = _kategorie(db_admin, e1, firma_a)
    aufgabe = _aufgabe(db_admin, e1, firma_a, kat_e1)

    # Loeschen der Kategorie leert nur kategorie_id, event_id bleibt.
    db_admin.execute("delete from event.aufgabe_kategorie where id = %s", (kat_e1,))
    zeile = db_admin.execute(
        "select event_id, kategorie_id from event.aufgabe where id = %s", (aufgabe,)
    ).fetchone()
    assert zeile == {"event_id": e1, "kategorie_id": None}

    # Event loeschen raeumt Kategorien und Aufgaben gemeinsam ab.
    _kategorie(db_admin, e1, firma_a, "Catering")
    db_admin.execute("delete from event.event where id = %s", (e1,))
    assert db_admin.execute(
        "select count(*) as n from event.aufgabe where event_id = %s", (e1,)
    ).fetchone()["n"] == 0


def _verlauf_event(db_admin):
    firma_id = _firma(db_admin)
    ev = db_admin.execute(
        "insert into event.event (firma_id, kategorie, name, beginn, ende)"
        " values (%s, 'fest', 'Sommerfest', now(), now()) returning id", (firma_id,),
    ).fetchone()["id"]
    return firma_id, ev


@pytest.mark.parametrize("quelle", ["oberflaeche", "system", "werkzeug_server", "a" * 32])
def test_verlauf_quelle_format_ok(db_admin, quelle):
    firma_id, ev = _verlauf_event(db_admin)
    db_admin.execute(
        "insert into event.verlauf (event_id, firma_id, actor, quelle, baustein, aktion)"
        " values (%s, %s, 'Erika', %s, 'event', 'angelegt')", (ev, firma_id, quelle))


@pytest.mark.parametrize("quelle", ["", "API", "ober-flaeche", "a" * 33, "x y", "quelle\n"])
def test_verlauf_quelle_format_abgelehnt(db_admin, quelle):
    firma_id, ev = _verlauf_event(db_admin)
    with pytest.raises(psycopg.errors.CheckViolation):
        db_admin.execute(
            "insert into event.verlauf (event_id, firma_id, actor, quelle, baustein, aktion)"
            " values (%s, %s, 'Erika', %s, 'event', 'angelegt')", (ev, firma_id, quelle))


def test_verlauf_actor_person_id_verweist_auf_person(db_admin):
    firma_id, ev = _verlauf_event(db_admin)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db_admin.execute(
            "insert into event.verlauf (event_id, firma_id, actor, quelle, baustein, aktion,"
            " actor_person_id) values (%s, %s, 'Erika', 'system', 'event', 'angelegt',"
            " gen_random_uuid())", (ev, firma_id))
    db_admin.execute(
        "insert into event.verlauf (event_id, firma_id, actor, quelle, baustein, aktion)"
        " values (%s, %s, 'System', 'system', 'event', 'angelegt')", (ev, firma_id))
    assert db_admin.execute("select actor_person_id from event.verlauf").fetchone()[
        "actor_person_id"] is None


def test_alle_migrationen_zweimal(db_admin):
    # Alle vorhandenen Dateien (nicht fest 001-009): zweiter Lauf aendert nichts.
    assert anwenden.anwenden(db_admin) == []
    assert anwenden.anwenden(db_admin) == []
    vermerkt = [z["nr"] for z in db_admin.execute(
        "select nr from event.migration order by nr").fetchall()]
    assert vermerkt == [nr for nr, _, _ in anwenden.migrationen()]


def test_abgebrochene_migration_hinterlaesst_nichts(db_admin, tmp_path):
    (tmp_path / "900_probe.sql").write_text(
        "create table event.probe_abbruch (id int);\n"
        "insert into event.probe_abbruch values (1);\n"
        "select 1 / 0;\n",
        encoding="utf-8",
    )
    with pytest.raises(psycopg.errors.DivisionByZero):
        anwenden.anwenden(db_admin, ordner=tmp_path)
    assert db_admin.execute(
        "select to_regclass('event.probe_abbruch') as t").fetchone()["t"] is None
    assert db_admin.execute(
        "select count(*) as n from event.migration where nr = 900").fetchone()["n"] == 0
