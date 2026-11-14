"""Ausrollen (Task 6, Repo-Teil): eigener Shim, Budget-Datei und anbinden.sh.

Statische Pruefungen, keine Container: das Skript laeuft nur auf der VM. Hier
wird festgehalten, dass kein Schluessel auf eine Kommandozeile oder in eine
Ausgabe gelangt und dass Saat und Netze so geschnitten sind wie beschlossen.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SPACE = Path(__file__).resolve().parents[1]
SKRIPT = SPACE / "deploy" / "anbinden.sh"
BUDGET = SPACE / "deploy" / "agent_budget.yaml"
COMPOSE = SPACE / "docker-compose.yml"
BEISPIEL = SPACE / "werte.beispiel.json"
OPENCLAW = SPACE / "config" / "openclaw.json"
GITIGNORE = SPACE / ".gitignore"

# Namen der Shell-Variablen, die Schluessel tragen (Zeilen 1-7 von stdin).
SCHLUESSEL_VARIABLEN = ("GW", "TG", "APP", "CC", "MLD", "BNL", "BNA")


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _code() -> str:
    text = SKRIPT.read_text(encoding="utf-8")
    return "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))


def _shell_zeilen() -> list[str]:
    """Zeilen der Shell, ohne die eingebetteten JavaScript- und Python-Heredocs."""
    aus: list[str] = []
    ende: str | None = None
    for z in _code().splitlines():
        treffer = re.search(r"<<\s*'(JS|PY)'", z)
        if treffer and ende is None:
            ende = treffer.group(1)
            aus.append(z)  # die oeffnende Zeile selbst ist Shell
            continue
        if ende is None:
            aus.append(z)
        elif z.strip() == ende:
            ende = None
    return aus


def _heredoc(oeffnend: str, ende: str) -> str:
    """Rumpf des Heredocs, dessen oeffnende Zeile `oeffnend` enthaelt."""
    text = SKRIPT.read_text(encoding="utf-8")
    start = text.index("\n", text.index(oeffnend)) + 1
    return text[start:text.index(f"\n{ende}\n", start)]


# --- anbinden.sh -------------------------------------------------------------

def test_skript_hat_gueltige_bash_syntax() -> None:
    try:
        r = subprocess.run(["bash", "-n"], input=SKRIPT.read_bytes(), capture_output=True)
    except FileNotFoundError:
        pytest.skip("kein bash")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")


def test_set_eu_und_umask() -> None:
    code = _code()
    assert re.search(r"^set -eu\b", code, re.M)
    assert "umask 077" in code
    assert not re.search(r"set -[a-z]*x", code)
    # Das Skript wechselt selbst in den Space-Ordner.
    assert re.search(r'cd "\$\(dirname', code)


def test_werte_nur_per_stdin_sieben_zeilen() -> None:
    shell = "\n".join(_shell_zeilen())
    assert len(re.findall(r"\bread\b", shell)) == 7
    assert all(re.search(rf"IFS= read -r {v}\b", shell) for v in SCHLUESSEL_VARIABLEN)
    # Kein Wert als Argument: $1..$9, $@ und $* kommen in der Shell nicht vor.
    # (Die Hilfsfunktion zeichen_pruefen darf ihre eigenen Parameter lesen.)
    ohne_funktion = re.sub(r"zeichen_pruefen\(\) \{.*?\n\}", "", shell, flags=re.S)
    assert not re.search(r"\$[1-9@*]|\$\{[1-9@*]", ohne_funktion)
    # Leere Zeilen 1-3 und 5-7 sind ein Fehler, Zeile 4 darf leer sein.
    for v in ("GW", "TG", "APP", "MLD", "BNL", "BNA"):
        assert re.search(rf'\[ -z "\${v}" \]', shell), v
    assert not re.search(r'\[ -z "\$CC" \]\s*&&', shell)
    for nr, v in enumerate(SCHLUESSEL_VARIABLEN, start=1):
        assert re.search(rf'zeichen_pruefen {nr} "\${v}"', shell), v
    kopf = SKRIPT.read_text(encoding="utf-8").split("set -eu", 1)[0]
    assert "Genau sieben Zeilen" in kopf
    assert "'%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n'" in kopf  # Aufrufbeispiel mit 7 Werten


def _bash() -> str:
    """Git-Bash (bzw. bash unter Linux). WSL-bash versteht die Windows-Pfade nicht."""
    pfad = shutil.which("bash")
    if pfad is None:
        pytest.skip("kein bash")
    klein = pfad.lower()
    if os.name == "nt" and ("system32" in klein or "windowsapps" in klein):
        pytest.skip("nur WSL-bash gefunden")
    return pfad


def _anbinden_lauf(tmp_path: Path, zeilen: list[str]) -> subprocess.CompletedProcess[bytes]:
    """Startet eine KOPIE des Skripts in tmp_path/deploy (ohne build/): auch wenn die
    stdin-Pruefung durchliefe, bricht es vor jeder Wirkung an `build/openclaw.json fehlt`
    ab. Es fasst so weder den Space-Ordner noch docker an."""
    deploy = tmp_path / "deploy"
    deploy.mkdir(exist_ok=True)
    kopie = deploy / "anbinden.sh"
    kopie.write_bytes(SKRIPT.read_bytes())
    eingabe = "".join(z + "\n" for z in zeilen).encode("utf-8")
    return subprocess.run([_bash(), str(kopie)], input=eingabe, capture_output=True,
                          timeout=60, cwd=tmp_path)


def test_anbinden_braucht_sieben_zeilen(tmp_path: Path) -> None:
    werte = ["gateway-beispiel", "123:tg-beispiel", "Bearer evk_beispiel_app", "",
             "Bearer evk_beispiel_melden", "Bearer evk_beispiel_bn_lesen",
             "Bearer evk_beispiel_bn_auftrag"]
    faelle = {
        "sechs Zeilen": (werte[:6], "Zeile 7"),
        "Zeile 6 leer": (werte[:5] + ["", werte[6]], "Zeile 6"),
        "Zeile 7 leer": (werte[:6] + [""], "Zeile 7"),
    }
    for fall, (zeilen, meldung) in faelle.items():
        r = _anbinden_lauf(tmp_path, zeilen)
        ausgabe = (r.stdout + r.stderr).decode("utf-8", "replace")
        assert r.returncode != 0, fall
        assert "ABBRUCH" in ausgabe and meldung in ausgabe, (fall, ausgabe)
        assert "build/openclaw.json" not in ausgabe, fall  # Abbruch schon an stdin
        for wert in werte:
            if wert:
                assert wert not in ausgabe, fall
        assert not (tmp_path / "laufzeit").exists(), fall
    # Gegenprobe: mit sieben vollen Zeilen laeuft die stdin-Pruefung durch und das
    # Skript bricht erst an der fehlenden Saat ab (in der Kopie gibt es kein build/).
    r = _anbinden_lauf(tmp_path, werte)
    ausgabe = (r.stdout + r.stderr).decode("utf-8", "replace")
    assert r.returncode != 0 and "build/openclaw.json fehlt" in ausgabe, ausgabe
    for wert in werte:
        if wert:
            assert wert not in ausgabe


def test_kein_docker_cp_und_keine_werte_in_docker_argumenten() -> None:
    code = _code()
    assert "docker cp" not in code
    for z in _shell_zeilen():
        if "docker" in z:
            # Links von der Pipe (printf ... |) duerfen Werte stehen, rechts nicht.
            rechts = z.split("|", 1)[1] if "|" in z else z
            for v in SCHLUESSEL_VARIABLEN:
                assert f"${v}" not in rechts and f"${{{v}}}" not in rechts, z
    # Beide node-Skripte laufen ohne laufenden Container (frisches Volume).
    lauf = "docker compose run --rm --no-deps -T --entrypoint node"
    assert code.count(lauf) == 2
    assert f'| {lauf} "$CLAW" -e "$SCHLUESSEL_JS"' in code  # Werte nur ueber stdin
    assert f'{lauf} "$CLAW" -e "$SAAT_JS" </dev/null' in code
    assert "docker exec -i" not in code


def test_keine_schluessel_in_echo_oder_ausgabe() -> None:
    for z in _shell_zeilen():
        if re.search(r"\becho\b", z):
            ausgabe = z.split("echo", 1)[1]
            assert not re.search(r"\$\{?\w*(TOKEN|token|auth|AUTH)", ausgabe), z
            for v in SCHLUESSEL_VARIABLEN:
                assert f"${v}" not in ausgabe and f"${{{v}}}" not in ausgabe, z
        # Jedes printf mit Schluessel geht in Pipe oder Datei, nie auf die Ausgabe.
        if "printf" in z and any(f"${v}" in z for v in SCHLUESSEL_VARIABLEN):
            assert "|" in z or ">" in z, z


def test_config_get_ausgabe_wird_nie_angezeigt() -> None:
    code = _code()
    for z in code.splitlines():
        if "config get" in z:
            assert ">/dev/null" in z or "$(" in z, z
    # Werte werden im Container (node) verglichen und geschrieben; nie als
    # Argument von `config set` (waere in der Prozessliste sichtbar).
    assert not re.search(r"config set\s+\S+\s+\"?\$", code)


def test_schluesseldateien_nur_0600_und_per_printf() -> None:
    code = _code()
    assert "./laufzeit/claude-token" in code and "./laufzeit/gateway-token" in code
    assert "CLAUDE_CODE_OAUTH_TOKEN=%s" in code
    assert "./laufzeit/shim-budget" in code
    assert "chmod 600" in code
    assert not re.search(r"\becho\b[^\n]*>\s*\./laufzeit", code)


def test_saat_ueberschreibt_nur_soul_agents_und_skills() -> None:
    code = SKRIPT.read_text(encoding="utf-8")
    assert "SOUL.md" in code and "AGENTS.md" in code and "skills" in code
    assert "SEED = '/seed'" in code
    assert "['workspace', 'workspace-brain', 'workspace-legacy']" in code
    # Kein Ueberschreiben des ganzen Arbeitsbereichs.
    assert not re.search(r"cpSync\(\s*[^,]*workspace[^,]*,\s*[^,]*workspace[^,]*\)", code)
    assert "vor-anbinden-" in code
    assert "SEED + '/openclaw.json'" in code


def test_reihenfolge_schluesseldateien_saat_compose_up_healthz() -> None:
    code = _code()
    dateien = code.index("> ./laufzeit/gateway-token")
    saat = code.index('-e "$SAAT_JS"')
    schluessel = code.index('-e "$SCHLUESSEL_JS"')
    hoch = code.index("docker compose up -d\n")
    gesund = code.index("/healthz")
    shim = code.index("shim /v1/models")
    assert dateien < saat < schluessel < hoch < gesund < shim
    assert code.index("> ./laufzeit/claude-token") < saat
    assert "mkdir -p ./laufzeit ./laufzeit/shim-budget" in code
    assert code.index("mkdir -p ./laufzeit") < saat


def test_abbruch_ohne_claude_token_und_ohne_zeile_4() -> None:
    code = _code()
    pruefung = code.index('[ ! -f ./laufzeit/claude-token ] && [ -z "$CC" ]')
    assert "exit 1" in code[pruefung:pruefung + 400]
    # Vor jedem Schreiben und vor jedem docker-Aufruf mit Wirkung.
    assert pruefung < code.index("> ./laufzeit/gateway-token")
    assert pruefung < code.index("docker compose run")
    # Keine Vorbedingung "Container laeuft" mehr.
    assert 'if [ "$(docker inspect' not in code
    assert "ABBRUCH: Container" not in code


def test_neustart_nur_bei_aenderung_und_healthz_probe() -> None:
    code = _code()
    assert "docker restart" not in code
    assert code.count("docker compose restart") == 1
    pos = code.index("docker compose restart")
    assert re.search(r'if \[ "\$GEAENDERT" = 1 \] && \[ "\$CLAW_LAUFEND" = "true" \]', code[:pos][-300:])
    # Neuer Claude-Token: Shim und Claw neu anlegen (env_file nur beim Anlegen gelesen).
    assert 'docker compose up -d --force-recreate "$SHIM"' in code
    assert 'docker compose up -d --force-recreate "$CLAW"' in code
    port = json.loads(OPENCLAW.read_text(encoding="utf-8"))["gateway"]["port"]
    assert f"127.0.0.1:{port}/healthz" in code
    # Erlaubt: compose run/up/restart. Verboten: Swarm-Wege.
    assert "stack deploy" not in code and "service update" not in code
    ausgefuehrt = [z for z in _shell_zeilen() if "echo" not in z and "docker compose" in z]
    for z in ausgefuehrt:
        assert re.search(r"docker compose (run --rm --no-deps -T --entrypoint node|up -d|restart)", z), z


def test_shim_probe_ohne_logausgabe() -> None:
    code = _code()
    assert 'docker exec "$SHIM" python3 -c' in code
    assert "127.0.0.1:8125/v1/models" in code
    assert "docker logs" not in "\n".join(z for z in _shell_zeilen() if "echo" not in z)
    assert "docker logs $SHIM" in code  # nur als Hinweis in der Meldung


def test_melden_schluessel_fuer_den_empfang_nur_per_stdin() -> None:
    """Zeile 5: Authorization des Servers `melden` (Empfangs-Agent `main`, Task 8)."""
    code = SKRIPT.read_text(encoding="utf-8")
    # Im Saat-Vergleich ignoriert und als vierter Wert per stdin gesetzt.
    assert code.count("['mcp', 'servers', 'melden', 'headers', 'Authorization']") == 2
    assert "'mcp.servers.melden.headers.Authorization'" in code
    assert ("'%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n' \"$GW\" \"$TG\" \"$APP\" \"$MLD\" \"$BNL\" \"$BNA\""
            " | docker compose run") in code


@pytest.mark.parametrize("server", ["bn-lesen", "bn-auftrag"])
def test_brain_schluessel_nur_per_stdin(server: str) -> None:
    """Zeilen 6 und 7: Authorization der Server `bn-lesen` und `bn-auftrag` (Brain)."""
    code = SKRIPT.read_text(encoding="utf-8")
    # Im Saat-Vergleich ignoriert und per stdin gesetzt.
    assert code.count(f"['mcp', 'servers', '{server}', 'headers', 'Authorization']") == 2
    assert f"'mcp.servers.{server}.headers.Authorization'" in code
    kopf = code.split("set -eu", 1)[0]
    assert f"mcp.servers.{server}.headers.Authorization" in kopf


def test_vergleich_ignoriert_meta_und_wizard() -> None:
    code = SKRIPT.read_text(encoding="utf-8")
    assert "delete c.meta" in code and "delete c.wizard" in code


def test_zeichenpruefung_ohne_wert_in_der_meldung() -> None:
    code = _code()
    assert "LC_ALL=C" in code
    assert "[!" in code  # case-Muster fuer verbotene Zeichen
    assert "nicht druckbare" in code


# --- Schutz der Legacy-Gruppe (Ruling T8-a) ------------------------------------

LEGACY_BINDING = {"agentId": "ev-legacy", "match": {
    "channel": "telegram", "peer": {"kind": "group", "id": "{{TELEGRAM_GRUPPE}}"}}}


def _schutz(tmp_path: Path, konfig: object | str) -> int:
    """Fuehrt den Python-Heredoc des Schutzes gegen eine Vorlage aus; Rueckgabe: Exit-Code."""
    vorlage = tmp_path / "openclaw.json"
    vorlage.write_text(konfig if isinstance(konfig, str) else json.dumps(konfig),
                       encoding="utf-8")
    skript = tmp_path / "schutz.py"
    skript.write_text(_heredoc("<<'PY'", "PY"), encoding="utf-8")
    r = subprocess.run([sys.executable, str(skript), str(vorlage)], stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=60)
    # Der Schutz selbst gibt nichts aus (keine Daten, keine Tokens).
    assert r.stdout == b"" and r.stderr == b""
    return r.returncode


def test_schutz_bricht_ab_solange_ev_legacy_fehlt(tmp_path: Path) -> None:
    vorlage = json.loads(OPENCLAW.read_text(encoding="utf-8"))
    assert "{{TELEGRAM_GRUPPE}}" in OPENCLAW.read_text(encoding="utf-8")
    # Ohne Binding an die Legacy-Gruppe in der Saat -> Abbruch (die echte Saat hat es seit Etappe 4).
    vorlage.pop("bindings")
    assert _schutz(tmp_path, vorlage) == 3
    # Nur `main` gebunden reicht nicht.
    nur_main = dict(vorlage, bindings=[dict(LEGACY_BINDING, agentId="main")])
    assert _schutz(tmp_path, nur_main) == 3
    # Ein anderer Agent an einer anderen Gruppe reicht auch nicht.
    anderswo = {"agentId": "ev-legacy", "match": {
        "channel": "telegram", "peer": {"kind": "group", "id": "-1001234567890"}}}
    assert _schutz(tmp_path, dict(vorlage, bindings=[anderswo])) == 3
    # Etappe 4: ev-legacy an der Legacy-Gruppe -> weiter.
    assert _schutz(tmp_path, dict(vorlage, bindings=[LEGACY_BINDING])) == 0
    # Ohne Legacy-Gruppe in der Saat gibt es nichts zu schuetzen.
    ohne = json.loads(json.dumps(vorlage).replace("{{TELEGRAM_GRUPPE}}", "-1001234567890"))
    assert _schutz(tmp_path, ohne) == 0
    # Kaputte Vorlage: eigener Code, eigene Meldung.
    assert _schutz(tmp_path, "{kein json") == 2


def test_kopf_nennt_python3_als_voraussetzung() -> None:
    kopf = SKRIPT.read_text(encoding="utf-8").split("set -eu", 1)[0]
    assert re.search(r"^# .*Deploy-Host wird python3 gebraucht", kopf, re.M)


def test_schutz_steht_vor_jeder_aenderung_und_nennt_keine_daten() -> None:
    code = _code()
    schutz = code.index("python3 - config/openclaw.json <<'PY' || RC=$?")
    for spaeter in ("mkdir -p ./laufzeit", "> ./laufzeit/gateway-token", "docker inspect",
                    "docker compose run", "docker compose up -d"):
        assert schutz < code.index(spaeter), spaeter
    assert code.index('[ -z "$MLD" ]') < schutz  # stdin ist schon gelesen und geprueft
    meldung = next(z for z in _shell_zeilen() if "ev-legacy fehlt" in z)
    assert "ABBRUCH: ev-legacy fehlt – erst Etappe 4" in meldung and ">&2" in meldung
    assert "$" not in meldung.split("echo", 1)[1]
    block = code[code.index('if [ "$RC" = 3 ]'):]
    assert "exit 1" in block[:block.index("\nfi\n")]


# --- ev-legacy: Legacy-Gruppe mit eigenem Agenten (Etappe 4, Task 2) ------------

def _vorlage() -> dict:
    return json.loads(OPENCLAW.read_text(encoding="utf-8"))


def test_ev_legacy_an_legacy_gruppe_gebunden() -> None:
    bindings = [b for b in _vorlage()["bindings"]
                if b["match"].get("peer", {}).get("id") == "{{TELEGRAM_GRUPPE}}"]
    assert len(bindings) == 1
    assert bindings[0] == LEGACY_BINDING
    assert bindings[0]["match"]["peer"]["kind"] == "group"
    gruppen = _vorlage()["channels"]["telegram"]["groups"]
    assert gruppen["{{TELEGRAM_GRUPPE}}"]["requireMention"] is False


def test_ev_legacy_nur_alte_werkzeuge() -> None:
    agenten = {a["id"]: a for a in _vorlage()["agents"]["list"]}
    assert agenten["ev-legacy"] == {
        "id": "ev-legacy", "workspace": "/home/node/.openclaw/workspace-legacy",
        "tools": {"allow": ["event-app__*"]}}


def test_main_bleibt_empfang() -> None:
    konfig = _vorlage()
    agenten = {a["id"]: a for a in konfig["agents"]["list"]}
    assert agenten["main"]["tools"]["allow"] == ["melden__gruppe_melden", "melden__chat_melden"]
    assert not [b for b in konfig["bindings"] if b["agentId"] == "main"]


def test_schutz_laesst_ausrollen_zu(tmp_path: Path) -> None:
    # Mit der echten Saat-Datei: bisher Exit 3, jetzt 0.
    assert _schutz(tmp_path, _vorlage()) == 0


def test_saat_kennt_workspace_legacy() -> None:
    assert "'workspace-legacy'" in _code()
    assert (SPACE / "config" / "workspace-legacy" / "SOUL.md").is_file()


def test_ev_legacy_ist_kein_arbeiter_agent() -> None:
    # Ruling T8-b: ev-legacy gehoert zur Saat; das Arbeiter-Muster darf ihn nicht erfassen.
    treffer = re.search(r"const AGENT = /(.+)/;", _code())
    assert treffer is not None
    muster = re.compile(treffer.group(1))
    assert muster.pattern == "^(ev|ld|bo)-[0-9a-f]{8}$"
    assert not muster.match("ev-legacy")
    assert muster.match("ev-1a2b3c4d")


def _echte_saat() -> dict:
    return _vorlage()


def test_zweiter_lauf_erhaelt_alles(tmp_path: Path) -> None:
    # Task 4 erweitert diese Pruefung um die Server bn-lesen/bn-auftrag.
    saat = _echte_saat()
    arbeiter = {"id": "ev-1a2b3c4d", "workspace": "/home/node/.openclaw/workspace-ev-1a2b3c4d",
                "tools": {"allow": ["ev-1a2b3c4d__*"]}}
    server = {"url": "http://event-mcp.example.org:8171/mcp", "transport": "streamable-http",
              "headers": {"Authorization": "Bearer evk_beispiel_nur_fuer_den_test"}}
    bind = {"agentId": "ev-1a2b3c4d", "match": {
        "channel": "telegram", "peer": {"kind": "group", "id": "-1001234567890"}}}
    # Review R1: je ein Arbeiter-Agent jeder Art (ev- Gruppe, ld- Leitung im Direktchat,
    # bo- Betreiber im Direktchat), alle drei mit eigenem Server und Binding.
    leitung = {"id": "ld-5e6f7a8b", "workspace": "/home/node/.openclaw/workspace-ld-5e6f7a8b",
               "tools": {"allow": ["ld-5e6f7a8b__*"]}}
    betreiber = {"id": "bo-9c0d1e2f", "workspace": "/home/node/.openclaw/workspace-bo-9c0d1e2f",
                 "tools": {"allow": ["bo-9c0d1e2f__*"]}}
    server_ld = {**server, "headers": {"Authorization": "Bearer evk_beispiel_nur_fuer_den_test_ld"}}
    server_bo = {**server, "headers": {"Authorization": "Bearer evk_beispiel_nur_fuer_den_test_bo"}}
    bind_ld = {"agentId": "ld-5e6f7a8b", "match": {
        "channel": "telegram", "peer": {"kind": "direct", "id": "1234567"}},
        "session": {"dmScope": "per-peer"}}
    bind_bo = {"agentId": "bo-9c0d1e2f", "match": {
        "channel": "telegram", "peer": {"kind": "direct", "id": "7654321"}},
        "session": {"dmScope": "per-peer"}}

    def _arbeiter_dazu(konfig: dict) -> None:
        konfig["agents"]["list"].extend([arbeiter, leitung, betreiber])
        konfig["mcp"]["servers"].update({"ev-1a2b3c4d": server, "ld-5e6f7a8b": server_ld,
                                         "bo-9c0d1e2f": server_bo})
        konfig["bindings"].extend([bind, bind_ld, bind_bo])
        konfig["channels"]["telegram"]["groups"]["-1001234567890"] = {"requireMention": False}

    aktuell = json.loads(json.dumps(saat))
    _arbeiter_dazu(aktuell)
    aktuell["gateway"]["port"] = 19999  # abweichende Saat -> Ersetzen

    ausgabe, cfg = _saat_laufen(tmp_path, aktuell, seed=saat)
    assert "gesichert und durch die Saat ersetzt" in ausgabe
    erwartet = json.loads(json.dumps(saat))
    _arbeiter_dazu(erwartet)
    neu = json.loads(cfg.read_text(encoding="utf-8"))
    assert neu == erwartet
    ids = [a["id"] for a in neu["agents"]["list"]]
    assert {"ev-legacy", "ev-1a2b3c4d", "ld-5e6f7a8b", "bo-9c0d1e2f"} <= set(ids)
    assert {b["agentId"] for b in neu["bindings"]} == {"ev-legacy", "ev-1a2b3c4d", "ld-5e6f7a8b",
                                                       "bo-9c0d1e2f"}
    assert {"ld-5e6f7a8b", "bo-9c0d1e2f"} <= set(neu["mcp"]["servers"])
    assert "evk_" not in ausgabe and "-1001234567890" not in ausgabe
    assert "1234567" not in ausgabe and "7654321" not in ausgabe

    # Brain (Task 4): beide Server und die Erlaubnislisten kommen aus der Saat.
    for name, agent in (("bn-lesen", "brain-lesen"), ("bn-auftrag", "brain-auftrag")):
        assert neu["mcp"]["servers"][name] == saat["mcp"]["servers"][name]
        allow = next(a for a in neu["agents"]["list"] if a["id"] == agent)["tools"]["allow"]
        vorlage = next(a for a in saat["agents"]["list"] if a["id"] == agent)["tools"]["allow"]
        assert allow == vorlage
    # F-2: bn-lesen bleibt ein Muster (Katalog nur lesend), bn-auftrag ist eine feste Liste
    # ohne Muster, ohne Loeschen und ohne die vier nach aussen wirkenden Werkzeuge.
    auftrag = next(a for a in neu["agents"]["list"] if a["id"] == "brain-auftrag")["tools"]["allow"]
    bn = [n for n in auftrag if n.startswith("bn-auftrag__")]
    assert bn and "bn-auftrag__*" not in auftrag and not [n for n in bn if "*" in n]
    for verboten in ("nachricht_an_gruppe", "einladen", "firma_anlegen", "event_anlegen",
                     "event_loeschen", "aufgaben_loeschen"):
        assert f"bn-auftrag__{verboten}" not in auftrag, verboten
    assert "bn-lesen__*" in next(a for a in neu["agents"]["list"]
                                 if a["id"] == "brain-lesen")["tools"]["allow"]

    # Zwischen den Laeufen setzt Schritt 4 die Schluessel (hier nachgestellt): die
    # Header der Brain-Server, des Empfangs und der Event-App.
    mit_schluesseln = json.loads(cfg.read_text(encoding="utf-8"))
    for name in ("event-app", "melden", "bn-lesen", "bn-auftrag"):
        mit_schluesseln["mcp"]["servers"][name]["headers"] = {
            "Authorization": f"Bearer evk_beispiel_nur_fuer_den_test_{name}"}
    cfg.write_text(json.dumps(mit_schluesseln), encoding="utf-8")

    # Zweiter Lauf, gleiche Saat: nichts mehr zu tun, alle Header bleiben.
    ausgabe2, cfg = _saat_laufen(tmp_path, None, seed=saat)
    assert "openclaw.json: unveraendert" in ausgabe2
    assert json.loads(cfg.read_text(encoding="utf-8")) == mit_schluesseln
    assert "evk_" not in ausgabe2


# --- Saat erhaelt bereitgestellte Agenten (Ruling T8-b) -------------------------

SAAT = {
    "gateway": {"port": 19100, "auth": {"mode": "token"}},
    "agents": {"list": [{"id": "main", "tools": {"allow": ["melden__gruppe_melden"]}}]},
    "bindings": [{"agentId": "ev-legacy", "match": {
        "channel": "telegram", "peer": {"kind": "group", "id": "-1009"}}}],
    "channels": {"telegram": {"groups": {"-1009": {"requireMention": False},
                                         "*": {"requireMention": True}}}},
    "mcp": {"servers": {"melden": {"url": "http://melden.example.org/mcp"}}},
}
GEHEIM_EV = "evk_beispiel_nur_fuer_den_test_ev"
GEHEIM_LD = "evk_beispiel_nur_fuer_den_test_ld"
GRUPPE_EV = "-1001234567890"
BEREITGESTELLT = {
    "agenten": [{"id": "ev-0123abcd", "workspace": "/home/node/.openclaw/workspace-ev-0123abcd",
                 "tools": {"allow": ["ev-0123abcd__*"]}},
                {"id": "ld-89abcdef", "workspace": "/home/node/.openclaw/workspace-ld-89abcdef",
                 "tools": {"allow": ["ld-89abcdef__*"]}}],
    "server": {
        "ev-0123abcd": {"url": "http://event-mcp.example.org:8171/mcp",
                        "transport": "streamable-http",
                        "headers": {"Authorization": f"Bearer {GEHEIM_EV}"}},
        "ld-89abcdef": {"url": "http://event-mcp.example.org:8171/mcp",
                        "transport": "streamable-http",
                        "headers": {"Authorization": f"Bearer {GEHEIM_LD}"}}},
    "bindings": [
        {"agentId": "ev-0123abcd", "match": {
            "channel": "telegram", "peer": {"kind": "group", "id": GRUPPE_EV}}},
        {"agentId": "ld-89abcdef", "match": {
            "channel": "telegram", "peer": {"kind": "direct", "id": "123456789"}},
         "session": {"dmScope": "per-peer"}}],
}


def _mit_bereitgestellten(basis: dict) -> dict:
    c = json.loads(json.dumps(basis))
    c["agents"]["list"] += BEREITGESTELLT["agenten"]
    c["mcp"]["servers"].update(BEREITGESTELLT["server"])
    c["bindings"] = c.get("bindings", []) + BEREITGESTELLT["bindings"]
    c["channels"]["telegram"]["groups"][GRUPPE_EV] = {"requireMention": False}
    return c


# JS-Vorspann: protokolliert die PFADE (nie Inhalte) der Schreibaufrufe nach $SPION_LOG.
SPION_JS = r"""
const __fs = require('fs');
const __log = [];
for (const __n of ['writeFileSync', 'copyFileSync', 'renameSync', 'chmodSync']) {
  const __o = __fs[__n];
  __fs[__n] = function (...a) {
    const n = (__n === 'copyFileSync' || __n === 'renameSync') ? 2 : 1;
    __log.push([__n].concat(a.slice(0, n).map(String)));
    return __o.apply(this, a);
  };
}
process.on('exit', () => { __fs.appendFileSync(process.env.SPION_LOG, JSON.stringify(__log)); });
"""


def _node(tmp_path: Path, js: str, eingabe: bytes = b"") -> tuple[str, list[list[str]]]:
    """Fuehrt js (mit Spion-Vorspann) mit lokalem node aus; Ausgabe und Schreibprotokoll."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("kein node")
    skript = tmp_path / "lauf.js"
    skript.write_text(SPION_JS + js, encoding="utf-8")
    log = tmp_path / "spion.json"
    log.unlink(missing_ok=True)  # mehrere Laeufe je Test: Protokoll nie anhaengen
    r = subprocess.run([node, str(skript)], input=eingabe, capture_output=True, timeout=60,
                       env={**os.environ, "SPION_LOG": str(log)})
    ausgabe = r.stdout.decode("utf-8") + r.stderr.decode("utf-8")
    assert r.returncode == 0, ausgabe
    return ausgabe, json.loads(log.read_text(encoding="utf-8"))


