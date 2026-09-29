"""vorlagen_einspielen (final-fix-findings.md, Minor): unveraenderte Vorlagen
werden uebersprungen, eine Freigabe wird nie zurueckgestuft. Ohne echte DB."""
import json

import pytest

from spaces.marketing.scripts import vorlagen_einspielen as ve
from spaces.marketing.sync import _db


class FalscheDB:
    def __init__(self, gespeichert: dict):
        self.gespeichert = gespeichert      # name -> {"bloecke":..., "status":...}
        self.sql: list[str] = []

    def one(self, sql, streng=False, **k):
        self.sql.append(sql)
        if "pult_bloecke_fehler" in sql:
            return {"f": None}
        if "FROM marketing.newsletter_vorlagen" in sql:
            for name, zeile in self.gespeichert.items():
                if f"name = '{name}'" in sql:
                    return zeile
            return None
        if "pult_vorlage_speichern" in sql:
            return {"n": 2}
        raise AssertionError(sql)


@pytest.fixture
def ordner(tmp_path, monkeypatch):
    for name in ("alpha", "beta"):
        (tmp_path / f"{name}.json").write_text(json.dumps(
            {"name": name, "beschreibung": name, "bloecke": {"root": {"type": "EmailLayout", "data": {"x": name}}}}),
            encoding="utf-8")
    monkeypatch.setattr(ve, "ORDNER", tmp_path)
    return tmp_path


def test_unveraendert_wird_uebersprungen(ordner, monkeypatch, capsys):
    db = FalscheDB({"alpha": {"bloecke": {"root": {"type": "EmailLayout", "data": {"x": "alpha"}}},
                              "status": "freigegeben"}})
    monkeypatch.setattr(_db, "query_one", db.one)
    assert ve.main(wirklich=True) == 0
    aus = capsys.readouterr().out
    assert "alpha.json: unveraendert" in aus and "beta.json: eingespielt" in aus
    schreiben = [s for s in db.sql if "pult_vorlage_speichern" in s]
    assert len(schreiben) == 1 and "'beta'" in schreiben[0]


def test_freigabe_wird_nie_zurueckgestuft(ordner, monkeypatch, capsys):
    db = FalscheDB({"alpha": {"bloecke": {"alt": 1}, "status": "freigegeben"},
                    "beta": {"bloecke": {"alt": 1}, "status": "vorschlag"}})
    monkeypatch.setattr(_db, "query_one", db.one)
    assert ve.main(wirklich=True) == 0
    for s in db.sql:
        if "pult_vorlage_speichern" in s:
            assert s.rstrip().endswith("'freigegeben') AS n") and "'vorschlag'" not in s


def test_ohne_wirklich_wird_nichts_geschrieben(ordner, monkeypatch, capsys):
    db = FalscheDB({})
    monkeypatch.setattr(_db, "query_one", db.one)
    assert ve.main(wirklich=False) == 0
    assert not [s for s in db.sql if "pult_vorlage_speichern" in s]
