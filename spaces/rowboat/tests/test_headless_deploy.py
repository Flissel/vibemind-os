"""Vertrag fuer spaces/rowboat/deploy/headless (Rowboat-Server ohne Oberflaeche auf dem Mini-PC)."""
import json
import re
from pathlib import Path

import pytest
import yaml

HEADLESS = Path(__file__).resolve().parents[1] / "deploy" / "headless"
GEHEIM = re.compile(r"(key|token|secret|passw|geheim)", re.IGNORECASE)


def dienst(name: str = "rowboat-server") -> dict:
    compose = yaml.safe_load((HEADLESS / "compose.yml").read_text(encoding="utf-8"))
    assert sorted(compose["services"]) == ["rowboat-auftraege", "rowboat-server", "rowboat-shim", "rowboat-werkzeuge",
                                        "rowboat-wissen"]
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


# GITHUB_BESITZER nennt nur Besitzer und Variablennamen (die Werte stehen in werkzeuge.env)
NUR_NAMEN = re.compile(r"^[A-Za-z0-9-]+=[A-Z0-9_]+(,[A-Za-z0-9-]+=[A-Z0-9_]+)*$")


def _env_dateien(svc: dict) -> list[str]:
    roh = svc.get("env_file")
    if roh is None:
        return []
    eintraege = roh if isinstance(roh, list) else [roh]
    return [e["path"] if isinstance(e, dict) else e for e in eintraege]


def test_compose_ohne_geheimnis():
    for dienstname in ("rowboat-server", "rowboat-shim", "rowboat-wissen", "rowboat-werkzeuge", "rowboat-auftraege"):
        svc = dienst(dienstname)
        for name, wert in umgebung(svc).items():
            assert not GEHEIM.search(name), f"Geheimnis-artiger Name in environment: {name}"
            if name == "GITHUB_BESITZER":
                assert NUR_NAMEN.match(wert), wert
                continue
            assert len(wert) < 40, f"verdaechtig langer Wert in environment: {name}"
    assert _env_dateien(dienst("rowboat-server")) == []
    assert _env_dateien(dienst("rowboat-wissen")) == []
    assert _env_dateien(dienst("rowboat-shim")) == ["./claude-token"]
    assert _env_dateien(dienst("rowboat-werkzeuge")) == ["./werkzeuge.env"]
    assert _env_dateien(dienst("rowboat-auftraege")) == ["./auftraege.env"]


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
    assert "sudo nft -f" in readme and "enable --now rowboat-firewall" in readme and "NICHT `nftables.service`" in readme
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
                       "tagesgrenze": 300, "wrapper": "shim"}   # D6 (26.10.): Berechtigungspruefung je Werkzeugaufruf


def test_dockerignore_haelt_token_und_budget_aus_dem_build():
    zeilen = (HEADLESS / ".dockerignore").read_text(encoding="utf-8").split()
    assert "claude-token" in zeilen and "budget/" in zeilen


def test_server_wartet_auf_gesunde_shim():
    import yaml
    dienste = yaml.safe_load((HEADLESS / "compose.yml").read_text(encoding="utf-8"))["services"]
    assert dienste["rowboat-server"]["depends_on"] == {"rowboat-shim": {"condition": "service_healthy"}}


def test_firewall_einheit_ohne_nftables_conf():
    einheit = (HEADLESS / "rowboat-firewall.service").read_text(encoding="utf-8")
    assert "Before=network-pre.target docker.service" in einheit
    assert "ExecStart=/usr/sbin/nft -f /etc/nftables.d/rowboat-firewall.nft" in einheit
    assert "flush ruleset" not in einheit.replace('"flush ruleset"', "")
    bauen = (HEADLESS / "bauen.ps1").read_text(encoding="utf-8")
    assert "'rowboat-firewall.service'" in bauen


WISSEN = HEADLESS / "wissen"