def _atomar_geschrieben(log: list[list[str]], cfg: Path) -> None:
    """F6: openclaw.json nur per Temp-Datei im selben Verzeichnis (0600) + renameSync."""
    ziel = cfg.as_posix()
    assert not [e for e in log if e[0] in ("writeFileSync", "copyFileSync") and e[-1] == ziel]
    umbenannt = [e for e in log if e[0] == "renameSync" and e[2] == ziel]
    assert len(umbenannt) == 1
    tmp = umbenannt[0][1]
    assert tmp != ziel and tmp.rsplit("/", 1)[0] == ziel.rsplit("/", 1)[0]
    assert ["chmodSync", tmp] in log
    assert log.index(["chmodSync", tmp]) < log.index(umbenannt[0])
    assert not [p for p in cfg.parent.iterdir() if ".tmp-" in p.name]
    if os.name != "nt":
        assert stat.S_IMODE(cfg.stat().st_mode) == 0o600


def _saat_laufen(tmp_path: Path, aktuell: dict | None,
                 log: list[list[str]] | None = None,
                 seed: dict | None = None) -> tuple[str, Path]:
    """Fuehrt SAAT_JS mit lokalem node gegen Verzeichnisse unter tmp_path aus.

    aktuell None: es gibt noch keine openclaw.json. Ist `log` eine Liste, landet dort das
    Schreibprotokoll. `seed` ersetzt die Saat-Datei (Standard: SAAT)."""
    zustand, saat = tmp_path / "zustand", tmp_path / "saat"
    for d in (zustand, saat / "workspace", saat / "workspace-brain", saat / "workspace-legacy"):
        d.mkdir(parents=True, exist_ok=True)
    (saat / "openclaw.json").write_text(json.dumps(seed if seed is not None else SAAT),
                                        encoding="utf-8")
    if aktuell is not None:
        (zustand / "openclaw.json").write_text(json.dumps(aktuell), encoding="utf-8")
    js = _heredoc("SAAT_JS=$(cat <<'JS'", "JS")
    for alt, neu in (("const STATE = '/home/node/.openclaw';", zustand),
                     ("const SEED = '/seed';", saat)):
        assert js.count(alt) == 1
        js = js.replace(alt, alt.split("=")[0] + "= " + json.dumps(neu.as_posix()) + ";")
    ausgabe, protokoll = _node(tmp_path, js)
    if log is not None:
        log.extend(protokoll)
    return ausgabe, zustand / "openclaw.json"


