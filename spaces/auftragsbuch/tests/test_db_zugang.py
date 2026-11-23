"""Tests der Hilfe deploy/db_zugang.py (Anmelde-Rolle auftragsbuch_dienst, laufzeit/dsn).

Ohne Datenbank: geprueft werden der SCRAM-Verifier gegen den Testvektor aus RFC 7677, das
erzeugte SQL, die DSN und dass kein Passwort in einer Ausgabe steht.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
sys.path.insert(0, str(DEPLOY))
import db_zugang  # noqa: E402

PASSWORT = "0123456789abcdef" * 3  # 48 Hex-Zeichen wie `openssl rand -hex 24`
WERTE = ("AUFTRAGSBUCH_LADEN=laden-1\n"
         "AUFTRAGSBUCH_DB_CONTAINER=db-container-1\n"
         "AUFTRAGSBUCH_DB_HOST=db-host\n"
         "AUFTRAGSBUCH_DB_NAME=postgres\n")


class _Pipe(io.StringIO):
    def isatty(self) -> bool:
        return False


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def _dienst(tmp_path: Path, werte: str = WERTE) -> Path:
    (tmp_path / "laufzeit").mkdir()
    (tmp_path / "laufzeit" / "werte.env").write_text(werte, encoding="utf-8")
    return tmp_path


def test_scram_verifier_passt_zum_testvektor_aus_rfc_7677() -> None:
    salz = base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ==")
    verifier = db_zugang.scram_verifier("pencil", salz, 4096)
    kopf, schluessel = verifier.split("$", 2)[1:]
    assert kopf == "4096:W22ZaJ0SNY7soEsUEjb6gQ=="
    stored_key, server_key = (base64.b64decode(t) for t in schluessel.split(":"))
    nonce = "rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
    auth = (f"n=user,r=rOprNGfwEbeRWgbNEkqO,r={nonce},s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,"
            f"c=biws,r={nonce}").encode("ascii")
    # ClientProof = ClientKey XOR HMAC(StoredKey, AuthMessage); H(ClientKey) muss StoredKey sein.
    beweis = base64.b64decode("dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=")
    signatur = hmac.new(stored_key, auth, hashlib.sha256).digest()
    client_key = bytes(a ^ b for a, b in zip(beweis, signatur))
    assert hashlib.sha256(client_key).digest() == stored_key
    assert hmac.new(server_key, auth, hashlib.sha256).digest() == base64.b64decode(
        "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4=")


def test_verifier_hat_frisches_salz() -> None:
    a, b = db_zugang.scram_verifier(PASSWORT), db_zugang.scram_verifier(PASSWORT)
    assert a != b and a.startswith("SCRAM-SHA-256$4096:") and PASSWORT not in a


def test_rollen_sql_nur_verifier_und_nur_brain_app() -> None:
    verifier = db_zugang.scram_verifier(PASSWORT)
    sql = db_zugang.rollen_sql(verifier)
    assert PASSWORT not in sql
    assert f"ALTER ROLE auftragsbuch_dienst WITH LOGIN INHERIT PASSWORD '{verifier}';" in sql
    assert "CREATE ROLE auftragsbuch_dienst LOGIN INHERIT;" in sql
    assert "GRANT brain_app TO auftragsbuch_dienst;" in sql
    assert sql.count("GRANT ") == 1 and "brain_app TO" in sql
    assert "\\set ON_ERROR_STOP on" in sql and "\\set VERBOSITY terse" in sql
    assert sql.index("BEGIN;") < sql.index("ALTER ROLE") < sql.index("COMMIT;")
    # brain_app selbst bekommt nie eine Anmeldung
    assert "ALTER ROLE brain_app" not in sql
    with pytest.raises(db_zugang.ZugangsFehler):
        db_zugang.rollen_sql("x'; DROP ROLE brain_app; --")


def test_dsn_aus_werten_und_passwort() -> None:
    dsn =db_zugang.dsn_bauen({"AUFTRAGSBUCH_DB_HOST": "db-host", "AUFTRAGSBUCH_DB_NAME": "postgres"}, PASSWORT)
    assert dsn == f"host=db-host port=5432 dbname=postgres user=auftragsbuch_dienst password={PASSWORT}"


@pytest.mark.parametrize("werte, name", [
    ({"AUFTRAGSBUCH_DB_NAME": "postgres"}, "AUFTRAGSBUCH_DB_HOST"),
    ({"AUFTRAGSBUCH_DB_HOST": "<DB-HOST>", "AUFTRAGSBUCH_DB_NAME": "postgres"}, "AUFTRAGSBUCH_DB_HOST"),
    ({"AUFTRAGSBUCH_DB_HOST": "db-host", "AUFTRAGSBUCH_DB_NAME": "x y"}, "AUFTRAGSBUCH_DB_NAME"),
])
def test_dsn_lehnt_fehlende_und_platzhalter_ab_ohne_wert(werte: dict, name: str) -> None:
    with pytest.raises(db_zugang.ZugangsFehler) as e:
        db_zugang.dsn_bauen(werte, PASSWORT)
    assert name in str(e.value) and PASSWORT not in str(e.value) and "<DB-HOST>" not in str(e.value)


@pytest.mark.parametrize("passwort", ["", "kurz", "G" * 48, PASSWORT + " x", "ab'cd" * 10])
def test_dsn_lehnt_fremdes_passwort_ab(passwort: str) -> None:
    with pytest.raises(db_zugang.ZugangsFehler) as e:
        db_zugang.dsn_bauen({"AUFTRAGSBUCH_DB_HOST": "h", "AUFTRAGSBUCH_DB_NAME": "d"}, passwort)
    if passwort:
        assert passwort not in str(e.value)


def test_dsn_schreiben_legt_nur_die_datei_an(tmp_path: Path) -> None:
    dienst = _dienst(tmp_path)
    ziel = db_zugang.dsn_schreiben(dienst, PASSWORT)
    assert ziel == dienst / "laufzeit" / "dsn"
    assert ziel.read_text(encoding="utf-8") == (
        f"host=db-host port=5432 dbname=postgres user=auftragsbuch_dienst password={PASSWORT}\n")
    assert sorted(p.name for p in (dienst / "laufzeit").iterdir()) == ["dsn", "werte.env"]
    if os.name == "posix":
        assert (ziel.stat().st_mode & 0o777) == 0o600
        assert ((dienst / "laufzeit").stat().st_mode & 0o777) == 0o700


def test_main_sql_gibt_nur_sql_ohne_passwort(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    aus = _Pipe()
    assert db_zugang.main(["sql"], _Pipe(PASSWORT + "\n"), aus, _dienst(tmp_path)) == 0
    assert "SCRAM-SHA-256$4096:" in aus.getvalue() and PASSWORT not in aus.getvalue()
    fehler = capsys.readouterr()
    assert fehler.out == "" and fehler.err == ""


def test_main_verweigert_terminal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dienst = _dienst(tmp_path)
    assert db_zugang.main(["sql"], _Terminal(PASSWORT + "\n"), _Pipe(), dienst) == 2
    assert db_zugang.main(["sql"], _Pipe(PASSWORT + "\n"), _Terminal(), dienst) == 2
    assert db_zugang.main(["dsn-schreiben"], _Terminal(PASSWORT + "\n"), _Pipe(), dienst) == 2
    assert not (dienst / "laufzeit" / "dsn").exists()
    assert PASSWORT not in capsys.readouterr().err


def test_main_dsn_schreiben_ohne_ausgabe(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dienst = _dienst(tmp_path)
    aus = _Pipe()
    assert db_zugang.main(["dsn-schreiben"], _Pipe(PASSWORT + "\n"), aus, dienst) == 0
    assert aus.getvalue() == "" and capsys.readouterr().err == ""
    assert PASSWORT in (dienst / "laufzeit" / "dsn").read_text(encoding="utf-8")


def test_main_wert_nennt_nur_die_drei_datenbank_werte(tmp_path: Path) -> None:
    dienst = _dienst(tmp_path)
    aus = _Pipe()
    assert db_zugang.main(["wert", "AUFTRAGSBUCH_DB_CONTAINER"], _Pipe(), aus, dienst) == 0
    assert aus.getvalue() == "db-container-1\n"
    assert db_zugang.main(["wert", "AUFTRAGSBUCH_LADEN"], _Pipe(), _Pipe(), dienst) == 2
    assert db_zugang.main(["wert", "AUFTRAGSBUCH_DB_NAME"], _Pipe(), _Pipe(), tmp_path / "fehlt") == 2


def test_rollen_sql_abholer_nur_rowboat_empfang() -> None:
    verifier = db_zugang.scram_verifier(PASSWORT)
    sql = db_zugang.rollen_sql(verifier, "abholer")
    assert PASSWORT not in sql
    assert "CREATE ROLE rowboat_abholer LOGIN INHERIT;" in sql
    assert f"ALTER ROLE rowboat_abholer WITH LOGIN INHERIT PASSWORD '{verifier}';" in sql
    assert "GRANT rowboat_empfang TO rowboat_abholer;" in sql
    assert sql.count("GRANT ") == 1
    assert "erst Migration 101 einspielen" in sql
    assert "g.rolname <> 'rowboat_empfang'" in sql
    assert "brain_app" not in sql and "auftragsbuch_dienst" not in sql
    assert "ALTER ROLE rowboat_empfang" not in sql


def test_unbekannter_zugang_wird_abgelehnt(tmp_path: Path) -> None:
    verifier = db_zugang.scram_verifier(PASSWORT)
    with pytest.raises(db_zugang.ZugangsFehler):
        db_zugang.rollen_sql(verifier, "postgres")
    dienst = _dienst(tmp_path)
    assert db_zugang.main(["sql", "postgres"], _Pipe(PASSWORT + "\n"), _Pipe(), dienst) == 2
    assert db_zugang.main(["sql", "abholer", "x"], _Pipe(PASSWORT + "\n"), _Pipe(), dienst) == 2


def test_dsn_mit_port_aus_den_werten() -> None:
    werte = {"AUFTRAGSBUCH_DB_HOST": "127.0.0.1", "AUFTRAGSBUCH_DB_NAME": "postgres", "AUFTRAGSBUCH_DB_PORT": "6543"}
    assert db_zugang.dsn_bauen(werte, PASSWORT, "abholer") == (
        f"host=127.0.0.1 port=6543 dbname=postgres user=rowboat_abholer password={PASSWORT}")


@pytest.mark.parametrize("port", ["<DB-PORT>", "0", "65536", "54 32", "-1"])
def test_dsn_lehnt_falschen_port_ab_ohne_wert(port: str) -> None:
    werte = {"AUFTRAGSBUCH_DB_HOST": "h", "AUFTRAGSBUCH_DB_NAME": "d", "AUFTRAGSBUCH_DB_PORT": port}
    with pytest.raises(db_zugang.ZugangsFehler) as e:
        db_zugang.dsn_bauen(werte, PASSWORT)
    assert "AUFTRAGSBUCH_DB_PORT" in str(e.value) and port not in str(e.value)


def test_main_abholer_schreibt_eigene_dsn(tmp_path: Path) -> None:
    dienst = _dienst(tmp_path, WERTE + "AUFTRAGSBUCH_DB_PORT=6543\n")
    assert db_zugang.main(["dsn-schreiben", "abholer"], _Pipe(PASSWORT + "\n"), _Pipe(), dienst) == 0
    assert sorted(p.name for p in (dienst / "laufzeit").iterdir()) == ["abholer-dsn", "werte.env"]
    assert (dienst / "laufzeit" / "abholer-dsn").read_text(encoding="utf-8") == (
        f"host=db-host port=6543 dbname=postgres user=rowboat_abholer password={PASSWORT}\n")
    if os.name == "posix":
        assert ((dienst / "laufzeit" / "abholer-dsn").stat().st_mode & 0o777) == 0o600


def test_main_zugang_prueft_werte_und_nennt_die_rolle(tmp_path: Path) -> None:
    dienst = _dienst(tmp_path)
    aus = _Pipe()
    assert db_zugang.main(["zugang", "abholer"], _Pipe(), aus, dienst) == 0
    assert aus.getvalue() == "Rolle rowboat_abholer bereit (Mitglied von rowboat_empfang), laufzeit/abholer-dsn\n"
    aus = _Pipe()
    assert db_zugang.main(["zugang"], _Pipe(), aus, dienst) == 0
    assert aus.getvalue() == "Rolle auftragsbuch_dienst bereit (Mitglied von brain_app), laufzeit/dsn\n"
    assert db_zugang.main(["zugang", "postgres"], _Pipe(), _Pipe(), dienst) == 2
    (tmp_path / "b").mkdir()
    schlecht = _dienst(tmp_path / "b", WERTE + "AUFTRAGSBUCH_DB_PORT=<DB-PORT>\n")
    assert db_zugang.main(["zugang", "abholer"], _Pipe(), _Pipe(), schlecht) == 2


def test_cli_ueber_stdin_ohne_passwort_in_der_ausgabe() -> None:
    lauf = subprocess.run([sys.executable, str(DEPLOY / "db_zugang.py"), "sql"],
                          input=(PASSWORT + "\n").encode("ascii"), capture_output=True, timeout=60, check=False)
    assert lauf.returncode == 0, lauf.stderr
    assert PASSWORT.encode() not in lauf.stdout + lauf.stderr
    assert lauf.stdout.startswith(b"\\set ON_ERROR_STOP on")
    assert lauf.stderr == b""
