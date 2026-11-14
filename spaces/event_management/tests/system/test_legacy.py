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
