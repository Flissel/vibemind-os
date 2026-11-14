"""Space `event-management`: Saat, Vertrag und Fuell-Skript.

Das Repo ist oeffentlich. Darum pruefen die ersten Tests vor allem, was NICHT
in der Saat stehen darf (Schluessel, Nummern, Gruppen-ID, Ort/Datum des
Festivals), und dass alles Private nur als Platzhalter vorkommt, den
deploy/fuellen.py aus einer lokalen Wertedatei fuellt.
"""
from __future__ import annotations

import hashlib
import importlib.util
import ipaddress
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
# Empfangs-Agent `main` (Etappe 3, Task 8): nur die melden-Werkzeuge, eigene kurze SOUL.
EMPFANG = KONFIG / "workspace"
EMPFANG_SOUL = EMPFANG / "SOUL.md"
MELDEN = ["melden__gruppe_melden", "melden__chat_melden"]
# Bisherige Gruppen-SOUL des Festivals (Legacy). Bleibt fuer `ev-legacy` aufbewahrt, das
# Etappe 4 anlegt (Umbenennung main -> ev-legacy); bis dahin wird sie nicht ausgesaet.
WORKSPACE = KONFIG / "workspace-legacy"
SOUL = WORKSPACE / "SOUL.md"
# Eigener Arbeitsbereich der Brain-Agenten (brain-lesen, brain-auftrag): nur hier
# gilt `actor` = „VibeMind“; die Gruppen-SOUL kennt diese Ausnahme nicht.
WORKSPACE_BRAIN = KONFIG / "workspace-brain"
BRAIN_SOUL = WORKSPACE_BRAIN / "SOUL.md"
LAUFZEIT_WORKSPACE_BRAIN = "/home/node/.openclaw/workspace-brain"
VIBEMIND_ACTOR = "Setze `actor` auf „VibeMind“"
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
    (re.compile(r"evk_[A-Za-z0-9_-]"), "Bot-Schluessel"),
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
    assert "./build/workspace-brain:/seed/workspace-brain:ro" in volumes
    assert "./build/workspace-legacy:/seed/workspace-legacy:ro" in volumes


def test_telegram_gruppen_und_werkzeugsperren() -> None:
    konfig = json.loads(OPENCLAW.read_text(encoding="utf-8"))
    telegram = konfig["channels"]["telegram"]
    assert telegram["groupPolicy"] == "allowlist"
    # Unbekannte Gruppen erreichen den Empfang nur per Erwaehnung (Kosten durch Fremde);
    # die Legacy-Gruppe bleibt ohne Erwaehnung. Verbundene Gruppen traegt der Arbeiter ein.
    assert telegram["groups"] == {
        "{{TELEGRAM_GRUPPE}}": {"requireMention": False},
        "*": {"requireMention": True},
    }
    assert set(konfig["tools"]["deny"]) >= {"group:web", "group:ui", "group:automation"}
    # Der Foto-Skill braucht die Shell (curl/node/find) -- nicht sperren.
    assert "group:runtime" not in konfig["tools"]["deny"]


def test_direktchats_je_absender_eigene_sitzung() -> None:
    """Abschluss-Review F3: `main` (Empfang) bekommt jeden unbekannten Direktchat. Mit der
    Vorgabe teilten sich alle DMs eine Sitzung, und eine Person saehe Codes/Antworten einer
    anderen im Verlauf. Darum oben in der Config: eine Sitzung je Kanal und Absender."""
    konfig = json.loads(OPENCLAW.read_text(encoding="utf-8"))
    assert konfig["session"] == {"dmScope": "per-channel-peer"}


# --- Platzhalter -------------------------------------------------------------

def test_platzhalter_und_beispielwerte_sind_deckungsgleich() -> None:
    benutzt = {name for p in _saat_dateien() for name in PLATZHALTER.findall(p.read_text(encoding="utf-8"))}
    assert benutzt == set(_beispielwerte())


# --- Workspace und Skill -----------------------------------------------------

