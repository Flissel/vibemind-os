"""scripts/seite_mail_neu: ALLE gespeicherten Mail-Fassungen verwerfen (Probelauf ohne Aenderung, --ausfuehren)."""
import re

import pytest

from spaces.marketing.scripts import seite_mail_neu as skript
from spaces.marketing.sync import _db
from spaces.marketing.tests.test_pult_api import FalscheDB

A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
C = "33333333-3333-3333-3333-333333333333"
# je ein Inhalt in jedem Zustand - auch eingereicht/freigegeben/versendet wird verworfen
ZEILEN = [{"inhalt": A, "fassung": 2, "status": "entwurf"}, {"inhalt": A, "fassung": 3, "status": "entwurf"},
          {"inhalt": B, "fassung": 1, "status": "eingereicht"}, {"inhalt": C, "fassung": 4, "status": "freigegeben"},
          {"inhalt": "44444444-4444-4444-4444-444444444444", "fassung": 1, "status": "versendet"}]


@pytest.fixture
def db(monkeypatch):
    f = FalscheDB()
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    return f


def test_auswahl_filtert_nicht():
    """Die Auswahl nimmt jede gespeicherte Mail-Fassung: kein WHERE, keine Bedingung auf Status oder Fassung."""
    sql = skript.AUSWAHL.lower()
    assert "from marketing.seite_mailfassungen m" in sql and "join marketing.inhalte i on i.id = m.inhalt" in sql
    assert not re.search(r"\b(where|having|limit)\b", sql)


def test_gruppiert_nach_status():
    k = [(z["inhalt"], z["fassung"], z["status"]) for z in ZEILEN]
    g = skript.gruppiert(k)
    assert list(g) == ["eingereicht", "entwurf", "freigegeben", "versendet"]
    assert g["entwurf"] == [(A, 2), (A, 3)] and sum(len(v) for v in g.values()) == len(ZEILEN)


def test_probelauf_aendert_nichts(db):
    db.antworten.append(list(ZEILEN))
    zeilen = []
    assert skript.main(False, zeilen.append) == 0
    assert len(db.sql) == 1 and db.sql[0] == skript.AUSWAHL
    assert "[eingereicht] 1 Mail-Fassung(en)" in zeilen and "[entwurf] 2 Mail-Fassung(en)" in zeilen
    assert f"  würde verwerfen: {C} Fassung 4" in zeilen
    assert zeilen[-1].startswith("5 Mail-Fassung(en) - nichts geändert")


def test_ausfuehren_verwirft_alle_auch_eingereichte_und_freigegebene(db):
    db.antworten += [list(ZEILEN)] + [[{"namen": ["ms-x.jpg"]}]] * len(ZEILEN)
    zeilen = []
    assert skript.main(True, zeilen.append) == 0
    verworfen = db.sql[1:]
    assert len(verworfen) == len(ZEILEN)
    for z in ZEILEN:
        assert (f"SELECT marketing.pult_seite_mail_verwerfen('{z['inhalt']}'::uuid, {z['fassung']}) AS namen"
                in verworfen)
    assert f"  verworfen: {B} Fassung 1 (1 Bildstücke)" in zeilen


def test_keine_kandidaten(db):
    zeilen = []
    assert skript.main(True, zeilen.append) == 0
    assert len(db.sql) == 1 and zeilen == ["Keine gespeicherten Mail-Fassungen."]


def test_unsaubere_id_wird_nicht_ins_sql_gesetzt(db):
    db.antworten.append([{"inhalt": "x'; DROP TABLE y; --", "fassung": 1, "status": "entwurf"}])
    with pytest.raises(ValueError):
        skript.main(True, lambda _: None)
    assert len(db.sql) == 1
