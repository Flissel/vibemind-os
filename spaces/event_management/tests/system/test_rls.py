"""RLS als zweites Mandanten-Tor (002_rls.sql), gegen den Wegwerf-Cluster.

Testdaten legt der Admin an (Superuser umgeht RLS). Die Pruefungen laufen
ueber `db_app(...)` als `event_app_test` (Mitglied von `event_app`).
"""
from __future__ import annotations

import hashlib
from typing import Any

import psycopg
import pytest

TOKEN = "beispiel-token-nur-fuer-tests"
TOKEN_HASH = hashlib.sha256(TOKEN.encode("utf-8")).hexdigest()

# Neue Tabellen aus 003 (Zusatz-Bausteine und nachricht): Pflichtspalten einer gueltigen
# Zeile ausser event_id/firma_id. "<kontakt>"/"<team>" stehen fuer Zeilen derselben Firma
# bzw. desselben Events.
NEUE_TABELLEN: dict[str, dict[str, object]] = {
    "wettbewerb_team": {"name": "Team Beispiel"},
    "abgabe": {"team_id": "<team>", "titel": "Prototyp"},
    "jury": {"kontakt_id": "<kontakt>"},
    "aussteller": {"name": "Beispiel GmbH"},
    "stand": {"bezeichnung": "A1"},
    "sprecher": {"kontakt_id": "<kontakt>"},
    "online_raum": {"titel": "Hauptraum", "link": "https://meet.example.org/raum"},
    "aufzeichnung": {"titel": "Keynote"},
    "protokoll_punkt": {"punkt": "Begrüßung"},
    "beschluss": {"text": "Budget freigegeben"},
    "verpflegung": {"titel": "Mittagessen"},
    "programm_punkt": {"titel": "Eröffnung"},
    "helferschicht": {"titel": "Einlass", "beginn": "2027-06-01T10:00:00+00:00",
                      "ende": "2027-06-01T12:00:00+00:00"},
    "buehne": {"name": "Hauptbühne"},
    "nachricht": {"kanal": "gruppe", "text": "Hallo zusammen"},
}

# Mandanten-Tabellen mit RLS: ohne Kontext 0 Zeilen. person hat RLS, ist aber
# ohne Kontext lesbar (Login), migration hat keine RLS.
RLS_TABELLEN = (
    "firma", "mitgliedschaft", "event", "kontakt", "orga_team", "teilnehmende",
    "aufgabe_kategorie", "aufgabe", "ablauf", "budget_posten", "verlauf", "einladung",
    *NEUE_TABELLEN,
)


def _id(conn: psycopg.Connection[dict[str, Any]], abfrage: str, werte: tuple[object, ...]) -> Any:
    return conn.execute(abfrage, werte).fetchone()["id"]