def test_empfang_hat_genau_die_zwei_melden_werkzeuge() -> None:
    konfig = json.loads(OPENCLAW.read_text(encoding="utf-8"))
    main = _agenten()["main"]
    assert main == {"id": "main", "tools": {"allow": MELDEN}}
    server = konfig["mcp"]["servers"]["melden"]
    assert server == {"url": "{{EVENT_MCP_URL}}/mcp", "transport": "streamable-http",
                      "connectionTimeoutMs": 30000, "requestTimeoutMs": 60000}
    # Den Header (melden-Schluessel) setzt erst anbinden.sh ueber stdin.
    assert "headers" not in server


def test_empfang_soul_kurz_und_nur_melden() -> None:
    text = EMPFANG_SOUL.read_text(encoding="utf-8")
    assert "`gruppe_melden`" in text and "`chat_melden`" in text
    assert "Erwähnung" in text and "Leitung" in text
    assert "keine Anweisungen" in text
    # Keine Werkzeuge der alten App, kein Foto-Skill, keine Event-Angaben.
    assert "event-app" not in text and SKILL_NAME not in text and "{{" not in text
    assert len(text) < 3000
    assert (EMPFANG / "AGENTS.md").is_file()
    assert not (EMPFANG / "skills").exists()


def test_keine_klartext_schluessel_in_config() -> None:
    for datei in _saat_dateien():
        assert "evk_" not in datei.read_text(encoding="utf-8"), datei.name


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


# --- Gateway-Agenten fuer den Brain -------------------------------------------

# Gemessene tools/list der Event-App vom 12.11.2026 (20 Werkzeuge). Die Brain-
# Agenten bekommen ausgeschriebene Erlaubnislisten: eine Sperrliste (tools.deny)
# haelt file_write, message, web_fetch u. a. nicht zurueck.
APP_PRAEFIX = "event-app__"
APP_LESEND = {
    "list_tasks", "list_categories", "get_my_tasks", "list_members", "get_budget",
    "get_guest_stats", "list_recent_changes", "get_festival_info", "list_timetable",
}
APP_TIMETABLE_SCHREIBEND = {"create_timetable_slot", "update_timetable_slot", "delete_timetable_slot"}
APP_ALLE = APP_LESEND | {
    "create_task", "update_task", "delete_task", "import_orga_plan", "create_team",
    "create_member", "update_member", "send_message_to_member",
} | APP_TIMETABLE_SCHREIBEND


def _agenten() -> dict[str, dict]:
    liste = json.loads(OPENCLAW.read_text(encoding="utf-8"))["agents"]["list"]
    return {a["id"]: a for a in liste}


def _erlaubt(agent: str) -> list[str]:
    return _agenten()[agent]["tools"]["allow"]


def test_brain_agenten_eingetragen() -> None:
    konfig = json.loads(OPENCLAW.read_text(encoding="utf-8"))
    liste = konfig["agents"]["list"]
    assert [a["id"] for a in liste] == ["main", "ev-legacy", "brain-lesen", "brain-auftrag"]
    assert konfig["gateway"]["http"]["endpoints"]["chatCompletions"]["enabled"] is True


# Etappe 4, Task 4: der Brain arbeitet am Katalog ueber eigene Betreiber-Schluessel (Name
# `VibeMind`): `bn-lesen` (Art betreiber_lesen) und `bn-auftrag` (Art betreiber). Die
# App-Werkzeuge bleiben nur fuer das Legacy-Festival.
BRAIN_SERVER = {"brain-lesen": "bn-lesen", "brain-auftrag": "bn-auftrag"}


# Abschluss-Review F-2: brain-auftrag bekommt KEIN `bn-auftrag__*`, sondern eine feste
# Liste: der Betreiber-Katalog ohne die Werkzeuge, die sofort nach aussen wirken oder
# etwas anlegen, das nur Felix anlegt, und ohne jedes Loeschen. Ein neuer Baustein macht
# den Test rot, bis die Liste nachgezogen ist.
BRAIN_AUFTRAG_OHNE = frozenset({"nachricht_an_gruppe", "einladen", "firma_anlegen",
                                "event_anlegen"})