def test_wissen_dienst_vertrag():
    svc = dienst("rowboat-wissen")
    env = umgebung(svc)
    assert svc["build"] == "./wissen"
    assert svc["network_mode"] == "host"
    assert "ports" not in svc
    assert "depends_on" not in svc                   # unabhaengig vom Rowboat-Server
    assert svc["restart"] == "unless-stopped"
    assert svc["mem_limit"] == "512m"
    # Ein verwaister `docker exec`-Prozess (2026-10-26: Endlos-Glob durch /sys) darf nur einen Kern belegen.
    assert svc["cpus"] == "1.0"
    assert svc["user"] == "1000:1000"
    assert svc["volumes"] == [
        {"type": "bind", "source": "/home/debian/rowboat-data/knowledge", "target": "/wissen",
         "read_only": True, "bind": {"create_host_path": False}},
        {"type": "bind", "source": "./wissen-index", "target": "/index",
         "bind": {"create_host_path": False}},
    ]
    assert all(v["bind"]["create_host_path"] is False for v in svc["volumes"])
    assert env["WISSEN_PORT"] == "3240"
    assert env["WISSEN_ORDNER"] == "/wissen"
    assert env["WISSEN_INDEX"] == "/index/wissen.sqlite"
    assert env["OLLAMA_URL"] == "http://100.84.127.84:11435"
    assert env["EMBED_MODELL"] == "qwen3-embedding:0.6b"
    assert env["EMBED_DIM"] == "1024" and env["EMBED_NUM_CTX"] == "2048"
    assert env["INDEX_INTERVALL_S"] == "600"
    probe = " ".join(svc["healthcheck"]["test"])
    assert "http://127.0.0.1:3240/gesund" in probe


def test_wissen_dockerfile():
    text = (WISSEN / "Dockerfile").read_text(encoding="utf-8")
    assert text.splitlines()[0] == "FROM python:3.11-slim"
    assert "pip install --no-cache-dir -r requirements.txt" in text
    assert "COPY wissen_suche/ /opt/wissen_suche/" in text
    assert 'CMD ["python", "-m", "wissen_suche.server"]' in text
    zeilen = (WISSEN / "requirements.txt").read_text(encoding="utf-8").splitlines()
    anforderungen = [z for z in zeilen if z.strip() and not z.startswith("#")]
    # pydantic gepinnt: mcp 1.9.4 importiert nicht mehr mit pydantic >= 2.12
    assert anforderungen == ["mcp==1.9.4", "numpy==2.1.3", "pydantic==2.11.7"]


def test_kein_modell_im_image():
    verboten = re.compile(r"torch|onnx|sentence-transformers|transformers|llama|ollama/ollama|vllm", re.IGNORECASE)
    for datei in ("requirements.txt", "Dockerfile"):
        assert not verboten.search((WISSEN / datei).read_text(encoding="utf-8")), datei


def test_bauen_kopiert_wissen_kontext():
    skript = (HEADLESS / "bauen.ps1").read_text(encoding="utf-8")
    assert "wissen_suche" in skript
    assert "requirements.txt" in skript
    assert "mkdir -p -m 0700 $basis/wissen-index" in skript
    assert "$basis/wissen/wissen_suche" in skript


def test_dockerignore_haelt_wissen_aus_dem_server_build():
    zeilen = (HEADLESS / ".dockerignore").read_text(encoding="utf-8").split()
    assert "wissen/" in zeilen and "wissen-index/" in zeilen


def test_readme_bedeutungssuche():
    readme = (HEADLESS / "README.md").read_text(encoding="utf-8")
    assert "## Bedeutungssuche" in readme
    assert "3240" in readme and "/gesund" in readme


# --- Teil D: Werkzeug-Container vibemind-werkzeuge ----------------------------------------------

WERKZEUGE = HEADLESS / "werkzeuge"


