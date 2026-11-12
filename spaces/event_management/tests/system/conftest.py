"""Wegwerf-PostgreSQL fuer die System-Tests.

- `pg_cluster` (Session): eigener Cluster per initdb/pg_ctl in tmp, freier Port,
  Rueckgabe ``dict(port=..., admin_dsn=...)``. Fehlen die Binaries, wird
  uebersprungen (``EVENT_PG_BIN``, Vorgabe PG16 unter Program Files).
- `db_admin` (Funktion): autocommit-Verbindung als Superuser `postgres` auf einer
  frischen DB `event_test_<n>` mit allen Migrationen (row_factory dict_row).
  Die DB wird am Testende geloescht.
- `db_app` (Funktion): Factory ``db_app(firmen=None, betreiber=False, person=None,
  event=None)`` fuer autocommit-Verbindungen als Login-Rolle `event_app_test`
  (Mitglied von `event_app`) auf derselben DB. Gesetzt werden nur die uebergebenen
  Kontextwerte `event.firmen`, `event.betreiber`, `event.person`, `event.event`.
"""
from __future__ import annotations

import itertools
import os
import socket
import subprocess
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from spaces.event_management.db import anwenden

PG_BIN_VORGABE = r"C:\Program Files\PostgreSQL\16\bin"
APP_ROLLE = "event_app_test"
_ZAEHLER = itertools.count(1)


def _programm(ordner: Path, name: str) -> Path:
    return ordner / (f"{name}.exe" if os.name == "nt" else name)


def _freier_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _dsn(port: int, dbname: str, user: str = "postgres") -> str:
    return f"host=127.0.0.1 port={port} user={user} dbname={dbname}"


def _log_ende(log: Path) -> str:
    try:
        return log.read_text(encoding="utf-8", errors="replace")[-2000:]
    except OSError:
        return "(kein Log)"


@pytest.fixture(scope="session")
def pg_cluster(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    pg_bin = Path(os.environ.get("EVENT_PG_BIN", PG_BIN_VORGABE))
    initdb = _programm(pg_bin, "initdb")
    pg_ctl = _programm(pg_bin, "pg_ctl")
    if not (initdb.is_file() and pg_ctl.is_file()):
        pytest.skip("PostgreSQL-Binaries fehlen: EVENT_PG_BIN")

    basis = tmp_path_factory.mktemp("pg")
    daten = basis / "daten"
    log = basis / "pg.log"

    # Ausgaben gehen in Dateien/DEVNULL, nie in eine Pipe: der Postmaster erbt
    # sonst die Pipe und subprocess.run wartet ewig.
    with open(basis / "initdb.log", "wb") as initdb_log:
        ergebnis = subprocess.run(
            [str(initdb), "-D", str(daten), "-A", "trust", "-U", "postgres",
             "-E", "UTF8", "--locale=C"],
            stdin=subprocess.DEVNULL, stdout=initdb_log, stderr=subprocess.STDOUT,
            timeout=180, check=False,
        )
    if ergebnis.returncode != 0:
        raise RuntimeError(f"initdb fehlgeschlagen:\n{_log_ende(basis / 'initdb.log')}")

    port = _freier_port()
    try:
        start = subprocess.run(
            [str(pg_ctl), "-D", str(daten), "-l", str(log), "-w", "-t", "60",
             "-o", f"-c listen_addresses=127.0.0.1 -p {port}", "start"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=90, check=False,
        )
        if start.returncode != 0:
            raise RuntimeError(f"pg_ctl start fehlgeschlagen:\n{_log_ende(log)}")
        yield {"port": port, "admin_dsn": _dsn(port, "postgres")}
    finally:
        subprocess.run(
            [str(pg_ctl), "-D", str(daten), "-m", "fast", "-w", "stop"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=60, check=False,
        )


@contextmanager
def _wegwerf_db(cluster: dict[str, Any]) -> Iterator[str]:
    """Legt eine leere DB `event_test_<n>` an und loescht sie danach wieder."""
    name = f"event_test_{next(_ZAEHLER)}"
    with psycopg.connect(cluster["admin_dsn"], autocommit=True) as admin:
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    try:
        yield name
    finally:
        with psycopg.connect(cluster["admin_dsn"], autocommit=True) as admin:
            admin.execute(
                sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(name))
            )


@pytest.fixture
def leere_db_dsn(pg_cluster: dict[str, Any]) -> Iterator[str]:
    """DSN (Superuser) einer frischen DB ohne Migrationen."""
    with _wegwerf_db(pg_cluster) as name:
        yield _dsn(pg_cluster["port"], name)


@pytest.fixture
def db_admin(pg_cluster: dict[str, Any]) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with _wegwerf_db(pg_cluster) as name:
        with psycopg.connect(
            _dsn(pg_cluster["port"], name), autocommit=True, row_factory=dict_row
        ) as conn:
            anwenden.anwenden(conn)
            # Rolle ist clusterweit; 001 hat event_app angelegt.
            conn.execute(
                sql.SQL(
                    "do $$ begin"
                    " if not exists (select 1 from pg_roles where rolname = {rolle_text}) then"
                    " create role {rolle} login;"
                    " end if; end $$"
                ).format(rolle_text=sql.Literal(APP_ROLLE), rolle=sql.Identifier(APP_ROLLE))
            )
            conn.execute(sql.SQL("grant event_app to {}").format(sql.Identifier(APP_ROLLE)))
            yield conn


@pytest.fixture
def db_app(
    pg_cluster: dict[str, Any], db_admin: psycopg.Connection[dict[str, Any]]
) -> Iterator[Callable[..., psycopg.Connection[dict[str, Any]]]]:
    dsn = _dsn(pg_cluster["port"], db_admin.info.dbname, user=APP_ROLLE)
    offen: list[psycopg.Connection[dict[str, Any]]] = []

    def verbinden(
        firmen: Sequence[object] | None = None,
        betreiber: bool = False,
        person: object | None = None,
        event: object | None = None,
    ) -> psycopg.Connection[dict[str, Any]]:
        conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        offen.append(conn)
        werte: dict[str, str] = {}
        if firmen is not None:
            werte["event.firmen"] = ",".join(str(f) for f in firmen)
        if betreiber:
            werte["event.betreiber"] = "an"
        if person is not None:
            werte["event.person"] = str(person)
        if event is not None:
            werte["event.event"] = str(event)
        for schluessel, wert in werte.items():
            conn.execute("select set_config(%s, %s, false)", (schluessel, wert))
        return conn

    try:
        yield verbinden
    finally:
        for conn in offen:
            conn.close()
