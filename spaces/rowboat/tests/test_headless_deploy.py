"""Vertrag fuer spaces/rowboat/deploy/headless (Rowboat-Server ohne Oberflaeche auf dem Mini-PC)."""
import re
from pathlib import Path

import pytest
import yaml

HEADLESS = Path(__file__).resolve().parents[1] / "deploy" / "headless"
GEHEIM = re.compile(r"(key|token|secret|passw|geheim)", re.IGNORECASE)


def dienst(name: str = "rowboat-server") -> dict:
    compose = yaml.safe_load((HEADLESS / "compose.yml").read_text(encoding="utf-8"))
    assert sorted(compose["services"]) == ["rowboat-server", "rowboat-shim"]
    return compose["services"][name]


def umgebung(svc: dict) -> dict[str, str]:
    env = svc["environment"]
    if isinstance(env, list):
        env = dict(e.split("=", 1) for e in env)
    return {k: str(v) for k, v in env.items()}


def test_compose_vertrag():
    svc = dienst()
    env = umgebung(svc)
    # Host-Netz: die Host-Pruefung des Servers (server.ts buildAllowedHosts) kennt nur eigene
    # Schnittstellen; im Bridge-Netz fehlt die Tailscale-IP -> 403.
    assert svc["network_mode"] == "host"
    assert "ports" not in svc
    assert svc["restart"] == "unless-stopped"
    assert env["ROWBOAT_WORKDIR"] == "/data"
    assert env["ROWBOAT_SERVER_PORT"] == "3220"
    # Lange Form ohne create_host_path: fehlt der Ordner, legt Docker ihn NICHT als root an.
    assert svc["volumes"] == [{"type": "bind", "source": "/home/debian/rowboat-data", "target": "/data",
                               "bind": {"create_host_path": False}}]
    assert svc["mem_limit"] == "3g"
    assert env["NODE_OPTIONS"] == "--max-old-space-size=2048"
    probe = " ".join(svc["healthcheck"]["test"]) if isinstance(svc["healthcheck"]["test"], list) \
        else svc["healthcheck"]["test"]
    assert "http://127.0.0.1:3220/health" in probe
    assert "curl" not in probe and "wget" not in probe   # node:22-bookworm-Laufzeit ohne curl verlassen wir nicht
    assert "node" in probe


def test_compose_telemetrie_aus():
    env = umgebung(dienst())
    assert env["ROWBOAT_TELEMETRY_DISABLED"] == "1"  # core/src/analytics/posthog.ts
    assert env["DO_NOT_TRACK"] == "1"


def test_compose_ohne_geheimnis():
    for dienstname in ("rowboat-server", "rowboat-shim"):
        svc = dienst(dienstname)
        for name, wert in umgebung(svc).items():
            assert not GEHEIM.search(name), f"Geheimnis-artiger Name in environment: {name}"
            assert len(wert) < 40, f"verdaechtig langer Wert in environment: {name}"
    assert "env_file" not in dienst("rowboat-server")


def test_dockerfile_node22():
    zeilen = (HEADLESS / "Dockerfile").read_text(encoding="utf-8").splitlines()
    assert zeilen[0] == "FROM node:22-bookworm"
    text = "\n".join(zeilen)
    assert "npm install --omit=dev" in text
    assert 'CMD ["node", "rowboat-server.cjs"]' in text or 'CMD ["node","rowboat-server.cjs"]' in text


def test_start_schaltet_lan_ein_ohne_vorhandenes_zu_ueberschreiben():
    # Ohne lanEnabled bindet der Server nur 127.0.0.1 (server.ts) und waere ueber Tailscale tot.
    start = (HEADLESS / "start.sh").read_text(encoding="utf-8")
    assert '"lanEnabled": true' in start
    assert "if [ ! -e" in start          # vorhandene server.json bleibt unangetastet
    assert 'exec "$@"' in start
    assert b"\r\n" not in (HEADLESS / "start.sh").read_bytes()


def test_bauen_loescht_nichts_auf_der_vm():
    skript = (HEADLESS / "bauen.ps1").read_text(encoding="utf-8")
    for param in ("NurBauen", "Quelle", "Ziel"):
        assert f"${param}" in skript
    assert "offload-vm:~/rowboat-headless/app/" in skript
    assert "build:headless" in skript and "run deps" in skript
    code = re.sub(r"<#.*?#>", "", skript, flags=re.DOTALL)
    code = "\n".join(z for z in code.splitlines() if not z.lstrip().startswith("#"))
    assert not re.search(r"\brm\s|Remove-Item|rmdir|--delete|\bdel\s", code), "bauen.ps1 darf nichts loeschen"


def test_bauen_verweigert_den_arbeitsordner_als_ziel():
    skript = (HEADLESS / "bauen.ps1").read_text(encoding="utf-8")
    assert re.search(r"if \(\$Ziel -match 'rowboat-data'\) \{ throw", skript)


def test_bauen_kopiert_relative_namen_aus_dist_headless():
    skript = (HEADLESS / "bauen.ps1").read_text(encoding="utf-8")
    zeile = next(z for z in skript.splitlines() if "scp" in z and "$namen" in z)
    assert zeile.lstrip().startswith("Invoke-Schritt $dist ")   # Push-Location in dist-headless und zurueck
    assert "FullName" not in skript
    assert re.search(r"\$namen = @\(Get-ChildItem -LiteralPath \$dist -Name\)", skript)


def test_dockerignore_ohne_node_modules():
    zeilen = (HEADLESS / ".dockerignore").read_text(encoding="utf-8").split()
    assert "app/node_modules" in zeilen