def test_saat_ersetzt_und_uebernimmt_bereitgestellte_agenten(tmp_path: Path) -> None:
    aktuell = _mit_bereitgestellten(SAAT)
    aktuell["gateway"]["port"] = 19999                  # Abweichung -> Ersetzen
    aktuell["gateway"]["auth"]["token"] = "gateway-geheim-beispiel"
    aktuell["agents"]["list"].append({"id": "fremd"})   # nicht bereitgestellt -> weg
    ausgabe, cfg = _saat_laufen(tmp_path, aktuell)
    assert "openclaw.json: gesichert und durch die Saat ersetzt" in ausgabe
    assert "2 Agenten, 2 Server, 2 Bindings, 1 Gruppen" in ausgabe
    # Keine Werte in der Ausgabe: weder Schluessel noch Chat-IDs.
    for wert in ("evk_", GRUPPE_EV, "123456789", "gateway-geheim"):
        assert wert not in ausgabe
    neu = json.loads(cfg.read_text(encoding="utf-8"))
    assert neu == _mit_bereitgestellten(SAAT)
    assert [p.name for p in cfg.parent.iterdir() if ".vor-anbinden-" in p.name]


def test_saat_unveraendert_wenn_nur_bereitgestellte_teile_abweichen(tmp_path: Path) -> None:
    aktuell = _mit_bereitgestellten(SAAT)
    aktuell["gateway"]["auth"]["token"] = "gateway-geheim-beispiel"
    aktuell["meta"] = {"lastTouchedVersion": "x"}
    ausgabe, cfg = _saat_laufen(tmp_path, aktuell)
    assert "openclaw.json: unveraendert" in ausgabe
    assert json.loads(cfg.read_text(encoding="utf-8")) == aktuell
    assert not [p for p in cfg.parent.iterdir() if ".vor-anbinden-" in p.name]
    assert "evk_" not in ausgabe