def _brain_auftrag_katalog() -> set[str]:
    from spaces.event_management.system import werkzeuge

    return {n for n in werkzeuge.namen("betreiber")
            if n not in BRAIN_AUFTRAG_OHNE and not n.endswith("_loeschen")}


@pytest.mark.parametrize("agent", ["brain-lesen", "brain-auftrag"])
def test_brain_agenten_nur_app_werkzeuge_und_eigener_katalog(agent: str) -> None:
    eintrag = _agenten()[agent]
    assert "deny" not in eintrag["tools"]
    assert set(eintrag["tools"]) == {"allow"}
    for name in eintrag["tools"]["allow"]:
        if name == "bn-lesen__*" and agent == "brain-lesen":
            continue  # Katalog nur lesend (betreiber_lesen, test_betreiber_lesen_nur_lesend)
        if name.startswith("bn-auftrag__") and agent == "brain-auftrag":
            assert "*" not in name, name  # feste Liste, kein Muster (F-2)
            continue
        assert name.startswith(APP_PRAEFIX), name
        assert "*" not in name, name


def test_brain_auftrag_feste_liste_aus_dem_katalog() -> None:
    """F-2: genau der Betreiber-Katalog minus Ausschluesse, ohne Muster, ohne Doppel."""
    katalog = [n.removeprefix("bn-auftrag__") for n in _erlaubt("brain-auftrag")
               if n.startswith("bn-auftrag__")]
    assert len(katalog) == len(set(katalog))
    assert set(katalog) == _brain_auftrag_katalog()
    assert not [n for n in katalog if n.endswith("_loeschen")]
    assert not BRAIN_AUFTRAG_OHNE & set(katalog)
    # Lesen und die zweistufigen Aktionen bleiben erreichbar (die SOUL fuehrt sie).
    assert {"event_waehlen", "events_suchen", "kontakte_suchen", "aufgaben_liste",
            "aufgaben_anlegen", "event_absagen", "rolle_aendern"} <= set(katalog)


def _app_erlaubt(agent: str) -> list[str]:
    return [n.removeprefix(APP_PRAEFIX) for n in _erlaubt(agent) if n.startswith(APP_PRAEFIX)]


def test_brain_lesen_darf_nicht_schreiben() -> None:
    namen = _app_erlaubt("brain-lesen")
    assert len(namen) == len(set(namen))
    assert set(namen) == APP_LESEND
    _, schreiben = _werkzeuge_aus_soul()
    assert not set(namen) & (schreiben | APP_TIMETABLE_SCHREIBEND)


def test_brain_auftrag_hat_alle_app_werkzeuge() -> None:
    namen = _app_erlaubt("brain-auftrag")
    assert len(namen) == len(set(namen))
    assert set(namen) == APP_ALLE and len(APP_ALLE) == 20


def test_brain_server_und_allow_listen() -> None:
    konfig = json.loads(OPENCLAW.read_text(encoding="utf-8"))
    server = konfig["mcp"]["servers"]
    for name in ("bn-lesen", "bn-auftrag"):
        # Wie `melden`: dieselbe URL des Katalog-Servers; den Header (Schluessel mit Name
        # VibeMind) setzt erst anbinden.sh ueber stdin (Zeilen 6 und 7).
        assert server[name] == {"url": "{{EVENT_MCP_URL}}/mcp", "transport": "streamable-http",
                                "connectionTimeoutMs": 30000, "requestTimeoutMs": 60000}
    assert set(_erlaubt("brain-lesen")) == {APP_PRAEFIX + n for n in APP_LESEND} | {"bn-lesen__*"}
    assert set(_erlaubt("brain-auftrag")) == ({APP_PRAEFIX + n for n in APP_ALLE}
                                              | {"bn-auftrag__" + n for n in _brain_auftrag_katalog()})
    assert len(_erlaubt("brain-lesen")) == 10
    assert len(_erlaubt("brain-auftrag")) == 20 + len(_brain_auftrag_katalog())
    # Kein anderer Agent bekommt die Brain-Server.
    for agent, eintrag in _agenten().items():
        if agent not in BRAIN_SERVER:
            assert not [n for n in eintrag["tools"]["allow"] if n.startswith("bn-")], agent


