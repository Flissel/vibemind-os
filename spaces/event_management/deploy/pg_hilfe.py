"""Hilfen fuer deploy/ausrollen.sh: Datenbank-Zugang ohne Geheimnis im Argument.

`pg_dump` und `psql` bekommen die Admin-DSN sonst als Argument (sichtbar in der
Prozessliste). Dieses Programm macht daraus eine Umgebungsdatei (PGHOST, PGPORT, PGUSER,
PGDATABASE, PGPASSWORD ...) mit Rechten 0600, die das Skript in einer Unter-Shell einliest
und danach loescht.

  python3 deploy/pg_hilfe.py env <dsn-datei> <ziel> [--docker]
      schreibt die Umgebungsdatei (Shell-Form; mit --docker roh fuer `docker run --env-file`)
  python3 deploy/pg_hilfe.py deps <requirements.txt>  nennt fehlende Module (eins je Zeile)
  python3 deploy/pg_hilfe.py app-rolle <admin-dsn-datei> <app-dsn-datei>
      legt die Login-Rolle der App-DSN an (Mitglied von event_app) oder setzt ihr Passwort neu

Ausgaben nennen nie Werte der DSN.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shlex
import sys
from pathlib import Path

# libpq-Parameter der DSN -> Umgebungsvariable. Alles andere wird abgelehnt (fail-closed),
# damit kein Parameter still verloren geht.
PARAMETER = {
    "host": "PGHOST", "hostaddr": "PGHOSTADDR", "port": "PGPORT", "user": "PGUSER",
    "dbname": "PGDATABASE", "password": "PGPASSWORD", "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT", "connect_timeout": "PGCONNECT_TIMEOUT",
    "application_name": "PGAPPNAME",
}
# Name im Paketindex -> Importname (nur wo er abweicht).
MODULE = {"pyyaml": "yaml", "psycopg-pool": "psycopg_pool", "psycopg": "psycopg"}


class HilfeFehler(ValueError):
    """Meldung enthaelt nie Werte."""


def dsn_zu_umgebung(dsn: str) -> dict[str, str]:
    try:
        from psycopg.conninfo import conninfo_to_dict
    except ImportError:
        raise HilfeFehler("psycopg fehlt auf dem Host") from None
    try:
        teile = conninfo_to_dict(dsn.strip())
    except Exception:  # noqa: BLE001 - Meldung der Bibliothek koennte Werte enthalten
        raise HilfeFehler("DSN nicht lesbar") from None
    umgebung: dict[str, str] = {}
    for name, wert in teile.items():
        ziel = PARAMETER.get(name)
        if ziel is None:
            raise HilfeFehler(f"DSN-Parameter {name} wird nicht unterstuetzt")
        umgebung[ziel] = str(wert)
    if "PGUSER" not in umgebung or "PGDATABASE" not in umgebung:
        raise HilfeFehler("DSN braucht user und dbname")
    return umgebung


def umgebungsdatei(umgebung: dict[str, str], ziel: Path) -> None:
    text = "".join(f"{k}={shlex.quote(v)}\n" for k, v in sorted(umgebung.items()))
    fd = os.open(ziel, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(ziel, 0o600)


ROLLEN_NAME = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
ROLLEN_VERBOTEN = {"event_app", "postgres", "authenticator", "anon", "authenticated", "service_role"}
PASSWORT_MINDESTLAENGE = 24


def app_rolle_aus_dsn(dsn: str) -> tuple[str, str]:
    """Name und Passwort der Login-Rolle aus der App-DSN; prueft beides vor jeder DB-Arbeit."""
    try:
        from psycopg.conninfo import conninfo_to_dict
    except ImportError:
        raise HilfeFehler("psycopg fehlt auf dem Host") from None
    try:
        teile = conninfo_to_dict(dsn.strip())
    except Exception:  # noqa: BLE001 - Meldung der Bibliothek koennte Werte enthalten
        raise HilfeFehler("App-DSN nicht lesbar") from None
    name = str(teile.get("user") or "")
    passwort = str(teile.get("password") or "")
    if not ROLLEN_NAME.match(name) or name in ROLLEN_VERBOTEN or name.startswith(("pg_", "supabase")):
        raise HilfeFehler("App-DSN: user muss eine eigene Rolle sein (a-z, 0-9, _; nicht event_app/postgres/supabase*)")
    if len(passwort) < PASSWORT_MINDESTLAENGE:
        raise HilfeFehler(f"App-DSN: password fehlt oder ist kuerzer als {PASSWORT_MINDESTLAENGE} Zeichen")
    return name, passwort


def app_rolle(conn: object, app_dsn: str) -> str:
    """Legt die Login-Rolle der App an (oder setzt ihr Passwort neu) und macht sie zum Mitglied
    von event_app. Das Passwort geht nur als SCRAM-Verifier an die DB. Rollen mit
    Sonderrechten fasst die Funktion nicht an. Gibt den Rollennamen zurueck."""
    from psycopg import sql

    name, passwort = app_rolle_aus_dsn(app_dsn)
    if conn.execute("select 1 from pg_roles where rolname = 'event_app'").fetchone() is None:  # type: ignore[attr-defined]
        raise HilfeFehler("Rolle event_app fehlt (erst die Migrationen)")
    if conn.execute("select 1 from pg_namespace where nspname = 'event'").fetchone() is None:  # type: ignore[attr-defined]
        raise HilfeFehler("Schema event fehlt (erst die Migrationen)")
    vorhanden = conn.execute(  # type: ignore[attr-defined]
        "select (rolsuper or rolcreaterole or rolcreatedb or rolreplication or rolbypassrls) as sonder"
        " from pg_roles where rolname = %s", (name,)).fetchone()
    if vorhanden is not None:
        sonder = vorhanden["sonder"] if isinstance(vorhanden, dict) else vorhanden[0]
        if sonder:
            raise HilfeFehler("Rolle der App-DSN hat Sonderrechte und wird nicht angefasst")
    verifier = conn.pgconn.encrypt_password(  # type: ignore[attr-defined]
        passwort.encode("utf-8"), name.encode("utf-8"), b"scram-sha-256").decode("ascii")
    befehl = "alter role {} with login password {}" if vorhanden is not None else "create role {} login password {}"
    with conn.transaction():  # type: ignore[attr-defined]
        conn.execute(sql.SQL(befehl).format(sql.Identifier(name), sql.Literal(verifier)))  # type: ignore[attr-defined]
        conn.execute(sql.SQL("grant event_app to {}").format(sql.Identifier(name)))  # type: ignore[attr-defined]
    return name


def docker_umgebungsdatei(umgebung: dict[str, str], ziel: Path) -> None:
    """Rohform fuer `docker run --env-file` (Werte ohne Quoting, eine Zeile je Variable)."""
    for wert in umgebung.values():
        if any(z in wert for z in ("\n", "\r", "\0")):
            raise HilfeFehler("DSN-Wert mit Zeilenumbruch passt nicht in eine Docker-Env-Datei")
    text = "".join(f"{k}={v}\n" for k, v in sorted(umgebung.items()))
    fd = os.open(ziel, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(ziel, 0o600)


def fehlende_module(requirements: Path) -> list[str]:
    fehlt: list[str] = []
    for zeile in requirements.read_text(encoding="utf-8").splitlines():
        zeile = zeile.split("#", 1)[0].strip()
        if not zeile:
            continue
        name = re.split(r"[=<>!~\[ ;]", zeile, maxsplit=1)[0].lower()
        modul = MODULE.get(name, name.replace("-", "_"))
        try:
            gefunden = importlib.util.find_spec(modul) is not None
        except (ImportError, ValueError):
            gefunden = False
        if not gefunden:
            fehlt.append(modul)
    return fehlt


def main(argv: list[str]) -> int:
    try:
        if argv[1:2] == ["env"] and (len(argv) == 4 or (len(argv) == 5 and argv[4] == "--docker")):
            try:
                dsn = Path(argv[2]).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                raise HilfeFehler("DSN-Datei nicht lesbar") from None
            schreiber = docker_umgebungsdatei if len(argv) == 5 else umgebungsdatei
            schreiber(dsn_zu_umgebung(dsn), Path(argv[3]))
            return 0
        if len(argv) == 4 and argv[1] == "app-rolle":
            try:
                admin_dsn = Path(argv[2]).read_text(encoding="utf-8").strip()
                app_dsn = Path(argv[3]).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                raise HilfeFehler("DSN-Datei nicht lesbar") from None
            app_rolle_aus_dsn(app_dsn)  # vor jeder Verbindung pruefen
            try:
                import psycopg
            except ImportError:
                raise HilfeFehler("psycopg fehlt auf dem Host") from None
            try:
                conn = psycopg.connect(admin_dsn, autocommit=True)
            except Exception:  # noqa: BLE001 - Meldung koennte Werte enthalten
                raise HilfeFehler("Verbindung mit der Admin-DSN gescheitert") from None
            with conn:
                try:
                    app_rolle(conn, app_dsn)
                except HilfeFehler:
                    raise
                except Exception as fehler:  # noqa: BLE001 - nur die Klasse, nie Werte
                    raise HilfeFehler(f"App-Rolle nicht angelegt ({type(fehler).__name__})") from None
            print("App-Rolle bereit (Mitglied von event_app)", file=sys.stderr)
            return 0
        if len(argv) == 3 and argv[1] == "deps":
            for modul in fehlende_module(Path(argv[2])):
                print(modul)
            return 0
    except HilfeFehler as fehler:
        print(f"Fehler: {fehler}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
