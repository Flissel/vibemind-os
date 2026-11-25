"""Test-Mitglied ohne Mailversand (testmitglied.py) gegen den Wegwerf-Cluster.

Geprueft wird: das Konto entsteht ueber Einladung und Einloesen unter der App-Rolle, sitzt nur in
der Testfirma (als Mitglied), sieht keine Events anderer Firmen, kann sich anmelden, und der
Rueckweg macht es unbrauchbar. Vorhandene Adressen und echte Konten bleiben unberuehrt.
Alle Namen und Adressen sind erfunden.
"""
from __future__ import annotations

import io
from typing import Any
from uuid import UUID

import pytest

from spaces.event_management.system import betreiber, firmen, testmitglied, zugang
from spaces.event_management.system.kontext import Kontext, NichtGefunden

MAIL = "test.mitglied@example.org"
NAME = "Max Muster"
PASSWORT = "ein-langes-test-passwort-4711"
BK = Kontext(person_id=None, betreiber=True, rollen={})


@pytest.fixture(autouse=True)
def _ohne_sperre() -> None:
    zugang._sperre_zuruecksetzen()


def _person(admin: Any, pid: UUID) -> dict[str, Any]:
    zeile: dict[str, Any] = admin.execute(
        "select mail, name, betreiber, aktiv, sitzungs_version from event.person where id = %s",
        (pid,)).fetchone()
    return zeile


def _mitgliedschaften(admin: Any, pid: UUID) -> list[dict[str, Any]]:
    return admin.execute(
        "select f.kurz, m.rolle from event.mitgliedschaft m join event.firma f on f.id = m.firma_id"
        " where m.person_id = %s", (pid,)).fetchall()


def test_legt_mitglied_der_testfirma_an_das_sich_anmelden_kann(db_admin, db_app):
    app = db_app()
    pid = testmitglied.anlegen(app, mail=" Test.Mitglied@Example.org ", name=NAME, passwort=PASSWORT)
    p = _person(db_admin, pid)
    assert p["mail"] == MAIL and p["name"] == NAME and p["aktiv"] and not p["betreiber"]
    assert _mitgliedschaften(db_admin, pid) == [{"kurz": "testfirma", "rolle": "mitglied"}]
    assert zugang.anmelden(app, MAIL, PASSWORT) == pid
    k = firmen.kontext_fuer_person(app, pid)
    assert not k.betreiber and [f["kurz"] for f in firmen.firmen(app, k)] == ["testfirma"]
    # Die Einladung ist eingeloest, keine offene bleibt zurueck
    offen = db_admin.execute("select count(*) as n from event.einladung where eingeloest is null").fetchone()
    assert offen["n"] == 0


def test_sieht_keine_events_einer_echten_firma(db_admin, db_app):
    app = db_app()
    echt = firmen.firma_anlegen(app, BK, "Echte Firma", "echt")
    db_admin.execute(
        "insert into event.event (firma_id, kategorie, name, beginn, ende)"
        " values (%s, 'weihnachtsfeier', 'Sommerfest', now(), now() + interval '3 hours')", (echt["id"],))
    pid = testmitglied.anlegen(app, mail=MAIL, name=NAME, passwort=PASSWORT)
    sicht = db_app(firmen=[f for f in firmen.kontext_fuer_person(app, pid).firmen], person=pid)
    assert sicht.execute("select count(*) as n from event.event").fetchone()["n"] == 0
    assert db_admin.execute("select count(*) as n from event.event").fetchone()["n"] == 1


def test_nutzt_eine_vorhandene_testfirma(db_admin, db_app):
    app = db_app()
    vorher = firmen.firma_anlegen(app, BK, "Testfirma", "testfirma")
    pid = testmitglied.anlegen(app, mail=MAIL, name=NAME, passwort=PASSWORT)
    assert db_admin.execute("select count(*) as n from event.firma").fetchone()["n"] == 1
    zeile = db_admin.execute("select firma_id from event.mitgliedschaft where person_id = %s", (pid,)).fetchone()
    assert zeile["firma_id"] == vorher["id"]


def test_vorhandene_adresse_wird_nicht_angefasst(db_admin, db_app):
    erg = betreiber.anlegen(db_admin, mail=MAIL, name="Erika Beispiel", passwort="das-betreiber-passwort-1")
    with pytest.raises(ValueError, match="gibt es schon"):
        testmitglied.anlegen(db_app(), mail=MAIL, name=NAME, passwort=PASSWORT)
    p = _person(db_admin, erg.person_id)
    assert p["betreiber"] and p["name"] == "Erika Beispiel"
    assert _mitgliedschaften(db_admin, erg.person_id) == []
    assert db_admin.execute("select count(*) as n from event.einladung").fetchone()["n"] == 0
    assert zugang.anmelden(db_app(), MAIL, PASSWORT) is None