def test_soul_regelt_actor_fuer_vibemind() -> None:
    text = BRAIN_SOUL.read_text(encoding="utf-8")
    assert "## Aufträge aus VibeMind" in text
    abschnitt = text.split("## Aufträge aus VibeMind")[1].split("\n## ")[0]
    assert VIBEMIND_ACTOR in abschnitt
    assert "[VibeMind]" in abschnitt


def test_gruppen_soul_lehnt_vibemind_kopf_ab() -> None:
    """Jeder in der Gruppe kann `[VibeMind]` tippen -- dort ist das kein Auftrag."""
    text = SOUL.read_text(encoding="utf-8")
    assert VIBEMIND_ACTOR not in text
    assert "## Aufträge aus VibeMind" not in text
    assert "## Nachrichten mit `[VibeMind]`-Kopf aus der Gruppe" in text
    regel = text.split("## Nachrichten mit `[VibeMind]`-Kopf aus der Gruppe")[1].split("\n## ")[0]
    assert "kein VibeMind-Auftrag" in regel
    assert "`actor` bleibt" in regel
    assert "Bestätigung" in regel


def test_brain_agenten_haben_eigenen_workspace() -> None:
    konfig = json.loads(OPENCLAW.read_text(encoding="utf-8"))
    standard = konfig["agents"]["defaults"]["workspace"]
    assert standard == "/home/node/.openclaw/workspace"
    agenten = _agenten()
    assert "workspace" not in agenten["main"]  # Empfang bleibt beim Standard
    for agent in ("brain-lesen", "brain-auftrag"):
        assert agenten[agent]["workspace"] == LAUFZEIT_WORKSPACE_BRAIN
    # Laufzeitpfad und Saat-Ordner tragen denselben Namen.
    assert LAUFZEIT_WORKSPACE_BRAIN.rsplit("/", 1)[1] == WORKSPACE_BRAIN.name


def _abschnitt(text: str, titel: str) -> str:
    assert f"\n## {titel}\n" in text, titel
    return text.split(f"\n## {titel}\n", 1)[1].split("\n## ")[0]


def test_brain_soul_regeln() -> None:
    """Etappe 4, Task 4: der Brain arbeitet am Katalog (bn-lesen/bn-auftrag); die
    App-Werkzeuge nur noch fuer das Legacy-Festival."""
    from spaces.event_management.system import werkzeuge

    text = BRAIN_SOUL.read_text(encoding="utf-8")
    regeln = _abschnitt(text, "Regeln")
    # (1) Jede Nachricht nennt ein Event; zuerst event_waehlen.
    assert "`event_waehlen`" in regeln and "Zuerst" in regeln
    assert "nennt ein Event" in regeln
    # (2) Mehrere Treffer: Trefferliste, nichts aendern.
    assert "Trefferliste" in regeln and "änderst nichts" in regeln
    # (3) Legacy: genau der Text des Katalogs, dann (und nur dann) event-app__.
    assert werkzeuge.LEGACY == "Legacy-Event: bitte die alte App nutzen"
    assert f"„{werkzeuge.LEGACY}“" in regeln
    assert "`event-app__…`" in regeln and "sonst nie" in regeln
    # (4) Jede Antwort nennt Event und Firma.
    assert "Jede Antwort nennt Event und Firma" in regeln
    # (5) Riskantes zweistufig: beschreiben statt ausfuehren, „Bestätigung nötig“.
    assert "„Bestätigung nötig“" in regeln and "`bestaetigung`" in regeln
    assert "beschreibst" in regeln
    # Eigene Katalog-Server; der Name steht im Schluessel, nicht im Werkzeug.
    assert "`bn-lesen__…`" in text and "`bn-auftrag__…`" in text
    assert "bot:betreiber:VibeMind" in text
    # Die App-Tabelle bleibt (Legacy) und steht im Legacy-Abschnitt.
    legacy = _abschnitt(text, "Legacy-Festival")
    assert "**Lesen**" in legacy and "**Schreiben**" in legacy
    # Keine echten Daten: nur die drei Platzhalter des Legacy-Festivals.
    assert set(PLATZHALTER.findall(text)) == {"EVENT_NAME", "EVENT_DATUM", "EVENT_ORT"}
    for muster, was in VERBOTEN:
        assert not muster.search(text), was
    assert "example" not in text.lower() and "http" not in text