def test_werkzeuge_dienst_vertrag():
    svc = dienst("rowboat-werkzeuge")
    env = umgebung(svc)
    assert svc["build"] == "./werkzeuge"
    assert svc["image"] == "rowboat-werkzeuge:local"
    assert svc["network_mode"] == "host"
    assert "ports" not in svc
    assert svc["restart"] == "unless-stopped"
    assert svc["mem_limit"] == "256m"
    assert "depends_on" not in svc
    assert "volumes" not in svc                       # sieht nichts von /data (rowboat-data)
    # Fehlt werkzeuge.env, startet der Stack trotzdem (die Werkzeuge melden "nicht eingerichtet")
    assert svc["env_file"] == [{"path": "./werkzeuge.env", "required": False}]
    assert env["WERKZEUGE_PORT"] == "8150"
    assert env["PC_SUCHE_URL"] == "http://100.84.127.84:8149"
    assert env["GITHUB_BESITZER"] == "Flissel=GITHUB_LESEN_FLISSEL,Vibemind-LAB=GITHUB_LESEN_VIBEMIND_LAB"
    assert env["GITEA_URL"] == "http://127.0.0.1:3002"   # Loopback: Host-Netz, Gitea-Port der VM
    probe = " ".join(svc["healthcheck"]["test"])
    assert "http://127.0.0.1:8150/health" in probe and "python3" in probe
    # Der Rowboat-Server haengt nicht vom Werkzeug-Container ab
    assert "rowboat-werkzeuge" not in dienst("rowboat-server")["depends_on"]


def test_mcp_eintrag_ohne_geheimnis():
    e = json.loads((HEADLESS / "mcp-eintrag.json").read_text(encoding="utf-8"))
    # OHNE Schraegstrich: mcp 1.27 bedient /mcp und leitet /mcp/ per 307 um (rowboat-wissen mit
    # mcp 1.9.4 ist umgekehrt und braucht /mcp/, Teil C 231107b6) - am 26.10. mit TestClient geprueft.
    # rowboat-auftraege: der Bearer-Kopf ist nur ein Platzhalter; den Wert traegt der Betreiber auf der VM aus der
    # Datei auftraege-zugang ein (README "Auftraege").
    assert e == {"vibemind-werkzeuge": {"type": "http", "url": "http://127.0.0.1:8150/mcp"},
                 "rowboat-auftraege": {"type": "http", "url": "http://127.0.0.1:8156/mcp",
                                       "headers": {"Authorization": "Bearer <AUS-DATEI auftraege-zugang>"}}}


def test_dockerignore_haelt_werkzeuge_env_fern():
    zeilen = (HEADLESS / ".dockerignore").read_text(encoding="utf-8").split()
    assert "werkzeuge.env*" in zeilen and ".werkzeuge.env.neu.*" in zeilen and "werkzeuge/" in zeilen


def test_readme_werkzeuge_ohne_anmeldung_nur_lokal():
    readme = (HEADLESS / "README.md").read_text(encoding="utf-8")
    assert "## Werkzeuge `rowboat-werkzeuge`" in readme
    assert "127.0.0.1:8150/mcp" in readme and "ohne Anmeldung" in readme


def test_bauen_mkdir_mit_leerzeichen():
    skript = (HEADLESS / "bauen.ps1").read_text(encoding="utf-8")
    assert not re.search(r"\S&&|&&\S", skript)


def test_werkzeuge_dockerfile_pinnt_mcp():
    text = (WERKZEUGE / "Dockerfile").read_text(encoding="utf-8")
    assert text.splitlines()[0] == "FROM python:3.12-slim"
    assert "pip install --no-cache-dir mcp==1.27.0" in text
    for datei in ("server.py", "github_lesen.py", "gitea_lesen.py", "pc_suche_relay.py"):
        assert datei in text
    assert "USER 1000:1000" in text
    assert 'CMD ["python3", "/opt/werkzeuge/server.py"]' in text
    assert not re.search(r"(?i)werkzeuge\.env|token\s*=", text)


