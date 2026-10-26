"""werkzeuge-zugang-eintragen.sh (VM): Gleichlauf der Namen, Schreiben nur der eigenen Gruppe."""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

HEADLESS = Path(__file__).resolve().parents[1] / "deploy" / "headless"
SKRIPT = HEADLESS / "werkzeuge-zugang-eintragen.sh"


def _bash() -> str | None:
    pfad = shutil.which("bash")
    # WSL-bash (System32) sieht die Windows-Pfade anders - nur Git-Bash oder echtes Linux.
    if pfad is None or "system32" in pfad.lower():
        return None
    return pfad


BASH = _bash()
braucht_bash = pytest.mark.skipif(BASH is None, reason="bash nicht vorhanden")


def _lauf(*args: str, eingabe: str = "", umgebung: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(umgebung or {})}
    return subprocess.run([BASH, SKRIPT.as_posix(), *args], input=eingabe.encode(), capture_output=True,
                          env=env, timeout=60)


def _aeusseres_modul():
    for ort in HEADLESS.parents:
        kandidat = ort / "scripts" / "rowboat_zugang.py"
        if kandidat.is_file():
            import importlib.util
            spec = importlib.util.spec_from_file_location("rowboat_zugang_gleichlauf", kandidat)
            modul = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(modul)
            return modul
    return None


@braucht_bash
def test_namen_gleichlauf():
    gruppen = _lauf("--gruppen").stdout.decode().split()
    namen = {g: _lauf("--namen", g).stdout.decode().split() for g in gruppen}
    compose = yaml.safe_load((HEADLESS / "compose.yml").read_text(encoding="utf-8"))
    besitzer = compose["services"]["rowboat-werkzeuge"]["environment"]["GITHUB_BESITZER"]
    alle = {n for ns in namen.values() for n in ns}
    assert {p.split("=", 1)[1] for p in besitzer.split(",")} <= alle
    assert "PC_SUCHE_KEY" in alle
    modul = _aeusseres_modul()
    if modul is None:
        pytest.skip("aeusseres Repo mit scripts/rowboat_zugang.py nicht vorhanden")
    assert namen == modul.GRUPPEN


@braucht_bash
def test_unbekannte_gruppe_exit_2():
    assert _lauf("--namen", "evil").returncode == 2
    assert _lauf("evil", eingabe="x\n").returncode == 2


def _env(tmp_path: Path) -> Path:
    datei = tmp_path / "werkzeuge.env"
    datei.write_text("GITHUB_LESEN_VIBEMIND_LAB=alt-lab\nGITHUB_LESEN_FLISSEL=alt\n", encoding="utf-8")
    return datei


@braucht_bash
def test_schreibt_0600_und_ersetzt_nur_gruppe(tmp_path):
    datei = _env(tmp_path)
    r = _lauf("github_flissel", eingabe="github_pat_NEU123\n",
              umgebung={"WERKZEUGE_ENV": datei.as_posix(), "DOCKER": "false"})
    assert r.returncode == 0, r.stderr.decode()
    assert datei.read_text(encoding="utf-8").splitlines() == ["GITHUB_LESEN_VIBEMIND_LAB=alt-lab",
                                                              "GITHUB_LESEN_FLISSEL=github_pat_NEU123"]
    assert b"github_pat_NEU123" not in r.stdout + r.stderr
    assert b"nichts neu erzeugt" in r.stdout
    sicherungen = list(tmp_path.glob("werkzeuge.env.vor-github_flissel-*"))
    assert len(sicherungen) == 1 and "alt" in sicherungen[0].read_text(encoding="utf-8")
    if os.name != "nt":   # NTFS kennt keine Unix-Rechte
        assert stat.S_IMODE(datei.stat().st_mode) == 0o600
        assert stat.S_IMODE(sicherungen[0].stat().st_mode) == 0o600


@braucht_bash
def test_legt_fehlende_datei_an(tmp_path):
    datei = tmp_path / "werkzeuge.env"
    r = _lauf("pc_suche", eingabe="abc-DEF_123\r\n", umgebung={"WERKZEUGE_ENV": datei.as_posix(), "DOCKER": "false"})
    assert r.returncode == 0, r.stderr.decode()
    assert datei.read_text(encoding="utf-8") == "PC_SUCHE_KEY=abc-DEF_123\n"


@braucht_bash
@pytest.mark.parametrize("wert", ["mit leer", "a$b", "a#b", "'x'", ""])
def test_unzulaessiges_zeichen_nichts_geschrieben(tmp_path, wert):
    datei = _env(tmp_path)
    vorher = datei.read_bytes()
    r = _lauf("github_flissel", eingabe=wert + "\n", umgebung={"WERKZEUGE_ENV": datei.as_posix(), "DOCKER": "false"})
    assert r.returncode == 3
    assert datei.read_bytes() == vorher
    assert list(tmp_path.glob("werkzeuge.env.vor-*")) == []
    if wert:
        assert wert.encode() not in r.stdout + r.stderr


@braucht_bash
def test_erzeugt_nur_laufenden_dienst_neu(tmp_path):
    protokoll = tmp_path / "docker.log"
    attrappe = tmp_path / "docker-attrappe.sh"
    attrappe.write_text(f'#!/usr/bin/env bash\necho "$*" >> "{protokoll.as_posix()}"\n'
                        'if [ "$2" = "ps" ]; then echo abc123; fi\n', encoding="utf-8", newline="\n")
    datei = _env(tmp_path)
    r = _lauf("github_flissel", eingabe="neu\n",
              umgebung={"WERKZEUGE_ENV": datei.as_posix(), "DOCKER": f"bash {attrappe.as_posix()}"})
    assert r.returncode == 0, r.stderr.decode()
    zeilen = protokoll.read_text(encoding="utf-8").splitlines()
    assert zeilen == ["compose ps --status running -q rowboat-werkzeuge",
                      "compose up -d --no-deps --force-recreate rowboat-werkzeuge"]


def test_kein_set_x_und_read_s():
    t = SKRIPT.read_text(encoding="utf-8")
    assert "set -x" not in t.replace("set +x", "")
    assert "read -r -s" in t
    assert "umask 077" in t
    assert b"\r\n" not in SKRIPT.read_bytes()
    # Werte nie in echo-Ausgaben
    assert not re.search(r"echo[^\n]*\$\{?(wert|WERTE)", t)
