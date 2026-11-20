"""Testumgebung fuer "Zurueck" (Stufe A).

Die Tests brauchen ein echtes Postgres, weil Ausloeser und plpgsql in SQLite nicht
existieren. Sie laufen nur gegen ein Wegwerf-Postgres aus ``ZURUECK_TEST_DSN`` und
nie gegen die Supabase des Betriebs. Jeder Test bekommt ein frisches Schema.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

HIER = Path(__file__).resolve().parent
SCHEMA_AUSZUG = HIER / "schema_auszug.sql"
MIGRATION = HIER.parents[1] / "migrations" / "20261120_ideen_aenderungen.sql"
RUECKWEG = HIER.parents[1] / "migrations" / "rueckweg" / "20261120_ideen_aenderungen_rueckweg.sql"

# Adressen und Ports des Betriebs. Ein DSN, der dorthin zeigt, ist ein Bedienfehler.
_BETRIEB = ("100.67.177.45", "192.168.178.65", "offload-vm", ":54322", "port=54322", ":5432/", "port=5432 ")

UEBERWACHT = (
    "public.ideas",
    "public.canvas_nodes",
    "public.canvas_edges",
    "public.projects",
    "public.discovered_edges",
    "public.exploration_sessions",
    "public.shuttles",
    "public.research_report_artifacts",
    "public.mermaid_diagrams",
    "public.swe_design_runs",
    "marketing.broadcast_proposals",
    "marketing.crowdfunding_batches",
)


def _dsn() -> str:
    dsn = os.environ.get("ZURUECK_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("ZURUECK_TEST_DSN fehlt: kein Wegwerf-Postgres, Tests nicht gelaufen")
    if any(marke in dsn + " " for marke in _BETRIEB):
        pytest.exit(f"ZURUECK_TEST_DSN zeigt auf den Betrieb, Abbruch: {dsn!r}", returncode=2)
    return dsn


def _rollen_anlegen(verbindung) -> None:
    for rolle in ("anon", "authenticated", "service_role"):
        verbindung.execute(
            f"DO $$ BEGIN CREATE ROLE {rolle} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        )


def schema_frisch(verbindung, *, mit_migration: bool = True) -> None:
    verbindung.execute("DROP SCHEMA IF EXISTS marketing CASCADE")
    verbindung.execute("DROP SCHEMA IF EXISTS public CASCADE")
    verbindung.execute("CREATE SCHEMA public")
    verbindung.execute("GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role")
    verbindung.execute(SCHEMA_AUSZUG.read_text(encoding="utf-8"))
    verbindung.execute("GRANT USAGE ON SCHEMA marketing TO anon, authenticated, service_role")
    for tabelle in UEBERWACHT:
        # wie im Betrieb: anon und authenticated duerfen auf den Fachtabellen schreiben
        verbindung.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tabelle} TO anon, authenticated, service_role"
        )
    if mit_migration:
        verbindung.execute(MIGRATION.read_text(encoding="utf-8"))


@pytest.fixture()
def db():
    with psycopg.connect(_dsn(), autocommit=True) as verbindung:
        _rollen_anlegen(verbindung)
        schema_frisch(verbindung)
        yield verbindung


@pytest.fixture()
def db_roh():
    """Schema ohne die Migration, fuer Tests, die sie selbst einspielen."""
    with psycopg.connect(_dsn(), autocommit=True) as verbindung:
        _rollen_anlegen(verbindung)
        schema_frisch(verbindung, mit_migration=False)
        yield verbindung


class _Als:
    """Eine Transaktion mit (oder ohne) den Anfrage-Kopf, wie PostgREST ihn setzt."""

    def __init__(self, verbindung, kopf: dict | None, rolle: str | None = None):
        self._v = verbindung
        self._kopf = kopf
        self._rolle = rolle
        self._tx = None

    def __enter__(self):
        self._tx = self._v.transaction()
        self._tx.__enter__()
        wert = json.dumps(self._kopf) if self._kopf is not None else ""
        self._v.execute("SELECT set_config('request.headers', %s, true)", (wert,))
        if self._rolle:
            self._v.execute(f"SET LOCAL ROLE {self._rolle}")
        return self._v

    def __exit__(self, *fehler):
        return self._tx.__exit__(*fehler)


def als_agent(verbindung, gruppe: str, rolle: str | None = None) -> _Als:
    return _Als(verbindung, {"x-aenderung-id": gruppe}, rolle)


def als_hand(verbindung, rolle: str | None = None) -> _Als:
    return _Als(verbindung, None, rolle)


def protokoll(verbindung, where: str = "true", params: tuple = ()) -> list[dict]:
    with verbindung.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(f"SELECT * FROM public.ideen_aenderungen WHERE {where} ORDER BY id", params)
        return cur.fetchall()


def eine(verbindung, sql: str, params: tuple = ()):
    with verbindung.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def alle(verbindung, sql: str, params: tuple = ()) -> list[dict]:
    with verbindung.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()