def test_bauen_kopiert_werkzeuge_kontext():
    skript = (HEADLESS / "bauen.ps1").read_text(encoding="utf-8")
    assert "[switch]$NurWerkzeuge" in skript
    for datei in ("github_lesen.py", "gitea_lesen.py", "pc_suche_relay.py", "server.py", "werkzeuge-zugang-eintragen.sh",
                  "mcp-eintrag.json"):
        assert datei in skript
    assert "$basis/werkzeuge" in skript
    # werkzeuge.env wird hier nie angelegt oder kopiert (kommt nur per rowboat-zugang ueber ssh-stdin)
    code = re.sub(r"<#.*?#>", "", skript, flags=re.DOTALL)
    code = "\n".join(z for z in code.splitlines() if not z.lstrip().startswith("#"))
    assert "werkzeuge.env" not in code


def test_bauen_nur_werkzeuge_ueberspringt_den_server_bau():
    skript = (HEADLESS / "bauen.ps1").read_text(encoding="utf-8")
    zweig = skript.index("if ($NurWerkzeuge)")
    assert zweig < skript.index("Invoke-Schritt $Quelle @('npm', 'run', 'deps')")


# --- Teil B der Konzept-Flaeche: Auftraege-Container rowboat-auftraege -----------------------------------

AUFTRAEGE = HEADLESS / "auftraege"
WISSEN_NUR_LESEND = {"type": "bind", "source": "/home/debian/rowboat-data/knowledge", "target": "/wissen",
                     "read_only": True, "bind": {"create_host_path": False}}


def test_auftraege_dienst_vertrag():
    svc = dienst("rowboat-auftraege")
    env = umgebung(svc)
    assert svc["build"] == "./auftraege"
    assert svc["image"] == "rowboat-auftraege:local"
    # Host-Netz ohne ports: der Dienst bindet nur 127.0.0.1 (server.HOST), nur Prozesse auf der VM erreichen ihn.
    assert svc["network_mode"] == "host"
    assert "ports" not in svc
    assert svc["restart"] == "unless-stopped"
    assert svc["mem_limit"] == "256m"
    assert svc["cpus"] == "0.5"
    assert svc["user"] == "1000:1000"
    assert "depends_on" not in svc
    assert env["AUFTRAEGE_PORT"] == "8156"
    assert env["WISSEN_ORDNER"] == "/wissen"
    assert env["SALES_UI_URL"] == "http://127.0.0.1:8791"
    assert env["AUFTRAEGE_ZUGANG_DATEI"] == "/schluessel/auftraege-zugang"
    assert env["SALES_SCHLUESSEL_DATEI"] == "/schluessel/sales-schluessel"
    assert env["AUFTRAEGE_LOG"] == "/log/auftraege.log"
    # Laden-Anzeige ist kein Geheimnis, aber je Ausrollen verschieden: aus auftraege.env; fehlt sie, startet der
    # Dienst nicht (server.StartFehler), die anderen Dienste bleiben davon unberuehrt.
    assert svc["env_file"] == [{"path": "./auftraege.env", "required": False}]
    probe = " ".join(svc["healthcheck"]["test"])
    assert "http://127.0.0.1:8156/health" in probe and "python3" in probe
    assert "rowboat-auftraege" not in dienst("rowboat-server")["depends_on"]


def test_auftraege_nur_im_eigenen_profil():
    # I1: ein "docker compose up -d" fuer den ganzen Stapel darf nicht an fehlenden Schluesseldateien scheitern.
    compose = yaml.safe_load((HEADLESS / "compose.yml").read_text(encoding="utf-8"))
    for name, svc in compose["services"].items():
        if name == "rowboat-auftraege":
            assert svc["profiles"] == ["auftraege"]
        else:
            assert "profiles" not in svc, name
            assert "rowboat-auftraege" not in (svc.get("depends_on") or []), name


