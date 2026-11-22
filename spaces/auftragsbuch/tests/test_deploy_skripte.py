"""Die Ausroll-Skripte wirklich mit bash ausfuehren, mit Attrappen fuer docker und openssl.

Kein Docker, keine Datenbank: `docker` und `openssl` sind kleine Skripte im PATH, die ihre
Argumente und ihr stdin mitschreiben. Geprueft wird, dass das Passwort nur in laufzeit/dsn landet,
nie als Argument, nie in einer Ausgabe, und dass psql nur den SCRAM-Verifier sieht. Ohne bash
(Linux) bzw. Git Bash (Windows) werden die Tests uebersprungen.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
PASSWORT = "0123456789abcdef" * 3
SCHLUESSEL = "schluessel-GEHEIM-42"
WERTE = ("AUFTRAGSBUCH_LADEN=laden-1\nAUFTRAGSBUCH_DB_CONTAINER=db-container-1\n"
         "AUFTRAGSBUCH_DB_HOST=db-host\nAUFTRAGSBUCH_DB_NAME=postgres\n")


# Unter Windows die bash von Git selbst (usr\bin), nicht den Starter aus Git\bin: der stellt seine
# eigenen Ordner vor den PATH, und das echte openssl gewaenne gegen die Attrappe. Nie die WSL-bash.
_GIT_USR_BIN = Path(r"C:\Program Files\Git\usr\bin")


def _bash() -> str | None:
    if os.name == "nt":
        git_bash = _GIT_USR_BIN / "bash.exe"
        return str(git_bash) if git_bash.exists() else None
    return shutil.which("bash")


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="bash nicht vorhanden")


def _skript(pfad: Path, text: str) -> None:
    pfad.write_bytes(text.encode("utf-8"))
    pfad.chmod(0o755)


@pytest.fixture
def dienst(tmp_path: Path) -> Path:
    """Kopie von deploy/ in einem leeren Dienst-Ordner, dazu Attrappen fuer docker, openssl, python3."""
    ordner = tmp_path / "auftragsbuch"
    (ordner / "deploy").mkdir(parents=True)
    for name in ("anbinden.sh", "db_zugang.sh", "db_zugang.py"):
        shutil.copy2(DEPLOY / name, ordner / "deploy" / name)
    attrappen = tmp_path / "bin"
    attrappen.mkdir()
    protokoll = tmp_path / "protokoll"
    protokoll.mkdir()
    _skript(attrappen / "docker", '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$PROTOKOLL/docker-args"\n'
                                  'cat >> "$PROTOKOLL/docker-stdin"\n')
    _skript(attrappen / "openssl", f'#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$PROTOKOLL/openssl-args"\n'
                                   f"printf '%s\\n' {PASSWORT}\n")
    _skript(attrappen / "python3", '#!/usr/bin/env bash\nexec "$ECHT_PYTHON" "$@"\n')
    return ordner


def _lauf(dienst: Path, skript: str, eingabe: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    assert BASH is not None
    env = dict(os.environ)
    attrappen = dienst.parent / "bin"
    werkzeuge = [str(attrappen)] + ([str(_GIT_USR_BIN)] if os.name == "nt" else [])
    env["PATH"] = os.pathsep.join([*werkzeuge, env.get("PATH", "")])
    env["PROTOKOLL"] = str(dienst.parent / "protokoll")
    env["ECHT_PYTHON"] = Path(sys.executable).as_posix()
    return subprocess.run([BASH, (dienst / "deploy" / skript).as_posix()], input=eingabe,
                          capture_output=True, env=env, timeout=120, check=False)


def _protokoll(dienst: Path, name: str) -> str:
    datei = dienst.parent / "protokoll" / name
    return datei.read_text(encoding="utf-8") if datei.exists() else ""


def test_db_zugang_ohne_werte_bricht_ab(dienst: Path) -> None:
    lauf = _lauf(dienst, "db_zugang.sh")
    assert lauf.returncode == 2 and b"laufzeit/werte.env fehlt" in lauf.stderr
    assert _protokoll(dienst, "docker-args") == ""


def test_db_zugang_mit_platzhalter_bricht_vor_der_datenbank_ab(dienst: Path) -> None:
    (dienst / "laufzeit").mkdir()
    (dienst / "laufzeit" / "werte.env").write_text("AUFTRAGSBUCH_DB_CONTAINER=<DB-CONTAINER>\n", encoding="utf-8")
    lauf = _lauf(dienst, "db_zugang.sh")
    assert lauf.returncode != 0 and b"AUFTRAGSBUCH_DB_CONTAINER" in lauf.stderr
    assert _protokoll(dienst, "docker-args") == "" and _protokoll(dienst, "openssl-args") == ""


def test_db_zugang_schickt_nur_den_verifier_und_schreibt_die_dsn(dienst: Path) -> None:
    (dienst / "laufzeit").mkdir()
    (dienst / "laufzeit" / "werte.env").write_text(WERTE, encoding="utf-8")
    lauf = _lauf(dienst, "db_zugang.sh")
    assert lauf.returncode == 0, lauf.stderr
    args, sql = _protokoll(dienst, "docker-args"), _protokoll(dienst, "docker-stdin")
    assert args.split() == ["exec", "-i", "db-container-1", "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1",
                            "-U", "supabase_admin", "-d", "postgres"]
    assert _protokoll(dienst, "openssl-args").split() == ["rand", "-hex", "24"]
    assert "ALTER ROLE auftragsbuch_dienst WITH LOGIN INHERIT PASSWORD 'SCRAM-SHA-256$4096:" in sql
    assert "GRANT brain_app TO auftragsbuch_dienst;" in sql
    assert PASSWORT not in args + sql
    assert PASSWORT.encode() not in lauf.stdout + lauf.stderr
    assert (dienst / "laufzeit" / "dsn").read_text(encoding="utf-8") == (
        f"host=db-host port=5432 dbname=postgres user=auftragsbuch_dienst password={PASSWORT}\n")
    assert b"laufzeit/dsn geschrieben" in lauf.stdout


def test_anbinden_ohne_dsn_bricht_ab(dienst: Path) -> None:
    lauf = _lauf(dienst, "anbinden.sh", (SCHLUESSEL + "\n").encode())
    assert lauf.returncode == 2 and b"laufzeit/dsn fehlt" in lauf.stderr
    assert not (dienst / "laufzeit" / "schluessel-hash").exists()


def test_anbinden_schreibt_nur_den_hash(dienst: Path) -> None:
    (dienst / "laufzeit").mkdir()
    (dienst / "laufzeit" / "dsn").write_text("host=h dbname=d user=u password=p\n", encoding="utf-8")
    lauf = _lauf(dienst, "anbinden.sh", (SCHLUESSEL + "\n").encode())
    assert lauf.returncode == 0, lauf.stderr
    assert (dienst / "laufzeit" / "schluessel-hash").read_text(encoding="utf-8").strip() == \
        hashlib.sha256(SCHLUESSEL.encode()).hexdigest()
    assert (dienst / "laufzeit" / "dsn").read_text(encoding="utf-8") == "host=h dbname=d user=u password=p\n"
    assert SCHLUESSEL.encode() not in lauf.stdout + lauf.stderr
    assert sorted(p.name for p in (dienst / "laufzeit").iterdir()) == ["dsn", "schluessel-hash"]


def test_anbinden_leere_zeile_bricht_ab(dienst: Path) -> None:
    (dienst / "laufzeit").mkdir()
    (dienst / "laufzeit" / "dsn").write_text("x\n", encoding="utf-8")
    lauf = _lauf(dienst, "anbinden.sh", b"\n")
    assert lauf.returncode == 2 and not (dienst / "laufzeit" / "schluessel-hash").exists()
