"""deploy/db_zugang.py -- Hilfe fuer deploy/db_zugang.sh (nur Standardbibliothek, Python 3.11).

Laeuft auf dem Mini-PC. Das Passwort kommt immer als eine Zeile von stdin, nie als Argument,
und erscheint in keiner Ausgabe:

  printf '%s\\n' "$passwort" | python3 deploy/db_zugang.py sql | docker exec -i ... psql ...
      SQL fuer psql: Anmelde-Rolle auftragsbuch_dienst anlegen oder ihr Passwort neu setzen und
      sie zum Mitglied von brain_app machen. Das Passwort steht darin nur als SCRAM-Verifier
      (wie spaces/event_management/deploy/pg_hilfe.py), nie im Klartext.
  printf '%s\\n' "$passwort" | python3 deploy/db_zugang.py dsn-schreiben
      schreibt laufzeit/dsn (Modus 600, laufzeit/ mit 700) aus laufzeit/werte.env und dem Passwort.
  python3 deploy/db_zugang.py wert AUFTRAGSBUCH_DB_CONTAINER
      nennt einen der drei (nicht geheimen) Datenbank-Werte aus laufzeit/werte.env.

Meldungen nennen nur Variablennamen, nie Werte. Exit 2 bei Bedienfehlern.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

DIENST = Path(__file__).resolve().parents[1]
ROLLE = "auftragsbuch_dienst"
PORT = 5432
SCRAM_RUNDEN = 4096

# Die drei Werte aus laufzeit/werte.env und ihre Form (ein Platzhalter `<...>` passt nie).
DB_WERTE = {
    "AUFTRAGSBUCH_DB_CONTAINER": re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"),
    "AUFTRAGSBUCH_DB_HOST": re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?"),
    "AUFTRAGSBUCH_DB_NAME": re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}"),
}
# Erzeugt von `openssl rand -hex 24`: nur Hex-Zeichen, darum nirgends Anfuehrungszeichen noetig.
_PASSWORT = re.compile(r"[0-9a-f]{32,128}")
_VERIFIER = re.compile(r"SCRAM-SHA-256\$[0-9]+:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+")


class ZugangsFehler(ValueError):
    """Bedienfehler; die Meldung nennt nie einen Wert."""


def scram_verifier(passwort: str, salz: bytes | None = None, runden: int = SCRAM_RUNDEN) -> str:
    """SCRAM-SHA-256-Verifier im Format von PostgreSQL (RFC 5802/7677).

    Fuer ein reines Hex-Passwort ist SASLprep die Identitaet; andere Passwoerter nimmt
    dieses Modul nicht an.
    """
    salz = secrets.token_bytes(16) if salz is None else salz
    gesalzen = hashlib.pbkdf2_hmac("sha256", passwort.encode("utf-8"), salz, runden)
    client_key = hmac.new(gesalzen, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(gesalzen, b"Server Key", hashlib.sha256).digest()

    def b64(roh: bytes) -> str:
        return base64.b64encode(roh).decode("ascii")

    return f"SCRAM-SHA-256${runden}:{b64(salz)}${b64(stored_key)}:{b64(server_key)}"


def rollen_sql(verifier: str) -> str:
    """psql-Skript: Rolle anlegen oder Passwort neu setzen, Mitglied von brain_app, sonst nichts.

    Eine vorhandene Rolle mit Sonderrechten oder weiteren Mitgliedschaften wird nicht angefasst.
    """
    if _VERIFIER.fullmatch(verifier) is None:
        raise ZugangsFehler("Verifier hat nicht die erwartete Form")
    return f"""\\set ON_ERROR_STOP on
\\set VERBOSITY terse
\\set SHOW_CONTEXT never
BEGIN;
SET LOCAL lock_timeout = '5s';
DO $$
DECLARE v_sonder boolean;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_app') THEN
    RAISE EXCEPTION 'db_zugang: Rolle brain_app fehlt (erst Migration 100 einspielen)'; END IF;
  SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls INTO v_sonder
    FROM pg_roles WHERE rolname = '{ROLLE}';
  IF NOT FOUND THEN
    CREATE ROLE {ROLLE} LOGIN INHERIT;
  ELSIF v_sonder THEN
    RAISE EXCEPTION 'db_zugang: {ROLLE} hat Sonderrechte und wird nicht angefasst';
  ELSIF EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid
                  JOIN pg_roles u ON u.oid = m.member
                 WHERE u.rolname = '{ROLLE}' AND g.rolname <> 'brain_app') THEN
    RAISE EXCEPTION 'db_zugang: {ROLLE} gehoert weiteren Rollen an und wird nicht angefasst';
  END IF;
