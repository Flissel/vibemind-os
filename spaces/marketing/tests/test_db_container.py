"""Container-Wahl in sync/_db.py (Final-Review C1, 2026-09-25-marketing-schalter).

Modus B (SUPABASE_SSH_HOST leer) muss SUPABASE_DB_CONTAINER beachten: auf der
VM heisst der Container debian-supabase-db-1, nicht vibemind_supabase-db —
ohne das scheitert dort jede Abfrage mit "container not running".
"""
import pytest

from spaces.marketing.sync import _db


@pytest.fixture(autouse=True)
def _sauberes_env(monkeypatch):
    # Keine .env-Fallback-Lesung und keine Werte aus der Prozessumgebung.
    monkeypatch.setattr(_db, "_config_loaded", True)
    for k in _db._CONFIG_KEYS:
        monkeypatch.delenv(k, raising=False)


def _docker_verboten():
    raise AssertionError("find_supabase_container darf nicht gerufen werden")


def test_modus_b_nimmt_container_aus_env_ohne_docker(monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_CONTAINER", "debian-supabase-db-1")
    monkeypatch.setattr(_db, "find_supabase_container", _docker_verboten)
    assert _db._resolve_container(None) == "debian-supabase-db-1"


def test_modus_b_ohne_env_sucht_lokal(monkeypatch):
    monkeypatch.setattr(_db, "find_supabase_container", lambda: "lokal-id")
    assert _db._resolve_container(None) == "lokal-id"


def test_modus_a_unveraendert(monkeypatch):
    monkeypatch.setenv("SUPABASE_SSH_HOST", "offload-vm")
    monkeypatch.setenv("SUPABASE_DB_CONTAINER", "debian-supabase-db-1")
    monkeypatch.setattr(_db, "find_supabase_container", _docker_verboten)
    assert _db._resolve_container(None) == "debian-supabase-db-1"


def test_modus_a_ohne_container_bleibt_fehler(monkeypatch):
    monkeypatch.setenv("SUPABASE_SSH_HOST", "offload-vm")
    with pytest.raises(RuntimeError, match="SUPABASE_DB_CONTAINER is missing"):
        _db._resolve_container(None)


def test_expliziter_container_gewinnt(monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_CONTAINER", "debian-supabase-db-1")
    monkeypatch.setattr(_db, "find_supabase_container", _docker_verboten)
    assert _db._resolve_container("explizit") == "explizit"
