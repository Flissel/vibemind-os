"""Erster Betreiber per Admin-CLI (Etappe 4, Abschluss-Review F-5) gegen den Wegwerf-Cluster.

Ohne laufenden Betreiber gibt es keinen Weg zu `person.betreiber = true`: die Policy
`person_anlegen` verlangt `not betreiber`, aendern darf nur ein Betreiber. Die CLI arbeitet
darum mit der Admin-DSN (nur aus EVENT_ADMIN_DSN_FILE) und gibt nur die Person-ID aus.
"""
from __future__ import annotations

import io
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from spaces.event_management.db import anwenden
from spaces.event_management.system import betreiber, firmen, zugang
from spaces.event_management.system.kontext import NichtGefunden

MAIL = "erste.betreiberin@example.org"
NAME = "Erika Beispiel"
PASSWORT = "ein-langes-test-passwort-0815"


def _person(admin: Any, pid: UUID) -> dict[str, Any]:
    zeile: dict[str, Any] = admin.execute(
        "select mail, name, betreiber, aktiv, passwort_hash, sitzungs_version"
        " from event.person where id = %s", (pid,)).fetchone()
    return zeile


@pytest.fixture(autouse=True)
def _ohne_sperre() -> None:
    zugang._sperre_zuruecksetzen()


def test_legt_ersten_betreiber_an_der_sich_anmelden_kann(db_admin, db_app):
    erg = betreiber.anlegen(db_admin, mail=" Erste.Betreiberin@Example.org ", name=NAME,
                            passwort=PASSWORT)
    assert erg.neu and erg.passwort_gesetzt
    p = _person(db_admin, erg.person_id)
    assert p["betreiber"] and p["aktiv"] and p["mail"] == MAIL and p["name"] == NAME
    assert p["passwort_hash"] and PASSWORT not in p["passwort_hash"]
    app = db_app()
    assert zugang.anmelden(app, MAIL, PASSWORT) == erg.person_id
    assert firmen.kontext_fuer_person(app, erg.person_id).betreiber is True
    # Betreiber darf jetzt eine Firma anlegen (der Weg fuer die Firma des Legacy-Events).
    k = firmen.kontext_fuer_person(app, erg.person_id)
    firma = firmen.firma_anlegen(app, k, "Testfirma", "testfirma")
    assert firma["kurz"] == "testfirma"


def test_bestehende_person_ohne_passwort_wird_betreiber(db_admin, db_app):
    pid = db_admin.execute("insert into event.person (mail, name) values (%s, %s) returning id",
                           (MAIL, NAME)).fetchone()["id"]
    erg = betreiber.anlegen(db_admin, mail=MAIL, name="Anderer Name", passwort=PASSWORT)
    assert erg.person_id == pid and not erg.neu and erg.passwort_gesetzt
    p = _person(db_admin, pid)
    assert p["betreiber"] and p["name"] == NAME  # Name bleibt
    assert zugang.anmelden(db_app(), MAIL, PASSWORT) == pid


def test_vorhandenes_passwort_bleibt(db_admin, db_app):
    alt = zugang.passwort_hash("das-alte-passwort-0815")
    pid = db_admin.execute(
        "insert into event.person (mail, name, passwort_hash) values (%s, %s, %s) returning id",
        (MAIL, NAME, alt)).fetchone()["id"]
    erg = betreiber.anlegen(db_admin, mail=MAIL, name=NAME, passwort=PASSWORT)
    assert erg.person_id == pid and not erg.passwort_gesetzt
    assert _person(db_admin, pid)["passwort_hash"] == alt and _person(db_admin, pid)["betreiber"]
    assert zugang.anmelden(db_app(), MAIL, PASSWORT) is None
    # Zweiter Lauf: idempotent.
    assert betreiber.anlegen(db_admin, mail=MAIL, name=NAME, passwort=PASSWORT).person_id == pid
    assert db_admin.execute("select count(*) as n from event.person").fetchone()["n"] == 1


@pytest.mark.parametrize("mail,passwort,meldung", [
    (MAIL, "zu-kurz", "Das Passwort braucht mindestens 12 Zeichen"),
    (MAIL, "", "Das Passwort braucht mindestens 12 Zeichen"),
    ("kein-at-zeichen", PASSWORT, "Mailadresse ungültig"),
    ("a@b@example.org", PASSWORT, "Mailadresse ungültig"),
    ("Name <x@example.org>", PASSWORT, "Mailadresse ungültig"),
    ("x@example.org, y@example.org", PASSWORT, "Mailadresse ungültig"),
    ("x\n@example.org", PASSWORT, "Mailadresse ungültig"),
])
def test_eingaben_abgelehnt_ohne_werte(db_admin, mail, passwort, meldung):
    with pytest.raises(ValueError) as e:
        betreiber.anlegen(db_admin, mail=mail, name=NAME, passwort=passwort)
    assert str(e.value) == meldung
    assert db_admin.execute("select count(*) as n from event.person").fetchone()["n"] == 0


def test_name_pflicht(db_admin):
    for name in ("", "   "):
        with pytest.raises(ValueError) as e:
            betreiber.anlegen(db_admin, mail=MAIL, name=name, passwort=PASSWORT)
        assert str(e.value) == "Name fehlt"


