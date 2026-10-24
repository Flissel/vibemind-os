"""Vertrag fuer spaces/rowboat/deploy/headless (Rowboat-Server ohne Oberflaeche auf dem Mini-PC)."""
import re
from pathlib import Path

import yaml

HEADLESS = Path(__file__).resolve().parents[1] / "deploy" / "headless"
GEHEIM = re.compile(r"(key|token|secret|passw|geheim)", re.IGNORECASE)


def dienst() -> dict:
    compose = yaml.safe_load((HEADLESS / "compose.yml").read_text(encoding="utf-8"))
    assert list(compose["services"]) == ["rowboat-server"]
    return compose["services"]["rowboat-server"]


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
    assert "/home/debian/rowboat-data:/data" in svc["volumes"]
    assert svc["mem_limit"] == "3g"
    probe = " ".join(svc["healthcheck"]["test"]) if isinstance(svc["healthcheck"]["test"], list) \
        else svc["healthcheck"]["test"]
    assert "http://127.0.0.1:3220/health" in probe
    assert "curl" not in probe and "wget" not in probe   # node:22-bookworm-Laufzeit ohne curl verlassen wir nicht
    assert "node" in probe


def test_compose_ohne_geheimnis():
    svc = dienst()
    for name, wert in umgebung(svc).items():
        assert not GEHEIM.search(name), f"Geheimnis-artiger Name in environment: {name}"
        assert len(wert) < 40, f"verdaechtig langer Wert in environment: {name}"
    assert "env_file" not in svc


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