def test_auftraege_container_gehaertet():
    svc = dienst("rowboat-auftraege")
    assert svc["read_only"] is True
    assert svc["tmpfs"] == ["/tmp"]
    assert svc["cap_drop"] == ["ALL"]
    assert "cap_add" not in svc and "privileged" not in svc
    assert svc["security_opt"] == ["no-new-privileges:true"]
    assert svc["pids_limit"] == 64
    assert svc["user"] == "1000:1000"
    # Schreibbar ist nur das Protokoll (eigener Mount); alles andere nur lesend.
    schreibbar = [v["target"] for v in svc["volumes"] if not v.get("read_only")]
    assert schreibbar == ["/log"]


def test_andere_dienste_ohne_auftraege_haertung_unveraendert():
    # Die Haertung gilt nur fuer rowboat-auftraege; die anderen Dienste bleiben wie sie waren.
    for name in ("rowboat-server", "rowboat-shim", "rowboat-werkzeuge", "rowboat-wissen"):
        svc = dienst(name)
        for schluessel in ("profiles", "read_only", "cap_drop", "security_opt", "pids_limit", "tmpfs"):
            assert schluessel not in svc, (name, schluessel)


def test_auftraege_wissen_nur_lesend_und_schluessel_pflicht():
    volumes = dienst("rowboat-auftraege")["volumes"]
    assert volumes[0] == WISSEN_NUR_LESEND
    # Schluesseldateien als nur lesende Datei-Mounts ohne create_host_path: fehlt eine, legt compose den Container
    # nicht an (und erfindet keinen root-Ordner an ihrer Stelle); ist sie leer, verweigert server.py den Start.
    assert volumes[1:3] == [
        {"type": "bind", "source": "./auftraege-zugang", "target": "/schluessel/auftraege-zugang", "read_only": True,
         "bind": {"create_host_path": False}},
        {"type": "bind", "source": "./sales-schluessel", "target": "/schluessel/sales-schluessel", "read_only": True,
         "bind": {"create_host_path": False}},
    ]
    assert volumes[3] == {"type": "bind", "source": "./auftraege-log", "target": "/log",
                          "bind": {"create_host_path": False}}
    assert len(volumes) == 4
    assert all(v["read_only"] for v in volumes if v["target"] != "/log")


def test_auftraege_ports_stimmen_mit_dem_dienst():
    import importlib.util
    import sys
    sys.path.insert(0, str(AUFTRAEGE))
    spec = importlib.util.spec_from_file_location("auftraege_server_deploy", AUFTRAEGE / "server.py")
    modul = importlib.util.module_from_spec(spec)
    sys.modules["auftraege_server_deploy"] = modul          # dataclass loest Annotationen ueber sys.modules auf
    spec.loader.exec_module(modul)
    assert modul.HOST == "127.0.0.1" and modul.PORT_VORGABE == 8156
    assert modul.SALES_URL_VORGABE == umgebung(dienst("rowboat-auftraege"))["SALES_UI_URL"]


def test_auftraege_dockerfile():
    text = (AUFTRAEGE / "Dockerfile").read_text(encoding="utf-8")
    assert text.splitlines()[0] == "FROM python:3.12-slim"
    assert "pip install --no-cache-dir mcp==1.27.0" in text
    assert "COPY server.py konzept_lesen.py /opt/auftraege/" in text
    assert "USER 1000:1000" in text
    assert 'CMD ["python3", "/opt/auftraege/server.py"]' in text
    befehle = "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))
    assert not re.search(r"(?i)auftraege-zugang|sales-schluessel|auftraege\.env|token\s*=", befehle)


def test_dockerignore_haelt_auftraege_schluessel_fern():
    zeilen = (HEADLESS / ".dockerignore").read_text(encoding="utf-8").split()
    for muster in ("auftraege/", "auftraege.env", "auftraege-zugang", "sales-schluessel", "auftraege-log/"):
        assert muster in zeilen, muster