@pytest.fixture
def welt(db_admin: psycopg.Connection[dict[str, Any]]) -> dict[str, Any]:
    """Firmen A und B mit Events, Kontakten, Aufgaben, Verlauf, Einladung (als Admin)."""
    a = _id(db_admin, "insert into event.firma (name, kurz) values (%s, %s) returning id",
            ("Beispiel GmbH", "firma-a"))
    b = _id(db_admin, "insert into event.firma (name, kurz) values (%s, %s) returning id",
            ("Muster AG", "firma-b"))

    def event(firma: object, name: str) -> Any:
        return _id(db_admin,
                   "insert into event.event (firma_id, kategorie, name, beginn, ende)"
                   " values (%s, 'fest', %s, now(), now()) returning id", (firma, name))

    def kontakt(firma: object, name: str) -> Any:
        return _id(db_admin,
                   "insert into event.kontakt (firma_id, name) values (%s, %s) returning id",
                   (firma, name))

    def aufgabe(event_id: object, firma: object, titel: str) -> Any:
        return _id(db_admin,
                   "insert into event.aufgabe (event_id, firma_id, titel)"
                   " values (%s, %s, %s) returning id", (event_id, firma, titel))

    e1 = event(a, "Sommerfest")
    e2 = event(a, "Winterfest")
    eb = event(b, "Hoffest")
    person = _id(db_admin,
                 "insert into event.person (mail, name) values (%s, %s) returning id",
                 ("anna@example.org", "Anna Beispiel"))
    db_admin.execute(
        "insert into event.mitgliedschaft (person_id, firma_id, rolle) values (%s, %s, 'mitglied')",
        (person, b),
    )
    welt = {
        "a": a, "b": b, "e1": e1, "e2": e2, "eb": eb, "person": person,
        "kontakt_a": kontakt(a, "Alma Beispiel"),
        "kontakt_b": kontakt(b, "Bea Beispiel"),
        "aufgabe_a1": aufgabe(e1, a, "Buehne aufbauen"),
        "aufgabe_a2": aufgabe(e2, a, "Glühwein bestellen"),
        "aufgabe_b": aufgabe(eb, b, "Zelt mieten"),
    }
    db_admin.execute(
        "insert into event.verlauf (event_id, firma_id, actor, quelle, baustein, aktion)"
        " values (%s, %s, 'test', 'test', 'aufgabe', 'angelegt')", (e1, a),
    )
    welt["einladung"] = _id(
        db_admin,
        "insert into event.einladung (person_id, firma_id, rolle, token_hash, gueltig_bis)"
        " values (%s, %s, 'mitglied', %s, now() + interval '1 day') returning id",
        (person, a, TOKEN_HASH),
    )
    return welt


def _anzahl(conn: psycopg.Connection[dict[str, Any]], tabelle: str) -> int:
    return int(conn.execute(f"select count(*) as n from event.{tabelle}").fetchone()["n"])


def _neue_aufgabe(conn: psycopg.Connection[dict[str, Any]], event_id: object, firma: object) -> None:
    conn.execute(
        "insert into event.aufgabe (event_id, firma_id, titel) values (%s, %s, 'Neu')",
        (event_id, firma),
    )


def _neue_zeile(conn: psycopg.Connection[dict[str, Any]], welt: dict[str, Any], tabelle: str,
                event_id: object, firma: object, team: object = None) -> Any:
    werte = {
        spalte: welt["kontakt_a"] if wert == "<kontakt>" else team if wert == "<team>" else wert
        for spalte, wert in NEUE_TABELLEN[tabelle].items()
    }
    spalten = ["event_id", "firma_id", *werte]
    return _id(conn,
               f"insert into event.{tabelle} ({', '.join(spalten)})"
               f" values ({', '.join(['%s'] * len(spalten))}) returning id",
               (event_id, firma, *werte.values()))


@pytest.mark.parametrize("tabelle", sorted(NEUE_TABELLEN))
def test_neue_tabellen_mandantenmatrix(welt, db_app, db_admin, tabelle):
    """Zeile von Firma A (Event e1): Firma B sieht/aendert/loescht sie nicht und schreibt
    nicht in A; das gewaehlte Event e2 blendet sie aus; Firma A selbst arbeitet normal."""
    team = None
    if tabelle == "abgabe":
        team = _id(db_admin, "insert into event.wettbewerb_team (event_id, firma_id, name)"
                   " values (%s, %s, 'Team A') returning id", (welt["e1"], welt["a"]))
    zeile = _neue_zeile(db_admin, welt, tabelle, welt["e1"], welt["a"], team)
    vorher = db_admin.execute(f"select * from event.{tabelle} order by id").fetchall()

    fremd = db_app(firmen=[welt["b"]])
    assert fremd.execute(f"select * from event.{tabelle} where id = %s", (zeile,)).fetchall() == []
    assert _anzahl(fremd, tabelle) == 0
    assert fremd.execute(
        f"update event.{tabelle} set angelegt = now() where id = %s", (zeile,)).rowcount == 0
    if tabelle == "nachricht":  # kein delete-Recht fuer event_app
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            fremd.execute("delete from event.nachricht where id = %s", (zeile,))
    else:
        assert fremd.execute(
            f"delete from event.{tabelle} where id = %s", (zeile,)).rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _neue_zeile(fremd, welt, tabelle, welt["e1"], welt["a"], team)
    assert db_admin.execute(f"select * from event.{tabelle} order by id").fetchall() == vorher

    # Event-Bindung: mit e2 gewaehlt ist die Zeile aus e1 unsichtbar und unerreichbar.
    gebunden = db_app(firmen=[welt["a"]], event=welt["e2"])
    assert _anzahl(gebunden, tabelle) == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _neue_zeile(gebunden, welt, tabelle, welt["e1"], welt["a"], team)

    # Positivkontrolle: Firma A sieht, aendert und legt an.
    eigen = db_app(firmen=[welt["a"]])
    assert [z["id"] for z in eigen.execute(f"select id from event.{tabelle}").fetchall()] == [
        zeile]
    assert eigen.execute(
        f"update event.{tabelle} set angelegt = now() where id = %s", (zeile,)).rowcount == 1
    # ... verschiebt die Zeile aber nicht in Firma B (with check).
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        eigen.execute(f"update event.{tabelle} set firma_id = %s, event_id = %s where id = %s",
                      (welt["b"], welt["eb"], zeile))
    _neue_zeile(eigen, welt, tabelle, welt["e1"], welt["a"], team)
    assert _anzahl(eigen, tabelle) == 2
    # Betreiber sieht alles.
    assert _anzahl(db_app(betreiber=True), tabelle) == 2