def test_brain_soul_sendet_nichts_und_legt_nichts_an() -> None:
    """Ruling T4-b: nachricht_an_gruppe (sendet sofort), einladen (sendet Mail),
    firma_anlegen und event_anlegen stehen in Regel 5 (nur beschreiben); seit F-2 fehlen
    sie zusaetzlich in der Erlaubnisliste von brain-auftrag. Senden bleibt ausdruecklich
    ausgeschlossen."""
    text = BRAIN_SOUL.read_text(encoding="utf-8")
    regeln = _abschnitt(text, "Regeln")
    regel5 = regeln.split("5. ", 1)[1]
    for werkzeug in ("nachricht_an_gruppe", "einladen", "firma_anlegen", "event_anlegen"):
        assert f"`{werkzeug}`" in regel5, werkzeug
    assert "„Bestätigung nötig“" in regel5
    nicht = _abschnitt(text, "Was du nicht kannst")
    assert "- Nachrichten in Gruppen oder Chats schicken." in nicht
    assert "- E-Mails an Gäste schicken." in nicht


# F-1: was ohne Rueckfrage loescht oder nach aussen wirkt. Zweistufig ist ein Werkzeug,
# wenn sein Schema `bestaetigung` kennt (einladen nur fuer die Rolle leitung, darum hier).
NACH_AUSSEN = frozenset({"nachricht_an_gruppe", "einladen", "firma_anlegen", "event_anlegen"})


def _regel5(text: str) -> str:
    return _abschnitt(text, "Regeln").split("5. ", 1)[1]


def brain_soul_regel5_pruefen(text: str) -> None:
    from spaces.event_management.system import werkzeuge

    regel5 = _regel5(text)
    erreichbar = {n.removeprefix("bn-auftrag__") for n in _erlaubt("brain-auftrag")
                  if n.startswith("bn-auftrag__")}
    sofort = {n for n in werkzeuge.namen("betreiber")
              if n.endswith("_loeschen") or n in NACH_AUSSEN}
    for name in sorted(sofort):
        # Entweder gar nicht erreichbar (F-2) oder in Regel 5 als „nur beschreiben“.
        assert name not in erreichbar or f"`{name}`" in regel5, name
    for name in sorted(NACH_AUSSEN):
        assert f"`{name}`" in regel5, name
    assert "`<baustein>_loeschen`" in regel5 and "ohne Rückfrage" in regel5
    # Die zweistufigen, die erreichbar bleiben, stehen namentlich in Regel 5.
    for name in ("event_absagen", "event_archivieren", "kontakt_entfernen", "rolle_aendern"):
        assert name in erreichbar and f"`{name}`" in regel5, name
    # Die falsche Pauschale (F-1) ist weg: nicht jedes Katalog-Werkzeug fragt erst.
    flach = " ".join(text.split())
    assert "Ein Katalog-Werkzeug liefert ohne `bestaetigung` nur eine Zusammenfassung" not in flach
    # „Du kennst nur Zahlen“ gilt nur fuer das Legacy-Festival; der Katalog liest Namen.
    niemals = " ".join(_abschnitt(text, "Was du niemals tust").split())
    assert "Keine Gästedaten herausgeben. Du kennst nur Zahlen." not in niemals
    assert "`kontakte_suchen`" in niemals and "`…_liste`" in niemals
    assert "`get_guest_stats`" in niemals