def test_bauen_kopiert_auftraege_kontext():
    skript = (HEADLESS / "bauen.ps1").read_text(encoding="utf-8")
    assert "[switch]$NurAuftraege" in skript
    assert "$auftraegeDateien = @('Dockerfile', 'server.py', 'konzept_lesen.py')" in skript
    assert "$basis/auftraege" in skript and "mkdir -p -m 0700 $basis/auftraege-log" in skript
    zweig = skript.index("if ($NurAuftraege)")
    assert zweig < skript.index("Invoke-Schritt $Quelle @('npm', 'run', 'deps')")
    # Schluesseldateien und Einstellungen legt das Skript nie an und kopiert sie nie (nur der Betreiber auf der VM).
    code = re.sub(r"<#.*?#>", "", skript, flags=re.DOTALL)
    code = "\n".join(z for z in code.splitlines() if not z.lstrip().startswith("#"))
    for name in ("auftraege-zugang", "sales-schluessel", "auftraege.env"):
        assert name not in code, name
    # Gedruckte Startbefehle fuer den Auftraege-Dienst nennen das Profil (sonst startet compose ihn nicht).
    for zeile in skript.splitlines():
        if "up -d" in zeile and "rowboat-auftraege" in zeile:
            assert "--profile auftraege" in zeile, zeile


def test_readme_auftraege():
    readme = (HEADLESS / "README.md").read_text(encoding="utf-8")
    teil = readme.split("## Aufträge `rowboat-auftraege` (127.0.0.1:8156)", 1)[1].split("\n## ", 1)[0]
    for wort in ("127.0.0.1:8156/mcp", "video_auftrag_aus_konzept", "auftraege-zugang", "sales-schluessel",
                 "AUFTRAEGE_LADEN_ANZEIGE", "SALES_UI_ANZEIGE_URL", "umask 077", "stty -echo", "mcp.json", "Rückweg",
                 "/health", "401", "readOnlyMcpTools", "auftraege.log", "Neustart", "--profile auftraege",
                 "vorbereitet", "500 Zeichen", "O_EXCL"):
        assert wort in teil, wort
    assert "read -rs" not in teil and "`stand`" not in teil and "pfad, stand" not in teil
    for zeile in teil.splitlines():
        if "docker compose" in zeile and "up" in zeile and "rowboat-auftraege" in zeile:
            assert "--profile auftraege" in zeile, zeile
    # Kein Befehl gibt einen Schluessel aus: kein cat der Schluesseldateien, echo/printf nur in eine Datei.
    for zeile in teil.splitlines():
        if "auftraege-zugang" in zeile or "sales-schluessel" in zeile:
            assert not re.search(r"\bcat\b", zeile), zeile
            assert not re.search(r"\b(echo|printf)\b", zeile) or ">" in zeile, zeile


def test_auftraege_readme_nennt_reihenfolge_und_einheitliche_dateinamen():
    readme = (HEADLESS / "README.md").read_text(encoding="utf-8")
    abschnitt = readme[readme.index("## Aufträge `rowboat-auftraege`"):]
    for muster in ("Reihenfolge beim Ausrollen", "SQL in Sales zuerst", "--profile auftraege", "UI_BASIS_URL",
                   "SALES_UI_ANZEIGE_URL", "ss -ltn | grep ':8156 '", "03_RUNBOOK.md", "Rückweg"):
        assert muster in abschnitt, muster
    assert abschnitt.index("SQL in Sales zuerst") < abschnitt.index("Zuletzt der Eintrag in `mcp.json`")
    # Dieselben zwei Dateinamen wie compose.yml und mcp-eintrag.json
    compose = (HEADLESS / "compose.yml").read_text(encoding="utf-8")
    eintrag = (HEADLESS / "mcp-eintrag.json").read_text(encoding="utf-8")
    for name in ("auftraege-zugang", "sales-schluessel"):
        assert name in compose and name in abschnitt
    assert "auftraege-zugang" in eintrag
    assert "auftraege-sales-schluessel" not in compose + eintrag
    assert "auftraege-sales-schluessel" not in abschnitt.replace("(`auftraege-sales-schluessel`)", "")