def test_neue_tabellen_rechte(db_admin):
    """Grants an event_app: alles ausser delete auf nachricht; PUBLIC hat nichts."""
    for tabelle in NEUE_TABELLEN:
        rechte = db_admin.execute(
            "select has_table_privilege('event_app', %(t)s, 'select') as s,"
            " has_table_privilege('event_app', %(t)s, 'insert') as i,"
            " has_table_privilege('event_app', %(t)s, 'update') as u,"
            " has_table_privilege('event_app', %(t)s, 'delete') as d,"
            " has_table_privilege('public', %(t)s, 'select') as p",
            {"t": f"event.{tabelle}"}).fetchone()
        assert rechte == {"s": True, "i": True, "u": True, "d": tabelle != "nachricht",
                          "p": False}, tabelle


def test_ohne_kontext_keine_zeilen(welt, db_app):
    conn = db_app()
    for tabelle in ("event", "aufgabe", "kontakt"):
        assert _anzahl(conn, tabelle) == 0, tabelle
    # Fail-closed auf allen RLS-Tabellen, nicht nur den drei aus dem Brief.
    for tabelle in RLS_TABELLEN:
        assert _anzahl(conn, tabelle) == 0, tabelle
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _neue_aufgabe(conn, welt["e1"], welt["a"])


def test_leerer_kontext_ist_kein_kontext(welt, db_app):
    # Einstellungen auf '' (so bleiben sie nach einer lokalen set_config-Transaktion).
    conn = db_app(firmen=[])
    for name in ("event.betreiber", "event.person", "event.event"):
        conn.execute("select set_config(%s, '', false)", (name,))
    zeile = conn.execute(
        "select event.firmen() as firmen, event.ist_betreiber() as betreiber,"
        " event.person_id() as person, event.aktuelles_event() as event"
    ).fetchone()
    assert zeile == {"firmen": [], "betreiber": False, "person": None, "event": None}
    for tabelle in RLS_TABELLEN:
        assert _anzahl(conn, tabelle) == 0, tabelle

    ohne = db_app().execute(
        "select event.firmen() as firmen, event.ist_betreiber() as betreiber,"
        " event.person_id() as person, event.aktuelles_event() as event"
    ).fetchone()
    assert ohne == {"firmen": [], "betreiber": False, "person": None, "event": None}


def test_kaputter_firmen_wert_oeffnet_nichts(welt, db_app):
    conn = db_app(firmen=["kein-uuid"])
    try:
        n = _anzahl(conn, "aufgabe")
    except psycopg.errors.InvalidTextRepresentation:
        return
    assert n == 0


