"""Messpunkte (Etappe 3, Task 9, Ruling S7): deploy/messen.sh und deploy/messen.md.

Das Skript misst spaeter gegen ein TEST-Paar auf offload-vm; hier laeuft es nie mit
`--ich-habe-ok`. Geprueft wird:
- ohne den Schalter tut es nichts (echter Lauf mit bash, alle Programme durch Attrappen
  ersetzt, die jeden Aufruf protokollieren; das Protokoll muss leer bleiben),
- statisch: Schalter vor jedem Programm, nur Testcontainer, kein Compose-Projekt, Werte
  nur ueber stdin, Images wie im Compose,
- die eingebetteten Python-Teile (Test-Config, Testdaten, Code, Pruefung) gegen den
  Wegwerf-Postgres bzw. mit Beispielwerten,
- die Anleitung nennt jeden Messpunkt mit Abbruchkriterium und Alternative.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

SPACE = Path(__file__).resolve().parents[2]
SKRIPT = SPACE / "deploy" / "messen.sh"
ANLEITUNG = SPACE / "deploy" / "messen.md"
COMPOSE = SPACE / "docker-compose.yml"
SCHALTER = "--ich-habe-ok"
ATTRAPPEN = ("docker", "ssh", "scp", "curl", "python", "python3", "node", "rm", "mkdir",
             "cp", "mv", "chmod", "tee", "sudo", "systemctl", "sleep", "date", "cat", "sed")
FAKE_SCHLUESSEL = "evk_" + "A" * 43
FAKE_TOKEN = "123456:" + "B" * 35


def _text() -> str:
    return SKRIPT.read_text(encoding="utf-8")


def _code() -> str:
    """Skript ohne Kommentarzeilen."""
    return "\n".join(z for z in _text().splitlines() if not z.lstrip().startswith("#"))


def _heredocs() -> dict[str, str]:
    """Eingebettete Programme: ``NAME=$(cat <<'PY'`` ... ``PY`` (bzw. JS)."""
    text = _text()
    funde = re.findall(r"^([A-Z_]+)=\$\(cat <<'(PY|JS)'\n(.*?)\n\2\n\)", text, re.S | re.M)
    return {name: rumpf for name, _, rumpf in funde}


def _shell() -> str:
    """Shell-Code ohne Kommentare und ohne die Rumpfe der eingebetteten Programme."""
    text = _code()
    for rumpf in _heredocs().values():
        text = text.replace(rumpf, "")
    return text


def _bash() -> str | None:
    """Git-Bash bzw. bash; unter Windows nie die WSL-bash aus System32."""
    kandidaten = [r"C:\Program Files\Git\bin\bash.exe"] if os.name == "nt" else []
    gefunden = shutil.which("bash")
    if gefunden and "system32" not in gefunden.lower():
        kandidaten.append(gefunden)
    return next((k for k in kandidaten if Path(k).is_file()), None)


# --- Ohne Schalter passiert nichts ---------------------------------------------------


@pytest.fixture
def attrappen(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    ordner = tmp_path / "bin"
    ordner.mkdir()
    protokoll = tmp_path / "aufrufe.log"
    for name in ATTRAPPEN:
        datei = ordner / name
        datei.write_text(f'#!/bin/sh\necho "{name} $*" >> "$MESS_PROTOKOLL"\nexit 0\n',
                         encoding="utf-8", newline="\n")
        datei.chmod(0o755)
    yield ordner, protokoll


@pytest.mark.parametrize("argumente", [
    [], ["vorbereiten"], ["stand"], ["aufraeumen"], ["anlegen", "ev-0123abcd", "gruppe"],
    ["--ich-habe-OK", "vorbereiten"], ["--ich-habe-ok=ja", "vorbereiten"],
    ["vorbereiten", "--ich-habe-okay"], ["-ich-habe-ok", "stand"],
], ids=lambda a: " ".join(a) or "ohne")
def test_ohne_schalter_tut_das_skript_nichts(argumente: list[str], attrappen, tmp_path):
    bash = _bash()
    if bash is None:
        pytest.skip("keine bash")
    ordner, protokoll = attrappen
    umgebung = {**os.environ, "PATH": str(ordner) + os.pathsep + os.environ.get("PATH", ""),
                "MESS_PROTOKOLL": str(protokoll), "HOME": str(tmp_path)}
    eingabe = f"{FAKE_TOKEN}\n{FAKE_SCHLUESSEL}\n-1001234567890\n"
    lauf = subprocess.run([bash, str(SKRIPT), *argumente], input=eingabe.encode(),
                          cwd=tmp_path, env=umgebung, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=60, check=False)
    fehler = lauf.stderr.decode("utf-8", "replace")
    assert lauf.returncode == 2, fehler
    assert SCHALTER in fehler and "nichts" in fehler
    assert lauf.stdout == b""
    assert not protokoll.exists(), protokoll.read_text(encoding="utf-8")
    assert list(tmp_path.iterdir()) == [ordner]  # nichts angelegt
    for wert in (FAKE_TOKEN, FAKE_SCHLUESSEL, "-1001234567890"):
        assert wert not in fehler


def test_bash_syntax():
    bash = _bash()
    if bash is None:
        pytest.skip("keine bash")
    r = subprocess.run([bash, "-n", str(SKRIPT)], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=60, check=False)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")


def test_schalter_steht_vor_jedem_programm():
    code = _shell()
    ende = code.index("exit 2")
    davor = code[:ende]
    assert SCHALTER in davor
    for programm in ("docker", "python", "node", "ssh", "curl", "mkdir", "rm ", "cd ", "$(",
                     "cat ", "printf '%s"):
        assert programm not in davor, programm
    # Der Schalter muss exakt passen (kein Praefix-/Muster-Vergleich).
    assert re.search(r"^\s*--ich-habe-ok\) ", davor, re.M)
    assert re.search(r"^set -eu$", code, re.M) and "umask 077" in code
    assert not re.search(r"set -[a-z]*x", code)


# --- Nur Testcontainer -----------------------------------------------------------------


def _konstanten() -> dict[str, str]:
    return dict(re.findall(r"^([A-Z_]+)=([A-Za-z0-9_./:-]+)$", _shell(), re.M))


def test_nur_das_testpaar_wird_angefasst():
    k = _konstanten()
    for name in ("CLAW", "SHIM", "MCP", "PG", "VOL_STATE"):
        assert k[name].startswith("event-messung-"), name
    shell = _shell()
    for verboten in ("event-management-claw", "event-management-shim", "docker compose",
                     "docker-compose", "service update", "stack deploy", "ssh ", "scp ",
                     "docker stop", "docker restart", "docker kill", "--privileged",
                     "docker.sock", "pkill", "killall"):
        assert verboten not in shell, verboten
    # Jeder docker-Aufruf nennt ein Testobjekt ueber seine Konstante (nie einen Namen
    # aus einer Variablen der Umgebung).
    testobjekt = re.compile(r"\$(CLAW|SHIM|MCP|PG|VOL_STATE)\b")
    for z in shell.splitlines():
        if re.search(r"\bdocker (run|exec|rm|logs|stats|inspect|volume)\b", z):
            assert testobjekt.search(z), z
    for name in ("CLAW", "SHIM", "MCP", "PG", "VOL_STATE"):
        assert not re.search(rf"\b{name}=\$\{{", shell), name  # keine Umgebung als Ziel


def test_images_wie_im_compose():
    k = _konstanten()
    dienste = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    assert k["CLAW_IMAGE"] == dienste["event-management-claw"]["image"]
    assert k["SHIM_IMAGE"] == dienste["event-management-shim"]["image"]
    assert k["SYSTEM_IMAGE"] == dienste["event-mcp"]["image"]


def test_werte_nur_ueber_stdin():
    shell = _shell()
    # Tokens, Schluessel und Chat-IDs kommen aus `read`, nie aus Argumenten.
    for variable in ("TG", "CC", "GW", "CHAT", "ERWARTET"):
        assert re.search(rf"IFS= read -r {variable}\b", shell), variable
    for z in shell.splitlines():
        if re.search(r"\b(docker|python3)\b", z):
            rechts = z.split("|", 1)[1] if "|" in z else z
            for variable in ("TG", "CC", "GW", "CHAT", "ERWARTET"):
                assert f"${variable}" not in rechts and f"${{{variable}}}" not in rechts, z
    # Schluessel und Tokens werden nie ausgegeben.
    for z in shell.splitlines():
        if re.search(r"\b(echo|printf)\b", z) and ">" not in z and "|" not in z:
            for variable in ("TG", "CC", "GW"):
                assert f"${variable}" not in z, z


def test_programme_kompilieren():
    programme = _heredocs()
    assert {"SAAT_PY", "SAAT_JS", "TESTDATEN_PY", "ABLEGEN_PY", "CODE_PY", "PRUEFEN_PY",
            "ANLEGEN_PY", "CONFIG_PY", "LAST_PY"} <= set(programme)
    for name, rumpf in programme.items():
        if name.endswith("_PY"):
            compile(rumpf, name, "exec")
        assert "print(key" not in rumpf and "print(schl" not in rumpf, name
    node = shutil.which("node")
    if node is None:
        return
    for name, rumpf in programme.items():
        if name.endswith("_JS"):
            r = subprocess.run([node, "--check", "-"], input=rumpf.encode(), timeout=60,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            assert r.returncode == 0, (name, r.stderr.decode("utf-8", "replace"))


# --- Eingebettete Programme mit Beispielwerten ----------------------------------------


def _ausfuehren(name: str, *, stdin: str = "", argv: list[str] | None = None,
                umgebung: dict[str, str] | None = None) -> str:
    """Fuehrt ein eingebettetes Python-Programm im Testprozess aus (Arbeitsordner = Space)."""
    rumpf = _heredocs()[name]
    alt_argv, alt_stdin, alt_cwd = sys.argv, sys.stdin, Path.cwd()
    alt_env = dict(os.environ)
    ausgabe = io.StringIO()
    try:
        sys.argv = [name, *(argv or [])]
        sys.stdin = io.StringIO(stdin)
        os.environ.update(umgebung or {})
        os.chdir(SPACE)
        with contextlib.redirect_stdout(ausgabe):
            exec(compile(rumpf, name, "exec"), {"__name__": "__main__"})  # noqa: S102
    finally:
        sys.argv, sys.stdin = alt_argv, alt_stdin
        os.chdir(alt_cwd)
        os.environ.clear()
        os.environ.update(alt_env)
    return ausgabe.getvalue()


def test_test_config_ohne_betrieb_und_ohne_platzhalter():
    aus = json.loads(_ausfuehren("SAAT_PY", stdin=f"{FAKE_TOKEN}\ngateway-test\n"))
    config, dateien = aus["config"], aus["dateien"]
    text = json.dumps(config)
    assert "{{" not in text
    assert [a["id"] for a in config["agents"]["list"]] == ["main"]
    assert config["mcp"]["servers"] == {}
    telegram = config["channels"]["telegram"]
    assert telegram["groups"] == {"*": {"requireMention": True}}
    assert telegram["groupPolicy"] == "allowlist"
    assert telegram["botToken"] == FAKE_TOKEN
    assert config["gateway"]["bind"] == "loopback"
    assert config["gateway"]["auth"] == {"mode": "token", "token": "gateway-test"}
    assert config["models"]["providers"]["shim"]["baseUrl"] == "http://127.0.0.1:8125/v1"
    assert config["channels"]["whatsapp"] == {"enabled": False}
    assert set(dateien) == {"SOUL.md", "AGENTS.md"}
    assert "gruppe_melden" in dateien["SOUL.md"]


@pytest.fixture
def mess_dsn(db_admin: Any, pg_cluster: dict[str, Any]) -> dict[str, str]:
    basis = f"host=127.0.0.1 port={pg_cluster['port']} dbname={db_admin.info.dbname}"
    return {"MESS_ADMIN_DSN": f"{basis} user=postgres", "MESS_APP_DSN": f"{basis} user=event_mess"}


def test_testdaten_code_und_pruefung(db_admin, mess_dsn, tmp_path):
    # Die Migrationen sind durch db_admin schon da; TESTDATEN_PY wendet sie idempotent an.
    roh = _ausfuehren("TESTDATEN_PY", umgebung=mess_dsn)
    daten = json.loads(roh)
    agenten = daten["agenten"]
    assert sorted(a["name"] for a in agenten.values()) == ["Leitung Messung", "Messung A",
                                                           "Messung B"]
    assert set(daten["schluessel"]) == set(agenten) | {"melden"}
    assert all(re.fullmatch(r"(ev|ld)-[0-9a-f]{8}", a) for a in agenten)

    # Ablegen: Dateien 0600 unter laufzeit/messung, Ausgabe nur Namen.
    ablage = tmp_path / "messung"
    ausgabe = _ausfuehren("ABLEGEN_PY", stdin=roh, argv=[str(ablage)])
    for name, klartext in daten["schluessel"].items():
        datei = ablage / f"{name}.schluessel"
        assert datei.read_text(encoding="utf-8").strip() == klartext
        assert klartext not in ausgabe
    assert json.loads((ablage / "agenten.json").read_text(encoding="utf-8")) == agenten
    if os.name != "nt":
        assert (ablage / "melden.schluessel").stat().st_mode & 0o077 == 0

    # Code fuer Gruppe A und Direktchat; die Pruefung findet sie offen, ohne Chat-ID.
    code_a = _ausfuehren("CODE_PY", argv=["A"], umgebung=mess_dsn).strip()
    code_d = _ausfuehren("CODE_PY", argv=["direkt"], umgebung=mess_dsn).strip()
    for code in (code_a, code_d):
        assert re.fullmatch(r"[A-Z2-9]{8}", code)
    bericht = _ausfuehren("PRUEFEN_PY", stdin="-1001234567890\n", umgebung=mess_dsn)
    assert "gruppe Messung A: offen" in bericht and "direkt: offen" in bericht
    assert "-1001234567890" not in bericht

    # Nach einer Meldung mit passender Chat-ID: "passt"; mit anderer: "passt NICHT".
    db_admin.execute("update event.verbindung set status = 'gemeldet', chat_id = %s,"
                     " gemeldet = now() where art = 'gruppe'", (-1001234567890,))
    bericht = _ausfuehren("PRUEFEN_PY", stdin="-1001234567890\n", umgebung=mess_dsn)
    assert "gruppe Messung A: gemeldet, Chat-ID passt" in bericht
    bericht = _ausfuehren("PRUEFEN_PY", stdin="-1009999999999\n", umgebung=mess_dsn)
    assert "gruppe Messung A: gemeldet, Chat-ID passt NICHT" in bericht
    assert "Aufgaben: Messung A 0, Messung B 0" in bericht


# --- Anleitung -------------------------------------------------------------------------

MESSPUNKTE = {
    "M1": ("Chat-ID",),
    "M2": ("tools.allow", "<agent>__*"),
    "M3": ("Neustart", "bindings", "mcp.servers"),
    "M4": ("Tagesbudget", "300", "@-Erwähnung"),
    "M5": ("dmPolicy", "chat_melden"),
    "M6": ("Speicher", "Platte"),
}


def _abschnitte() -> dict[str, str]:
    text = ANLEITUNG.read_text(encoding="utf-8")
    teile = re.split(r"^## ", text, flags=re.M)
    return {t.split(" ", 1)[0]: t for t in teile[1:]}


def test_anleitung_nennt_jeden_messpunkt_mit_abbruch_und_alternative():
    abschnitte = _abschnitte()
    for punkt, begriffe in MESSPUNKTE.items():
        assert punkt in abschnitte, punkt
        teil = abschnitte[punkt]
        for begriff in begriffe:
            assert begriff in teil, (punkt, begriff)
        assert "**Abbruchkriterium:**" in teil, punkt
        assert "**Alternative (Etappe 4):**" in teil, punkt
        assert "messen.sh --ich-habe-ok" in teil, punkt


def test_anleitung_nennt_rahmen_und_deploy_blocker():
    text = ANLEITUNG.read_text(encoding="utf-8")
    for begriff in ("nicht ausgeführt", SCHALTER, "Test-Bot", "ev-legacy", "anbinden.sh",
                    "DEPLOY-BLOCKER", "event-messung-", "aufraeumen", "WORKBOARD",
                    "http://event-mcp:8171", "ip_range"):
        assert begriff in text, begriff


def test_keine_geheimnisse_oder_echten_ziele():
    for datei in (SKRIPT, ANLEITUNG):
        text = datei.read_text(encoding="utf-8")
        for muster in (r"evk_[A-Za-z0-9_-]{8}", r"bot\d+:", r"\b\d{6,}:[A-Za-z0-9_-]{20,}",
                       r"-100\d{6,}", r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
                       r"vercel\.app", r"@(?!example\.org)[a-z0-9-]+\.[a-z]{2,}"):
            # Erlaubt: Loopback, "alle Schnittstellen" und das Subnetz aus dem Compose.
            treffer = [t for t in re.findall(muster, text)
                       if t not in ("127.0.0.1", "0.0.0.0") and not t.startswith("10.250.10.")]
            assert treffer == [], (datei.name, muster, treffer)
