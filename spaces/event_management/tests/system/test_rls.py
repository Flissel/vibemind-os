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

# Bot-Tabellen aus 004 mit RLS-Grants fuer event_app (Ruling S4). schluessel, marke und
# bestaetigung haben gar keine Grants (test_schluessel.py).
BOT_TABELLEN = ("verbindung", "bereitstellung")

# Mandanten-Tabellen mit RLS: ohne Kontext 0 Zeilen. person hat RLS, ist aber
# ohne Kontext lesbar (Login), migration hat keine RLS.
RLS_TABELLEN = (
    "firma", "mitgliedschaft", "event", "kontakt", "orga_team", "teilnehmende",
    "aufgabe_kategorie", "aufgabe", "ablauf", "budget_posten", "verlauf", "einladung",
    *NEUE_TABELLEN, *BOT_TABELLEN,
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


# --- Bot-Tabellen verbindung und bereitstellung (004, Ruling S4) ----------------------

CODE_HASH = hashlib.sha256(b"ABCD2345").hexdigest()
CHAT_GRUPPE = -1001234567890
CHAT_DIREKT = 123456789


def _verbindung(conn: psycopg.Connection[dict[str, Any]], person: object, event_id: object = None,
                firma: object = None, code: str = "ABCD2345") -> Any:
    art = "gruppe" if event_id is not None else "direkt"
    return _id(conn,
               "insert into event.verbindung (art, code_hash, event_id, firma_id, person_id,"
               " ablauf) values (%s, %s, %s, %s, %s, now() + interval '15 minutes') returning id",
               (art, hashlib.sha256(code.encode("utf-8")).hexdigest(), event_id, firma, person))


def test_verbindung_mandantenmatrix(welt, db_app, db_admin):
    """Gruppen-Zeile von Firma A (Event e1): wie die Bausteine, aber ohne delete."""
    def gruppe(conn: psycopg.Connection[dict[str, Any]], code: str) -> Any:
        return _verbindung(conn, welt["person"], welt["e1"], welt["a"], code)

    zeile = gruppe(db_admin, "ABCD2345")
    vorher = db_admin.execute("select * from event.verbindung order by id").fetchall()
    verwerfen = "update event.verbindung set status = 'verworfen' where id = %s"

    fremd = db_app(firmen=[welt["b"]])
    assert fremd.execute("select * from event.verbindung where id = %s", (zeile,)).fetchall() == []
    assert _anzahl(fremd, "verbindung") == 0
    assert fremd.execute(verwerfen, (zeile,)).rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        gruppe(fremd, "FREMD234")
    assert db_admin.execute("select * from event.verbindung order by id").fetchall() == vorher

    gebunden = db_app(firmen=[welt["a"]], event=welt["e2"])
    assert _anzahl(gebunden, "verbindung") == 0
    assert gebunden.execute(verwerfen, (zeile,)).rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        gruppe(gebunden, "GEBUN234")

    eigen = db_app(firmen=[welt["a"]])
    assert [z["id"] for z in eigen.execute("select id from event.verbindung").fetchall()] == [
        zeile]
    assert eigen.execute(verwerfen, (zeile,)).rowcount == 1
    # ... verschiebt die Zeile aber nicht in Firma B (with check).
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        eigen.execute("update event.verbindung set firma_id = %s, event_id = %s where id = %s",
                      (welt["b"], welt["eb"], zeile))
    gruppe(eigen, "EIGEN234")
    assert _anzahl(eigen, "verbindung") == 2
    # Kein delete fuer event_app, auch nicht fuer Betreiber.
    for conn in (eigen, db_app(betreiber=True)):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("delete from event.verbindung where id = %s", (zeile,))
    assert _anzahl(db_app(betreiber=True), "verbindung") == 2


def test_verbindung_direktchat_nur_eigene_person(welt, db_app, db_admin):
    anna = welt["person"]
    bert = _id(db_admin, "insert into event.person (mail, name) values (%s, %s) returning id",
               ("bert@example.org", "Bert Beispiel"))
    zeile = _verbindung(db_admin, anna, code="ANNA2345")
    selbst = db_app(person=anna)
    assert [z["id"] for z in selbst.execute("select id from event.verbindung").fetchall()] == [
        zeile]
    _verbindung(selbst, anna, code="ANNA3456")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _verbindung(selbst, bert, code="BERT2345")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        selbst.execute("update event.verbindung set person_id = %s where id = %s", (bert, zeile))
    for andere in (db_app(person=bert), db_app(firmen=[welt["a"], welt["b"]]), db_app()):
        assert _anzahl(andere, "verbindung") == 0
    assert _anzahl(db_app(betreiber=True), "verbindung") == 2


def test_bot_tabellen_rechte_und_checks(welt, db_admin):
    for tabelle in BOT_TABELLEN:
        rechte = db_admin.execute(
            "select has_table_privilege('event_app', %(t)s, 'select') as s,"
            " has_table_privilege('event_app', %(t)s, 'insert') as i,"
            " has_table_privilege('event_app', %(t)s, 'update') as u,"
            " has_table_privilege('event_app', %(t)s, 'delete') as d,"
            " has_table_privilege('public', %(t)s, 'select') as p",
            {"t": f"event.{tabelle}"}).fetchone()
        assert rechte == {"s": True, "i": True, "u": tabelle == "verbindung", "d": False,
                          "p": False}, tabelle
    # Gruppe braucht Event und Firma (zusammengesetzter FK), Direktchat keins von beiden.
    with pytest.raises(psycopg.errors.CheckViolation):
        db_admin.execute(
            "insert into event.verbindung (art, code_hash, person_id, ablauf)"
            " values ('gruppe', %s, %s, now())", (CODE_HASH, welt["person"]))
    with pytest.raises(psycopg.errors.CheckViolation):
        db_admin.execute(
            "insert into event.verbindung (art, code_hash, event_id, firma_id, person_id, ablauf)"
            " values ('direkt', %s, %s, %s, %s, now())",
            (CODE_HASH, welt["e1"], welt["a"], welt["person"]))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _verbindung(db_admin, welt["person"], welt["e1"], welt["b"])
    with pytest.raises(psycopg.errors.CheckViolation):  # Klartext-Code statt Hash
        db_admin.execute(
            "insert into event.verbindung (art, code_hash, person_id, ablauf)"
            " values ('direkt', 'ABCD2345', %s, now())", (welt["person"],))
    # Agent-ID nur nach Ruling S5; Gruppe braucht ein Event, Direktchat eine Person.
    for ziel, event_id, person, agent in (
        ("gruppe", welt["e1"], None, "ev-../../x"),
        ("gruppe", None, None, "ev-12345678"),
        ("leitung", None, None, "ld-12345678"),
        ("betreiber", welt["e1"], welt["person"], "bo-12345678"),
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            db_admin.execute(
                "insert into event.bereitstellung (art, ziel, event_id, person_id, chat_id,"
                " agent_id) values ('anlegen', %s, %s, %s, %s, %s)",
                (ziel, event_id, person, CHAT_GRUPPE, agent))


def test_passwort_hash_fuer_event_app_unlesbar(welt, db_app, db_admin):
    db_admin.execute("update event.person set passwort_hash = 'scrypt$geheim' where id = %s",
                     (welt["person"],))
    for conn in (db_app(), db_app(betreiber=True), db_app(person=welt["person"])):
        for abfrage in (
            "select passwort_hash from event.person",
            "select * from event.person",
            "select id from event.person where passwort_hash is null",
            "select id from event.person where passwort_hash like 'scrypt%'",
            "select p from event.person p",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(abfrage)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("update event.person set name = name where id = %s"
                         " returning passwort_hash", (welt["person"],))
    # Die freigegebenen Spalten bleiben lesbar.
    zeile = db_app().execute(
        "select id, mail, name, betreiber, aktiv, sitzungs_version, angelegt from event.person"
        " where id = %s", (welt["person"],)).fetchone()
    assert zeile["mail"] == "anna@example.org"
    # Der Hash kommt nur ueber die Login-Funktion heraus.
    holen = db_app().execute("select * from event.anmeldung_holen(%s)",
                             ("ANNA@example.org",)).fetchall()
    assert holen == [{"id": welt["person"], "passwort_hash": "scrypt$geheim", "aktiv": True}]
    for leer in ("niemand@example.org", "", None):
        assert db_app().execute("select * from event.anmeldung_holen(%s)",
                                (leer,)).fetchall() == []
    assert db_app().execute("select event.hat_passwort(%s) as h",
                            (welt["person"],)).fetchone()["h"] is True


def test_passwort_setzen_wenn_leer(welt, db_app, db_admin):
    anna = welt["person"]
    bert = _id(db_admin, "insert into event.person (mail, name) values (%s, %s) returning id",
               ("bert@example.org", "Bert Beispiel"))

    def setzen(conn: psycopg.Connection[dict[str, Any]], person: object, h: str) -> bool:
        return conn.execute("select event.passwort_setzen_wenn_leer(%s, %s, %s) as ok",
                            (person, h, "Neuer Name")).fetchone()["ok"]

    assert db_app().execute("select event.hat_passwort(%s) as h",
                            (anna,)).fetchone()["h"] is False
    # Nur die eigene Person (oder Betreiber), nur wenn leer, nur ein scrypt-Hash.
    assert setzen(db_app(), anna, "scrypt$ohne-kontext") is False
    assert setzen(db_app(person=bert), anna, "scrypt$fremd") is False
    assert setzen(db_app(firmen=[welt["a"], welt["b"]]), anna, "scrypt$mandant") is False
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        setzen(db_app(person=anna), anna, "klartext")
    assert setzen(db_app(person=anna), anna, "scrypt$erst") is True
    assert setzen(db_app(person=anna), anna, "scrypt$zweit") is False
    assert setzen(db_app(betreiber=True), anna, "scrypt$betreiber") is False
    db_admin.execute("update event.person set aktiv = false where id = %s", (bert,))
    assert setzen(db_app(betreiber=True), bert, "scrypt$inaktiv") is False
    zeilen = {z["id"]: (z["passwort_hash"], z["name"]) for z in db_admin.execute(
        "select id, passwort_hash, name from event.person").fetchall()}
    assert zeilen == {anna: ("scrypt$erst", "Neuer Name"), bert: (None, "Bert Beispiel")}


@pytest.fixture
def bot_welt(welt: dict[str, Any], db_admin: psycopg.Connection[dict[str, Any]]) -> dict[str, Any]:
    """welt plus Leitung Lena (Firma A), Mitglied Mia (Firma A) und Betreiber Chef.
    Anna ist nur Mitglied in B, also nirgends Leitung."""
    def person(mail: str, name: str, betreiber: bool = False) -> Any:
        return _id(db_admin, "insert into event.person (mail, name, betreiber)"
                   " values (%s, %s, %s) returning id", (mail, name, betreiber))

    lena = person("lena@example.org", "Lena Beispiel")
    mia = person("mia@example.org", "Mia Beispiel")
    chef = person("chef@example.org", "Chef Beispiel", betreiber=True)
    db_admin.execute("insert into event.mitgliedschaft values (%s, %s, 'leitung')",
                     (lena, welt["a"]))
    db_admin.execute("insert into event.mitgliedschaft values (%s, %s, 'mitglied')",
                     (mia, welt["a"]))
    return {**welt, "lena": lena, "mia": mia, "chef": chef}


def _auftrag(conn: psycopg.Connection[dict[str, Any]], ziel: str, event_id: object = None,
             person: object = None) -> Any:
    praefix = {"gruppe": "ev", "leitung": "ld", "betreiber": "bo"}[ziel]
    quelle = event_id if ziel == "gruppe" else person
    return _id(conn,
               "insert into event.bereitstellung (art, ziel, event_id, person_id, chat_id,"
               " agent_id) values ('anlegen', %s, %s, %s, %s, %s) returning id",
               (ziel, event_id, person, CHAT_GRUPPE if ziel == "gruppe" else CHAT_DIREKT,
                f"{praefix}-" + str(quelle).replace("-", "")[:8]))


def test_bereitstellung_lesen_mandant_und_person(bot_welt, db_app, db_admin):
    w = bot_welt
    gruppe = _auftrag(db_admin, "gruppe", event_id=w["e1"])
    direkt = _auftrag(db_admin, "leitung", person=w["lena"])

    def ids(conn: psycopg.Connection[dict[str, Any]]) -> set[Any]:
        return {z["id"] for z in conn.execute("select id from event.bereitstellung").fetchall()}

    assert ids(db_app(firmen=[w["a"]])) == {gruppe}          # Mitglied oder Leitung der Firma
    assert ids(db_app(firmen=[w["b"]], person=w["person"])) == set()
    assert ids(db_app(firmen=[w["a"]], event=w["e2"])) == set()
    assert ids(db_app(person=w["lena"])) == {direkt}
    assert ids(db_app(person=w["mia"])) == set()
    assert ids(db_app()) == set()
    assert ids(db_app(betreiber=True)) == {gruppe, direkt}


def test_bereitstellung_anlegen_nur_berechtigt(bot_welt, db_app, db_admin):
    w = bot_welt
    leitung_a = db_app(firmen=[w["a"]], person=w["lena"])
    mitglied_a = db_app(firmen=[w["a"]], person=w["mia"])
    anna_b = db_app(firmen=[w["b"]], person=w["person"])

    def abgelehnt(conn: psycopg.Connection[dict[str, Any]], ziel: str, **werte: object) -> None:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _auftrag(conn, ziel, **werte)

    # gruppe: nur Leitung der Firma des Events.
    abgelehnt(mitglied_a, "gruppe", event_id=w["e1"])           # Mitglied reicht nicht
    abgelehnt(db_app(firmen=[w["a"]]), "gruppe", event_id=w["e1"])  # Firma ohne Person
    abgelehnt(anna_b, "gruppe", event_id=w["e1"])               # fremde Firma
    abgelehnt(anna_b, "gruppe", event_id=w["eb"])               # Mitglied in B
    abgelehnt(leitung_a, "gruppe", event_id=w["eb"])            # Leitung, aber nicht in B
    abgelehnt(db_app(firmen=[w["a"]], person=w["lena"], event=w["e2"]), "gruppe",
              event_id=w["e1"])                                 # Event-Bindung
    # leitung: nur fuer sich selbst und nur mit einer aktuellen Leitung.
    abgelehnt(db_app(person=w["mia"]), "leitung", person=w["mia"])
    abgelehnt(anna_b, "leitung", person=w["person"])
    abgelehnt(leitung_a, "leitung", person=w["mia"])
    # betreiber: nur im Betreiber-Kontext, auch nicht fuer Leitung oder die eigene Person.
    abgelehnt(leitung_a, "betreiber", person=w["lena"])
    abgelehnt(db_app(person=w["chef"]), "betreiber", person=w["chef"])
    assert db_admin.execute("select count(*) as n from event.bereitstellung").fetchone()["n"] == 0

    # Positivkontrollen.
    _auftrag(leitung_a, "gruppe", event_id=w["e1"])
    _auftrag(db_app(person=w["lena"]), "leitung", person=w["lena"])
    _auftrag(db_app(betreiber=True, person=w["chef"]), "betreiber", person=w["chef"])
    assert db_admin.execute("select count(*) as n from event.bereitstellung").fetchone()["n"] == 3
    # Nach Entzug der Leitung: kein neuer Direktchat-Auftrag mehr.
    db_admin.execute("update event.mitgliedschaft set rolle = 'mitglied' where person_id = %s",
                     (w["lena"],))
    abgelehnt(db_app(person=w["lena"]), "leitung", person=w["lena"])
    abgelehnt(leitung_a, "gruppe", event_id=w["e1"])


def test_bereitstellung_nicht_aenderbar_fuer_event_app(bot_welt, db_app, db_admin):
    """Auftraege aendert nur der Arbeiter mit der Admin-DSN (Task 8)."""
    w = bot_welt
    zeile = _auftrag(db_admin, "gruppe", event_id=w["e1"])
    for conn in (db_app(firmen=[w["a"]], person=w["lena"]), db_app(betreiber=True)):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("update event.bereitstellung set status = 'erfolg' where id = %s",
                         (zeile,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("delete from event.bereitstellung where id = %s", (zeile,))
    assert db_admin.execute("select status from event.bereitstellung where id = %s",
                            (zeile,)).fetchone()["status"] == "offen"