def test_firma_a_sieht_b_nicht(welt, db_app):
    conn = db_app(firmen=[welt["a"]])
    assert conn.execute(
        "select * from event.aufgabe where id = %s", (welt["aufgabe_b"],)
    ).fetchall() == []
    assert _anzahl(conn, "aufgabe") == 2
    assert conn.execute(
        "update event.aufgabe set titel = 'Gekapert' where id = %s", (welt["aufgabe_b"],)
    ).rowcount == 0
    assert conn.execute(
        "delete from event.aufgabe where id = %s", (welt["aufgabe_b"],)
    ).rowcount == 0
    assert [z["id"] for z in conn.execute("select id from event.firma").fetchall()] == [welt["a"]]


def test_firma_a_kann_nicht_in_b_schreiben(welt, db_app):
    conn = db_app(firmen=[welt["a"]])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _neue_aufgabe(conn, welt["eb"], welt["b"])
    # Positivkontrolle: in die eigene Firma geht es.
    _neue_aufgabe(conn, welt["e1"], welt["a"])


def test_zeile_nicht_in_fremde_firma_verschiebbar(welt, db_app, db_admin):
    conn = db_app(firmen=[welt["a"]])
    # Positivkontrolle: die eigene Zeile ist sicht- und aenderbar.
    assert conn.execute(
        "update event.kontakt set name = 'Alma Muster' where id = %s", (welt["kontakt_a"],)
    ).rowcount == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(
            "update event.aufgabe set firma_id = %s where id = %s",
            (welt["b"], welt["aufgabe_a1"]),
        )
    # Kontakt hat keine abhaengigen FKs: hier haelt nur das with check.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(
            "update event.kontakt set firma_id = %s where id = %s",
            (welt["b"], welt["kontakt_a"]),
        )
    assert db_admin.execute(
        "select firma_id from event.kontakt where id = %s", (welt["kontakt_a"],)
    ).fetchone()["firma_id"] == welt["a"]


def test_event_bindung(welt, db_app):
    conn = db_app(firmen=[welt["a"]], event=welt["e1"])
    assert conn.execute(
        "select * from event.aufgabe where id = %s", (welt["aufgabe_a2"],)
    ).fetchall() == []
    assert [z["id"] for z in conn.execute("select id from event.aufgabe").fetchall()] == [
        welt["aufgabe_a1"]
    ]
    assert [z["id"] for z in conn.execute("select id from event.event").fetchall()] == [welt["e1"]]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _neue_aufgabe(conn, welt["e2"], welt["a"])


def test_betreiber_sieht_alles(welt, db_app):
    conn = db_app(betreiber=True)
    assert _anzahl(conn, "firma") == 2
    assert _anzahl(conn, "event") == 3
    assert _anzahl(conn, "kontakt") == 2
    assert _anzahl(conn, "aufgabe") == 3
    assert _anzahl(conn, "einladung") == 1
    assert _anzahl(conn, "verlauf") == 1


def test_kontakt_fremder_firma_nicht_verknuepfbar(welt, db_app):
    conn = db_app(betreiber=True)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "insert into event.orga_team (event_id, firma_id, kontakt_id) values (%s, %s, %s)",
            (welt["e1"], welt["a"], welt["kontakt_b"]),
        )


