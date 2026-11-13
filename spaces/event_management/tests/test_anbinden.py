"""Ausrollen (Task 6, Repo-Teil): eigener Shim, Budget-Datei und anbinden.sh.

Statische Pruefungen, keine Container: das Skript laeuft nur auf der VM. Hier
wird festgehalten, dass kein Schluessel auf eine Kommandozeile oder in eine
Ausgabe gelangt und dass Saat und Netze so geschnitten sind wie beschlossen.
"""
from __future__ import annotations

import json
import re
import subprocess
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

# Namen der Shell-Variablen, die Schluessel tragen (Zeilen 1-5 von stdin).
SCHLUESSEL_VARIABLEN = ("GW", "TG", "APP", "CC", "MLD")


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _code() -> str:
    text = SKRIPT.read_text(encoding="utf-8")
    return "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))


def _shell_zeilen() -> list[str]:
    """Zeilen der Shell, ohne die eingebetteten JavaScript-Heredocs."""
    aus: list[str] = []
    im_js = False
    for z in _code().splitlines():
        if re.search(r"<<\s*'JS'", z):
            im_js = True
        if not im_js:
            aus.append(z)
        elif z.strip() == "JS":
            im_js = False
    return aus


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


def test_werte_nur_per_stdin_fuenf_zeilen() -> None:
    shell = "\n".join(_shell_zeilen())
    assert len(re.findall(r"\bread\b", shell)) == 5
    assert all(re.search(rf"IFS= read -r {v}\b", shell) for v in SCHLUESSEL_VARIABLEN)
    # Kein Wert als Argument: $1..$9, $@ und $* kommen in der Shell nicht vor.
    # (Die Hilfsfunktion zeichen_pruefen darf ihre eigenen Parameter lesen.)
    ohne_funktion = re.sub(r"zeichen_pruefen\(\) \{.*?\n\}", "", shell, flags=re.S)
    assert not re.search(r"\$[1-9@*]|\$\{[1-9@*]", ohne_funktion)
    # Leere Zeilen 1-3 und 5 sind ein Fehler, Zeile 4 darf leer sein.
    for v in ("GW", "TG", "APP", "MLD"):
        assert re.search(rf'\[ -z "\${v}" \]', shell), v
    assert not re.search(r'\[ -z "\$CC" \]\s*&&', shell)


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
    assert "['workspace', 'workspace-brain']" in code
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
    assert "'%s\\n%s\\n%s\\n%s\\n' \"$GW\" \"$TG\" \"$APP\" \"$MLD\" | docker compose run" in code


def test_vergleich_ignoriert_meta_und_wizard() -> None:
    code = SKRIPT.read_text(encoding="utf-8")
    assert "delete c.meta" in code and "delete c.wizard" in code


def test_zeichenpruefung_ohne_wert_in_der_meldung() -> None:
    code = _code()
    assert "LC_ALL=C" in code
    assert "[!" in code  # case-Muster fuer verbotene Zeichen
    assert "nicht druckbare" in code


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
        "tagesgrenze": 300, "wrapper": "shim"}
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
