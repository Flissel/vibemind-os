"""Login-Rolle der Event-App (deploy/pg_hilfe.py app-rolle).

`event_app` ist NOLOGIN (001). Im Betrieb verbindet sich die App ueber eine Login-Rolle, die
Mitglied von `event_app` ist; Name und Passwort stehen in `laufzeit/event-app-dsn`. `migrieren`
legt sie nach den Migrationen an oder setzt das Passwort neu. Das Passwort geht nur als
SCRAM-Verifier an die Datenbank, nie im Klartext.
"""
from __future__ import annotations

import itertools
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql

SPACE = Path(__file__).resolve().parents[2]
DEPLOY = SPACE / "deploy"
GEHEIM = "geheim-app-rolle-0815-abcdefgh"
_NR = itertools.count(1)


def _hilfe():
    sys.path.insert(0, str(DEPLOY))
    try:
        import pg_hilfe  # noqa: PLC0415
    finally:
        sys.path.remove(str(DEPLOY))
    return pg_hilfe


@pytest.fixture
def rolle(db_admin: psycopg.Connection[dict[str, Any]]) -> Iterator[str]:
    """Eindeutiger Rollenname (Rollen sind clusterweit); am Ende geloescht."""
    name = f"event_dienst_t{next(_NR)}"
    yield name
    db_admin.execute(sql.SQL("drop role if exists {}").format(sql.Identifier(name)))


def _app_dsn(name: str, passwort: str = GEHEIM) -> str:
    return f"host=host.docker.internal port=54322 user={name} password={passwort} dbname=postgres"


def _info(conn: psycopg.Connection[dict[str, Any]], name: str) -> dict[str, Any]:
    zeile = conn.execute(
        "select r.rolcanlogin, r.rolsuper, a.rolpassword,"
        " pg_has_role(r.oid, 'event_app', 'member') as mitglied"
        " from pg_roles r join pg_authid a on a.oid = r.oid where r.rolname = %s", (name,)).fetchone()
    assert zeile is not None, name
    return zeile


def test_legt_login_rolle_als_mitglied_von_event_app_an(db_admin, rolle):
    h = _hilfe()
    h.app_rolle(db_admin, _app_dsn(rolle))
    info = _info(db_admin, rolle)
    assert info["rolcanlogin"] and not info["rolsuper"] and info["mitglied"]
    assert info["rolpassword"].startswith("SCRAM-SHA-256$")
    assert GEHEIM not in info["rolpassword"]


def test_zweiter_lauf_setzt_passwort_neu_und_bleibt_mitglied(db_admin, rolle):
    h = _hilfe()
    h.app_rolle(db_admin, _app_dsn(rolle))
    vorher = _info(db_admin, rolle)["rolpassword"]
    h.app_rolle(db_admin, _app_dsn(rolle, GEHEIM + "-neu"))
    nachher = _info(db_admin, rolle)
    assert nachher["rolpassword"] != vorher and nachher["mitglied"] and nachher["rolcanlogin"]


def test_rolle_mit_sonderrechten_wird_nicht_angefasst(db_admin, rolle):
    h = _hilfe()
    db_admin.execute(sql.SQL("create role {} createrole nologin").format(sql.Identifier(rolle)))
    with pytest.raises(h.HilfeFehler) as e:
        h.app_rolle(db_admin, _app_dsn(rolle))
    assert GEHEIM not in str(e.value)
    info = _info(db_admin, rolle)
    assert not info["rolcanlogin"] and not info["mitglied"]


def test_rolle_mit_fremder_mitgliedschaft_wird_nicht_angefasst(db_admin, rolle):
    # Review 2: Rechte kommen auch ueber Mitgliedschaften (pg_read_all_data, service_role ...).
    h = _hilfe()
    db_admin.execute(sql.SQL("create role {} nologin").format(sql.Identifier(rolle)))
    db_admin.execute(sql.SQL("grant pg_read_all_data to {}").format(sql.Identifier(rolle)))
    with pytest.raises(h.HilfeFehler):
        h.app_rolle(db_admin, _app_dsn(rolle))
    info = _info(db_admin, rolle)
    assert not info["rolcanlogin"] and not info["mitglied"]


class _Spion:
    """Reicht alles an die Verbindung durch und merkt sich die SQL-Texte von execute."""

    def __init__(self, conn):
        self._conn = conn
        self.texte: list[str] = []

    def execute(self, anfrage, *args, **kwargs):
        text = anfrage if isinstance(anfrage, str) else anfrage.as_string(self._conn)
        self.texte.append(text + " " + repr(args))
        return self._conn.execute(anfrage, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_passwort_geht_nie_im_klartext_an_die_datenbank(db_admin, rolle):
    # Review 5: der Server hasht Klartext selbst; pruefbar ist nur, was gesendet wird.
    h = _hilfe()
    spion = _Spion(db_admin)
    h.app_rolle(spion, _app_dsn(rolle))
    assert spion.texte and all(GEHEIM not in t for t in spion.texte)
    assert any("SCRAM-SHA-256$" in t and "password" in t.lower() for t in spion.texte)


def test_ohne_event_app_bricht_ab(leere_db_dsn):
    # event_app ist clusterweit und existiert nach anderen Tests womoeglich schon; geprueft wird
    # deshalb nur, dass die Meldung keine Werte nennt, wenn die DB ohne Schema event ist.
    h = _hilfe()
    with psycopg.connect(leere_db_dsn, autocommit=True) as conn:
        with pytest.raises(h.HilfeFehler) as e:
            h.app_rolle(conn, _app_dsn("event_dienst_ohne_schema"))
        assert GEHEIM not in str(e.value)
        assert conn.execute("select 1 from pg_roles where rolname = 'event_dienst_ohne_schema'").fetchone() is None


@pytest.mark.parametrize("dsn", [
    _app_dsn("event_app"), _app_dsn("postgres"), _app_dsn("supabase_admin"), _app_dsn("pg_monitor"),
    _app_dsn("Gross"), _app_dsn("a"), _app_dsn("event_dienst", "kurz"),
    _app_dsn("pgbouncer"), _app_dsn("pgsodium_keyholder"), _app_dsn("service_role"), _app_dsn("dienst_event"),
    "host=x port=1 user=event_dienst dbname=postgres",
    f"host=x port=1 password={GEHEIM} dbname=postgres",
    f"kein dsn {GEHEIM}",
])
def test_ungueltige_app_dsn_wird_vor_der_datenbank_abgelehnt(dsn):
    h = _hilfe()
    with pytest.raises(h.HilfeFehler) as e:
        h.app_rolle_aus_dsn(dsn)
    assert GEHEIM not in str(e.value)


def test_app_rolle_aus_dsn_liefert_name_und_passwort():
    h = _hilfe()
    assert h.app_rolle_aus_dsn(_app_dsn("event_dienst")) == ("event_dienst", GEHEIM)


def test_cli_app_rolle_nennt_keine_werte(tmp_path: Path, pg_cluster, db_admin, rolle, capsys):
    h = _hilfe()
    admin = tmp_path / "admin-dsn"
    admin.write_text(f"host=127.0.0.1 port={pg_cluster['port']} user=postgres dbname={db_admin.info.dbname}\n",
                     encoding="utf-8")
    app = tmp_path / "event-app-dsn"
    app.write_text(_app_dsn(rolle) + "\n", encoding="utf-8")
    assert h.main(["pg_hilfe.py", "app-rolle", str(admin), str(app)]) == 0
    aus = capsys.readouterr()
    assert GEHEIM not in aus.out + aus.err
    assert _info(db_admin, rolle)["mitglied"]
