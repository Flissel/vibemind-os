"""scripts/seite_mail_neu: ungesendete Mail-Fassungen verwerfen (Probelauf ohne Aenderung, --ausfuehren)."""
import pytest

from spaces.marketing.scripts import seite_mail_neu as skript
from spaces.marketing.sync import _db
from spaces.marketing.tests.test_pult_api import FalscheDB

A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def db(monkeypatch):
    f = FalscheDB()
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    return f


def test_probelauf_aendert_nichts(db):
    db.antworten.append([{"inhalt": A, "fassung": 2}, {"inhalt": B, "fassung": 1}])
    zeilen = []
    assert skript.main(False, zeilen.append) == 0
    assert len(db.sql) == 1 and "seite_mailfassungen" in db.sql[0]
    assert "eingereichte_fassung" in db.sql[0] and "freigegebene_fassung" in db.sql[0]
    assert not any("verwerfen" in s for s in db.sql)
    assert zeilen[-1].startswith("2 Mail-Fassung(en) - nichts geändert")


def test_ausfuehren_verwirft_jede_fassung(db):
    db.antworten += [[{"inhalt": A, "fassung": 2}, {"inhalt": B, "fassung": 1}],
                     [{"namen": ["ms-11111111-2-01.jpg"]}], [{"namen": []}]]
    zeilen = []
    assert skript.main(True, zeilen.append) == 0
    assert db.sql[1] == f"SELECT marketing.pult_seite_mail_verwerfen('{A}'::uuid, 2) AS namen"
    assert db.sql[2] == f"SELECT marketing.pult_seite_mail_verwerfen('{B}'::uuid, 1) AS namen"
    assert zeilen == [f"verworfen: {A} Fassung 2 (1 Bildstücke)", f"verworfen: {B} Fassung 1 (0 Bildstücke)"]


def test_keine_kandidaten(db):
    zeilen = []
    assert skript.main(True, zeilen.append) == 0
    assert len(db.sql) == 1 and zeilen == ["Keine ungesendeten Mail-Fassungen."]


def test_unsaubere_id_wird_nicht_ins_sql_gesetzt(db):
    db.antworten.append([{"inhalt": "x'; DROP TABLE y; --", "fassung": 1}])
    with pytest.raises(ValueError):
        skript.main(True, lambda _: None)
    assert len(db.sql) == 1