END $$;
ALTER ROLE {ROLLE} WITH LOGIN INHERIT PASSWORD '{verifier}';
GRANT brain_app TO {ROLLE};
COMMIT;
"""


def werte_lesen(datei: Path) -> dict[str, str]:
    """NAME=Wert-Zeilen einer Env-Datei (Kommentare und Leerzeilen zaehlen nicht)."""
    try:
        text = datei.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ZugangsFehler(f"{datei.name} nicht lesbar") from None
    werte: dict[str, str] = {}
    for zeile in text.splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        name, wert = zeile.split("=", 1)
        werte[name.strip()] = wert.strip()
    return werte


def db_wert(werte: Mapping[str, str], name: str) -> str:
    """Einer der drei Datenbank-Werte, geprueft; Meldung nennt nur den Namen."""
    muster = DB_WERTE.get(name)
    if muster is None:
        raise ZugangsFehler(f"{name} ist kein Datenbank-Wert dieses Skripts")
    wert = werte.get(name, "")
    if not wert:
        raise ZugangsFehler(f"{name} fehlt in laufzeit/werte.env")
    if muster.fullmatch(wert) is None:
        raise ZugangsFehler(f"{name} in laufzeit/werte.env ungueltig (Platzhalter noch drin?)")
    return wert


def dsn_bauen(werte: Mapping[str, str], passwort: str) -> str:
    """libpq-DSN (Schluesselwort-Form) der Anmelde-Rolle."""
    if _PASSWORT.fullmatch(passwort) is None:
        raise ZugangsFehler("Passwort hat nicht die erwartete Form (Hex, 32 bis 128 Zeichen)")
    host = db_wert(werte, "AUFTRAGSBUCH_DB_HOST")
    name = db_wert(werte, "AUFTRAGSBUCH_DB_NAME")
    return f"host={host} port={PORT} dbname={name} user={ROLLE} password={passwort}"


def dsn_schreiben(dienst: Path, passwort: str) -> Path:
    """Schreibt laufzeit/dsn (600) in einem Zug; laufzeit/ bekommt 700."""
    werte = werte_lesen(dienst / "laufzeit" / "werte.env")
    dsn = dsn_bauen(werte, passwort)
    laufzeit = dienst / "laufzeit"
    laufzeit.mkdir(mode=0o700, exist_ok=True)
    os.chmod(laufzeit, 0o700)
    neu = laufzeit / "dsn.neu"
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (dsn + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(neu, 0o600)
    ziel = laufzeit / "dsn"
    os.replace(neu, ziel)
    return ziel


def _passwort_von(eingabe: TextIO) -> str:
    if eingabe.isatty():
        raise ZugangsFehler("das Passwort kommt per Pipe auf stdin, nicht vom Terminal")
    passwort = eingabe.readline().strip()
    if _PASSWORT.fullmatch(passwort) is None:
        raise ZugangsFehler("Passwort hat nicht die erwartete Form (Hex, 32 bis 128 Zeichen)")
    return passwort


def main(argv: list[str], eingabe: TextIO | None = None, ausgabe: TextIO | None = None,
         dienst: Path = DIENST) -> int:
    ein = sys.stdin if eingabe is None else eingabe
    aus = sys.stdout if ausgabe is None else ausgabe
    try:
        if argv == ["sql"]:
            if aus.isatty():
                raise ZugangsFehler("sql gehoert in die Pipe zu psql, nicht aufs Terminal")
            aus.write(rollen_sql(scram_verifier(_passwort_von(ein))))
            return 0
        if argv == ["dsn-schreiben"]:
            dsn_schreiben(dienst, _passwort_von(ein))
            return 0
        if len(argv) == 2 and argv[0] == "wert":
            werte = werte_lesen(dienst / "laufzeit" / "werte.env")
            aus.write(db_wert(werte, argv[1]) + "\n")
            return 0
    except ZugangsFehler as fehler:
        print(f"db_zugang: {fehler}", file=sys.stderr)
        return 2
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
