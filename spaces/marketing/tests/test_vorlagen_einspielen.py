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
    # nie eine echte DB: schreibende Aufrufe ohne eigenen Schreiber laufen ins Leere
    monkeypatch.setattr(_db, "_run_psql", lambda sql, container=None, streng=False: "")
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


class Schreiber:
    """Faengt die schreibenden psql-Aufrufe ab (UPDATE ... RETURNING name)."""

    def __init__(self, vorhanden=("newsletter", "einladung")):
        self.vorhanden = set(vorhanden)
        self.sql: list[str] = []

    def __call__(self, sql, container=None, streng=False):
        assert streng, "Schreiben nur streng (ON_ERROR_STOP)"
        self.sql.append(sql)
        if "zurueckgezogen" in sql:
            return "".join(f"{n}\n" for n in self.vorhanden if f"name = '{n}'" in sql)
        return "x\n"


def test_wirklich_gibt_frei_fuer_alle_und_zieht_alte_zurueck(ordner, monkeypatch, capsys):
    db, schreiber = FalscheDB({}), Schreiber()
    monkeypatch.setattr(_db, "query_one", db.one)
    monkeypatch.setattr(_db, "_run_psql", schreiber)
    assert ve.main(wirklich=True) == 0
    aus = capsys.readouterr().out
    fuer_alle = [s for s in schreiber.sql if "fuer_alle = true" in s]
    assert len(fuer_alle) == 2 and any("'alpha'" in s for s in fuer_alle) and any("'beta'" in s for s in fuer_alle)
    zurueck = [s for s in schreiber.sql if "status = 'zurueckgezogen'" in s]
    assert {n for n in ve.ALTE if any(f"name = '{n}'" in s for s in zurueck)} == set(ve.ALTE)
    assert all("status <> 'zurueckgezogen'" in s for s in zurueck)
    assert "newsletter: zurückgezogen" in aus and "einladung: zurückgezogen" in aus
    assert "ankuendigung: zurückgezogen" not in aus


def test_alte_mit_datei_bleiben(ordner, monkeypatch, capsys):
    (ordner / "newsletter.json").write_text(json.dumps(
        {"name": "newsletter", "beschreibung": "n", "bloecke": {"root": {"type": "EmailLayout", "data": {}}}}),
        encoding="utf-8")
    db, schreiber = FalscheDB({}), Schreiber()
    monkeypatch.setattr(_db, "query_one", db.one)
    monkeypatch.setattr(_db, "_run_psql", schreiber)
    assert ve.main(wirklich=True) == 0
    assert not [s for s in schreiber.sql if "zurueckgezogen" in s and "name = 'newsletter'" in s]


def test_bei_ungueltiger_datei_wird_nichts_zurueckgezogen(ordner, monkeypatch, capsys):
    db, schreiber = FalscheDB({}), Schreiber()
    (ordner / "gamma.json").write_text(json.dumps({"name": "falsch", "beschreibung": "", "bloecke": {}}),
                                       encoding="utf-8")
    monkeypatch.setattr(_db, "query_one", db.one)
    monkeypatch.setattr(_db, "_run_psql", schreiber)
    assert ve.main(wirklich=True) == 1
    assert not [s for s in schreiber.sql if "zurueckgezogen" in s]


def test_ohne_wirklich_kein_update(ordner, monkeypatch, capsys):
    db, schreiber = FalscheDB({}), Schreiber()
    monkeypatch.setattr(_db, "query_one", db.one)
    monkeypatch.setattr(_db, "_run_psql", schreiber)
    assert ve.main(wirklich=False) == 0
    assert schreiber.sql == []
    aus = capsys.readouterr().out
    assert "newsletter: würde zurückgezogen" in aus
