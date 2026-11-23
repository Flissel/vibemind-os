"""deploy/db_zugang.py -- Hilfe fuer deploy/db_zugang.sh (nur Standardbibliothek, Python 3.11).

Laeuft auf dem Mini-PC. Das Passwort kommt immer als eine Zeile von stdin, nie als Argument,
und erscheint in keiner Ausgabe:

  python3 deploy/db_zugang.py zugang [dienst|abholer]
      prueft den Namen des Zugangs und die Datenbank-Werte der DSN; nennt Rolle, Gruppe und DSN-Datei
      (Teil der Schlussmeldung von db_zugang.sh).
  printf '%s\\n' "$passwort" | python3 deploy/db_zugang.py sql [dienst|abholer] | docker exec -i ... psql ...
      SQL fuer psql: Anmelde-Rolle anlegen oder ihr Passwort neu setzen und sie zum Mitglied ihrer
      Gruppe machen. Das Passwort steht darin nur als SCRAM-Verifier
      (wie spaces/event_management/deploy/pg_hilfe.py), nie im Klartext.
  printf '%s\\n' "$passwort" | python3 deploy/db_zugang.py dsn-schreiben [dienst|abholer]
      schreibt die DSN-Datei des Zugangs (Modus 600, laufzeit/ mit 700) aus laufzeit/werte.env und dem
      Passwort.
  python3 deploy/db_zugang.py wert AUFTRAGSBUCH_DB_CONTAINER
      nennt einen der (nicht geheimen) Datenbank-Werte aus laufzeit/werte.env.

Zugaenge (ohne Angabe: dienst):
  dienst   Rolle auftragsbuch_dienst in brain_app (Migration 100), DSN laufzeit/dsn
  abholer  Rolle rowboat_abholer in rowboat_empfang (Migration 101), DSN laufzeit/abholer-dsn

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
from typing import NamedTuple, TextIO

DIENST = Path(__file__).resolve().parents[1]
PORT = 5432  # gilt, wenn AUFTRAGSBUCH_DB_PORT in laufzeit/werte.env fehlt
SCRAM_RUNDEN = 4096


class Zugang(NamedTuple):
    rolle: str
    gruppe: str
    migration: str
    dsn_datei: str


# Feste Zugaenge: Anmelde-Rolle, ihre einzige Gruppe (NOLOGIN, aus der Migration) und die DSN-Datei.
ZUGAENGE = {
    "dienst": Zugang("auftragsbuch_dienst", "brain_app", "100", "dsn"),
    "abholer": Zugang("rowboat_abholer", "rowboat_empfang", "101", "abholer-dsn"),
}

# Die Werte aus laufzeit/werte.env und ihre Form (ein Platzhalter `<...>` passt nie).
DB_WERTE = {
    "AUFTRAGSBUCH_DB_CONTAINER": re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"),
    "AUFTRAGSBUCH_DB_HOST": re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?"),
    "AUFTRAGSBUCH_DB_PORT": re.compile(r"[1-9][0-9]{0,4}"),
    "AUFTRAGSBUCH_DB_NAME": re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}"),
}
# Erzeugt von `openssl rand -hex 24`: nur Hex-Zeichen, darum nirgends Anfuehrungszeichen noetig.
_PASSWORT = re.compile(r"[0-9a-f]{32,128}")
_VERIFIER = re.compile(r"SCRAM-SHA-256\$[0-9]+:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+")


class ZugangsFehler(ValueError):
    """Bedienfehler; die Meldung nennt nie einen Wert."""


def zugang(name: str) -> Zugang:
    """Einer der festen Zugaenge; jeder andere Name ist ein Bedienfehler (die Meldung nennt ihn nicht)."""
    if name not in ZUGAENGE:
        raise ZugangsFehler("unbekannter Zugang (dienst oder abholer)")
    return ZUGAENGE[name]


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


def rollen_sql(verifier: str, name: str = "dienst") -> str:
    """psql-Skript: Rolle anlegen oder Passwort neu setzen, Mitglied ihrer Gruppe, sonst nichts.

    Eine vorhandene Rolle mit Sonderrechten oder weiteren Mitgliedschaften wird nicht angefasst; die
    Gruppe selbst bleibt NOLOGIN und unberuehrt.
    """
    z = zugang(name)
    if _VERIFIER.fullmatch(verifier) is None:
        raise ZugangsFehler("Verifier hat nicht die erwartete Form")
    rolle, gruppe = z.rolle, z.gruppe
    return f"""\\set ON_ERROR_STOP on