def test_brain_soul_regel5_nennt_was_sofort_wirkt() -> None:
    brain_soul_regel5_pruefen(BRAIN_SOUL.read_text(encoding="utf-8"))


def test_brain_agents_kennt_beide_werkzeugfamilien() -> None:
    text = (WORKSPACE_BRAIN / "AGENTS.md").read_text(encoding="utf-8")
    assert "`bn-lesen__…`" in text and "`bn-auftrag__…`" in text and "`event-app__…`" in text
    assert "Du hast nur die Werkzeuge der Event-App" not in text


def test_brain_workspace_vorhanden_ohne_gruppenteile() -> None:
    assert (WORKSPACE_BRAIN / "AGENTS.md").is_file()
    text = BRAIN_SOUL.read_text(encoding="utf-8")
    # Die Brain-Agenten haben nur App-Werkzeuge: kein Foto-Skill, keine Gruppe.
    assert SKILL_NAME not in text
    assert "Telegram-Anzeigenamen" not in text


# --- Vertrag -----------------------------------------------------------------

def _werkzeuge_aus_soul(soul: Path = BRAIN_SOUL) -> tuple[set[str], set[str]]:
    """Werkzeugtabellen einer SOUL. Standard ist die Brain-SOUL: sie ist die
    SOUL, die die Agenten mit den geprueften `tools.allow`-Listen tatsaechlich
    sehen. test_werkzeugtabellen_beider_souls_gleich haelt die Gruppen-SOUL gleich."""
    text = soul.read_text(encoding="utf-8")
    lesen = text.split("**Lesen**")[1].split("**Schreiben**")[0]
    schreiben = text.split("**Schreiben**")[1].split("###")[0]
    zeile = re.compile(r"^\| `([a-z_]+)` \|", re.M)
    return set(zeile.findall(lesen)), set(zeile.findall(schreiben))


def test_werkzeugtabellen_beider_souls_gleich() -> None:
    assert _werkzeuge_aus_soul(SOUL) == _werkzeuge_aus_soul(BRAIN_SOUL)


def test_vertrag_hat_zwei_grobe_capabilities() -> None:
    """Grobe Events aussen: der Brain sieht ask/do, nicht die 20 Werkzeuge
    (die stehen in SOUL.md; die Einzelheiten prueft test_brain_anmeldung)."""
    vertrag = yaml.safe_load(VERTRAG.read_text(encoding="utf-8"))
    assert vertrag["id"] == "event-management"
    assert vertrag["prefixes"] == ["event."]
    assert {c["name"]: c["writes"] for c in vertrag["capabilities"]} == {
        "event_ask": False,
        "event_do": True,
    }
    lesen, schreiben = _werkzeuge_aus_soul()
    assert len(lesen) == 9 and len(schreiben) == 11


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
    for ws in ("workspace", "workspace-brain", "workspace-legacy"):
        assert f"{ws}/SOUL.md" in geschrieben
        assert f"{ws}/AGENTS.md" in geschrieben
    for datei in tmp_path.rglob("*"):
        if datei.is_file():
            assert "{{" not in datei.read_text(encoding="utf-8"), datei
    konfig = json.loads((tmp_path / "openclaw.json").read_text(encoding="utf-8"))
    assert list(konfig["channels"]["telegram"]["groups"]) == [werte["TELEGRAM_GRUPPE"], "*"]
    assert konfig["mcp"]["servers"]["event-app"]["url"] == werte["EVENT_APP_URL"] + "/api/mcp"
    assert konfig["mcp"]["servers"]["melden"]["url"] == werte["EVENT_MCP_URL"] + "/mcp"
    for name in ("bn-lesen", "bn-auftrag"):
        assert konfig["mcp"]["servers"][name]["url"] == werte["EVENT_MCP_URL"] + "/mcp"
    for ws in ("workspace-legacy", "workspace-brain"):
        assert werte["EVENT_ORT"] in (tmp_path / ws / "SOUL.md").read_text(encoding="utf-8")
    assert VIBEMIND_ACTOR in (tmp_path / "workspace-brain" / "SOUL.md").read_text(encoding="utf-8")
    for ws in ("workspace", "workspace-legacy"):
        assert VIBEMIND_ACTOR not in (tmp_path / ws / "SOUL.md").read_text(encoding="utf-8")


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


