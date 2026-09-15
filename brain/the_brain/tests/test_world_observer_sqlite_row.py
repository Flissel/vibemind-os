"""Der sqlite_row-Check: unabhaengige Rueckfrage an einen lokalen Speicher.

Warum es ihn gibt: `schedule` schreibt nach SQLite, nicht nach Supabase. Seine
Operationen lesen nach dem Schreiben zwar zurueck (`ScheduleRepository.update`
und `.create`), aber dieser Beleg steckt INNERHALB der Operation — von der
Capability-Ebene aus ist er nicht von einem Selbstbericht zu unterscheiden.
Keiner der acht bisherigen Checks kann eine SQLite-Zeile lesen.

Der Wert der Rueckfrage stammt aus einem Regex ueber den Ergebnistext der
Operation. Er ist damit von aussen beeinflussbar und darf niemals zu SQL
werden — deshalb pruefen zwei Tests hier ausdruecklich den Einschleusversuch.
"""
import os
import sqlite3

import pytest

from core.world_observer import observe


@pytest.fixture(autouse=True)
def _ground_truth_on(monkeypatch):
    """Ohne diesen Schalter liefert observe() grundsaetzlich UNVERIFIED.

    Er wird BEIM IMPORT in eine Modulkonstante gelesen
    (`world_observer.py:45`), nicht bei jedem Aufruf. `monkeypatch.setenv`
    kommt darum zu spaet und wirkt nicht — die Konstante selbst muss gesetzt
    werden. Dieselbe Falle gilt fuer jeden Aufrufer: wer die Variable erst
    nach dem Import exportiert, bekommt stillschweigend keine Beobachtung.
    """
    from core import world_observer as wo
    monkeypatch.setattr(wo, "GROUND_TRUTH_ENABLED", True)


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "schedule.sqlite3"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE scheduled_tasks (id TEXT PRIMARY KEY, status TEXT)")
    con.execute("INSERT INTO scheduled_tasks VALUES (?, ?)",
                ("6f1e9c2a-4b77-4d31", "cancelled"))
    con.commit()
    con.close()
    return path


def _probe(store, **spec):
    return observe({"check": "sqlite_row", "path": str(store),
                    "table": "scheduled_tasks", **spec})


def test_an_existing_row_is_verified(store):
    assert _probe(store, match="id=6f1e9c2a-4b77-4d31").verdict == "VERIFIED"


def test_a_missing_row_is_refuted_not_merely_unverified(store):
    """Der Unterschied traegt die ganze Konstruktion: 'nicht da' ist ein
    Befund, 'konnte nicht nachsehen' ist keiner."""
    assert _probe(store, match="id=gibtsnicht").verdict == "REFUTED"


def test_the_expected_column_narrows_the_row(store):
    assert _probe(store, match="id=6f1e9c2a-4b77-4d31",
                  expect_column="status", expect_value="cancelled").verdict == "VERIFIED"


def test_a_wrong_expected_value_is_refuted(store):
    """Sonst wuerde ein 'cancel', das den Status gar nicht gesetzt hat,
    als Erfolg durchgehen, solange die Zeile ueberhaupt noch existiert."""
    assert _probe(store, match="id=6f1e9c2a-4b77-4d31",
                  expect_column="status", expect_value="active").verdict == "REFUTED"


def test_expect_absent_inverts_the_verdict(store):
    assert _probe(store, match="id=gibtsnicht", expect="absent").verdict == "VERIFIED"
    assert _probe(store, match="id=6f1e9c2a-4b77-4d31", expect="absent").verdict == "REFUTED"


# ---------------------------------------------------------------------
# Der Wert darf nicht zu SQL werden
# ---------------------------------------------------------------------

def test_a_value_that_looks_like_sql_stays_a_value(store):
    """`' OR '1'='1` ist als gebundener Parameter schlicht kein Treffer.
    Wuerde er interpoliert, waere das Ergebnis VERIFIED — und jede
    Rueckfrage damit wertlos."""
    assert _probe(store, match="id=x' OR '1'='1").verdict == "REFUTED"


def test_a_table_name_that_carries_sql_is_rejected(store):
    result = _probe(store, match="id=x", table="scheduled_tasks; DROP TABLE scheduled_tasks")
    assert result.verdict == "UNVERIFIED"
    assert "table" in result.reason
    con = sqlite3.connect(store)
    try:
        assert con.execute("SELECT count(*) FROM scheduled_tasks").fetchone()[0] == 1
    finally:
        con.close()


def test_a_match_column_that_carries_sql_is_rejected(store):
    assert _probe(store, match="id=1 OR 1=1").verdict == "REFUTED"
    assert _probe(store, match='id"=x').verdict == "UNVERIFIED"


# ---------------------------------------------------------------------
# Nicht nachsehen koennen ist nie ein Fehlschlag der Handlung
# ---------------------------------------------------------------------

def test_a_missing_file_is_unverified_never_refuted(tmp_path):
    result = observe({"check": "sqlite_row", "path": str(tmp_path / "weg.sqlite3"),
                      "table": "scheduled_tasks", "match": "id=x"})
    assert result.verdict == "UNVERIFIED"


def test_an_unknown_table_is_unverified(store):
    assert _probe(store, match="id=x", table="gibt_es_nicht").verdict == "UNVERIFIED"


def test_an_empty_value_does_not_produce_a_blind_query(store):
    """Ein leerer Platzhalter darf keine Rueckfrage `id=` erzeugen — sonst
    entstuende ein falsches REFUTED aus einem Fuellfehler."""
    assert _probe(store, match="id=").verdict == "UNVERIFIED"


def test_a_match_without_a_separator_is_unverified(store):
    assert _probe(store, match="id").verdict == "UNVERIFIED"


def test_the_path_may_come_from_the_environment(store, monkeypatch):
    monkeypatch.setenv("SCHEDULE_DB_PATH", str(store))
    result = observe({"check": "sqlite_row", "path_env": "SCHEDULE_DB_PATH",
                      "table": "scheduled_tasks", "match": "id=6f1e9c2a-4b77-4d31"})
    assert result.verdict == "VERIFIED"


def test_the_probe_never_writes(store):
    """Read-only geoeffnet: ein Beleg, der den Zustand aendert, waere keiner."""
    _probe(store, match="id=6f1e9c2a-4b77-4d31")
    con = sqlite3.connect(store)
    try:
        assert con.execute("SELECT status FROM scheduled_tasks").fetchone()[0] == "cancelled"
    finally:
        con.close()
