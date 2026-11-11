"""Space `event-management`: Saat, Vertrag und Fuell-Skript.

Das Repo ist oeffentlich. Darum pruefen die ersten Tests vor allem, was NICHT
in der Saat stehen darf (Schluessel, Nummern, Gruppen-ID, Ort/Datum des
Festivals), und dass alles Private nur als Platzhalter vorkommt, den
deploy/fuellen.py aus einer lokalen Wertedatei fuellt.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SPACE = Path(__file__).resolve().parents[1]
SPACES = SPACE.parent
KONFIG = SPACE / "config"
OPENCLAW = KONFIG / "openclaw.json"
WORKSPACE = KONFIG / "workspace"
SOUL = WORKSPACE / "SOUL.md"
SKILL_NAME = "event-task-image-uploads"
SKILL = WORKSPACE / "skills" / SKILL_NAME / "SKILL.md"
VERTRAG = SPACES / "_contract" / "event-management.contract.yaml"
BEISPIEL = SPACE / "werte.beispiel.json"
COMPOSE = SPACE / "docker-compose.yml"
FUELLEN = SPACE / "deploy" / "fuellen.py"

PLATZHALTER = re.compile(r"\{\{([A-Z_]+)\}\}")


def _fuellen_modul():
    spec = importlib.util.spec_from_file_location("event_fuellen", FUELLEN)
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _saat_dateien() -> list[Path]:
    return sorted(p for p in KONFIG.rglob("*") if p.is_file())


def _beispielwerte() -> dict[str, str]:
    return json.loads(BEISPIEL.read_text(encoding="utf-8"))


# --- Saat: nichts Geheimes, nichts Privates --------------------------------

VERBOTEN = [
    (re.compile(r"bot\d+:"), "Telegram-Bot-Token"),
    (re.compile(r"\bsk-[A-Za-z0-9]"), "API-Schluessel"),
    (re.compile(r"Bearer\s+[A-Za-z0-9]"), "Bearer-Token"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}"), "JWT"),
    (re.compile(r"\+\d{8,}|\b0\d{9,}\b"), "Telefonnummer"),
    (re.compile(r"-100\d{6,}"), "Telegram-Gruppen-ID"),
    (re.compile(r"vercel\.app", re.I), "Adresse der Festival-App"),
    (re.compile(r"clawd-festival"), "alter Workspace-Pfad"),
]

# Ort und Datum des Festivals duerfen auch HIER nicht im Klartext stehen --
# darum nur ihre SHA-256 (Ort: ein Wort, klein; Datum: "<tag> <monat>").
VERBOTENE_PRUEFSUMMEN = {
    "228c6117b482cd68a3e0885c8fc51efbba0a7a726784562ed725ce70755364c1": "Ort des Festivals",
    "36fde7d5acedba0f1178aabc621680f48aace5c484c04056cba36ea079f15e45": "Datum des Festivals",
}


def _pruefsummen(text: str) -> set[str]:
    klein = text.lower()
    kandidaten = set(re.findall(r"[a-zäöüß]{4,}", klein))
    kandidaten |= {f"{tag} {monat}" for tag, monat in re.findall(r"(\d{1,2})\.\s*([a-zä]+)", klein)}
    return {hashlib.sha256(k.encode("utf-8")).hexdigest() for k in kandidaten}


@pytest.mark.parametrize("datei", _saat_dateien(), ids=lambda p: p.relative_to(KONFIG).as_posix())
def test_saat_ohne_geheimnisse_und_private_angaben(datei: Path) -> None:
    text = datei.read_text(encoding="utf-8")
    funde = [was for muster, was in VERBOTEN if muster.search(text)]
    funde += [was for summe, was in VERBOTENE_PRUEFSUMMEN.items() if summe in _pruefsummen(text)]
    assert funde == [], f"{datei.name}: {funde}"


def test_pruefsummen_erkennen_ort_und_datum() -> None:
    # Gegenprobe mit erfundenem Text: die Summenpruefung selbst funktioniert.
    summe = hashlib.sha256(b"musterdorf").hexdigest()
    assert summe in _pruefsummen("Fest in Musterdorf am 1. August")
    assert hashlib.sha256(b"1 august").hexdigest() in _pruefsummen("am 1. August")


def test_openclaw_saat_hat_keine_schluesselfelder() -> None:
    konfig = json.loads(OPENCLAW.read_text(encoding="utf-8"))
    assert konfig["gateway"]["auth"] == {"mode": "token"}
    assert "env" not in konfig
    assert "botToken" not in konfig["channels"]["telegram"]
    assert "headers" not in konfig["mcp"]["servers"]["event-app"]
    text = OPENCLAW.read_text(encoding="utf-8")
    for feld in ('"token":', '"botToken"', '"Authorization"', "OPENAI_API_KEY", "UPLOAD_TOKEN"):
        assert feld not in text, feld


def test_gateway_nur_loopback_und_healthcheck_auf_dem_gebundenen_port() -> None:
    gateway = json.loads(OPENCLAW.read_text(encoding="utf-8"))["gateway"]
    assert gateway["bind"] == "loopback"
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    probe = " ".join(compose["services"]["event-management-claw"]["healthcheck"]["test"])
    assert f"127.0.0.1:{gateway['port']}/healthz" in probe
    assert "18789" not in probe


def test_compose_ohne_docker_socket_und_mit_gefuellter_saat() -> None:
    dienst = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["event-management-claw"]
    volumes = dienst["volumes"]
    assert not any("docker.sock" in v for v in volumes)
    assert "./build/workspace:/seed/workspace:ro" in volumes


def test_telegram_nur_eingetragene_gruppe_und_werkzeugsperren() -> None:
    konfig = json.loads(OPENCLAW.read_text(encoding="utf-8"))
    telegram = konfig["channels"]["telegram"]
    assert telegram["groupPolicy"] == "allowlist"
    assert list(telegram["groups"]) == ["{{TELEGRAM_GRUPPE}}"]
    assert set(konfig["tools"]["deny"]) >= {"group:web", "group:ui", "group:automation"}
    # Der Foto-Skill braucht die Shell (curl/node/find) -- nicht sperren.
    assert "group:runtime" not in konfig["tools"]["deny"]


# --- Platzhalter -------------------------------------------------------------

def test_platzhalter_und_beispielwerte_sind_deckungsgleich() -> None:
    benutzt = {name for p in _saat_dateien() for name in PLATZHALTER.findall(p.read_text(encoding="utf-8"))}
    assert benutzt == set(_beispielwerte())


# --- Workspace und Skill -----------------------------------------------------

def test_workspace_und_skill_vorhanden() -> None:
    assert (WORKSPACE / "AGENTS.md").is_file()
    assert SOUL.is_file()
    kopf = SKILL.read_text(encoding="utf-8").split("---")[1]
    assert yaml.safe_load(kopf)["name"] == SKILL_NAME
    assert f"`{SKILL_NAME}`" in SOUL.read_text(encoding="utf-8")


def test_skill_liest_den_mcp_server_der_saat() -> None:
    server = json.loads(OPENCLAW.read_text(encoding="utf-8"))["mcp"]["servers"]
    namen = set(re.findall(r"servers\['([^']+)'\]", SKILL.read_text(encoding="utf-8")))
    assert namen == {"event-app"}
    assert namen <= set(server)
    assert server["event-app"]["url"].endswith("/api/mcp")


# --- Vertrag -----------------------------------------------------------------

def _werkzeuge_aus_soul() -> tuple[set[str], set[str]]:
    text = SOUL.read_text(encoding="utf-8")
    lesen = text.split("**Lesen**")[1].split("**Schreiben**")[0]
    schreiben = text.split("**Schreiben**")[1].split("###")[0]
    zeile = re.compile(r"^\| `([a-z_]+)` \|", re.M)
    return set(zeile.findall(lesen)), set(zeile.findall(schreiben))


def test_vertrag_bildet_jedes_werkzeug_mit_richtigem_writes_ab() -> None:
    vertrag = yaml.safe_load(VERTRAG.read_text(encoding="utf-8"))
    assert vertrag["id"] == "event-management"
    assert vertrag["prefixes"] == ["event."]
    caps = {c["name"]: c["writes"] for c in vertrag["capabilities"]}
    lesen, schreiben = _werkzeuge_aus_soul()
    assert len(lesen) == 8 and len(schreiben) == 8
    erwartet = {f"event_{w}": False for w in lesen}
    erwartet.update({f"event_{w}": True for w in schreiben})
    erwartet["event_task_image_upload"] = True
    assert caps == erwartet


def test_praefix_gehoert_keinem_anderen_vertrag() -> None:
    fremde = set()
    for datei in (SPACES / "_contract").glob("*.contract.yaml"):
        if datei != VERTRAG:
            fremde.update(yaml.safe_load(datei.read_text(encoding="utf-8")).get("prefixes") or [])
    assert "event." not in fremde


# --- deploy/fuellen.py -------------------------------------------------------

def test_fuellen_mit_beispielwerten(tmp_path: Path) -> None:
    werte = _beispielwerte()
    geschrieben = _fuellen_modul().fuellen(werte, KONFIG, tmp_path)
    assert "openclaw.json" in geschrieben
    assert "workspace/SOUL.md" in geschrieben
    for datei in tmp_path.rglob("*"):
        if datei.is_file():
            assert "{{" not in datei.read_text(encoding="utf-8"), datei
    konfig = json.loads((tmp_path / "openclaw.json").read_text(encoding="utf-8"))
    assert list(konfig["channels"]["telegram"]["groups"]) == [werte["TELEGRAM_GRUPPE"]]
    assert konfig["mcp"]["servers"]["event-app"]["url"] == werte["EVENT_APP_URL"] + "/api/mcp"
    assert werte["EVENT_ORT"] in (tmp_path / "workspace" / "SOUL.md").read_text(encoding="utf-8")


def test_fuellen_maskiert_anfuehrungszeichen_in_json(tmp_path: Path) -> None:
    werte = _beispielwerte() | {"EVENT_APP_URL": 'https://x.example.com/"a\\b'}
    _fuellen_modul().fuellen(werte, KONFIG, tmp_path)
    konfig = json.loads((tmp_path / "openclaw.json").read_text(encoding="utf-8"))
    assert konfig["mcp"]["servers"]["event-app"]["url"] == 'https://x.example.com/"a\\b/api/mcp'


def test_fuellen_fehlender_und_ueberzaehliger_wert(tmp_path: Path) -> None:
    modul = _fuellen_modul()
    werte = _beispielwerte()
    del werte["EVENT_ORT"]
    with pytest.raises(modul.FuellFehler, match="EVENT_ORT"):
        modul.fuellen(werte, KONFIG, tmp_path)
    with pytest.raises(modul.FuellFehler, match="TIPPFEHLER"):
        modul.fuellen(_beispielwerte() | {"TIPPFEHLER": "x"}, KONFIG, tmp_path / "b")


@pytest.mark.parametrize("schlecht", ["", "a\nb", "{{EVENT_ORT}}", 5])
def test_fuellen_lehnt_unbrauchbare_werte_ab(tmp_path: Path, schlecht: object) -> None:
    modul = _fuellen_modul()
    with pytest.raises(modul.FuellFehler, match="EVENT_ORT"):
        modul.fuellen(_beispielwerte() | {"EVENT_ORT": schlecht}, KONFIG, tmp_path)


def test_fuellen_schreibt_nie_in_die_saat() -> None:
    modul = _fuellen_modul()
    with pytest.raises(modul.FuellFehler):
        modul.fuellen(_beispielwerte(), KONFIG, KONFIG / "build")
    with pytest.raises(modul.FuellFehler):
        modul.fuellen(_beispielwerte(), KONFIG, SPACE)


def test_cli_gibt_keine_werte_aus(tmp_path: Path) -> None:
    werte = _beispielwerte() | {"EVENT_ORT": "Geheimhausen"}
    datei = tmp_path / "werte.local.json"
    datei.write_text(json.dumps(werte), encoding="utf-8")
    ok = subprocess.run([sys.executable, str(FUELLEN), str(datei), str(tmp_path / "out")],
                        capture_output=True, text=True, timeout=60)
    assert ok.returncode == 0, ok.stderr
    del werte["SHIM_URL"]
    datei.write_text(json.dumps(werte), encoding="utf-8")
    fehler = subprocess.run([sys.executable, str(FUELLEN), str(datei), str(tmp_path / "out2")],
                            capture_output=True, text=True, timeout=60)
    assert fehler.returncode == 2
    assert "SHIM_URL" in fehler.stderr
    for lauf in (ok, fehler):
        ausgabe = lauf.stdout + lauf.stderr
        for wert in werte.values():
            assert wert not in ausgabe