def test_paket_importierbar():
    """Der Brain ruft Code per `direct:spaces.event_management.<modul>:<fn>`."""
    import importlib

    importlib.import_module("spaces.event_management")


# --- Compose: Dienste des Event-Systems (Etappe 3, Task 9; nur Definition) --------------
#
# event-api, event-web und event-mcp stehen im Compose-Projekt des Space, starten aber nicht
# mit: anbinden.sh ruft `docker compose up -d` ohne Profil auf, und das soll weiter nur
# Shim und Claw hochfahren. Ihre Images baut Etappe 4 (Dockerfile). Geprueft wird nur die
# YAML-Struktur; `docker compose config` laeuft hier nie.

PROFIL = "event-system"
NEUE_DIENSTE = ("event-api", "event-web", "event-mcp")
PYTHON_DIENSTE = ("event-api", "event-mcp")


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _netze(compose: dict) -> dict[str, ipaddress.IPv4Network | ipaddress.IPv6Network]:
    return {name: ipaddress.ip_network(netz["ipam"]["config"][0]["subnet"])
            for name, netz in compose["networks"].items()}


def test_neue_dienste_nur_mit_profil_alte_ohne() -> None:
    dienste = _compose()["services"]
    for name in NEUE_DIENSTE:
        assert dienste[name]["profiles"] == [PROFIL], name
    for name in ("event-management-shim", "event-management-claw"):
        assert "profiles" not in dienste[name], name


def test_python_dienste_teilen_das_image_des_space() -> None:
    dienste = _compose()["services"]
    images = {dienste[name]["image"] for name in PYTHON_DIENSTE}
    assert len(images) == 1
    [image] = images
    assert image.startswith("event-management/") and ":" in image
    assert dienste["event-web"]["image"].startswith("event-management/")
    assert dienste["event-web"]["image"] != image


def test_event_mcp_kommando_port_und_healthcheck() -> None:
    from spaces.event_management.system import mcp_server

    mcp = _compose()["services"]["event-mcp"]
    port = mcp_server.PORT_VORGABE
    assert mcp["command"] == ["python", "-m", "spaces.event_management.system.mcp_server"]
    assert mcp["environment"]["EVENT_MCP_PORT"] == str(port)
    # Im Container auf allen Schnittstellen (der Claw kommt ueber event-agent), auf dem Host
    # nur Loopback.
    assert mcp["environment"]["EVENT_MCP_HOST"] == "0.0.0.0"
    assert mcp["ports"] == [f"127.0.0.1:{port}:{port}"]
    probe = " ".join(mcp["healthcheck"]["test"])
    assert f"http://127.0.0.1:{port}/gesund" in probe
    assert "event-mcp" in mcp_server.ERLAUBTE_HOSTS  # Name, unter dem der Claw ihn ruft


def test_event_api_und_web_nur_loopback() -> None:
    from spaces.event_management.system import api

    dienste = _compose()["services"]
    a = dienste["event-api"]
    assert a["command"] == ["python", "-m", "spaces.event_management.system.api"]
    assert a["environment"]["EVENT_API_PORT"] == str(api.PORT_VORGABE)
    assert a["ports"] == [f"127.0.0.1:{api.PORT_VORGABE}:{api.PORT_VORGABE}"]
    assert f"'127.0.0.1', {api.PORT_VORGABE}" in " ".join(a["healthcheck"]["test"])
    w = dienste["event-web"]
    assert w["ports"] == ["127.0.0.1:3170:3170"]
    assert w["depends_on"]["event-api"]["condition"] == "service_healthy"
    for name, dienst in dienste.items():
        for p in dienst.get("ports", []):
            assert str(p).startswith("127.0.0.1:"), name