def test_verlauf_unveraenderlich(welt, db_app):
    conn = db_app(betreiber=True)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("update event.verlauf set aktion = 'geaendert'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("delete from event.verlauf")
    # Anfuegen geht (Sequenz-Recht fuer bigserial).
    conn.execute(
        "insert into event.verlauf (event_id, firma_id, actor, quelle, baustein, aktion)"
        " values (%s, %s, 'test', 'test', 'aufgabe', 'geaendert')", (welt["e1"], welt["a"]),
    )
    assert _anzahl(conn, "verlauf") == 2


def test_einladung_holen_nur_mit_hash(welt, db_app):
    conn = db_app()
    assert conn.execute("select * from event.einladung").fetchall() == []
    treffer = conn.execute("select * from event.einladung_holen(%s)", (TOKEN_HASH,)).fetchall()
    assert len(treffer) == 1
    assert treffer[0]["id"] == welt["einladung"]
    assert treffer[0]["firma_id"] == welt["a"]
    assert treffer[0]["firma_name"] == "Beispiel GmbH"
    assert treffer[0]["rolle"] == "mitglied"
    falsch = hashlib.sha256(b"falsches-token").hexdigest()
    assert conn.execute("select * from event.einladung_holen(%s)", (falsch,)).fetchall() == []
    assert conn.execute("select * from event.einladung_holen(%s)", ("",)).fetchall() == []
    assert conn.execute("select * from event.einladung_holen(null)").fetchall() == []


def test_firma_anlegen_nur_als_betreiber(welt, db_app):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db_app(firmen=[welt["a"]]).execute(
            "insert into event.firma (name, kurz) values ('Neu GmbH', 'neu')"
        )
    db_app(betreiber=True).execute(
        "insert into event.firma (name, kurz) values ('Neu GmbH', 'neu')"
    )


def test_person_lesbar_ohne_kontext_aber_nicht_loeschbar(welt, db_app):
    conn = db_app()
    # Login sucht per Mail und prueft das Cookie, bevor es einen Kontext gibt.
    assert conn.execute(
        "select id from event.person where lower(mail) = %s", ("anna@example.org",)
    ).fetchone()["id"] == welt["person"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("delete from event.person")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select * from event.migration")


def test_person_anlegen_nur_mail_und_name(welt, db_app, db_admin):
    conn = db_app()
    # Positivkontrolle: (mail, name) geht ohne Kontext.
    neu = conn.execute(
        "insert into event.person (mail, name) values ('neu@example.org', 'Nora Beispiel')"
        " returning id"
    ).fetchone()["id"]
    assert db_admin.execute(
        "select betreiber, passwort_hash from event.person where id = %s", (neu,)
    ).fetchone() == {"betreiber": False, "passwort_hash": None}
    for betreiber_conn in (conn, db_app(betreiber=True)):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            betreiber_conn.execute(
                "insert into event.person (mail, name, betreiber)"
                " values ('chef@example.org', 'Chef Beispiel', true)"
            )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(
            "insert into event.person (mail, name, passwort_hash)"
            " values ('hash@example.org', 'Hanna Beispiel', 'x')"
        )
    # Zweites Netz hinter dem Spaltenrecht: die Policy verbietet betreiber = true.
    assert db_admin.execute(
        "select with_check from pg_policies where schemaname = 'event'"
        " and tablename = 'person' and policyname = 'person_anlegen'"
    ).fetchone()["with_check"] == "(NOT betreiber)"


def test_person_aendern_nur_selbst_oder_betreiber(welt, db_app, db_admin):
    anna = welt["person"]
    chef = db_admin.execute(
        "insert into event.person (mail, name, passwort_hash, betreiber)"
        " values ('chef@example.org', 'Chef Beispiel', 'alt', true) returning id"
    ).fetchone()["id"]

    def hash_setzen(conn, ziel, wert):
        return conn.execute(
            "update event.person set passwort_hash = %s where id = %s", (wert, ziel)
        ).rowcount

    selbst = db_app(person=anna)
    assert hash_setzen(selbst, chef, "gekapert") == 0
    assert hash_setzen(selbst, anna, "anna-neu") == 1
    assert hash_setzen(db_app(), anna, "ohne-kontext") == 0
    assert hash_setzen(db_app(), chef, "ohne-kontext") == 0
    assert hash_setzen(db_app(firmen=[welt["a"], welt["b"]]), chef, "mandant") == 0
    assert hash_setzen(db_app(betreiber=True), chef, "chef-neu") == 1
    hashes = {
        z["id"]: z["passwort_hash"]
        for z in db_admin.execute("select id, passwort_hash from event.person").fetchall()
    }
    assert hashes == {anna: "anna-neu", chef: "chef-neu"}
    # Betreiber-Flag bleibt fuer event_app unaenderbar, auch fuer die eigene Zeile.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        selbst.execute("update event.person set betreiber = true where id = %s", (anna,))


def test_firma_aendern_und_loeschen_nur_betreiber(welt, db_app, db_admin):
    mandant = db_app(firmen=[welt["a"]])
    # Positivkontrolle: die eigene Firma ist sichtbar.
    assert [z["id"] for z in mandant.execute("select id from event.firma").fetchall()] == [
        welt["a"]
    ]
    assert mandant.execute(
        "update event.firma set name = 'Umbenannt GmbH' where id = %s", (welt["a"],)
    ).rowcount == 0
    assert mandant.execute("delete from event.firma where id = %s", (welt["a"],)).rowcount == 0

    leer = db_admin.execute(
        "insert into event.firma (name, kurz) values ('Leer GmbH', 'leer') returning id"
    ).fetchone()["id"]
    betreiber = db_app(betreiber=True)
    assert betreiber.execute(
        "update event.firma set name = 'Beispiel Neu GmbH' where id = %s", (welt["a"],)
    ).rowcount == 1
    assert betreiber.execute("delete from event.firma where id = %s", (leer,)).rowcount == 1
    assert db_admin.execute(
        "select name from event.firma where id = %s", (welt["a"],)
    ).fetchone()["name"] == "Beispiel Neu GmbH"


def test_verlauf_nicht_in_fremde_firma(welt, db_app):
    conn = db_app(firmen=[welt["a"]])
    eintrag = (
        "insert into event.verlauf (event_id, firma_id, actor, quelle, baustein, aktion)"
        " values (%s, %s, 'test', 'test', 'aufgabe', 'angelegt')"
    )
    conn.execute(eintrag, (welt["e1"], welt["a"]))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(eintrag, (welt["eb"], welt["b"]))


def test_mitgliedschaft_eigene_sichtbar_aber_nicht_selbst_anlegbar(welt, db_app):
    conn = db_app(person=welt["person"])
    zeilen = conn.execute("select firma_id from event.mitgliedschaft").fetchall()
    assert [z["firma_id"] for z in zeilen] == [welt["b"]]
    # Die eigene person_id oeffnet nur das Lesen, nicht das Beitreten.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(
            "insert into event.mitgliedschaft (person_id, firma_id, rolle)"
            " values (%s, %s, 'leitung')", (welt["person"], welt["a"]),
        )
    assert conn.execute(
        "update event.mitgliedschaft set rolle = 'leitung' where person_id = %s",
        (welt["person"],),
    ).rowcount == 0


def test_public_hat_keine_rechte(db_admin):
    zeile = db_admin.execute(
        "select has_schema_privilege('public', 'event', 'usage') as schema,"
        " has_function_privilege('public', 'event.einladung_holen(text)', 'execute') as holen,"
        " has_function_privilege('public', 'event.mandant_ok(uuid)', 'execute') as mandant,"
        " has_function_privilege('event_app', 'event.einladung_holen(text)', 'execute') as app"
    ).fetchone()
    assert zeile == {"schema": False, "holen": False, "mandant": False, "app": True}
    sicherheit = db_admin.execute(
        "select p.prosecdef, p.proconfig, p.provolatile from pg_proc p"
        " join pg_namespace n on n.oid = p.pronamespace"
        " where n.nspname = 'event' and p.proname = 'einladung_holen'"
    ).fetchone()
    assert sicherheit["prosecdef"] is True
    assert sicherheit["proconfig"] == ["search_path=event, pg_temp"]
    invoker = db_admin.execute(
        "select p.proname, p.prosecdef, p.provolatile from pg_proc p"
        " join pg_namespace n on n.oid = p.pronamespace"
        " where n.nspname = 'event' and p.proname in"
        " ('firmen', 'ist_betreiber', 'person_id', 'aktuelles_event', 'mandant_ok', 'event_ok')"
        " order by p.proname"
    ).fetchall()
    assert len(invoker) == 6
    assert all(z["prosecdef"] is False and z["provolatile"] == "s" for z in invoker)
    ohne_rls = db_admin.execute(
        "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace"
        " where n.nspname = 'event' and c.relkind = 'r' and not c.relrowsecurity"
        " order by c.relname"
    ).fetchall()
    assert [z["relname"] for z in ohne_rls] == ["migration"]
