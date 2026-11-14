"""Tests fuer den Legacy-Katalogeintrag (Etappe 4, Task 3) gegen den Wegwerf-Cluster."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from spaces.event_management.db import anwenden
from spaces.event_management.system import legacy, marke, schluessel, werkzeuge

BEGINN = datetime(2027, 8, 1, 10, 0, tzinfo=timezone.utc)
URL = "https://alte-app.example.org/festival/42"
LEGACY_TEXT = "Legacy-Event: bitte die alte App nutzen"


def _firma(admin: Any, kurz: str = "testfirma") -> Any:
    return admin.execute(
        "insert into event.firma (name, kurz) values ('Testfirma', %s) returning id", (kurz,),
    ).fetchone()["id"]


def _eintragen(admin: Any, **ueber: Any) -> Any:
    werte: dict[str, Any] = dict(
        firma_kurz="testfirma", name="Testfestival", beginn=BEGINN,
        ende=BEGINN + timedelta(days=2), ort="Musterdorf", legacy_url=URL,
    )
    werte.update(ueber)
    return legacy.eintragen(admin, **werte)


def test_eintragen_legt_legacy_event_an(db_admin):
    firma = _firma(db_admin)
    eid = _eintragen(db_admin)
    z = db_admin.execute("select * from event.event where id = %s", (eid,)).fetchone()
    assert z["quelle"] == "legacy" and z["kategorie"] == "festival"
    assert z["legacy_url"] == URL and z["firma_id"] == firma
    assert z["status"] == "geplant" and z["ort_adresse"] == "Musterdorf"
    assert z["name"] == "Testfestival"


def test_eintragen_idempotent_ueber_url(db_admin):
    _firma(db_admin)
    erste = _eintragen(db_admin)
    zweite = _eintragen(db_admin, name="Neuer Name", ort=None,
                        ende=BEGINN + timedelta(days=3))
    assert erste == zweite
    zeilen = db_admin.execute("select * from event.event").fetchall()
    assert len(zeilen) == 1
    assert zeilen[0]["name"] == "Neuer Name" and zeilen[0]["ort_adresse"] is None
    assert zeilen[0]["ende"] == BEGINN + timedelta(days=3)


def test_unbekannte_firma_abgelehnt(db_admin):
    with pytest.raises(ValueError, match="Firma nicht gefunden"):
        _eintragen(db_admin, firma_kurz="gibtsnicht")
    assert db_admin.execute("select count(*) as n from event.event").fetchone()["n"] == 0


@pytest.mark.parametrize("url", ["http://alte-app.example.org/x", "ftp://x", "", "javascript:1"])
def test_url_nur_https(db_admin, url):
    _firma(db_admin)
    with pytest.raises(ValueError):
        _eintragen(db_admin, legacy_url=url)
    assert db_admin.execute("select count(*) as n from event.event").fetchone()["n"] == 0


def test_cli_gibt_nur_id_aus(leere_db_dsn, tmp_path, monkeypatch, capsys):
    with psycopg.connect(leere_db_dsn, autocommit=True, row_factory=dict_row) as c:
        anwenden.anwenden(c)
        _firma(c)
    dsn_datei = tmp_path / "dsn.txt"
    dsn_datei.write_text(leere_db_dsn + "\n", encoding="utf-8")
    eingabe = tmp_path / "e.json"
    eingabe.write_text(json.dumps({
        "firma_kurz": "testfirma", "name": "Geheimname", "beginn": BEGINN.isoformat(),
        "ende": (BEGINN + timedelta(days=1)).isoformat(), "ort": "Geheimort",
        "legacy_url": URL,
    }), encoding="utf-8")
    monkeypatch.setenv("EVENT_ADMIN_DSN_FILE", str(dsn_datei))
    monkeypatch.delenv("EVENT_ADMIN_DSN", raising=False)

    assert legacy.main(["eintragen", str(eingabe)]) == 0
    roh = capsys.readouterr()
    ausgabe = roh.out + roh.err
    assert len(roh.out.strip()) == 36 and roh.out.count("-") == 4
    for geheim in ("Geheimname", "Geheimort", "alte-app", leere_db_dsn):
        assert geheim not in ausgabe


def test_cli_nimmt_keinen_dsn_aus_env(tmp_path, monkeypatch, capsys):
    eingabe = tmp_path / "e.json"
    eingabe.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("EVENT_ADMIN_DSN_FILE", raising=False)
    monkeypatch.setenv("EVENT_ADMIN_DSN", "host=x")
    assert legacy.main(["eintragen", str(eingabe)]) == 2
    assert capsys.readouterr().out == ""


def test_legacy_event_fuer_bot_gesperrt(db_admin, db_app):
    _firma(db_admin)
    eid = _eintragen(db_admin)
    app = db_app()
    person = db_admin.execute(
        "insert into event.person (mail, name, betreiber) values ('b@example.org', 'Bea', true)"
        " returning id").fetchone()["id"]
    _, klartext = schluessel.ausstellen(db_admin, art="betreiber", bezeichnung="T",
                                        person_id=person)
    info = schluessel.pruefen(db_admin, klartext)

    with pytest.raises(ValueError) as e:
        marke.marke_ausstellen(app, info, eid)
    assert str(e.value) == LEGACY_TEXT

    # Marke direkt anlegen (die Fachschicht stellt fuer Legacy keine aus), dann Werkzeug.
    wert = "mk_aaaaaaaaaaaa"
    db_admin.execute("select event.marke_anlegen(%s, %s, %s, 30)",
                     (marke._hash(wert), info.schluessel_hash, eid))
    with pytest.raises(ValueError) as e:
        werkzeuge.ausfuehren(app, info, "aufgaben_anlegen", {"marke": wert, "titel": "x"})
    assert str(e.value) == LEGACY_TEXT
    assert db_admin.execute("select count(*) as n from event.aufgabe").fetchone()["n"] == 0


def test_andere_firma_abgelehnt(db_admin):
    _firma(db_admin, "testfirma")
    _firma(db_admin, "zweitfirma")
    eid = _eintragen(db_admin)
    vorher = db_admin.execute("select * from event.event where id = %s", (eid,)).fetchone()
    with pytest.raises(ValueError, match="Legacy-Event gehoert einer anderen Firma"):
        _eintragen(db_admin, firma_kurz="zweitfirma", name="Anderer Name")
    nachher = db_admin.execute("select * from event.event where id = %s", (eid,)).fetchone()
    assert nachher == vorher
    assert db_admin.execute("select count(*) as n from event.event").fetchone()["n"] == 1


def test_cli_meldet_andere_firma(leere_db_dsn, tmp_path, monkeypatch, capsys):
    with psycopg.connect(leere_db_dsn, autocommit=True, row_factory=dict_row) as c:
        anwenden.anwenden(c)
        _firma(c, "testfirma")
        _firma(c, "zweitfirma")
        _eintragen(c)
    dsn_datei = tmp_path / "dsn.txt"
    dsn_datei.write_text(leere_db_dsn + "\n", encoding="utf-8")
    eingabe = tmp_path / "e.json"
    eingabe.write_text(json.dumps({
        "firma_kurz": "zweitfirma", "name": "Geheimname", "beginn": BEGINN.isoformat(),
        "ende": (BEGINN + timedelta(days=1)).isoformat(), "ort": None, "legacy_url": URL,
    }), encoding="utf-8")
    monkeypatch.setenv("EVENT_ADMIN_DSN_FILE", str(dsn_datei))
    assert legacy.main(["eintragen", str(eingabe)]) == 2
    roh = capsys.readouterr()
    assert roh.out == ""
    assert "Legacy-Event gehoert einer anderen Firma" in roh.err
    assert "Geheimname" not in roh.err


def test_eintragen_serialisiert_ueber_advisory_lock(db_admin, pg_cluster):
    """Zweiter Aufruf wartet auf den Advisory-Lock der URL und sieht danach das Event des
    ersten (kein Duplikat). Deterministisch: Warten wird in pg_stat_activity beobachtet."""
    import threading
    import time

    _firma(db_admin)
    dsn = f"host=127.0.0.1 port={pg_cluster['port']} user=postgres dbname={db_admin.info.dbname}"
    ergebnis: dict[str, Any] = {}

    def zweiter() -> None:
        try:
            with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as c2:
                ergebnis["id"] = _eintragen(c2, name="Zweiter")
        except Exception as fehler:  # noqa: BLE001 - im Hauptthread geprueft
            ergebnis["fehler"] = fehler

    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as c1:
        with c1.transaction():
            c1.execute("select pg_advisory_xact_lock(hashtext('legacy:' || %s))", (URL,))
            erste = c1.execute(
                "insert into event.event (firma_id, kategorie, name, beginn, ende, quelle,"
                " legacy_url) select id, 'festival', 'Erster', %s, %s, 'legacy', %s"
                " from event.firma where kurz = 'testfirma' returning id",
                (BEGINN, BEGINN, URL)).fetchone()["id"]
            thread = threading.Thread(target=zweiter)
            thread.start()
            ende = time.monotonic() + 30
            wartet = False
            while time.monotonic() < ende and thread.is_alive():
                wartet = db_admin.execute(
                    "select count(*) > 0 as w from pg_locks where locktype = 'advisory'"
                    " and not granted").fetchone()["w"]
                if wartet:
                    break
                time.sleep(0.05)
            assert wartet, "zweiter Aufruf wartet nicht auf den Advisory-Lock"
            assert "id" not in ergebnis and "fehler" not in ergebnis
        # Transaktion committet: Lock frei, der zweite Aufruf laeuft weiter.
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert "fehler" not in ergebnis, ergebnis.get("fehler")
    assert ergebnis["id"] == erste
    zeilen = db_admin.execute("select name from event.event").fetchall()
    assert [z["name"] for z in zeilen] == ["Zweiter"]
