"""Ausroll-Skript (Etappe 4, Task 6): deploy/ausrollen.sh, deploy/pg_hilfe.py, deploy/ausrollen.md.

Das Skript laeuft hier nur gegen protokollierende Attrappen (docker, pg_dump, psql, python3,
tailscale, systemctl, sudo, curl) auf dem PATH, in einer Kopie des Space unter tmp_path;
nie gegen echte Dienste. Geprueft wird:
- ohne den Schalter tut es nichts,
- die Reihenfolge wird erzwungen (Stempel je Phase),
- `bauen` baut genau ein Image je Aufruf und gedeckelt,
- keine Werte in Ausgabe oder Protokoll (Argumente der Attrappen),
- jede Phase hat einen Rueckweg,
- die Messungen von `pruefen`, die Shim-Phase, der Hinweis vor `anbinden`.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SPACE = Path(__file__).resolve().parents[2]
DEPLOY = SPACE / "deploy"
SKRIPT = DEPLOY / "ausrollen.sh"
ANLEITUNG = DEPLOY / "ausrollen.md"
SCHALTER = "--ich-habe-ok"
# Abschluss-Review F-3: gebaut wird gleich nach `pruefen` (Task 7 braucht tags.env und das
# System-Image), `tailnet` kommt vor `anbinden` (erster Betreiber per Oberflaeche).
PHASEN = ["pruefen", "bauen-system", "bauen-web", "sichern", "migrieren", "shim", "dienste",
          "tailnet", "anbinden", "timer"]
BIS_DIENSTE = PHASEN[:PHASEN.index("dienste") + 1]
GEHEIM = "geheim-xyz-0815"
FAKE_TOKEN = "123456:" + "B" * 35


def _bash() -> str | None:
    kandidaten = [r"C:\Program Files\Git\bin\bash.exe"] if os.name == "nt" else []
    gefunden = shutil.which("bash")
    if gefunden and "system32" not in gefunden.lower():
        kandidaten.append(gefunden)
    return next((k for k in kandidaten if Path(k).is_file()), None)


BASH = _bash()
braucht_bash = pytest.mark.skipif(BASH is None, reason="keine bash")


def _code() -> str:
    text = SKRIPT.read_text(encoding="utf-8")
    return "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))


# --- Attrappen und Projektkopie ---------------------------------------------------------

ATTRAPPEN_ALLGEMEIN = ("tailscale", "sudo")
# `systemctl is-active` antwortet je Unit: aktiv sind die Units in FAKE_AKTIV (Leerzeichen-
# getrennt; ohne Variable die beiden Timer, kein Dienst laeuft gerade). Leere Variable: keine.
SYSTEMCTL = r"""#!/bin/sh
echo "systemctl $*" >> "$AR_PROTOKOLL"
if [ "$1" = is-active ]; then
  shift
  aktiv=" ${FAKE_AKTIV-bereitstellung.timer nachrichten.timer} "
  for u; do
    case "$u" in --*) continue ;; esac
    case "$aktiv" in *" $u "*) ;; *) exit 3 ;; esac
  done
fi
exit 0
"""
STAT = r"""#!/bin/sh
for _letzte; do :; done
if [ -f "$_letzte.modus" ]; then cat "$_letzte.modus"; echo; else echo 600; fi
"""

DOCKER = r"""#!/bin/sh
echo "docker $*" >> "$AR_PROTOKOLL"
case "$1 $2" in
  "image inspect")
    [ -n "${FAKE_IMAGE_FEHLT:-}" ] && case "$*" in *"$FAKE_IMAGE_FEHLT"*) exit 1;; esac
    case "$*" in *"-f"*) echo sha256:vorher123;; esac
    exit 0 ;;
  "network inspect") [ -n "${FAKE_NETZ:-}" ] && { printf '%s\n' "$FAKE_NETZ"; exit 0; }; exit 1 ;;
  "container inspect") exit 0 ;;
esac
if [ "$1" = run ]; then
  shift; ep=""; ef=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --entrypoint) ep=$2; shift 2 ;;
      --env-file) ef=$2; shift 2 ;;
      --network) shift 2 ;;
      --rm|-i) shift ;;
      *) break ;;
    esac
  done
  shift
  if [ -n "$ef" ]; then while IFS== read -r k v; do export "$k=$v"; done < "$ef"; fi
  exec "$(dirname "$0")/$ep" "$@"
fi
case "$1" in
  inspect) case "$*" in *Health*) echo "${FAKE_HEALTH:-healthy}";; *) echo sha256:vorher123;; esac ;;
  cp) case "$2" in event-management-claw:*) printf '{}' > "$3";; esac ;;
esac
exit 0
"""
PG_DUMP = r"""#!/bin/sh
echo "pg_dump $*" >> "$AR_PROTOKOLL"
[ "$1" = --version ] && { echo "pg_dump (PostgreSQL) ${FAKE_PGDUMP_HAUPT:-16}.4"; exit 0; }
[ "${PGPASSWORD:-}" = "geheim-xyz-0815" ] || exit 9
printf 'dump'
exit 0
"""
FLOCK = r"""#!/bin/sh
[ -n "${FAKE_FLOCK_HELD:-}" ] && exit 1
exit 0
"""
PSQL = r"""#!/bin/sh
echo "psql $*" >> "$AR_PROTOKOLL"
case "$*" in
  *pg_namespace*) [ "${FAKE_SCHEMA:-ja}" = ja ] && echo 1; exit 0 ;;
esac
echo "${FAKE_PG_NUM:-160004}"
"""
PYTHON3 = r"""#!/bin/sh
echo "python3 $*" >> "$AR_PROTOKOLL"
case "$1 $2" in
  "deploy/pg_hilfe.py env")
    printf 'PGHOST=db.example.org\nPGUSER=%s\nPGDATABASE=postgres\nPGPASSWORD=geheim-xyz-0815\n' "${FAKE_PGUSER:-supabase_admin}" > "$4" ;;
  "deploy/pg_hilfe.py deps") [ -n "${FAKE_DEPS_FEHLEN:-}" ] && echo "$FAKE_DEPS_FEHLEN" ;;
  "deploy/pg_hilfe.py app-rolle") [ -n "${FAKE_APP_ROLLE_FEHLER:-}" ] && exit 1 ;;
