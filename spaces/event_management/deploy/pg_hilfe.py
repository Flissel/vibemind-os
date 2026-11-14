"""Hilfen fuer deploy/ausrollen.sh: Datenbank-Zugang ohne Geheimnis im Argument.

`pg_dump` und `psql` bekommen die Admin-DSN sonst als Argument (sichtbar in der
Prozessliste). Dieses Programm macht daraus eine Umgebungsdatei (PGHOST, PGPORT, PGUSER,
PGDATABASE, PGPASSWORD ...) mit Rechten 0600, die das Skript in einer Unter-Shell einliest
und danach loescht.

  python3 deploy/pg_hilfe.py env <dsn-datei> <ziel>   schreibt die Umgebungsdatei
  python3 deploy/pg_hilfe.py deps <requirements.txt>  nennt fehlende Module (eins je Zeile)

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
        if len(argv) == 4 and argv[1] == "env":
            try:
                dsn = Path(argv[2]).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                raise HilfeFehler("DSN-Datei nicht lesbar") from None
            umgebungsdatei(dsn_zu_umgebung(dsn), Path(argv[3]))
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