def test_saat_ersetzt_atomar(tmp_path: Path) -> None:
    aktuell = _mit_bereitgestellten(SAAT)
    aktuell["gateway"]["port"] = 19999
    log: list[list[str]] = []
    ausgabe, cfg = _saat_laufen(tmp_path, aktuell, log)
    assert "openclaw.json: gesichert und durch die Saat ersetzt" in ausgabe
    _atomar_geschrieben(log, cfg)
    assert json.loads(cfg.read_text(encoding="utf-8")) == _mit_bereitgestellten(SAAT)


def test_saat_legt_atomar_an(tmp_path: Path) -> None:
    log: list[list[str]] = []
    ausgabe, cfg = _saat_laufen(tmp_path, None, log)
    assert "openclaw.json: angelegt" in ausgabe
    _atomar_geschrieben(log, cfg)
    assert json.loads(cfg.read_text(encoding="utf-8")) == SAAT


def test_schluessel_atomar_und_ohne_werte_in_der_ausgabe(tmp_path: Path) -> None:
    zustand = tmp_path / "zustand"
    zustand.mkdir()
    cfg = zustand / "openclaw.json"
    cfg.write_text(json.dumps(SAAT), encoding="utf-8")
    js = _heredoc("SCHLUESSEL_JS=$(cat <<'JS'", "JS")
    alt = "const CFG = '/home/node/.openclaw/openclaw.json';"
    assert js.count(alt) == 1
    js = js.replace(alt, "const CFG = " + json.dumps(cfg.as_posix()) + ";")
    werte = ["gateway-beispiel", "123:tg-beispiel", "Bearer evk_beispiel_app",
             "Bearer evk_beispiel_melden", "Bearer evk_beispiel_bn_lesen",
             "Bearer evk_beispiel_bn_auftrag"]
    ausgabe, log = _node(tmp_path, js, ("\n".join(werte) + "\n").encode("utf-8"))
    assert ausgabe.count(": gesetzt") == 6
    for wert in werte:
        assert wert not in ausgabe
    _atomar_geschrieben(log, cfg)
    neu = json.loads(cfg.read_text(encoding="utf-8"))
    assert neu["gateway"]["auth"]["token"] == "gateway-beispiel"
    assert neu["mcp"]["servers"]["melden"]["headers"]["Authorization"] == werte[3]
    assert neu["mcp"]["servers"]["bn-lesen"]["headers"]["Authorization"] == werte[4]
    assert neu["mcp"]["servers"]["bn-auftrag"]["headers"]["Authorization"] == werte[5]
    # Ein zweiter Lauf mit denselben Werten aendert nichts.
    ausgabe, log = _node(tmp_path, js, ("\n".join(werte) + "\n").encode("utf-8"))
    assert ausgabe.count(": unveraendert") == 6 and ": gesetzt" not in ausgabe
    assert not [e for e in log if e[0] == "renameSync"]