\\set VERBOSITY terse
\\set SHOW_CONTEXT never
BEGIN;
SET LOCAL lock_timeout = '5s';
DO $$
DECLARE v_sonder boolean;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{gruppe}') THEN
    RAISE EXCEPTION 'db_zugang: Rolle {gruppe} fehlt (erst Migration {z.migration} einspielen)'; END IF;
  SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls INTO v_sonder
    FROM pg_roles WHERE rolname = '{rolle}';
  IF NOT FOUND THEN
    CREATE ROLE {rolle} LOGIN INHERIT;
  ELSIF v_sonder THEN
    RAISE EXCEPTION 'db_zugang: {rolle} hat Sonderrechte und wird nicht angefasst';
  ELSIF EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid
                  JOIN pg_roles u ON u.oid = m.member
                 WHERE u.rolname = '{rolle}' AND g.rolname <> '{gruppe}') THEN
    RAISE EXCEPTION 'db_zugang: {rolle} gehoert weiteren Rollen an und wird nicht angefasst';
  END IF;
END $$;
ALTER ROLE {rolle} WITH LOGIN INHERIT PASSWORD '{verifier}';
GRANT {gruppe} TO {rolle};
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
    """Einer der Datenbank-Werte, geprueft; Meldung nennt nur den Namen."""
    muster = DB_WERTE.get(name)
    if muster is None:
        raise ZugangsFehler(f"{name} ist kein Datenbank-Wert dieses Skripts")
    wert = werte.get(name, "")
    if not wert:
        raise ZugangsFehler(f"{name} fehlt in laufzeit/werte.env")
    if muster.fullmatch(wert) is None or (name == "AUFTRAGSBUCH_DB_PORT" and int(wert) > 65535):
        raise ZugangsFehler(f"{name} in laufzeit/werte.env ungueltig (Platzhalter noch drin?)")
    return wert


def _ort(werte: Mapping[str, str]) -> tuple[str, str, str]:
    """(Host, Port, Datenbank) fuer die DSN, geprueft; ohne AUFTRAGSBUCH_DB_PORT gilt 5432."""
    port = db_wert(werte, "AUFTRAGSBUCH_DB_PORT") if "AUFTRAGSBUCH_DB_PORT" in werte else str(PORT)
    return db_wert(werte, "AUFTRAGSBUCH_DB_HOST"), port, db_wert(werte, "AUFTRAGSBUCH_DB_NAME")


def dsn_bauen(werte: Mapping[str, str], passwort: str, name: str = "dienst") -> str:
    """libpq-DSN (Schluesselwort-Form) der Anmelde-Rolle des Zugangs."""
    z = zugang(name)
    if _PASSWORT.fullmatch(passwort) is None:
        raise ZugangsFehler("Passwort hat nicht die erwartete Form (Hex, 32 bis 128 Zeichen)")
    host, port, datenbank = _ort(werte)
    return f"host={host} port={port} dbname={datenbank} user={z.rolle} password={passwort}"


def dsn_schreiben(dienst: Path, passwort: str, name: str = "dienst") -> Path:
    """Schreibt die DSN-Datei des Zugangs (600) in einem Zug; laufzeit/ bekommt 700."""
    z = zugang(name)
    werte = werte_lesen(dienst / "laufzeit" / "werte.env")
    dsn = dsn_bauen(werte, passwort, name)
    laufzeit = dienst / "laufzeit"
    laufzeit.mkdir(mode=0o700, exist_ok=True)
    os.chmod(laufzeit, 0o700)
    neu = laufzeit / f"{z.dsn_datei}.neu"
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (dsn + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(neu, 0o600)
    ziel = laufzeit / z.dsn_datei
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
        befehl, rest = (argv[0], argv[1:]) if argv else ("", [])
        if befehl in ("zugang", "sql", "dsn-schreiben") and len(rest) <= 1:
            name = rest[0] if rest else "dienst"
            z = zugang(name)
            if befehl == "zugang":
                _ort(werte_lesen(dienst / "laufzeit" / "werte.env"))
                aus.write(f"Rolle {z.rolle} bereit (Mitglied von {z.gruppe}), laufzeit/{z.dsn_datei}\n")
            elif befehl == "sql":
                if aus.isatty():
                    raise ZugangsFehler("sql gehoert in die Pipe zu psql, nicht aufs Terminal")
                aus.write(rollen_sql(scram_verifier(_passwort_von(ein)), name))
            else:
                dsn_schreiben(dienst, _passwort_von(ein), name)
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
