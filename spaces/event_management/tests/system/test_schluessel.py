"""Bot-Schluessel (system/schluessel.py, db/004_bot.sql) gegen den Wegwerf-Cluster.

Fachschicht ueber `db_admin` (ausstellen/widerrufen) und ueber `db_app()` (pruefen und
kontext_fuer laufen im Betrieb mit der App-DSN des MCP-Servers).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from spaces.event_management.system import events, firmen, schluessel
from spaces.event_management.system.kontext import Kontext, NichtGefunden

BETREIBER = Kontext(person_id=None, betreiber=True, rollen={})


def _firma(admin: Any, kurz: str) -> UUID:
    return admin.execute(
        "insert into event.firma (name, kurz) values (%s, %s) returning id",
        ("Beispiel " + kurz, kurz),
    ).fetchone()["id"]


def _event(admin: Any, firma: UUID, name: str = "Sommerfest") -> UUID:
    return admin.execute(
        "insert into event.event (firma_id, kategorie, name, beginn, ende)"
        " values (%s, 'fest', %s, now(), now()) returning id",
        (firma, name),
    ).fetchone()["id"]


def _person(admin: Any, mail: str, betreiber: bool = False) -> UUID:
    return admin.execute(
        "insert into event.person (mail, name, betreiber) values (%s, %s, %s) returning id",
        (mail, "Person " + mail.split("@")[0], betreiber),
    ).fetchone()["id"]


def _mitglied(admin: Any, person: UUID, firma: UUID, rolle: str) -> None:
    admin.execute("insert into event.mitgliedschaft values (%s, %s, %s)", (person, firma, rolle))


@pytest.fixture
def welt(db_admin: Any) -> dict[str, Any]:
    a = _firma(db_admin, "firma-a")
    b = _firma(db_admin, "firma-b")
    lea = _person(db_admin, "lea@example.org")
    zweite = _person(db_admin, "zweite@example.org")
    _mitglied(db_admin, lea, a, "leitung")
    _mitglied(db_admin, lea, b, "mitglied")
    _mitglied(db_admin, zweite, a, "leitung")
    return {"a": a, "b": b, "ea": _event(db_admin, a), "eb": _event(db_admin, b, "Hoffest"),
            "lea": lea, "zweite": zweite}


# --- Ausstellen ---------------------------------------------------------------------


def test_ausstellen_speichert_nur_hash(db_admin, welt):
    sid, klartext = schluessel.ausstellen(
        db_admin, art="event", bezeichnung="Gruppe Sommerfest", event_id=welt["ea"])
    assert isinstance(sid, UUID)
    assert klartext.startswith("evk_") and len(klartext) == 4 + 43
    token = klartext.removeprefix("evk_")
    zeile = db_admin.execute(
        "select row_to_json(s)::text as j, s.hash, s.firma_id, s.ablauf - s.angelegt as dauer"
        " from event.schluessel s where id = %s", (sid,)).fetchone()
    assert klartext not in zeile["j"] and token not in zeile["j"]
    assert zeile["hash"] == hashlib.sha256(klartext.encode("utf-8")).hexdigest()
    assert zeile["firma_id"] == welt["a"]  # aus dem Event abgeleitet
    assert zeile["dauer"].days == 365
    # Jeder Aufruf erzeugt einen neuen Schluessel.
    assert schluessel.ausstellen(db_admin, art="melden", bezeichnung="Empfang")[1] != klartext


def test_kombinationen(db_admin, welt):
    falsch: list[dict[str, Any]] = [
        {"art": "event", "bezeichnung": "x"},                                   # ohne Event
        {"art": "leitung", "bezeichnung": "x"},                                 # ohne Person
        {"art": "leitung", "bezeichnung": "x", "person_id": welt["lea"], "event_id": welt["ea"]},
        {"art": "betreiber", "bezeichnung": "x", "event_id": welt["ea"]},
        {"art": "betreiber_lesen", "bezeichnung": "x", "event_id": welt["ea"]},
        {"art": "melden", "bezeichnung": "x", "event_id": welt["ea"]},
        {"art": "chef", "bezeichnung": "x"},
        {"art": "melden", "bezeichnung": "   "},
        {"art": "melden", "bezeichnung": "x", "gueltig_tage": 731},
        {"art": "melden", "bezeichnung": "x", "gueltig_tage": 0},
        {"art": "melden", "bezeichnung": "x", "gueltig_tage": True},
    ]
    for argumente in falsch:
        with pytest.raises(ValueError):
            schluessel.ausstellen(db_admin, **argumente)
    with pytest.raises(NichtGefunden):
        schluessel.ausstellen(db_admin, art="event", bezeichnung="x", event_id=uuid4())
    with pytest.raises(NichtGefunden):
        schluessel.ausstellen(db_admin, art="leitung", bezeichnung="x", person_id=uuid4())
    # Betreiber-Arten nur fuer Betreiber-Personen; keine Schluessel fuer inaktive Personen.
    inaktiv = _person(db_admin, "inaktiv@example.org")
    db_admin.execute("update event.person set aktiv = false where id = %s", (inaktiv,))
    chef_inaktiv = _person(db_admin, "chef-inaktiv@example.org", betreiber=True)
    db_admin.execute("update event.person set aktiv = false where id = %s", (chef_inaktiv,))
    for art, person in (("betreiber", welt["lea"]), ("betreiber_lesen", welt["lea"]),
                        ("leitung", inaktiv), ("melden", inaktiv),
                        ("betreiber", chef_inaktiv)):
        with pytest.raises(ValueError):
            schluessel.ausstellen(db_admin, art=art, bezeichnung="x", person_id=person)
    with pytest.raises(ValueError):
        schluessel.ausstellen(db_admin, art="event", bezeichnung="x", event_id=welt["ea"],
                              person_id=inaktiv)
    assert db_admin.execute("select count(*) as n from event.schluessel").fetchone()["n"] == 0
    # Grenzwert: 730 Tage gehen.
    schluessel.ausstellen(db_admin, art="melden", bezeichnung="x", gueltig_tage=730)


def test_kombinationen_auch_in_der_datenbank(db_admin, welt):
    """Zweites Netz hinter der Fachschicht: Checks und FK in 004."""
    h = hashlib.sha256(b"beispiel").hexdigest()

    def einfuegen(art: str, event_id: Any = None, firma_id: Any = None, person_id: Any = None,
                  hash_: str = h, tage: int = 30) -> None:
        db_admin.execute(
            "insert into event.schluessel (hash, art, event_id, firma_id, person_id,"
            " bezeichnung, ablauf) values (%s, %s, %s, %s, %s, 'x',"
            " now() + make_interval(days => %s))",
            (hash_, art, event_id, firma_id, person_id, tage))

    with pytest.raises(psycopg.errors.CheckViolation):
        einfuegen("event")
    with pytest.raises(psycopg.errors.CheckViolation):
        einfuegen("event", event_id=welt["ea"])  # firma_id fehlt
    with pytest.raises(psycopg.errors.CheckViolation):
        einfuegen("leitung")
    with pytest.raises(psycopg.errors.CheckViolation):
        einfuegen("melden", event_id=welt["ea"], firma_id=welt["a"])
    with pytest.raises(psycopg.errors.CheckViolation):
        einfuegen("melden", hash_="evk_klartext-statt-hash")
    with pytest.raises(psycopg.errors.CheckViolation):
        einfuegen("melden", tage=731)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        einfuegen("event", event_id=welt["ea"], firma_id=welt["b"])  # Event aus Firma A


# --- Pruefen ------------------------------------------------------------------------


def test_pruefen_ok_und_einheitlich_abgelehnt(db_admin, db_app, welt):
    sid, klartext = schluessel.ausstellen(
        db_admin, art="leitung", bezeichnung="Lea direkt", person_id=welt["lea"])
    erwartet = schluessel.SchluesselInfo(
        id=sid, art="leitung", event_id=None, firma_id=None, person_id=welt["lea"])
    assert schluessel.pruefen(db_admin, klartext) == erwartet
    assert schluessel.pruefen(db_app(), klartext) == erwartet
    assert schluessel.pruefen(db_app(), klartext).schluessel_hash == hashlib.sha256(
        klartext.encode("utf-8")).hexdigest()
    assert "schluessel_hash" not in repr(erwartet)

    abgelaufen_id, abgelaufen = schluessel.ausstellen(db_admin, art="melden", bezeichnung="alt")
    db_admin.execute("update event.schluessel set ablauf = now() - interval '1 second'"
                     " where id = %s", (abgelaufen_id,))
    widerrufen_id, widerrufen = schluessel.ausstellen(db_admin, art="melden", bezeichnung="weg")
    schluessel.widerrufen(db_admin, widerrufen_id)
    inaktiv_person = _person(db_admin, "inaktiv@example.org")
    _, inaktiv = schluessel.ausstellen(
        db_admin, art="leitung", bezeichnung="inaktiv", person_id=inaktiv_person)
    db_admin.execute("update event.person set aktiv = false where id = %s", (inaktiv_person,))
    ex_betreiber = _person(db_admin, "ex-betreiber@example.org", betreiber=True)
    _, ex_klartext = schluessel.ausstellen(
        db_admin, art="betreiber", bezeichnung="ex", person_id=ex_betreiber)
    assert schluessel.pruefen(db_admin, ex_klartext).art == "betreiber"
    db_admin.execute("update event.person set betreiber = false where id = %s", (ex_betreiber,))

    unbekannt = "evk_" + "A" * 43
    schlecht: list[Any] = [
        unbekannt, abgelaufen, widerrufen, inaktiv, ex_klartext, "",
        klartext.removeprefix("evk_"),              # ohne Praefix
        "xyz_" + klartext.removeprefix("evk_"),     # falsches Praefix
        klartext + "A", klartext[:-1],              # falsche Laenge
        klartext[:-1] + "\x00",                     # NUL
        klartext[:-1] + "\ud800",                   # einzelnes Surrogat
        " " + klartext, klartext + "\n",
        None, 42, b"evk_bytes",
    ]
    meldungen = set()
    for wert in schlecht:
        for conn in (db_admin, db_app()):
            with pytest.raises(NichtGefunden) as e:
                schluessel.pruefen(conn, wert)
            meldungen.add(str(e.value))
    assert len(meldungen) == 1
    (meldung,) = meldungen
    assert "evk_" not in meldung


def test_widerrufen(db_admin, welt):
    sid, klartext = schluessel.ausstellen(db_admin, art="melden", bezeichnung="Empfang")
    schluessel.widerrufen(db_admin, sid)
    erst = db_admin.execute("select widerrufen from event.schluessel where id = %s",
                            (sid,)).fetchone()["widerrufen"]
    assert erst is not None
    schluessel.widerrufen(db_admin, sid)  # zweimal: bleibt beim ersten Zeitpunkt
    assert db_admin.execute("select widerrufen from event.schluessel where id = %s",
                            (sid,)).fetchone()["widerrufen"] == erst
    with pytest.raises(NichtGefunden):
        schluessel.pruefen(db_admin, klartext)
    with pytest.raises(NichtGefunden):
        schluessel.widerrufen(db_admin, uuid4())


def test_event_loeschen_raeumt_schluessel_ab(db_admin, db_app, welt):
    """Ein Event-Schluessel blockiert das Loeschen des Events nicht und stirbt mit ihm."""
    _, klartext = schluessel.ausstellen(db_admin, art="event", bezeichnung="x",
                                        event_id=welt["ea"])
    leitung = Kontext(person_id=welt["lea"], betreiber=False, rollen={welt["a"]: "leitung"})
    events.event_loeschen(db_app(), leitung, welt["ea"])
    with pytest.raises(NichtGefunden):
        schluessel.pruefen(db_admin, klartext)


# --- Kontext ------------------------------------------------------------------------


def test_kontext_event_schluessel(db_admin, db_app, welt):
    _, klartext = schluessel.ausstellen(
        db_admin, art="event", bezeichnung="Gruppe", event_id=welt["ea"], person_id=welt["lea"])
    app = db_app()
    k = schluessel.kontext_fuer(app, schluessel.pruefen(app, klartext))
    assert k == Kontext(person_id=None, betreiber=False, rollen={welt["a"]: "mitglied"},
                        event_id=welt["ea"], actor="bot:event", quelle="bot")


def test_kontext_leitung_nur_leitungsfirmen(db_admin, db_app, welt):
    _, klartext = schluessel.ausstellen(
        db_admin, art="leitung", bezeichnung="Lea direkt", person_id=welt["lea"])
    app = db_app()
    info = schluessel.pruefen(app, klartext)
    k = schluessel.kontext_fuer(app, info)
    assert dict(k.rollen) == {welt["a"]: "leitung"}  # Mitglied-Firma B fehlt
    assert (k.person_id, k.betreiber, k.event_id, k.actor, k.quelle) == (
        welt["lea"], False, None, "bot:leitung", "bot")

    firmen.rolle_aendern(app, BETREIBER, welt["lea"], welt["a"], "mitglied")
    # Rollen werden bei jedem Aufruf neu gelesen, nicht beim Ausstellen eingefroren.
    assert dict(schluessel.kontext_fuer(app, info).rollen) == {}


def test_kontext_leitung_auch_als_betreiber_ohne_betreiberrecht(db_admin, db_app, welt):
    chef = _person(db_admin, "chef@example.org", betreiber=True)
    _mitglied(db_admin, chef, welt["b"], "leitung")
    _, klartext = schluessel.ausstellen(db_admin, art="leitung", bezeichnung="x", person_id=chef)
    app = db_app()
    k = schluessel.kontext_fuer(app, schluessel.pruefen(app, klartext))
    assert k.betreiber is False and dict(k.rollen) == {welt["b"]: "leitung"}


def test_kontext_leitung_inaktive_person(db_admin, db_app, welt):
    _, klartext = schluessel.ausstellen(
        db_admin, art="leitung", bezeichnung="x", person_id=welt["lea"])
    app = db_app()
    info = schluessel.pruefen(app, klartext)
    db_admin.execute("update event.person set aktiv = false where id = %s", (welt["lea"],))
    with pytest.raises(NichtGefunden) as e:
        schluessel.kontext_fuer(app, info)
    # Dieselbe Meldung wie jede Ablehnung in pruefen (kein Orakel ueber den Grund).
    with pytest.raises(NichtGefunden) as einheitlich:
        schluessel.pruefen(app, "")
    assert str(e.value) == str(einheitlich.value)


@pytest.mark.parametrize("art", ["betreiber", "betreiber_lesen"])
def test_kontext_betreiber(db_admin, db_app, welt, art):
    _, klartext = schluessel.ausstellen(db_admin, art=art, bezeichnung="Betrieb")
    app = db_app()
    k = schluessel.kontext_fuer(app, schluessel.pruefen(app, klartext))
    assert k == Kontext(person_id=None, betreiber=True, rollen={}, event_id=None,
                        actor=f"bot:{art}", quelle="bot")


def test_kontext_melden_ohne_firmen(db_admin, db_app, welt):
    _, klartext = schluessel.ausstellen(db_admin, art="melden", bezeichnung="Empfang")
    app = db_app()
    k = schluessel.kontext_fuer(app, schluessel.pruefen(app, klartext))
    assert k == Kontext(person_id=None, betreiber=False, rollen={}, event_id=None,
                        actor="bot:melden", quelle="bot")


# --- Marke und Bestaetigung (nur die SD-Funktionen; Fachschicht folgt in Task 3) ------


def _h(wert: str) -> str:
    return hashlib.sha256(wert.encode("utf-8")).hexdigest()


def test_marke_sd_funktionen(db_admin, db_app, welt):
    """Gebunden an den Schluessel-HASH (Besitznachweis), nicht an die id."""
    app = db_app()
    lea_id, lea = schluessel.ausstellen(db_admin, art="leitung", bezeichnung="x",
                                        person_id=welt["lea"])
    _, zweite = schluessel.ausstellen(db_admin, art="leitung", bezeichnung="y",
                                      person_id=welt["zweite"])
    _, melden = schluessel.ausstellen(db_admin, art="melden", bezeichnung="z")
    _, ev = schluessel.ausstellen(db_admin, art="event", bezeichnung="e", event_id=welt["ea"])

    def anlegen(wert: str, klartext: str, event_id: UUID, minuten: int = 30) -> bool:
        return app.execute("select event.marke_anlegen(%s, %s, %s, %s) as ok",
                           (_h(wert), _h(klartext), event_id, minuten)).fetchone()["ok"]

    def pruefen(wert: str, klartext: str) -> Any:
        return app.execute("select event.marke_pruefen(%s, %s) as e",
                           (_h(wert), _h(klartext))).fetchone()["e"]

    assert anlegen("mk_lea", lea, welt["ea"]) is True
    assert pruefen("mk_lea", lea) == welt["ea"]
    assert pruefen("mk_lea", zweite) is None          # fremder Schluessel
    assert pruefen("mk_unbekannt", lea) is None
    # Die id (oder ihr Text) ist kein Besitznachweis.
    for falsch in (str(lea_id), _h(str(lea_id)), "", None):
        assert app.execute("select event.marke_pruefen(%s, %s) as e",
                           (_h("mk_lea"), falsch)).fetchone()["e"] is None
        assert app.execute("select event.marke_anlegen(%s, %s, %s, 30) as ok",
                           (_h("mk_id"), falsch, welt["ea"])).fetchone()["ok"] is False
    # Lea ist in B nur Mitglied, melden darf nie, Event-Schluessel nur sein Event.
    assert anlegen("mk_b", lea, welt["eb"]) is False
    assert anlegen("mk_m", melden, welt["ea"]) is False
    assert anlegen("mk_e", ev, welt["eb"]) is False
    assert anlegen("mk_e", ev, welt["ea"]) is True
    # Kaputte Eingaben sind Programmierfehler: Ausnahme statt false.
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        anlegen("mk_lang", lea, welt["ea"], minuten=61)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        app.execute("select event.marke_anlegen('kein-hash', %s, %s, 30)", (_h(lea), welt["ea"]))
    # Abgelaufen, oder Leitung inzwischen entzogen: Marke gilt nicht mehr.
    db_admin.execute("update event.marke set ablauf = now() - interval '1 second'"
                     " where wert_hash = %s", (_h("mk_e"),))
    assert pruefen("mk_e", ev) is None
    db_admin.execute("update event.mitgliedschaft set rolle = 'mitglied'"
                     " where person_id = %s and firma_id = %s", (welt["lea"], welt["a"]))
    assert pruefen("mk_lea", lea) is None
    # Python-Seite: pruefen liefert den Hash fuer genau diese Aufrufe.
    assert schluessel.pruefen(app, lea).schluessel_hash == _h(lea)


def test_bestaetigung_sd_funktionen(db_admin, db_app, welt):
    app = db_app()
    sid, eigen = schluessel.ausstellen(db_admin, art="betreiber", bezeichnung="x")
    _, anderer = schluessel.ausstellen(db_admin, art="betreiber", bezeichnung="y")
    ziel = json.dumps({"event_id": str(welt["ea"])})

    def anlegen(code: str, klartext: str, aktion: str = "event_loeschen", z: str = ziel,
                minuten: int = 5) -> bool:
        return app.execute("select event.bestaetigung_anlegen(%s, %s, %s, %s::jsonb, %s) as ok",
                           (_h(code), _h(klartext), aktion, z, minuten)).fetchone()["ok"]

    def einloesen(code: str, klartext: str, aktion: str, z: str) -> bool:
        return app.execute("select event.bestaetigung_einloesen(%s, %s, %s, %s::jsonb) as ok",
                           (_h(code), _h(klartext), aktion, z)).fetchone()["ok"]

    assert anlegen("bs_1", eigen) is True
    for aktion, z, minuten in (("Event Loeschen", ziel, 5), ("event_loeschen", "[1]", 5),
                               ("event_loeschen", ziel, 0)):
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            anlegen("bs_x", eigen, aktion, z, minuten)
    # Die id ist kein Besitznachweis.
    assert app.execute("select event.bestaetigung_anlegen(%s, %s, 'event_loeschen',"
                       " %s::jsonb, 5) as ok", (_h("bs_id"), str(sid), ziel)
                       ).fetchone()["ok"] is False
    assert app.execute("select event.bestaetigung_einloesen(%s, %s, 'event_loeschen',"
                       " %s::jsonb) as ok", (_h("bs_1"), str(sid), ziel)).fetchone()["ok"] is False
    assert einloesen("bs_1", anderer, "event_loeschen", ziel) is False
    assert einloesen("bs_1", eigen, "event_absagen", ziel) is False
    assert einloesen("bs_1", eigen, "event_loeschen",
                     json.dumps({"event_id": str(welt["eb"])})) is False
    assert einloesen("bs_1", eigen, "event_loeschen", ziel) is True
    assert einloesen("bs_1", eigen, "event_loeschen", ziel) is False  # nur einmal
    anlegen("bs_2", eigen)
    db_admin.execute("update event.bestaetigung set ablauf = now() - interval '1 second'"
                     " where code_hash = %s", (_h("bs_2"),))
    assert einloesen("bs_2", eigen, "event_loeschen", ziel) is False
    anlegen("bs_3", eigen)
    schluessel.widerrufen(db_admin, sid)
    assert einloesen("bs_3", eigen, "event_loeschen", ziel) is False
    assert anlegen("bs_4", eigen) is False  # widerrufener Schluessel stellt nichts mehr aus


# --- Rechte -------------------------------------------------------------------------


def test_event_app_sieht_schluesseltabellen_nicht(db_admin, db_app, welt):
    schluessel.ausstellen(db_admin, art="melden", bezeichnung="x")
    for conn in (db_app(), db_app(betreiber=True), db_app(firmen=[welt["a"]])):
        for tabelle in ("schluessel", "marke", "bestaetigung"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f"select * from event.{tabelle}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f"delete from event.{tabelle}")
    for tabelle in ("schluessel", "marke", "bestaetigung"):
        for recht in ("select", "insert", "update", "delete"):
            for rolle in ("event_app", "public"):
                assert db_admin.execute(
                    "select has_table_privilege(%s, %s, %s) as r",
                    (rolle, f"event.{tabelle}", recht)).fetchone()["r"] is False, (tabelle, recht)


SD_FUNKTIONEN = (
    "schluessel_pruefen(text)",
    "marke_anlegen(text, text, uuid, integer)",
    "marke_pruefen(text, text)",
    "bestaetigung_anlegen(text, text, text, jsonb, integer)",
    "bestaetigung_einloesen(text, text, text, jsonb)",
    "anmeldung_holen(text)",
    "hat_passwort(uuid)",
    "passwort_setzen_wenn_leer(uuid, text, text)",
)


def test_sd_funktionen_nur_fuer_event_app(db_admin):
    for sig in SD_FUNKTIONEN:
        zeile = db_admin.execute(
            "select p.prosecdef, p.proconfig,"
            " has_function_privilege('event_app', p.oid, 'execute') as app,"
            " has_function_privilege('public', p.oid, 'execute') as pub"
            " from pg_proc p where p.oid = %s::regprocedure", (f"event.{sig}",)).fetchone()
        assert zeile == {"prosecdef": True, "proconfig": ["search_path=event, pg_temp"],
                         "app": True, "pub": False}, sig
    # Interne Helfer der SD-Funktionen sind fuer event_app nicht aufrufbar.
    for sig in ("schluessel_aktiv(uuid)", "schluessel_darf_event(uuid, uuid)"):
        zeile = db_admin.execute(
            "select has_function_privilege('event_app', %s::regprocedure, 'execute') as app,"
            " has_function_privilege('public', %s::regprocedure, 'execute') as pub",
            (f"event.{sig}", f"event.{sig}")).fetchone()
        assert zeile == {"app": False, "pub": False}, sig


# --- CLI ----------------------------------------------------------------------------


def test_cli_ausstellen_und_widerrufen(pg_cluster, db_admin, tmp_path, monkeypatch, capsys,
                                       welt):
    dsn = f"host=127.0.0.1 port={pg_cluster['port']} user=postgres dbname={db_admin.info.dbname}"
    datei = tmp_path / "dsn.txt"
    datei.write_text(dsn + "\r\n", encoding="utf-8")
    monkeypatch.setenv("EVENT_ADMIN_DSN_FILE", str(datei))

    assert schluessel.main(["ausstellen", "--art", "event", "--bezeichnung", "Gruppe",
                            "--event-id", str(welt["ea"])]) == 0
    aus = capsys.readouterr()
    klartext = aus.out.strip()
    assert aus.out == klartext + "\n" and klartext.startswith("evk_")
    assert klartext not in aus.err and "127.0.0.1" not in aus.out + aus.err
    info = schluessel.pruefen(db_admin, klartext)
    assert info.event_id == welt["ea"]

    assert schluessel.main(["widerrufen", str(info.id)]) == 0
    weg = capsys.readouterr()
    assert weg.out == "" and "evk_" not in weg.err
    with pytest.raises(NichtGefunden):
        schluessel.pruefen(db_admin, klartext)

    # Fehler: Meldung ohne Werte, Rueckgabecode != 0, kein Klartext.
    assert schluessel.main(["ausstellen", "--art", "event", "--bezeichnung", "x"]) != 0
    assert schluessel.main(["widerrufen", str(uuid4())]) != 0
    fehler = capsys.readouterr()
    assert fehler.out == "" and "evk_" not in fehler.err

    monkeypatch.delenv("EVENT_ADMIN_DSN_FILE")
    assert schluessel.main(["ausstellen", "--art", "melden", "--bezeichnung", "x"]) == 2
    capsys.readouterr()

    # Unlesbare DSN-Datei (fehlt, Verzeichnis, kein UTF-8, leer): feste Meldung, Exit 2.
    kaputt = tmp_path / "kaputt.txt"
    kaputt.write_bytes(b"\xff\xfe\x00geheim")
    leer = tmp_path / "leer.txt"
    leer.write_text("  \n", encoding="utf-8")
    for pfad in (tmp_path / "fehlt.txt", tmp_path, kaputt, leer):
        monkeypatch.setenv("EVENT_ADMIN_DSN_FILE", str(pfad))
        assert schluessel.main(["widerrufen", str(uuid4())]) == 2
        aus = capsys.readouterr()
        assert aus.out == "" and "Traceback" not in aus.err and "DSN" in aus.err
        assert str(tmp_path) not in aus.err and "geheim" not in aus.err