def test_schluessel_bricht_ohne_brain_werte_ab(tmp_path: Path) -> None:
    """Fehlt Wert 5 oder 6 (Zeile 6/7 von stdin), wird nichts geschrieben."""
    zustand = tmp_path / "zustand"
    zustand.mkdir()
    cfg = zustand / "openclaw.json"
    cfg.write_text(json.dumps(SAAT), encoding="utf-8")
    js = _heredoc("SCHLUESSEL_JS=$(cat <<'JS'", "JS")
    js = js.replace("const CFG = '/home/node/.openclaw/openclaw.json';",
                    "const CFG = " + json.dumps(cfg.as_posix()) + ";")
    skript = tmp_path / "schluessel.js"
    skript.write_text(js, encoding="utf-8")
    node = shutil.which("node")
    if node is None:
        pytest.skip("kein node")
    werte = ["gateway-beispiel", "123:tg-beispiel", "Bearer evk_beispiel_app",
             "Bearer evk_beispiel_melden", "Bearer evk_beispiel_bn_lesen"]
    r = subprocess.run([node, str(skript)], input=("\n".join(werte) + "\n").encode("utf-8"),
                       capture_output=True, timeout=60)
    ausgabe = (r.stdout + r.stderr).decode("utf-8")
    assert r.returncode != 0
    assert "mcp.servers.bn-auftrag.headers.Authorization: Wert fehlt" in ausgabe
    assert json.loads(cfg.read_text(encoding="utf-8")) == SAAT
    for wert in werte:
        assert wert not in ausgabe