@pytest.mark.parametrize("werte, text", [
    ({"mail": "kein-at-zeichen"}, "Mailadresse"),
    ({"mail": "a@b"}, "Mailadresse"),
    ({"name": "   "}, "Name fehlt"),
    ({"passwort": "kurz"}, "mindestens 12"),
])
def test_eingaben_werden_vor_dem_schreiben_geprueft(db_admin, db_app, werte, text):
    args = {"mail": MAIL, "name": NAME, "passwort": PASSWORT, **werte}
    with pytest.raises(ValueError, match=text):
        testmitglied.anlegen(db_app(), **args)
    for tabelle in ("person", "firma", "einladung"):
        assert db_admin.execute(f"select count(*) as n from event.{tabelle}").fetchone()["n"] == 0


def test_rueckweg_macht_das_konto_unbrauchbar(db_admin, db_app):
    app = db_app()
    pid = testmitglied.anlegen(app, mail=MAIL, name=NAME, passwort=PASSWORT)
    version = _person(db_admin, pid)["sitzungs_version"]
    assert testmitglied.entfernen(app, mail=MAIL) == pid
    p = _person(db_admin, pid)
    assert not p["aktiv"] and p["sitzungs_version"] == version + 1
    assert _mitgliedschaften(db_admin, pid) == []
    assert zugang.anmelden(app, MAIL, PASSWORT) is None
    # Wiederholbar
    assert testmitglied.entfernen(app, mail=MAIL) == pid


def test_rueckweg_laesst_echte_konten_in_ruhe(db_admin, db_app):
    app = db_app()
    erg = betreiber.anlegen(db_admin, mail="chefin@example.org", name="Erika Beispiel",
                            passwort="das-betreiber-passwort-1")
    with pytest.raises(ValueError, match="kein reines Testkonto"):
        testmitglied.entfernen(app, mail="chefin@example.org")
    assert _person(db_admin, erg.person_id)["aktiv"]

    pid = testmitglied.anlegen(app, mail=MAIL, name=NAME, passwort=PASSWORT)
    echt = firmen.firma_anlegen(app, BK, "Echte Firma", "echt")
    firmen.mitgliedschaft_setzen(app, BK, pid, echt["id"], "mitglied")
    with pytest.raises(ValueError, match="kein reines Testkonto"):
        testmitglied.entfernen(app, mail=MAIL)
    assert _person(db_admin, pid)["aktiv"] and len(_mitgliedschaften(db_admin, pid)) == 2


def test_rueckweg_unbekannte_adresse(db_app):
    with pytest.raises(NichtGefunden):
        testmitglied.entfernen(db_app(), mail="niemand@example.org")


# --- CLI --------------------------------------------------------------------------

@pytest.fixture
def dsn_datei(app_dsn, tmp_path, monkeypatch):
    datei = tmp_path / "app.dsn"
    datei.write_text(app_dsn, encoding="utf-8")
    monkeypatch.setenv("EVENT_APP_DSN_FILE", str(datei))
    return datei


def test_cli_gibt_nur_die_id_aus(db_admin, dsn_datei, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORT + "\r\n"))
    assert testmitglied.main(["anlegen", "--mail", MAIL, "--name", NAME]) == 0
    roh = capsys.readouterr()
    pid = UUID(roh.out.strip())
    for geheim in (MAIL, NAME, PASSWORT, str(dsn_datei.read_text())):
        assert geheim not in roh.out and geheim not in roh.err
    assert "keine Mail versendet" in roh.err
    # Zweiter Lauf: Adresse vergeben, fester Text, nichts doppelt
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORT + "\n"))
    assert testmitglied.main(["anlegen", "--mail", MAIL, "--name", NAME]) == 2
    assert "gibt es schon" in capsys.readouterr().err
    assert testmitglied.main(["entfernen", "--mail", MAIL]) == 0
    roh = capsys.readouterr()
    assert roh.out.strip() == str(pid) and MAIL not in roh.err
    assert testmitglied.main(["entfernen", "--mail", "niemand@example.org"]) == 1


def test_cli_ohne_passwort_oder_mit_kurzem(dsn_datei, monkeypatch, capsys):
    for eingabe in ("", "\n", "kurz\n"):
        monkeypatch.setattr("sys.stdin", io.StringIO(eingabe))
        assert testmitglied.main(["anlegen", "--mail", MAIL, "--name", NAME]) == 2
        assert "kurz" not in capsys.readouterr().out


def test_cli_ohne_dsn_datei(monkeypatch, capsys):
    monkeypatch.delenv("EVENT_APP_DSN_FILE", raising=False)
    monkeypatch.setenv("EVENT_APP_DSN", "host=127.0.0.1 dbname=gibt_es_nicht")
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORT + "\n"))
    assert testmitglied.main(["anlegen", "--mail", MAIL, "--name", NAME]) == 2
    assert "EVENT_APP_DSN_FILE" in capsys.readouterr().err