def test_firewall_nur_loopback_und_tailnet():
    regel = (HEADLESS / "rowboat-firewall.nft").read_text(encoding="utf-8")
    flach = " ".join(regel.split())
    assert "table inet rowboat {" in flach
    assert "type filter hook input priority -10; policy accept;" in flach
    accept = flach.index('iifname { "lo", "tailscale0" } tcp dport 3220-3229 accept')
    drop = flach.index("tcp dport 3220-3229 drop")
    assert accept < drop
    assert "flush ruleset" not in flach   # Dockers Tabellen bleiben unberuehrt
    readme = (HEADLESS / "README.md").read_text(encoding="utf-8")
    assert "## Firewall (vor dem ersten Start)" in readme
    assert "sudo nft -f" in readme and "/etc/nftables.conf" in readme and "systemctl enable nftables" in readme
    assert "port 3220 is taken" in readme


# --- Task 4: Claude-Shim mit Budget-Huelle -------------------------------------------------------

SHIM = HEADLESS / "shim"
SHIM_DATEIEN = ("claude_code_openai_shim.py", "claude_code_openai_tools.py", "claude_budget.py",
                "agent_budget.yaml", "rowboat_shim_huelle.py")


def _befehl(svc: dict) -> str:
    cmd = svc.get("command", "")
    return " ".join(cmd) if isinstance(cmd, list) else str(cmd)


def test_shim_nur_loopback():
    svc = dienst("rowboat-shim")
    assert svc["network_mode"] == "host"          # Rowboat (Host-Netz) erreicht 127.0.0.1:8121
    assert "ports" not in svc
    befehl = _befehl(svc)
    assert "--host 127.0.0.1" in befehl and "--port 8121" in befehl
    assert "0.0.0.0" not in befehl
    assert "rowboat_shim_huelle.py" in befehl     # nie den Shim ohne Budget-Huelle starten
    env = umgebung(svc)
    assert env["SHIM_TOOL_CALLS"] == "1"
    assert env["VIBEMIND_AGENT"] == "rowboat-server"
    assert env["VIBEMIND_BUDGET_DIR"] == "/budget"
    probe = " ".join(svc["healthcheck"]["test"])
    assert "127.0.0.1:8121/v1/models" in probe


def test_shim_token_nur_aus_datei():
    svc = dienst("rowboat-shim")
    assert svc["env_file"] in ("./claude-token", ["./claude-token"])
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in umgebung(svc)
    for datei in ("Dockerfile", "start.sh", "rowboat_shim_huelle.py"):
        text = (SHIM / datei).read_text(encoding="utf-8")
        assert not re.search(r"sk-ant-|CLAUDE_CODE_OAUTH_TOKEN\s*=\s*\S", text), datei
    assert "sk-ant-" not in (HEADLESS / "compose.yml").read_text(encoding="utf-8")
    # start.sh prueft nur, dass der Token da ist, und gibt ihn nie aus.
    start = (SHIM / "start.sh").read_text(encoding="utf-8")
    assert '[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]' in start
    assert not re.search(r"(echo|printf)[^\n]*\$\{?CLAUDE_CODE_OAUTH_TOKEN", start)
    assert b"\r\n" not in (SHIM / "start.sh").read_bytes()


def test_shim_budget_volume():
    svc = dienst("rowboat-shim")
    # Lange Form ohne create_host_path: fehlt ./budget, legt Docker ihn NICHT als root an.
    assert svc["volumes"] == [{"type": "bind", "source": "./budget", "target": "/budget",
                               "bind": {"create_host_path": False}}]


def test_shim_dockerfile():
    zeilen = (SHIM / "Dockerfile").read_text(encoding="utf-8").splitlines()
    assert zeilen[0] == "FROM node:22-bookworm-slim"
    text = "\n".join(zeilen)
    assert re.search(r"npm install -g @anthropic-ai/claude-code@\d+\.\d+\.\d+", text)   # gepinnt
    assert "python3-yaml" in text                  # claude_budget braucht pyyaml
    for datei in SHIM_DATEIEN:
        assert datei in text
    assert "USER node" in text
    assert "VIBEMIND_BUDGET_CONFIG=/opt/shim/agent_budget.yaml" in text


def test_bauen_kopiert_shim_kontext():
    skript = (HEADLESS / "bauen.ps1").read_text(encoding="utf-8")
    for datei in SHIM_DATEIEN:
        assert datei in skript
    assert "rowboat-server:" in skript              # bricht ab, wenn der Budget-Eintrag fehlt
    assert "mkdir -p -m 0700 $basis/budget" in skript


def _budget_yaml() -> Path | None:
    for ort in HEADLESS.parents:
        kandidat = ort / "config" / "agent_budget.yaml"
        if kandidat.is_file():
            return kandidat
    return None


def test_budget_eintrag_rowboat_server():
    pfad = _budget_yaml()
    if pfad is None:
        pytest.skip("aeusseres Repo mit config/agent_budget.yaml nicht vorhanden (CI ohne Vibemind_V1)")
    eintrag = yaml.safe_load(pfad.read_text(encoding="utf-8"))["agenten"]["rowboat-server"]
    assert eintrag == {"bereich": "wissen", "klasse": "auftrag", "modell": "sonnet",
                       "tagesgrenze": 150, "wrapper": "shim"}


def test_dockerignore_haelt_token_und_budget_aus_dem_build():
    zeilen = (HEADLESS / ".dockerignore").read_text(encoding="utf-8").split()
    assert "claude-token" in zeilen and "budget/" in zeilen


def test_server_wartet_auf_gesunde_shim():
    import yaml
    dienste = yaml.safe_load((HEADLESS / "compose.yml").read_text(encoding="utf-8"))["services"]
    assert dienste["rowboat-server"]["depends_on"] == {"rowboat-shim": {"condition": "service_healthy"}}