# --- Compose -----------------------------------------------------------------

def test_shim_dienst_mit_eigenem_budget_agenten() -> None:
    shim = _compose()["services"]["event-management-shim"]
    assert shim["image"] == "event-management/shim:2.1.268"
    assert shim["container_name"] == "event-management-shim"
    assert shim["init"] is True and shim["read_only"] is True
    env = shim["environment"]
    assert env["VIBEMIND_AGENT"] == "event-management-claw"
    assert env["SHIM_TOOL_CALLS"] == "1"
    assert env["VIBEMIND_BUDGET_DIR"] == "/budget"
    assert env["VIBEMIND_BUDGET_CONFIG"] == "/opt/event/agent_budget.yaml"
    assert shim["env_file"] == [{"path": "./laufzeit/claude-token", "required": False}]
    assert shim["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in shim["security_opt"]
    assert "./deploy/agent_budget.yaml:/opt/event/agent_budget.yaml:ro" in shim["volumes"]
    budget = [v for v in shim["volumes"] if isinstance(v, dict)]
    assert budget and budget[0]["target"] == "/budget"
    assert budget[0]["source"] == "./laufzeit/shim-budget"
    assert budget[0]["bind"]["create_host_path"] is False
    assert "8125" in " ".join(shim["healthcheck"]["test"])


def test_netze_getrennt_und_agentennetz_intern() -> None:
    compose = _compose()
    claw = compose["services"]["event-management-claw"]
    shim = compose["services"]["event-management-shim"]
    assert set(claw["networks"]) == {"event-agent", "event-claw-aus"}
    assert set(shim["networks"]) == {"event-agent", "event-shim-aus"}
    assert "event-shim-aus" not in claw["networks"]
    assert "event-claw-aus" not in shim["networks"]
    assert compose["networks"]["event-agent"]["internal"] is True
    assert not (compose["networks"]["event-claw-aus"] or {}).get("internal")
    assert not (compose["networks"]["event-shim-aus"] or {}).get("internal")


def test_claw_wartet_auf_den_shim_und_haengt_die_saat_ein() -> None:
    claw = _compose()["services"]["event-management-claw"]
    assert claw["depends_on"]["event-management-shim"]["condition"] == "service_healthy"
    assert "./build/openclaw.json:/seed/openclaw.json:ro" in claw["volumes"]


def test_budget_datei_kennt_genau_den_claw() -> None:
    budget = yaml.safe_load(BUDGET.read_text(encoding="utf-8"))
    assert list(budget["agenten"]) == ["event-management-claw"]
    assert budget["agenten"]["event-management-claw"] == {
        "bereich": "event-management", "klasse": "auftrag", "modell": "sonnet",
        "tagesgrenze": 300, "wrapper": "shim", "warnen_ab": 0.8}
    assert budget["uebergang_unbekannte_zulassen"] is False
    assert budget["pause_bei_user_aktiv"] is False
    assert budget["pause_fenster_min"] == 10
    assert budget["pause_ausnahmen"] == [] and budget["behalten_ohne_llm"] == []


def test_shim_url_zeigt_auf_den_eigenen_shim() -> None:
    url = json.loads(BEISPIEL.read_text(encoding="utf-8"))["SHIM_URL"]
    assert "event-management-shim:8125" in url
    assert url == "http://event-management-shim:8125/v1"


def test_laufzeit_ist_gitignored() -> None:
    zeilen = {z.strip() for z in GITIGNORE.read_text(encoding="utf-8").splitlines()}
    assert "laufzeit/" in zeilen


def test_whatsapp_bleibt_aus() -> None:
    # OpenClaw 2026.7.1-slim bringt das WhatsApp-Plugin nicht mit; die aktuelle
    # Fassung von clawhub verlangt Plugin-API >= 2026.11.3, der Start bricht
    # dann ab (gemessen 12.11.2026 auf offload-vm). Die WhatsApp-Kopplung ist
    # ohnehin nicht mit umgezogen.
    cfg = json.loads((SPACE / "config" / "openclaw.json").read_text(encoding="utf-8"))
    assert cfg["plugins"]["entries"]["whatsapp"]["enabled"] is False
    assert cfg["channels"]["whatsapp"]["enabled"] is False