def test_event_mcp_feste_adresse_im_agentennetz() -> None:
    compose = _compose()
    netz = compose["networks"]["event-agent"]
    assert netz["internal"] is True
    # Ruling T9-a: das bestehende Netz bleibt wie es ist (kein ip_range), sonst muesste
    # Docker es samt laufendem Shim und Claw neu anlegen.
    assert netz["ipam"]["config"] == [{"subnet": "10.250.10.0/28"}]
    subnetz = ipaddress.ip_network(netz["ipam"]["config"][0]["subnet"])
    mcp = compose["services"]["event-mcp"]["networks"]
    adresse = ipaddress.ip_address(mcp["event-agent"]["ipv4_address"])
    # Fest, im /28 und weder Netz-, Gateway- noch Broadcast-Adresse.
    assert adresse in subnetz
    assert adresse not in (subnetz.network_address, subnetz.network_address + 1,
                           subnetz.broadcast_address)
    feste = [n["ipv4_address"] for d in compose["services"].values()
             if isinstance(d.get("networks"), dict)
             for n in d["networks"].values() if isinstance(n, dict) and "ipv4_address" in n]
    assert len(feste) == len(set(feste))


def test_netze_im_freien_26_und_ueberschneidungsfrei() -> None:
    compose = _compose()
    netze = _netze(compose)
    frei = ipaddress.ip_network("10.250.10.0/26")
    assert all(n.subnet_of(frei) for n in netze.values())
    liste = list(netze.values())
    for i, a in enumerate(liste):
        for b in liste[i + 1:]:
            assert not a.overlaps(b), (a, b)
    # Der Ausgang der App-Dienste (DB, Telegram, Mail) ist ein eigenes Netz; das
    # Agentennetz bleibt intern.
    aus = compose["networks"]["event-app-aus"]
    assert not aus.get("internal")
    dienste = compose["services"]
    assert set(dienste["event-mcp"]["networks"]) == {"event-agent", "event-app-aus"}
    assert set(dienste["event-api"]["networks"]) == {"event-app-aus"}
    assert set(dienste["event-web"]["networks"]) == {"event-app-aus"}


def test_geheimnisse_nur_als_datei_aus_laufzeit() -> None:
    compose = _compose()
    text = COMPOSE.read_text(encoding="utf-8")
    for muster in (r"evk_[A-Za-z0-9]", r"bot\d+:", r"postgres(ql)?://", r"password=",
                   r"-100\d{6,}"):
        assert not re.search(muster, text), muster
    geheim = compose["secrets"]
    for name, eintrag in geheim.items():
        assert eintrag["file"].startswith("./laufzeit/"), name
    for name in PYTHON_DIENSTE:
        dienst = compose["services"][name]
        env = dienst["environment"]
        for variable in env:
            if any(t in variable for t in ("DSN", "TOKEN", "PASSWORT", "GEHEIMNIS")):
                assert variable.endswith("_FILE"), (name, variable)
                assert env[variable].startswith("/run/secrets/"), (name, variable)
        eingehaengt = set(dienst["secrets"])
        assert eingehaengt <= set(geheim)
        for variable, wert in env.items():
            if variable.endswith("_FILE"):
                assert wert.removeprefix("/run/secrets/") in eingehaengt, (name, variable)
        assert dienst["environment"]["EVENT_APP_DSN_FILE"] == "/run/secrets/event-app-dsn"
        assert "TELEGRAM_BOT_TOKEN_FILE" in env
    assert "EVENT_SITZUNG_GEHEIMNIS_FILE" in compose["services"]["event-api"]["environment"]


def test_python_dienste_gehaertet() -> None:
    dienste = _compose()["services"]
    for name in NEUE_DIENSTE:
        d = dienste[name]
        assert d["read_only"] is True and d["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in d["security_opt"], name
        assert d["restart"] == "unless-stopped", name
        assert "docker.sock" not in json.dumps(d), name