def test_inaktive_person_abgelehnt(db_admin):
    db_admin.execute("insert into event.person (mail, name, aktiv) values (%s, %s, false)",
                     (MAIL, NAME))
    with pytest.raises(ValueError) as e:
        betreiber.anlegen(db_admin, mail=MAIL, name=NAME, passwort=PASSWORT)
    assert str(e.value) == "Person ist inaktiv"
    assert not db_admin.execute("select betreiber from event.person").fetchone()["betreiber"]


def test_passwort_min_wie_einladung():
    assert betreiber.PASSWORT_MIN == zugang._PASSWORT_MIN


def test_entziehen_ist_der_rueckweg(db_admin, db_app):
    erg = betreiber.anlegen(db_admin, mail=MAIL, name=NAME, passwort=PASSWORT)
    vorher = _person(db_admin, erg.person_id)["sitzungs_version"]
    betreiber.entziehen(db_admin, erg.person_id)
    p = _person(db_admin, erg.person_id)
    assert not p["betreiber"] and p["sitzungs_version"] == vorher + 1  # alte Sitzungen ungueltig
    assert firmen.kontext_fuer_person(db_app(), erg.person_id).betreiber is False
    with pytest.raises(NichtGefunden):
        betreiber.entziehen(db_admin, erg.person_id)  # ist keiner mehr
    with pytest.raises(NichtGefunden):
        betreiber.entziehen(db_admin, uuid4())


# --- CLI ------------------------------------------------------------------------------


def _cli_db(leere_db_dsn: str, tmp_path: Any, monkeypatch: Any) -> None:
    with psycopg.connect(leere_db_dsn, autocommit=True, row_factory=dict_row) as c:
        anwenden.anwenden(c)
    dsn_datei = tmp_path / "dsn.txt"
    dsn_datei.write_text(leere_db_dsn + "\r\n", encoding="utf-8")
    monkeypatch.setenv("EVENT_ADMIN_DSN_FILE", str(dsn_datei))


def test_cli_gibt_nur_die_id_aus(leere_db_dsn, tmp_path, monkeypatch, capsys):
    _cli_db(leere_db_dsn, tmp_path, monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORT + "\r\n"))
    assert betreiber.main(["anlegen", "--mail", MAIL, "--name", NAME]) == 0
    roh = capsys.readouterr()
    pid = UUID(roh.out.strip())
    for geheim in (MAIL, NAME, PASSWORT, leere_db_dsn, "example.org"):
        assert geheim not in roh.out + roh.err
    with psycopg.connect(leere_db_dsn, autocommit=True, row_factory=dict_row) as c:
        p = _person(c, pid)
        assert p["betreiber"] and zugang.passwort_ok(PASSWORT, p["passwort_hash"])

    # Zweiter Lauf: dieselbe ID, Passwort bleibt (fester Text auf stderr).
    monkeypatch.setattr("sys.stdin", io.StringIO("ein-ganz-anderes-passwort\n"))
    assert betreiber.main(["anlegen", "--mail", MAIL, "--name", NAME]) == 0
    roh = capsys.readouterr()
    assert UUID(roh.out.strip()) == pid and "unverändert" in roh.err

    # Rueckweg
    assert betreiber.main(["entziehen", str(pid)]) == 0
    roh = capsys.readouterr()
    assert roh.out == "" and str(pid) in roh.err
    assert betreiber.main(["entziehen", str(pid)]) == 1


def test_cli_ohne_passwort_oder_mit_kurzem(leere_db_dsn, tmp_path, monkeypatch, capsys):
    _cli_db(leere_db_dsn, tmp_path, monkeypatch)
    for eingabe in ("", "\n", "kurz\n"):
        monkeypatch.setattr("sys.stdin", io.StringIO(eingabe))
        assert betreiber.main(["anlegen", "--mail", MAIL, "--name", NAME]) == 2
        roh = capsys.readouterr()
        assert roh.out == "" and "kurz" not in roh.err and MAIL not in roh.err
    with psycopg.connect(leere_db_dsn, autocommit=True, row_factory=dict_row) as c:
        assert c.execute("select count(*) as n from event.person").fetchone()["n"] == 0


def test_cli_nimmt_keinen_dsn_aus_env(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("EVENT_ADMIN_DSN_FILE", raising=False)
    monkeypatch.setenv("EVENT_ADMIN_DSN", "host=x")
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORT + "\n"))
    assert betreiber.main(["anlegen", "--mail", MAIL, "--name", NAME]) == 2
    roh = capsys.readouterr()
    assert roh.out == "" and "EVENT_ADMIN_DSN_FILE" in roh.err
    leer = tmp_path / "leer"
    leer.write_text("\n", encoding="utf-8")
    monkeypatch.setenv("EVENT_ADMIN_DSN_FILE", str(leer))
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORT + "\n"))
    assert betreiber.main(["anlegen", "--mail", MAIL, "--name", NAME]) == 2


def test_cli_verbindungsfehler_ohne_dsn(tmp_path, monkeypatch, capsys):
    datei = tmp_path / "dsn"
    datei.write_text("host=127.0.0.1 port=1 user=geheimnutzer password=geheim-0815 dbname=x"
                     " connect_timeout=2", encoding="utf-8")
    monkeypatch.setenv("EVENT_ADMIN_DSN_FILE", str(datei))
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORT + "\n"))
    assert betreiber.main(["anlegen", "--mail", MAIL, "--name", NAME]) == 1
    roh = capsys.readouterr()
    assert "geheim" not in roh.out + roh.err and "Verbindung fehlgeschlagen" in roh.err