esac
exit 0
"""
CURL = r"""#!/bin/sh
echo "curl $*" >> "$AR_PROTOKOLL"
echo 200
"""
ANBINDEN = r"""#!/bin/sh
n=0
while IFS= read -r _z; do n=$((n+1)); done
echo "anbinden.sh stdin-zeilen=$n" >> "$AR_PROTOKOLL"
exit 0
"""


def _schreibe(datei: Path, text: str, modus: int = 0o755) -> None:
    datei.write_text(text, encoding="utf-8", newline="\n")
    datei.chmod(modus)


def modus(datei: Path, wert: str) -> None:
    """Dateimodus fuer die Attrappe `stat`: unter Windows wirkt chmod nicht (Git-Bash ohne ACL).

    Die Attrappe meldet 600, ausser neben der Datei liegt `<datei>.modus` mit dem Wert."""
    marke = datei.with_name(datei.name + ".modus")
    if wert == "600":
        marke.unlink(missing_ok=True)
    else:
        marke.write_text(wert, encoding="utf-8")


class Projekt:
    """Kopie des Space unter tmp/spaces/event_management mit Attrappen auf dem PATH."""

    def __init__(self, tmp: Path) -> None:
        self.wurzel = tmp
        self.space = tmp / "spaces" / "event_management"
        (self.space / "deploy").mkdir(parents=True)
        for name in ("ausrollen.sh", "pg_hilfe.py", "bereitstellung.service", "bereitstellung.timer",
                     "nachrichten.service", "nachrichten.timer"):
            shutil.copy(DEPLOY / name, self.space / "deploy" / name)
        for name in ("docker-compose.yml", "werte.beispiel.json", "requirements.txt"):
            shutil.copy(SPACE / name, self.space / name)
        _schreibe(self.space / "deploy" / "anbinden.sh", ANBINDEN)
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.protokoll = tmp / "aufrufe.log"
        for name in ATTRAPPEN_ALLGEMEIN:
            _schreibe(self.bin / name, f'#!/bin/sh\necho "{name} $*" >> "$AR_PROTOKOLL"\nexit 0\n')
        _schreibe(self.bin / "systemctl", SYSTEMCTL)
        _schreibe(self.bin / "docker", DOCKER)
        _schreibe(self.bin / "pg_dump", PG_DUMP)
        _schreibe(self.bin / "psql", PSQL)
        _schreibe(self.bin / "python3", PYTHON3)
        _schreibe(self.bin / "curl", CURL)
        _schreibe(self.bin / "stat", STAT)
        _schreibe(self.bin / "flock", FLOCK)
        self.etc = tmp / "etc"
        self.etc.mkdir()
        self.systemd = tmp / "systemd"
        self.systemd.mkdir()
        self.meminfo = tmp / "meminfo"
        self.meminfo.write_text("MemTotal: 16000000 kB\nMemAvailable: 8000000 kB\n", encoding="utf-8")
        self.lz = self.space / "laufzeit"
        (self.lz / "ausrollen").mkdir(parents=True)

    # Vorbereitung eines Zustands, in dem `pruefen` besteht
    def pruefen_bereit(self) -> None:
        wurzel = self._pfad_wie_bash(self.wurzel)
        for name in ("bereitstellung", "nachrichten"):
            dienst = self.space / "deploy" / f"{name}.service"
            text = re.sub(r"^WorkingDirectory=.*$", f"WorkingDirectory={wurzel}",
                          dienst.read_text(encoding="utf-8"), flags=re.M)
            text = re.sub(r"^ExecStart=\S+", f"ExecStart={wurzel}/spaces/event_management/.venv/bin/python",
                          text, flags=re.M)
            dienst.write_text(text, encoding="utf-8", newline="\n")
            self._geheim(self.etc / f"{name}.env", "EVENT_MCP_URL=http://event-mcp:8171\n")
        werte = '{\n' + ",\n".join(f'  "{k}": "x"' for k in
                                   ("EVENT_NAME", "EVENT_DATUM", "EVENT_ORT", "EVENT_APP_URL", "EVENT_MCP_URL",
                                    "TELEGRAM_GRUPPE", "WHATSAPP_ERLAUBT", "SHIM_URL")) + '\n}\n'
        (self.space / "werte.local.json").write_text(werte, encoding="utf-8")
        self._geheim(self.lz / "admin-dsn", f"host=db.example.org user=supabase_admin password={GEHEIM} dbname=postgres\n")
        for name in ("event-app-dsn", "event-sitzung-geheimnis", "event-telegram-token",
                     "event-smtp-passwort", "event-app.env"):
            self._geheim(self.lz / name, "platzhalter\n")
        (self.lz / "ausrollen" / "claim").write_text("WORKBOARD Zeile\n", encoding="utf-8")

    @staticmethod
    def _geheim(datei: Path, text: str) -> None:
        datei.write_text(text, encoding="utf-8", newline="\n")
        datei.chmod(0o600)

    @staticmethod
    def _pfad_wie_bash(pfad: Path) -> str:
        r = subprocess.run([BASH or "bash", "-c", f'cd "{pfad.as_posix()}" && pwd'], stdout=subprocess.PIPE,
                           check=True, timeout=60)
        return r.stdout.decode().strip()

    def stempel(self, *phasen: str) -> None:
        for p in phasen:
            (self.lz / "ausrollen" / f"{p}.ok").write_text("x\n", encoding="utf-8")

    def tags(self, system: str = "abc12345", web: str = "abc12345") -> None:
        (self.lz / "ausrollen" / "tags.env").write_text(
            f"EVENT_SYSTEM_TAG={system}\nEVENT_WEB_TAG={web}\n", encoding="utf-8")

    def lauf(self, *argumente: str, ok: bool = True, eingabe: str = "", umgebung: dict[str, str] | None = None):
        env = {**os.environ,
               "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
               "AR_PROTOKOLL": str(self.protokoll), "HOME": str(self.wurzel),
               "AUSROLLEN_STAT": self._pfad_wie_bash(self.bin) + "/stat",
               "AUSROLLEN_FLOCK": self._pfad_wie_bash(self.bin) + "/flock",
               "AUSROLLEN_PYTHON": self._pfad_wie_bash(self.bin) + "/python3",
               "AUSROLLEN_CURL": self._pfad_wie_bash(self.bin) + "/curl",
               "AUSROLLEN_MEMINFO": self._pfad_wie_bash(self.meminfo.parent) + "/" + self.meminfo.name,
               "AUSROLLEN_ETC": self._pfad_wie_bash(self.etc), "AUSROLLEN_SYSTEMD": self._pfad_wie_bash(self.systemd),
               **(umgebung or {})}
        for k in ("EVENT_SYSTEM_TAG", "EVENT_WEB_TAG", "EVENT_ADMIN_DSN_FILE", "EVENT_SHIM_BAUKONTEXT"):
            if umgebung is None or k not in umgebung:
                env.pop(k, None)
        befehl = [BASH, str(self.space / "deploy" / "ausrollen.sh"), *(([SCHALTER]) if ok else []), *argumente]
        return subprocess.run(befehl, input=eingabe.encode(), cwd=self.wurzel, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=120, check=False)

    def log(self) -> list[str]:
        if not self.protokoll.exists():
            return []
        return self.protokoll.read_text(encoding="utf-8").splitlines()

    def stempel_da(self, name: str) -> bool:
        return (self.lz / "ausrollen" / f"{name}.ok").exists()


@pytest.fixture
def pj(tmp_path: Path) -> Projekt:
    if BASH is None:
        pytest.skip("keine bash")
    return Projekt(tmp_path)


def _aus(lauf) -> tuple[str, str]:
    return lauf.stdout.decode("utf-8", "replace"), lauf.stderr.decode("utf-8", "replace")


# --- Ohne Schalter passiert nichts --------------------------------------------------------


@braucht_bash
@pytest.mark.parametrize("argumente", [
    [], ["pruefen"], ["dienste"], ["bauen", "system"], ["zurueck", "dienste"],
    ["--ich-habe-OK", "pruefen"], ["--ich-habe-ok=ja", "pruefen"], ["pruefen", "--ich-habe-okay"],
], ids=lambda a: " ".join(a) or "ohne")
def test_ohne_schalter_nichts(argumente, pj: Projekt):
    vorher = sorted(p.relative_to(pj.wurzel).as_posix() for p in pj.wurzel.rglob("*"))
    lauf = pj.lauf(*argumente, ok=False)
    out, err = _aus(lauf)
    assert lauf.returncode == 2, err
    assert SCHALTER in err and "nichts" in err and out == ""
    assert pj.log() == []
    assert sorted(p.relative_to(pj.wurzel).as_posix() for p in pj.wurzel.rglob("*")) == vorher


def test_schalter_steht_vor_jedem_programm():
    code = _code()
    davor = code[:code.index("exit 2")]
    assert SCHALTER in davor
    for programm in ("docker", "python", "mkdir", "rm ", "cd ", "$(", "cat ", "sudo"):
        assert programm not in davor, programm
    assert re.search(r"^\s*--ich-habe-ok\) ", davor, re.M)
    assert re.search(r"^set -eu$", code, re.M) and "set -o pipefail" in code and "umask 077" in code
    assert not re.search(r"set -[a-z]*x", code)


@braucht_bash
def test_bash_syntax_und_lf():
    r = subprocess.run([BASH, "-n", str(SKRIPT)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    for datei in (SKRIPT, DEPLOY / "pg_hilfe.py"):
        assert b"\r" not in datei.read_bytes(), datei.name


# --- Reihenfolge ----------------------------------------------------------------------------


@braucht_bash
def test_reihenfolge_erzwungen(pj: Projekt):
    pj.tags()
    pj.stempel("pruefen", "bauen-system", "bauen-web", "sichern")  # migrieren.ok fehlt
    lauf = pj.lauf("dienste")
    _, err = _aus(lauf)
    assert lauf.returncode == 1 and "migrieren" in err
    assert pj.log() == []  # kein einziger Aufruf
    assert not pj.stempel_da("dienste")


@braucht_bash
@pytest.mark.parametrize("phase,args,vorher,fehlt", [
    ("sichern", [], [], "pruefen"), ("sichern", [], ["pruefen"], "bauen-system"),
    ("sichern", [], ["pruefen", "bauen-system"], "bauen-web"),
    ("migrieren", [], ["pruefen", "bauen-system", "bauen-web"], "sichern"),
    ("bauen", ["system"], [], "pruefen"), ("bauen", ["web"], [], "pruefen"),
    ("shim", [], [], "pruefen"), ("anbinden", [], [], "pruefen"), ("timer", [], [], "pruefen"),
    ("tailnet", [], [], "pruefen"), ("anbinden", [], BIS_DIENSTE, "tailnet"),
])
def test_jede_phase_nennt_die_fehlende_vorphase(phase, args, vorher, fehlt, pj: Projekt):
    pj.stempel(*vorher)
    lauf = pj.lauf(phase, *args)
    _, err = _aus(lauf)
    assert lauf.returncode == 1 and f"Vorphase {fehlt} fehlt" in err
    assert pj.log() == []


@braucht_bash
def test_shim_braucht_beide_builds(pj: Projekt):
    pj.tags()
    pj.stempel("pruefen", "sichern", "migrieren", "bauen-system")
    lauf = pj.lauf("shim")
    assert lauf.returncode == 1 and "Vorphase bauen-web fehlt" in _aus(lauf)[1]


@braucht_bash
@pytest.mark.parametrize("was", ["system", "web"])
def test_bauen_braucht_nur_pruefen(was, pj: Projekt):
    """F-3: gebaut wird vor sichern/migrieren; jede bauen-Variante verlangt nur pruefen."""
    pj.stempel("pruefen")
    lauf = pj.lauf("bauen", was, umgebung={"EVENT_SYSTEM_TAG": "abc12345", "EVENT_WEB_TAG": "abc12345"})
    assert lauf.returncode == 0, _aus(lauf)[1]
    assert len([z for z in pj.log() if z.startswith("docker build")]) == 1
    assert pj.stempel_da(f"bauen-{was}")


@braucht_bash
def test_bauen_varianten_unabhaengig(pj: Projekt):
    """F-3: ein Neubau einer Variante loescht den Stempel der anderen nicht, wohl aber alle
    spaeteren Phasen; die spaeteren verlangen beide."""
    umg = {"EVENT_SYSTEM_TAG": "abc12345", "EVENT_WEB_TAG": "abc12345"}
    pj.stempel(*BIS_DIENSTE)
    assert pj.lauf("bauen", "system", umgebung=umg).returncode == 0
    assert pj.stempel_da("bauen-system") and pj.stempel_da("bauen-web") and pj.stempel_da("pruefen")
    for spaeter in ("sichern", "migrieren", "shim", "dienste"):
        assert not pj.stempel_da(spaeter), spaeter
    pj.stempel("sichern", "migrieren")
    assert pj.lauf("bauen", "web").returncode == 0
    assert pj.stempel_da("bauen-system") and pj.stempel_da("bauen-web")
    assert not pj.stempel_da("sichern") and not pj.stempel_da("migrieren")
    # sichern verlangt beide Builds
    (pj.lz / "ausrollen" / "bauen-web.ok").unlink()
    lauf = pj.lauf("sichern")
    assert lauf.returncode == 1 and "Vorphase bauen-web fehlt" in _aus(lauf)[1]


@braucht_bash
def test_unbekannte_phase(pj: Projekt):
    lauf = pj.lauf("loeschen")
    assert lauf.returncode == 64 and pj.log() == []


# --- bauen: einzeln und gedeckelt ----------------------------------------------------------


@braucht_bash
def test_bauen_einzeln_und_gedeckelt(pj: Projekt):
    pj.stempel("pruefen")
    lauf = pj.lauf("bauen", "system", umgebung={"EVENT_SYSTEM_TAG": "abc12345", "EVENT_WEB_TAG": "abc12345"})
    assert lauf.returncode == 0, _aus(lauf)[1]
    builds = [z for z in pj.log() if z.startswith("docker build")]
    assert len(builds) == 1
    assert "--memory=4g" in builds[0] and "--memory-swap=4g" in builds[0]
    assert "event-management/system:abc12345" in builds[0] and "Dockerfile.system" in builds[0]
    assert pj.stempel_da("bauen-system") and not pj.stempel_da("bauen-web")
    assert "EVENT_SYSTEM_TAG=abc12345" in (pj.lz / "ausrollen" / "tags.env").read_text(encoding="utf-8")

    pj.protokoll.unlink()
    lauf = pj.lauf("bauen", "web")
    assert lauf.returncode == 0, _aus(lauf)[1]
    builds = [z for z in pj.log() if z.startswith("docker build")]
    assert len(builds) == 1 and "--memory=4g" in builds[0] and "event-management/web:abc12345" in builds[0]


@braucht_bash
@pytest.mark.parametrize("args", [[], ["system", "web"], ["alles"], ["system", "x"]])
def test_bauen_nie_zwei_builds_in_einem_aufruf(args, pj: Projekt):
    pj.stempel("pruefen", "sichern", "migrieren")
    lauf = pj.lauf("bauen", *args, umgebung={"EVENT_SYSTEM_TAG": "abc12345", "EVENT_WEB_TAG": "abc12345"})
    assert lauf.returncode != 0
    assert not [z for z in pj.log() if z.startswith("docker build")]


@braucht_bash
def test_bauen_bricht_bei_wenig_speicher_ab(pj: Projekt):
    pj.stempel("pruefen", "sichern", "migrieren")
    pj.meminfo.write_text("MemAvailable: 900000 kB\n", encoding="utf-8")
    lauf = pj.lauf("bauen", "web", umgebung={"EVENT_SYSTEM_TAG": "abc12345", "EVENT_WEB_TAG": "abc12345"})
    assert lauf.returncode == 1 and "MemAvailable" in _aus(lauf)[1]
    assert not [z for z in pj.log() if z.startswith("docker build")]


def test_bauen_im_skript_gedeckelt_und_klassisch():
    code = _code()
    assert "DOCKER_BUILDKIT=0" in code
    assert len(re.findall(r"^\s*docker build\b", code, re.M)) == 3  # system, web, shim (je eigener Zweig)
    for z in re.findall(r"docker build[^\n]*(?:\\\n[^\n]*)*", code):
        assert "--memory=" in z and "--memory-swap=" in z, z
    assert not re.search(r"docker compose[^\n]* build\b", code) and not re.search(r"--build(?!-arg)", code)


@braucht_bash
def test_tags_widerspruch_bricht_ab(pj: Projekt):
    pj.stempel("pruefen", "sichern", "migrieren", "bauen-system", "bauen-web", "shim")
    pj.tags("abc12345", "abc12345")
    lauf = pj.lauf("dienste", umgebung={"EVENT_SYSTEM_TAG": "anderer", "EVENT_WEB_TAG": "abc12345"})
    assert lauf.returncode == 1 and "weicht" in _aus(lauf)[1]
    assert not [z for z in pj.log() if "compose" in z]


@braucht_bash
def test_dienste_exportiert_tags_und_startet_nur_die_drei(pj: Projekt):
    pj.stempel("pruefen", "sichern", "migrieren", "bauen-system", "bauen-web", "shim")
    pj.tags()
    lauf = pj.lauf("dienste")
    assert lauf.returncode == 0, _aus(lauf)[1]
    ups = [z for z in pj.log() if " up " in z]
    assert ups == ["docker compose --profile event-system up -d event-api event-mcp event-web"]
    assert any("127.0.0.1:8171/gesund" in z for z in pj.log())
    assert pj.stempel_da("dienste")


# --- pruefen ----------------------------------------------------------------------------------


@braucht_bash
def test_pruefen_besteht_und_stempelt(pj: Projekt):
    pj.pruefen_bereit()
    lauf = pj.lauf("pruefen")
    out, err = _aus(lauf)
    assert lauf.returncode == 0, out + err
    assert "FEHLT" not in out and pj.stempel_da("pruefen")
    # read-only: kein Build, kein up, kein Schreiben ausser Stempel
    log = " ".join(pj.log())
    for verboten in ("docker build", " up ", "docker run", "docker rm", "anwenden"):
        assert verboten not in log, verboten
    # pg_dump nur zur Versionsabfrage, nie ein Dump
    assert [z for z in pj.log() if z.startswith("pg_dump")] == ["pg_dump --version"]
    assert any(z.startswith("docker network inspect") for z in pj.log())
    assert not (pj.lz / "ausrollen" / "pg.env").exists()


@braucht_bash
@pytest.mark.parametrize("fall,veraendere,erwartet", [
    ("claim", lambda p: (p.lz / "ausrollen" / "claim").unlink(), "Claim"),
    ("werte", lambda p: (p.space / "werte.local.json").write_text('{"EVENT_NAME": "x"}', encoding="utf-8"), "EVENT_ORT"),
    ("modus", lambda p: modus(p.lz / "event-app-dsn", "644"), "event-app-dsn hat nicht 0600"),
    ("datei", lambda p: (p.lz / "event-smtp-passwort").unlink(), "event-smtp-passwort fehlt"),
    ("etc", lambda p: (p.etc / "nachrichten.env").unlink(), "nachrichten.env fehlt"),
])
def test_pruefen_meldet_fehlendes(fall, veraendere, erwartet, pj: Projekt):
    pj.pruefen_bereit()
    veraendere(pj)
    lauf = pj.lauf("pruefen")
    out, err = _aus(lauf)
    assert lauf.returncode == 1 and "FEHLT" in out and erwartet in out
    assert not pj.stempel_da("pruefen")


@braucht_bash
def test_pruefen_pg_14_ist_zu_alt(pj: Projekt):
    pj.pruefen_bereit()
    lauf = pj.lauf("pruefen", umgebung={"FAKE_PG_NUM": "140010"})
    assert lauf.returncode == 1 and "PostgreSQL >= 15 (gemessen: 14)" in _aus(lauf)[0]


@braucht_bash
def test_pruefen_image_fehlt(pj: Projekt):
    pj.pruefen_bereit()
    lauf = pj.lauf("pruefen", umgebung={"FAKE_IMAGE_FEHLT": "event-management/shim"})
    assert lauf.returncode == 1 and "FEHLT  Image event-management/shim" in _aus(lauf)[0]


@braucht_bash
def test_pruefen_adresse_14_belegt(pj: Projekt):
    pj.pruefen_bereit()
    frei = pj.lauf("pruefen", umgebung={"FAKE_NETZ": "event-management-shim 10.250.10.2/28"})
    assert frei.returncode == 0
    belegt = pj.lauf("pruefen", umgebung={"FAKE_NETZ": "andere-box 10.250.10.14/28"})
    assert belegt.returncode == 1 and "10.250.10.14" in _aus(belegt)[0] and "belegt" in _aus(belegt)[0]
    eigener = pj.lauf("pruefen", umgebung={"FAKE_NETZ": "event-mcp 10.250.10.14/28"})
    assert eigener.returncode == 0


@braucht_bash
def test_pruefen_python_umgebung_und_repo_pfad(pj: Projekt):
    pj.pruefen_bereit()
    lauf = pj.lauf("pruefen", umgebung={"FAKE_DEPS_FEHLEN": "psycopg_pool"})
    out = _aus(lauf)[0]
    assert lauf.returncode == 1 and "Python-Umgebung ohne Module: psycopg_pool" in out
    # Repo-Pfad der Units: falscher Pfad -> benannt (Pfad aus der .service-Datei)
    dienst = pj.space / "deploy" / "nachrichten.service"
    dienst.write_text(re.sub(r"^WorkingDirectory=.*$", "WorkingDirectory=/home/debian/vibemind-os",
                             dienst.read_text(encoding="utf-8"), flags=re.M), encoding="utf-8", newline="\n")
    lauf = pj.lauf("pruefen")
    assert lauf.returncode == 1 and "nachrichten.service erwartet das Repo unter /home/debian/vibemind-os" in _aus(lauf)[0]


@braucht_bash
def test_pruefen_ohne_python_umgebung_meldet_fehlt(pj: Projekt):
    pj.pruefen_bereit()
    lauf = pj.lauf("pruefen", umgebung={"AUSROLLEN_PYTHON": "/gibt/es/nicht/python"})
    out = _aus(lauf)[0]
    assert lauf.returncode == 1
    assert "FEHLT  Python-Umgebung fehlt: /gibt/es/nicht/python" in out
    assert not any("pg_hilfe.py deps" in z for z in pj.log())


@braucht_bash
def test_pruefen_unit_mit_system_python_meldet_fehlt(pj: Projekt):
    pj.pruefen_bereit()
    dienst = pj.space / "deploy" / "bereitstellung.service"
    dienst.write_text(re.sub(r"^ExecStart=\S+", "ExecStart=/usr/bin/python3", dienst.read_text(encoding="utf-8"),
                             flags=re.M), encoding="utf-8", newline="\n")
    lauf = pj.lauf("pruefen")
    out = _aus(lauf)[0]
    assert lauf.returncode == 1 and "bereitstellung.service startet /usr/bin/python3 statt" in out
    assert "ok     nachrichten.service nutzt die Python-Umgebung des Space" in out


def test_units_starten_die_python_umgebung_des_space():
    for name in ("bereitstellung", "nachrichten"):
        text = (DEPLOY / f"{name}.service").read_text(encoding="utf-8")
        assert re.search(r"^ExecStart=/home/debian/vibemind-os/spaces/event_management/\.venv/bin/python -m ",
                         text, flags=re.M), name
    assert ".venv/" in (SPACE / ".gitignore").read_text(encoding="utf-8").splitlines()


# --- sichern, migrieren ---------------------------------------------------------------------------


@braucht_bash
def test_sichern_ohne_schema_schreibt_marke_statt_dump(pj: Projekt):
    pj.pruefen_bereit()
    pj.stempel("pruefen", "bauen-system", "bauen-web")
    lauf = pj.lauf("sichern", umgebung={"FAKE_SCHEMA": "leer"})
    assert lauf.returncode == 0, _aus(lauf)[1]
    assert not any(z.startswith("pg_dump") for z in pj.log())
    assert any("pg_namespace" in z for z in pj.log())
    assert list((pj.lz / "sicherung").glob("event-*.keine-schema"))
    assert not list((pj.lz / "sicherung").glob("event-*.dump"))
    assert pj.stempel_da("sichern")


@braucht_bash
def test_sichern_dumpt_nur_schema_event_und_kopiert_openclaw_json(pj: Projekt):
    pj.pruefen_bereit()
    pj.stempel("pruefen", "bauen-system", "bauen-web")
    lauf = pj.lauf("sichern")
    assert lauf.returncode == 0, _aus(lauf)[1]
    dump = [z for z in pj.log() if z.startswith("pg_dump")]
    assert len(dump) == 1 and "-n event" in dump[0]
    assert list((pj.lz / "sicherung").glob("event-*.dump")) and list((pj.lz / "sicherung").glob("openclaw-*.json"))
    assert pj.stempel_da("sichern")
    assert not (pj.lz / "ausrollen" / "pg.env").exists()


@braucht_bash
def test_migrieren_nur_als_supabase_admin_und_dsn_aus_datei(pj: Projekt):
    pj.pruefen_bereit()
    pj.stempel("pruefen", "bauen-system", "bauen-web", "sichern")
    falsch = pj.lauf("migrieren", umgebung={"FAKE_PGUSER": "postgres"})
    assert falsch.returncode == 1 and "supabase_admin" in _aus(falsch)[1]
    assert not any("anwenden" in z for z in pj.log())
    lauf = pj.lauf("migrieren")
    assert lauf.returncode == 0, _aus(lauf)[1]
    log = pj.log()
    assert "python3 -m spaces.event_management.db.anwenden" in log
    rolle = [i for i, z in enumerate(log) if z.startswith("python3 deploy/pg_hilfe.py app-rolle ")]
    assert len(rolle) == 1 and rolle[0] > log.index("python3 -m spaces.event_management.db.anwenden")
    assert log[rolle[0]].endswith("laufzeit/event-app-dsn")
    assert pj.stempel_da("migrieren")


@braucht_bash
def test_migrieren_ohne_app_rolle_kein_stempel(pj: Projekt):
    pj.pruefen_bereit()
    pj.stempel("pruefen", "bauen-system", "bauen-web", "sichern")
    lauf = pj.lauf("migrieren", umgebung={"FAKE_APP_ROLLE_FEHLER": "1"})
    assert lauf.returncode == 1 and "Login-Rolle der App" in _aus(lauf)[1]
    assert not pj.stempel_da("migrieren")


@braucht_bash
def test_migrieren_pg_14_bricht_ab(pj: Projekt):
    pj.pruefen_bereit()
    pj.stempel("pruefen", "bauen-system", "bauen-web", "sichern")
    lauf = pj.lauf("migrieren", umgebung={"FAKE_PG_NUM": "140001"})
    assert lauf.returncode == 1 and not any("anwenden" in z for z in pj.log())
    assert "aelter als 15" in _aus(lauf)[1]  # nicht nur eine fehlende Vorphase


# --- shim ---------------------------------------------------------------------------------------


SHIM_DATEIEN = ("Dockerfile", "start.sh", "rowboat_shim_huelle.py", "claude_code_openai_shim.py",
                "claude_code_openai_tools.py", "claude_budget.py", "agent_budget.yaml")
# So sieht die Dockerfile nach dem Rezept in ausrollen.md aus: CMD auf 0.0.0.0:8125 (Healthcheck
# im Compose und SHIM_URL des Claw), nicht 127.0.0.1:8121 wie beim Rowboat-Shim.
SHIM_DOCKERFILE = ('FROM scratch\n'
                   'CMD ["python3", "/opt/shim/rowboat_shim_huelle.py", "--host", "0.0.0.0", "--port", "8125"]\n')
ROWBOAT_CMD = 'CMD ["python3", "/opt/shim/rowboat_shim_huelle.py", "--host", "127.0.0.1", "--port", "8121"]\n'


def _shim_kontext(pj: Projekt, *, ohne: str = "", dockerfile: str = SHIM_DOCKERFILE,
                  budget: str = "def budget_warnung(agent, heute, grenze):\n    pass\n") -> dict[str, str]:
    kontext = pj.wurzel / "shim-kontext"
    kontext.mkdir(exist_ok=True)
    for name in SHIM_DATEIEN:
        if name != ohne:
            inhalt = {"Dockerfile": dockerfile, "claude_budget.py": budget}.get(name, "# x\n")
            (kontext / name).write_text(inhalt, encoding="utf-8", newline="\n")
    return {"EVENT_SHIM_BAUKONTEXT": kontext.as_posix()}


def _shim_nichts_getan(pj: Projekt) -> None:
    assert not [z for z in pj.log() if z.startswith(("docker build", "docker tag", "docker compose"))]
    assert not pj.stempel_da("shim")


@braucht_bash
def test_shim_ohne_baukontext_bricht_ab(pj: Projekt):
    # Abschluss-Review F-5: ohne Neubau bliebe claude_budget.py alt (keine 80-%-Warnung).
    pj.stempel("pruefen", "sichern", "migrieren", "bauen-system", "bauen-web")
    pj.tags()
    lauf = pj.lauf("shim")
    assert lauf.returncode == 1 and "EVENT_SHIM_BAUKONTEXT" in _aus(lauf)[1]
    _shim_nichts_getan(pj)


@braucht_bash
@pytest.mark.parametrize("fehlt", SHIM_DATEIEN)
def test_shim_baukontext_unvollstaendig_bricht_ab(fehlt, pj: Projekt):
    pj.stempel("pruefen", "sichern", "migrieren", "bauen-system", "bauen-web")
    pj.tags()
    lauf = pj.lauf("shim", umgebung=_shim_kontext(pj, ohne=fehlt))
    assert lauf.returncode == 1 and fehlt in _aus(lauf)[1]
    _shim_nichts_getan(pj)


@braucht_bash
@pytest.mark.parametrize("fall,dockerfile,budget,erwartet", [
    ("altes claude_budget", SHIM_DOCKERFILE, "def pruefen(agent):\n    pass\n", "budget_warnung"),
    ("Rowboat-CMD unveraendert", "FROM scratch\n" + ROWBOAT_CMD, None, "8125"),
    ("ohne CMD", "FROM scratch\n", None, "8125"),
])
def test_shim_baukontext_inhalt_geprueft(fall, dockerfile, budget, erwartet, pj: Projekt):
    pj.stempel("pruefen", "sichern", "migrieren", "bauen-system", "bauen-web")
    pj.tags()
    extra = {} if budget is None else {"budget": budget}
    lauf = pj.lauf("shim", umgebung=_shim_kontext(pj, dockerfile=dockerfile, **extra))
    assert lauf.returncode == 1 and erwartet in _aus(lauf)[1], fall
    _shim_nichts_getan(pj)


@braucht_bash
def test_shim_mit_baukontext_baut_gedeckelt_und_merkt_den_rueckweg(pj: Projekt):
    pj.stempel("pruefen", "sichern", "migrieren", "bauen-system", "bauen-web")
    pj.tags()
    lauf = pj.lauf("shim", umgebung=_shim_kontext(pj))
    assert lauf.returncode == 0, _aus(lauf)[1]
    log = pj.log()
    assert any(z.startswith("docker tag event-management/shim:") and ":vorher" in z for z in log)
    builds = [z for z in log if z.startswith("docker build")]
    assert len(builds) == 1 and "--memory=4g" in builds[0]
    assert (pj.lz / "ausrollen" / "shim.vorher").read_text(encoding="utf-8").strip() == "sha256:vorher123"
    pj.protokoll.unlink()
    zurueck = pj.lauf("zurueck", "shim")
    assert zurueck.returncode == 0, _aus(zurueck)[1]
    assert any(z.startswith("docker tag sha256:vorher123 event-management/shim:") for z in pj.log())
    assert not pj.stempel_da("shim")


# --- anbinden --------------------------------------------------------------------------------------


@braucht_bash
def test_anbinden_verlangt_den_workspace_vergleich(pj: Projekt):
    pj.stempel(*BIS_DIENSTE, "tailnet")
    pj.tags()
    lauf = pj.lauf("anbinden", eingabe="a\nb\nc\nd\ne\nf\ng\n")
    assert lauf.returncode == 1 and "workspace-legacy" in _aus(lauf)[1]
    assert not any("anbinden.sh" in z for z in pj.log())


@braucht_bash
def test_anbinden_reicht_die_sieben_zeilen_durch_ohne_sie_zu_zeigen(pj: Projekt):
    pj.stempel(*BIS_DIENSTE, "tailnet")
    pj.tags()
    (pj.lz / "ausrollen" / "workspace-geprueft").write_text("x", encoding="utf-8")
    zeilen = [FAKE_TOKEN, "tg-" + GEHEIM, "Bearer " + GEHEIM, "oauth-" + GEHEIM, "m-" + GEHEIM, "l-" + GEHEIM, "a-" + GEHEIM]
    lauf = pj.lauf("anbinden", eingabe="\n".join(zeilen) + "\n")
    out, err = _aus(lauf)
    assert lauf.returncode == 0, err
    assert "anbinden.sh stdin-zeilen=7" in pj.log()
    assert any(z.startswith("python3 deploy/fuellen.py werte.local.json build/") for z in pj.log())
    for wert in zeilen:
        assert wert not in out + err and not any(wert in z for z in pj.log())
    assert pj.stempel_da("anbinden")


def _anbinden_bereit(pj: Projekt, *stempel: str) -> str:
    pj.stempel(*(stempel or (*BIS_DIENSTE, "tailnet")))
    pj.tags()
    (pj.lz / "ausrollen" / "workspace-geprueft").write_text("x", encoding="utf-8")
    return "a\nb\nc\nd\ne\nf\ng\n"


@braucht_bash
def test_anbinden_stoppt_den_bereitstellungs_timer(pj: Projekt):
    # Abschluss-Review F-5: der Arbeiter schreibt openclaw.json; anbinden.sh auch.
    eingabe = _anbinden_bereit(pj)
    lauf = pj.lauf("anbinden", eingabe=eingabe, umgebung={"FAKE_AKTIV": "bereitstellung.timer nachrichten.timer"})
    out, err = _aus(lauf)
    assert lauf.returncode == 0, err
    log = pj.log()
    stopp = log.index("sudo systemctl stop bereitstellung.timer")
    assert stopp < log.index("anbinden.sh stdin-zeilen=7")  # stdin nicht verbraucht
    assert not any("stop" in z and "nachrichten" in z for z in log)
    assert "--ich-habe-ok timer" in out


@braucht_bash
def test_anbinden_ohne_aktiven_timer_stoppt_nichts(pj: Projekt):
    eingabe = _anbinden_bereit(pj)
    lauf = pj.lauf("anbinden", eingabe=eingabe, umgebung={"FAKE_AKTIV": ""})
    assert lauf.returncode == 0, _aus(lauf)[1]
    assert not any("systemctl stop" in z for z in pj.log())
    assert "anbinden.sh stdin-zeilen=7" in pj.log()


@braucht_bash
def test_anbinden_bricht_ab_solange_der_arbeiter_laeuft(pj: Projekt):
    eingabe = _anbinden_bereit(pj)
    lauf = pj.lauf("anbinden", eingabe=eingabe,
                   umgebung={"FAKE_AKTIV": "bereitstellung.timer bereitstellung.service"})
    assert lauf.returncode == 1 and "bereitstellung.service" in _aus(lauf)[1]
    log = pj.log()
    assert "sudo systemctl stop bereitstellung.timer" in log  # Timer bleibt gestoppt
    assert not any("anbinden.sh" in z or "fuellen.py" in z for z in log)
    assert not pj.stempel_da("anbinden")


@braucht_bash
def test_anbinden_nach_timer_und_timer_startet_wieder(pj: Projekt):
    pj.pruefen_bereit()
    eingabe = _anbinden_bereit(pj, *PHASEN)
    lauf = pj.lauf("anbinden", eingabe=eingabe)
    assert lauf.returncode == 0, _aus(lauf)[1]
    assert "sudo systemctl stop bereitstellung.timer" in pj.log()
    assert pj.stempel_da("anbinden") and not pj.stempel_da("timer")  # T6-a
    pj.protokoll.unlink()
    wieder = pj.lauf("timer")
    assert wieder.returncode == 0, _aus(wieder)[1]
    assert "sudo systemctl enable --now bereitstellung.timer nachrichten.timer" in pj.log()
    assert pj.stempel_da("timer")


def test_anbinden_systemctl_ohne_stdin():
    # Die 7 Zeilen auf stdin gehoeren anbinden.sh; systemctl/sudo duerfen sie nicht lesen.
    code = _code()
    rumpf = code.split("phase_anbinden() {", 1)[1].split("\n}\n", 1)[0]
    zeilen = [z for z in rumpf.splitlines() if "systemctl" in z]
    assert len(zeilen) >= 3 and all("</dev/null" in z for z in zeilen)


# --- timer, tailnet ----------------------------------------------------------------------------------


@braucht_bash
def test_timer_installiert_und_startet_beide(pj: Projekt):
    pj.pruefen_bereit()
    pj.stempel(*BIS_DIENSTE, "tailnet", "anbinden")
    lauf = pj.lauf("timer")
    assert lauf.returncode == 0, _aus(lauf)[1]
    log = pj.log()
    assert any(z.startswith("sudo cp deploy/bereitstellung.service deploy/bereitstellung.timer") for z in log)
    assert any(z.startswith("sudo cp deploy/nachrichten.service deploy/nachrichten.timer") for z in log)
    assert "sudo systemctl enable --now bereitstellung.timer nachrichten.timer" in log
    assert pj.stempel_da("timer")


@braucht_bash
def test_tailnet_serve(pj: Projekt):
    pj.stempel(*BIS_DIENSTE)
    lauf = pj.lauf("tailnet")
    assert lauf.returncode == 0, _aus(lauf)[1]
    assert "tailscale serve --bg --https=3170 http://127.0.0.1:3170" in pj.log()


# --- keine Werte in der Ausgabe -------------------------------------------------------------------------


@braucht_bash
def test_keine_werte_in_ausgabe(pj: Projekt):
    pj.pruefen_bereit()
    gesammelt = ""
    for phase in (["pruefen"], ["sichern"], ["migrieren"]):
        if phase == ["sichern"]:
            pj.stempel("bauen-system", "bauen-web")  # gebaut wird zwischen pruefen und sichern
        lauf = pj.lauf(*phase, umgebung={"EVENT_SYSTEM_TAG": "abc12345", "EVENT_WEB_TAG": "abc12345"})
        assert lauf.returncode == 0, _aus(lauf)
        gesammelt += "".join(_aus(lauf))
    gesammelt += "\n".join(pj.log())
    assert GEHEIM not in gesammelt
    assert "password" not in gesammelt.lower()


def test_skript_gibt_keine_variablen_der_geheimnisse_aus():
    code = _code()
    # Nie ein Geheimnis-Ziel als Argument oder in echo/printf; DSN nur ueber die Datei.
    assert "$ADMIN_DSN_DATEI" in code
    for z in code.splitlines():
        if re.match(r"\s*(echo|info|abbruch)\b", z):
            assert not re.search(r"\$\{?(PGPASSWORD|ADMIN_DSN|DSN)\b", z), z
    assert "psql \"" not in code and "pg_dump \"" not in code


# --- zurueck ------------------------------------------------------------------------------------------------


@braucht_bash
def test_zurueck_je_phase_definiert(pj: Projekt):
    code = _code()
    zweig = code[code.index("phase_zurueck()"):code.index("PHASE=${1:-}")]
    for muster in ("pruefen|sichern)", "migrieren)", "bauen-system)", "bauen-web)", "shim)", "dienste)",
                   "anbinden)", "timer)", "tailnet)"):
        assert muster in zweig, muster
    pj.pruefen_bereit()
    pj.tags()
    (pj.lz / "sicherung").mkdir()
    (pj.lz / "sicherung" / "openclaw-20261114T000000Z.json").write_text("{}", encoding="utf-8")
    (pj.lz / "ausrollen" / "shim.vorher").write_text("sha256:vorher123\n", encoding="utf-8")
    pj.stempel(*PHASEN)
    rueckwaerts = [["timer"], ["anbinden"], ["tailnet"], ["dienste"], ["shim"], ["migrieren"]]
    for args in rueckwaerts:
        lauf = pj.lauf("zurueck", *args)
        assert lauf.returncode == 0, (args, _aus(lauf))
        assert "erledigt" in _aus(lauf)[0] or args == ["migrieren"]
    log = pj.log()
    assert "tailscale serve --https=3170 off" in log
    assert "sudo systemctl disable --now bereitstellung.timer nachrichten.timer" in log
    assert any(z.startswith("docker cp ") and "openclaw-20261114T000000Z.json" in z for z in log)
    assert "docker compose --profile event-system stop event-web event-mcp event-api" in log
    # migrieren: kein automatischer Eingriff in die Datenbank, Stempel bleibt bis zur Handarbeit
    assert not any("DROP" in z or "pg_restore" in z for z in log)
    assert pj.stempel_da("migrieren")
    (pj.lz / "ausrollen" / "migrieren.ok").unlink()
    assert pj.lauf("zurueck", "sichern").returncode == 0 and not pj.stempel_da("sichern")
    # F-3: die beiden bauen-Varianten sind voneinander unabhaengig, auch rueckwaerts.
    for args in (["bauen", "system"], ["bauen", "web"]):
        lauf = pj.lauf("zurueck", *args)
        assert lauf.returncode == 0, (args, _aus(lauf))
    log = pj.log()
    assert "docker rmi event-management/web:abc12345" in log and "docker rmi event-management/system:abc12345" in log
    assert pj.lauf("zurueck", "pruefen").returncode == 0 and not pj.stempel_da("pruefen")
    assert not pj.stempel_da("tailnet") and not pj.stempel_da("dienste")


@braucht_bash
def test_zurueck_bauen_verweigert_solange_sichern_gestempelt(pj: Projekt):
    pj.tags()
    pj.stempel("pruefen", "bauen-system", "bauen-web", "sichern")
    lauf = pj.lauf("zurueck", "bauen", "web")
    assert lauf.returncode == 1 and "erst 'zurueck sichern'" in _aus(lauf)[1]
    assert not any(z.startswith("docker rmi") for z in pj.log())


@braucht_bash
def test_zurueck_nur_rueckwaerts(pj: Projekt):
    pj.tags()
    pj.stempel("pruefen", "sichern", "migrieren", "bauen-system", "bauen-web", "shim", "dienste")
    lauf = pj.lauf("zurueck", "shim")
    assert lauf.returncode == 1 and "erst 'zurueck dienste'" in _aus(lauf)[1]
    assert pj.stempel_da("shim")
    assert not any("tag" in z.split()[1:2] for z in pj.log())


@braucht_bash
def test_zurueck_anbinden_ohne_sicherung_bricht_ab(pj: Projekt):
    pj.tags()
    pj.stempel(*BIS_DIENSTE, "tailnet", "anbinden")
    lauf = pj.lauf("zurueck", "anbinden")
    assert lauf.returncode == 1 and "keine Sicherung" in _aus(lauf)[1]


@braucht_bash
def test_zurueck_anbinden_frisches_volume_stoppt_nur_den_claw(pj: Projekt):
    pj.tags()
    pj.stempel(*BIS_DIENSTE, "tailnet", "anbinden")
    (pj.lz / "sicherung").mkdir()
    (pj.lz / "sicherung" / "openclaw-20261114T000000Z.keine").write_text("", encoding="utf-8")
    lauf = pj.lauf("zurueck", "anbinden")
    assert lauf.returncode == 0
    assert "docker compose stop event-management-claw" in pj.log()
    assert not any(z.startswith("docker rm") or "volume rm" in z or " down" in z for z in pj.log())


def test_keine_zerstoerenden_befehle_im_skript():
    code = _code()
    for verboten in ("docker volume rm", "docker compose down", "docker system prune", "docker rm ",
                     "service update", "stack deploy", "ssh ", "git reset", "git checkout", "DROP SCHEMA ",
                     "--force-recreate event-management-claw", "rm -rf"):
        # DROP SCHEMA steht nur in einer Hinweiszeile (info), nie als Befehl
        if verboten == "DROP SCHEMA ":
            for z in code.splitlines():
                if verboten in z:
                    assert z.lstrip().startswith("info "), z
            continue
        assert verboten not in code, verboten


# --- pg_hilfe -----------------------------------------------------------------------------------------------


def _hilfe():
    sys.path.insert(0, str(DEPLOY))
    try:
        import pg_hilfe  # noqa: PLC0415
    finally:
        sys.path.remove(str(DEPLOY))
    return pg_hilfe


def test_pg_hilfe_dsn_zu_umgebung_und_datei(tmp_path: Path):
    h = _hilfe()
    env = h.dsn_zu_umgebung(f"host=db.example.org port=5432 user=supabase_admin dbname=postgres password='{GEHEIM} x'")
    assert env == {"PGHOST": "db.example.org", "PGPORT": "5432", "PGUSER": "supabase_admin",
                   "PGDATABASE": "postgres", "PGPASSWORD": f"{GEHEIM} x"}
    ziel = tmp_path / "pg.env"
    h.umgebungsdatei(env, ziel)
    if os.name != "nt":
        assert oct(ziel.stat().st_mode & 0o777) == "0o600"
    sh = subprocess.run([BASH or "bash", "-c", f'set -a; . "{ziel.as_posix()}"; printf %s "$PGPASSWORD"'],
                        stdout=subprocess.PIPE, check=True, timeout=60)
    assert sh.stdout.decode() == f"{GEHEIM} x"


def test_pg_hilfe_uri_dsn_und_fehler_ohne_werte():
    h = _hilfe()
    assert h.dsn_zu_umgebung("postgresql://u:pw@db.example.org:5432/postgres")["PGUSER"] == "u"
    with pytest.raises(h.HilfeFehler) as e:
        h.dsn_zu_umgebung(f"host=x user=u dbname=d password={GEHEIM} options=-cfoo=bar")
    assert GEHEIM not in str(e.value)
    with pytest.raises(h.HilfeFehler) as e2:
        h.dsn_zu_umgebung(f"kein dsn {GEHEIM}")
    assert GEHEIM not in str(e2.value)


def test_pg_hilfe_deps(tmp_path: Path):
    h = _hilfe()
    req = tmp_path / "r.txt"
    req.write_text("PyYAML==6.0.3\npsycopg[binary]==3.3.5\nnicht-da-xyz==1.0  # x\n", encoding="utf-8")
    assert h.fehlende_module(req) == ["nicht_da_xyz"]
    # die echte requirements.txt bildet auf importierbare Namen ab (Abbildung, nicht Installation)
    namen = [re.split(r"[=<>!~\[ ;]", z, maxsplit=1)[0].lower() for z in
             (SPACE / "requirements.txt").read_text(encoding="utf-8").splitlines() if z.strip() and not z.startswith("#")]
    assert "pyyaml" in namen and "psycopg-pool" in namen


@braucht_bash
def test_pruefen_ohne_pg_werkzeuge_auf_dem_host_meldet_fehlt(pj: Projekt):
    pj.pruefen_bereit()
    if shutil.which("psql") is not None or shutil.which("pg_dump") is not None:
        pytest.skip("echte psql/pg_dump im PATH")
    (pj.bin / "psql").unlink()
    (pj.bin / "pg_dump").unlink()
    lauf = pj.lauf("pruefen")
    assert lauf.returncode == 1 and "psql/pg_dump fehlen auf dem Host" in _aus(lauf)[0]


@braucht_bash
def test_pruefen_mit_pg_image_nutzt_docker_run_mit_env_datei(pj: Projekt):
    pj.pruefen_bereit()
    (pj.lz / "ausrollen" / "pg-image").write_text("public.ecr.aws/supabase/postgres:17.6\n", encoding="utf-8")
    lauf = pj.lauf("pruefen", umgebung={"FAKE_PGDUMP_HAUPT": "17", "FAKE_PG_NUM": "170006"})
    out, err = _aus(lauf)
    assert lauf.returncode == 0, out + err
    assert "ok     Postgres-Werkzeuge aus Image public.ecr.aws/supabase/postgres:17.6" in out
    assert "ok     pg_dump 17 passt zum Server" in out
    runs = [z for z in pj.log() if z.startswith("docker run")]
    assert runs and all("--network host --env-file ./laufzeit/ausrollen/pg.env.docker" in z for z in runs)
    assert all(GEHEIM not in z for z in pj.log())
    assert not (pj.lz / "ausrollen" / "pg.env.docker").exists() and not (pj.lz / "ausrollen" / "pg.env").exists()


@braucht_bash
def test_pruefen_pg_dump_aelter_als_server_meldet_fehlt(pj: Projekt):
    pj.pruefen_bereit()
    lauf = pj.lauf("pruefen", umgebung={"FAKE_PGDUMP_HAUPT": "15", "FAKE_PG_NUM": "170006"})
    assert lauf.returncode == 1 and "pg_dump 15 ist aelter als der Server (17)" in _aus(lauf)[0]


@braucht_bash
def test_sichern_mit_pg_image_dumpt_ueber_docker(pj: Projekt):
    pj.pruefen_bereit()
    (pj.lz / "ausrollen" / "pg-image").write_text("pg:17\n", encoding="utf-8")
    pj.stempel("pruefen", "bauen-system", "bauen-web")
    lauf = pj.lauf("sichern")
    assert lauf.returncode == 0, _aus(lauf)[1]
    dumps = list((pj.lz / "sicherung").glob("event-*.dump"))
    assert len(dumps) == 1 and dumps[0].read_text(encoding="utf-8") == "dump"
    assert any(z.startswith("docker run") and "--entrypoint pg_dump pg:17 -n event -Fc" in z for z in pj.log())


def test_pg_hilfe_docker_env_ist_roh_und_lehnt_zeilenumbruch_ab(tmp_path: Path):
    h = _hilfe()
    ziel = tmp_path / "pg.env.docker"
    h.docker_umgebungsdatei({"PGUSER": "u", "PGPASSWORD": "a b'c$d"}, ziel)
    assert ziel.read_text(encoding="utf-8") == "PGPASSWORD=a b'c$d\nPGUSER=u\n"
    with pytest.raises(h.HilfeFehler) as e:
        h.docker_umgebungsdatei({"PGPASSWORD": f"{GEHEIM}\nX=1"}, ziel)
    assert GEHEIM not in str(e.value)


# --- Anleitung --------------------------------------------------------------------------------------------------


def test_anleitung_nennt_jede_phase_mit_rueckweg_und_abbruch():
    text = ANLEITUNG.read_text(encoding="utf-8")
    for phase in ("pruefen", "sichern", "migrieren", "bauen system", "bauen web", "shim", "dienste", "anbinden",
                  "timer", "tailnet"):
        assert f"--ich-habe-ok {phase}" in text, phase
        assert f"zurueck {phase}" in text, phase
    for stichwort in ("Rückweg", "Abbruch", "OK", "Offen", "tags.env", "workspace-geprueft", "betreiber_lesen",
                      "umask 077", "VIBEMIND_BUDGET_WARNEN_CMD", "EVENT_SHIM_BAUKONTEXT", "requirements.txt",
                      "/home/debian/vibemind-os", "10.250.10.14"):
        assert stichwort in text, stichwort
    # Klartext-Schluessel nie in den Terminal-Befehlen der Anleitung
    for z in text.splitlines():
        if "schluessel ausstellen" in z:
            assert "> " in z or ">" in z, z
    assert "evk_" not in text


def _abschnitt(text: str, titel_anfang: str) -> str:
    teile = [t for t in re.split(r"(?m)^(?=#{2,3} )", text) if t.startswith(("## " + titel_anfang,
                                                                            "### " + titel_anfang))]
    assert len(teile) == 1, titel_anfang
    return teile[0]


def test_anleitung_handschritte_aus_dem_abschluss_review():
    # Abschluss-Review F-5: die Teil-B-Schritte ohne Phasennamen stehen an ihrer Stelle, mit
    # OK, Abbruch und Rueckweg.
    text = ANLEITUNG.read_text(encoding="utf-8")
    titel = [z for z in text.splitlines() if z.startswith("### ")]
    def pos(anfang: str) -> int:
        treffer = [i for i, z in enumerate(titel) if z.startswith("### " + anfang)]
        assert len(treffer) == 1, (anfang, titel)
        return treffer[0]
    assert (pos("8. `tailnet`") < pos("Erster Betreiber") < pos("Vor `anbinden`") < pos("9. `anbinden`")
            < pos("10. `timer`") < pos("Brain-Umgebung"))

    erster = _abschnitt(text, "Erster Betreiber")
    for stichwort in ("system.betreiber anlegen", "betreiber entziehen", "EVENT_ADMIN_DSN_FILE",
                      "Firma anlegen", "system.legacy eintragen", "firma_kurz", "OK von Felix",
                      "Abbruch", "Rückweg"):
        assert stichwort in erster, stichwort
    assert "--passwort" not in erster  # Passwort nur per stdin

    brain = _abschnitt(text, "Brain-Umgebung")
    for stichwort in ("EVENT_MCP_URL", "EVENT_VERLAUF_TOKEN_FILE", "laufzeit/schluessel/pruefung",
                      "Launcher", "docker service update", "OK von Felix", "Abbruch", "Rückweg",
                      "Netzweg"):
        assert stichwort in brain, stichwort
    assert re.search(r"[Nn]ie\b.*`docker service update`", brain)

    anbinden = _abschnitt(text, "9. `anbinden`")
    assert "bereitstellung.timer" in anbinden and "--ich-habe-ok timer" in anbinden

    shim = _abschnitt(text, "6. `shim`")
    for stichwort in ("spaces/rowboat/deploy/headless/shim", "claude_code_openai_shim.py",
                      "claude_code_openai_tools.py", "rowboat_shim_huelle.py", "start.sh",
                      "scripts/claude_budget.py", "agent_budget.yaml", "budget_warnung",
                      "0.0.0.0", "8125", "EVENT_SHIM_BAUKONTEXT"):
        assert stichwort in shim, stichwort
    assert "Ohne Baukontext" in shim and "bricht" in shim.split("Ohne Baukontext", 1)[1].split("\n- ", 1)[0]
    assert "legt den\n  Shim neu an. Sie sagt es laut" not in shim


def test_keine_geheimnisse_oder_echten_ziele():
    for datei in (SKRIPT, ANLEITUNG, DEPLOY / "pg_hilfe.py"):
        text = datei.read_text(encoding="utf-8")
        for muster in (r"evk_[A-Za-z0-9_-]{8}", r"bot\d+:", r"\d{6,}:[A-Za-z0-9_-]{20,}",
                       r"-100\d{6,}", r"(?:\d{1,3}\.){3}\d{1,3}", r"vercel\.app",
                       r"@(?!example\.org)[a-z0-9-]+\.[a-z]{2,}"):
            treffer = [t for t in re.findall(muster, text)
                       if t not in ("127.0.0.1", "0.0.0.0") and not t.startswith("10.250.10.")]
            assert treffer == [], (datei.name, muster, treffer)


# --- Fix-Runde 1 (Rulings T6-a bis T6-d) ----------------------------------------------------------


@braucht_bash
def test_t6a_gescheiterter_wiederholungslauf_hinterlaesst_keinen_stempel(pj: Projekt):
    pj.pruefen_bereit()
    assert pj.lauf("pruefen").returncode == 0
    pj.stempel("bauen-system", "bauen-web", "sichern", "migrieren")
    assert pj.stempel_da("pruefen")
    (pj.lz / "ausrollen" / "claim").unlink()  # pruefen scheitert jetzt
    lauf = pj.lauf("pruefen")
    assert lauf.returncode == 1
    assert not pj.stempel_da("pruefen") and not pj.stempel_da("sichern") and not pj.stempel_da("migrieren")
    assert not pj.stempel_da("bauen-system") and not pj.stempel_da("bauen-web")
    folge = pj.lauf("sichern")
    assert folge.returncode == 1 and "Vorphase pruefen fehlt" in _aus(folge)[1]


@braucht_bash
def test_t6a_bauen_loescht_nur_eigene_und_spaetere(pj: Projekt):
    pj.stempel(*BIS_DIENSTE)
    pj.meminfo.write_text("MemAvailable: 100 kB\n", encoding="utf-8")  # Lauf scheitert
    lauf = pj.lauf("bauen", "web", umgebung={"EVENT_SYSTEM_TAG": "abc12345", "EVENT_WEB_TAG": "abc12345"})
    assert lauf.returncode == 1
    assert pj.stempel_da("bauen-system") and pj.stempel_da("pruefen")  # die andere Variante bleibt
    for weg in ("bauen-web", "sichern", "migrieren", "shim", "dienste"):
        assert not pj.stempel_da(weg), weg


@braucht_bash
def test_t6c_shim_vorher_bleibt_nach_gescheitertem_lauf(pj: Projekt):
    pj.stempel("pruefen", "sichern", "migrieren", "bauen-system", "bauen-web")
    pj.tags()
    umg = _shim_kontext(pj)
    # Lauf 1: Bau ok, Gesundheitspruefung scheitert (ein Versuch)
    erst = pj.lauf("shim", umgebung={**umg, "FAKE_HEALTH": "starting", "AUSROLLEN_GESUND_VERSUCHE": "1"})
    assert erst.returncode == 1 and not pj.stempel_da("shim")
    assert (pj.lz / "ausrollen" / "shim.vorher").read_text(encoding="utf-8").strip() == "sha256:vorher123"
    tags_lauf1 = [z for z in pj.log() if z.startswith("docker tag ")]
    assert len(tags_lauf1) == 1
    # Lauf 2: das Image heisst jetzt anders (neues Image unter dem alten Namen); der Rueckweg bleibt
    (pj.bin / "docker").write_text(DOCKER.replace("sha256:vorher123", "sha256:neu456"), encoding="utf-8", newline="\n")
    zweit = pj.lauf("shim", umgebung=umg)
    assert zweit.returncode == 0, _aus(zweit)[1]
    assert (pj.lz / "ausrollen" / "shim.vorher").read_text(encoding="utf-8").strip() == "sha256:vorher123"
    assert len([z for z in pj.log() if z.startswith("docker tag ")]) == 1  # nicht erneut getaggt
    # Lauf 3 nach erfolgreichem Lauf 2: jetzt darf der Rueckweg neu gesetzt werden
    dritt = pj.lauf("shim", umgebung=umg)
    assert dritt.returncode == 0
    assert (pj.lz / "ausrollen" / "shim.vorher").read_text(encoding="utf-8").strip() == "sha256:neu456"


@braucht_bash
def test_t6d_lock_gehalten_bricht_ab_auch_bei_zurueck(pj: Projekt):
    pj.pruefen_bereit()
    for args in (["pruefen"], ["zurueck", "pruefen"]):
        lauf = pj.lauf(*args, umgebung={"FAKE_FLOCK_HELD": "1"})
        assert lauf.returncode == 1 and "nie parallel" in _aus(lauf)[1]
        assert pj.log() == []
    assert not pj.stempel_da("pruefen")


@braucht_bash
def test_t6d_haken_werden_laut_genannt_ohne_werte(pj: Projekt):
    pj.pruefen_bereit()
    lauf = pj.lauf("pruefen")
    err = _aus(lauf)[1]
    assert "ACHTUNG: Test-Haken gesetzt:" in err
    for name in ("AUSROLLEN_STAT", "AUSROLLEN_CURL", "AUSROLLEN_MEMINFO", "AUSROLLEN_ETC", "AUSROLLEN_FLOCK"):
        assert name in err
    assert str(pj.bin.name) not in err.split("ACHTUNG", 1)[1].splitlines()[0].replace("AUSROLLEN_", "")


@braucht_bash
@pytest.mark.parametrize("wert", ["0g", "9g", "9000m", "4", "g", "-1g", "4G", "8192m1", "$(id)", "4g;x"])
def test_t6d_bau_speicher_wird_validiert(wert, pj: Projekt):
    pj.stempel("pruefen", "sichern", "migrieren")
    lauf = pj.lauf("bauen", "web", umgebung={"AUSROLLEN_BAU_SPEICHER": wert, "EVENT_SYSTEM_TAG": "abc12345",
                                             "EVENT_WEB_TAG": "abc12345"})
    assert lauf.returncode == 1 and "AUSROLLEN_BAU_SPEICHER" in _aus(lauf)[1]
    assert pj.log() == []


@braucht_bash
@pytest.mark.parametrize("wert", ["1m", "512m", "4g", "8g", "8192m"])
def test_t6d_bau_speicher_gueltig(wert, pj: Projekt):
    pj.stempel("pruefen", "sichern", "migrieren")
    lauf = pj.lauf("bauen", "web", umgebung={"AUSROLLEN_BAU_SPEICHER": wert, "EVENT_SYSTEM_TAG": "abc12345",
                                             "EVENT_WEB_TAG": "abc12345"})
    assert lauf.returncode == 0, _aus(lauf)[1]
    assert any(f"--memory={wert}" in z for z in pj.log())


def test_t6d_shim_wartet_150_sekunden():
    code = _code()
    assert 'GESUND_VERSUCHE=${AUSROLLEN_GESUND_VERSUCHE:-75}' in code  # 75 x 2 s
    assert 'seq 1 "$GESUND_VERSUCHE"' in code
