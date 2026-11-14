"""Bereitstellungs-Arbeiter (system/bereitstellung.py, db/007_bereitstellung.sql).

Auftraege entstehen wie im Betrieb ueber `verbinden.bestaetigen` (RLS aktiv, `db_app`), der
Arbeiter laeuft mit der Admin-DSN (`db_admin`). OpenClaw nur ueber `SpeicherAusfuehrer`, der
DockerAusfuehrer nur mit ersetztem `subprocess.run`: kein Docker, kein Container. Chat-IDs
sind Beispielwerte.
"""
from __future__ import annotations

import logging
import re
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import psycopg
import pytest

from spaces.event_management.system import (
    bereitstellung, events, schluessel, verbinden, zugang,
)
from spaces.event_management.system.bereitstellung import (
    AusfuehrerFehler, DockerAusfuehrer, SpeicherAusfuehrer,
)
from spaces.event_management.system.kontext import Kontext
from spaces.event_management.system.telegram import SpeicherTelegram

BEGINN = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)
CHAT = -1001234567890
CHAT_NEU = -1001234567891
DIREKT = 123456789
DIREKT_ANDERS = 123456780
MCP = "http://event-mcp.example.org:8171/mcp"
PW = "ein-langes-passwort-1"
WS = "/home/node/.openclaw/workspace-"
# Fremde Agenten in der Config, die der Arbeiter nie anfassen darf.
GRUND_CONFIG = {
    "agents": {"list": [{"id": "main", "tools": {"allow": ["melden__gruppe_melden"]}},
                        {"id": "ev-legacy"}]},
    "bindings": [{"agentId": "ev-legacy",
                  "match": {"channel": "telegram", "peer": {"kind": "group", "id": "-1009"}}}],
    "channels": {"telegram": {"groupPolicy": "allowlist",
                              "groups": {"*": {"requireMention": True},
                                         "-1009": {"requireMention": False}}}},
    "mcp": {"servers": {"melden": {"url": "http://x.example.org/mcp"}}},
}


@pytest.fixture(autouse=True)
def _sperren_leeren():
    verbinden._sperre_zuruecksetzen()
    yield
    verbinden._sperre_zuruecksetzen()


# --- Testdaten --------------------------------------------------------------------


def _id(admin: Any, abfrage: str, werte: tuple[object, ...]) -> Any:
    return admin.execute(abfrage, werte).fetchone()["id"]


def _person(admin: Any, mail: str, *, betreiber: bool = False) -> UUID:
    return _id(admin, "insert into event.person (mail, name, passwort_hash, betreiber)"
                      " values (%s, %s, %s, %s) returning id",
               (mail, "Person " + mail.split("@")[0], zugang.passwort_hash(PW), betreiber))


@pytest.fixture
def telegram() -> SpeicherTelegram:
    t = SpeicherTelegram()
    t.chats[CHAT] = {"titel": "Beispielgruppe", "art": "supergroup", "bot_ist_mitglied": True}
    t.chats[CHAT_NEU] = {"titel": "Neue Gruppe", "art": "group", "bot_ist_mitglied": True}
    t.chats[DIREKT] = {"titel": "Erika", "art": "private", "bot_ist_mitglied": True}
    t.chats[DIREKT_ANDERS] = {"titel": "Erika neu", "art": "private", "bot_ist_mitglied": True}
    return t


@pytest.fixture
def welt(db_admin: Any) -> dict[str, Any]:
    a = _id(db_admin, "insert into event.firma (name, kurz) values (%s, %s) returning id",
            ("Beispiel GmbH", "firma-a"))
    lea = _person(db_admin, "lea@example.org")
    bea = _person(db_admin, "bea@example.org", betreiber=True)
    db_admin.execute("insert into event.mitgliedschaft values (%s, %s, 'leitung')", (lea, a))
    fest = _id(db_admin, "insert into event.event (firma_id, kategorie, name, beginn, ende,"
                         " ort_adresse) values (%s, 'fest', 'Sommerfest', %s, %s, %s)"
                         " returning id",
               (a, BEGINN, BEGINN + timedelta(hours=3), "Musterstraße 1, Musterdorf"))
    return {
        "a": a, "lea": lea, "bea": bea, "fest": fest,
        "leitung": Kontext(person_id=lea, betreiber=False, rollen={a: "leitung"}),
        "betreiber": Kontext(person_id=bea, betreiber=True),
    }


def _hex8(wert: object) -> str:
    return str(wert).replace("-", "")[:8]


def _pruefcode(t: SpeicherTelegram, chat: int) -> str:
    """Pruefcode, den der Dienst nach dem Melden in den Chat geschickt hat (F1)."""
    treffer = re.search(r"Event-System: ([A-Z2-9]{6}) ",
                        [text for ziel, text in t.gesendet if ziel == chat][-1])
    assert treffer is not None
    return treffer.group(1)


def _gruppe(app: Any, welt: dict[str, Any], t: SpeicherTelegram, chat: int = CHAT) -> UUID:
    """Gruppe des Festes verbinden und bestaetigen; Rueckgabe: id des anlegen-Auftrags."""
    code = verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["fest"])
    assert verbinden.melden(app, code=code, chat_id=chat, art="gruppe",
                            telegram=t) == verbinden.DANKE
    [v] = [v for v in verbinden.offene(app, welt["leitung"], event_id=welt["fest"])
           if v["status"] == "gemeldet"]
    return verbinden.bestaetigen(app, welt["leitung"], v["id"], _pruefcode(t, chat))


def _direkt(app: Any, k: Kontext, t: SpeicherTelegram, chat: int = DIREKT) -> UUID:
    code = verbinden.code_erzeugen(app, k, art="direkt", event_id=None)
    assert verbinden.melden(app, code=code, chat_id=chat, art="direkt",
                            telegram=t) == verbinden.DANKE
    [v] = [v for v in verbinden.offene(app, k) if v["status"] == "gemeldet"]
    return verbinden.bestaetigen(app, k, v["id"], _pruefcode(t, chat))


def _auftrag(admin: Any, auftrag_id: UUID) -> dict[str, Any]:
    return admin.execute("select * from event.bereitstellung where id = %s",
                         (auftrag_id,)).fetchone()


def _auftraege(admin: Any) -> list[dict[str, Any]]:
    return admin.execute("select * from event.bereitstellung order by angelegt, id").fetchall()


def _schluessel(admin: Any, schluessel_id: UUID) -> dict[str, Any]:
    return admin.execute("select * from event.schluessel where id = %s",
                         (schluessel_id,)).fetchone()


def _agent(config: dict[str, Any], agent: str) -> dict[str, Any] | None:
    return next((a for a in config.get("agents", {}).get("list", []) if a["id"] == agent), None)


def _bindungen(config: dict[str, Any], agent: str) -> list[dict[str, Any]]:
    return [b for b in config.get("bindings", []) if b.get("agentId") == agent]


def _bearer(config: dict[str, Any], agent: str) -> str:
    wert = config["mcp"]["servers"][agent]["headers"]["Authorization"]
    assert wert.startswith("Bearer evk_")
    return wert.removeprefix("Bearer ")


def _fremdes_unveraendert(config: dict[str, Any]) -> None:
    assert _agent(config, "main") == GRUND_CONFIG["agents"]["list"][0]
    assert _agent(config, "ev-legacy") == {"id": "ev-legacy"}
    assert _bindungen(config, "ev-legacy") == GRUND_CONFIG["bindings"]
    gruppen = config["channels"]["telegram"]["groups"]
    assert gruppen["*"] == {"requireMention": True} and gruppen["-1009"] == {
        "requireMention": False}
    assert config["mcp"]["servers"]["melden"] == {"url": "http://x.example.org/mcp"}


def _aus() -> SpeicherAusfuehrer:
    return SpeicherAusfuehrer(config=GRUND_CONFIG)


def _teilstand_weg(aus: SpeicherAusfuehrer, agent: str) -> None:
    """Nach einem gescheiterten/haengenden Anlegen: weder Agent noch Server noch Binding."""
    c = aus.config
    assert _agent(c, agent) is None and _bindungen(c, agent) == []
    assert agent not in c["mcp"]["servers"]
    _fremdes_unveraendert(c)


# --- Anlegen ------------------------------------------------------------------------


def test_anlegen_gruppe_erzeugt_vier_teile(db_admin, db_app, welt, telegram):
    app = db_app()
    auftrag_id = _gruppe(app, welt, telegram)
    aus = _aus()
    assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == [auftrag_id]

    agent = "ev-" + _hex8(welt["fest"])
    c = aus.config
    # (3) Agent mit eigenem Arbeitsbereich und nur den eigenen Werkzeugen.
    assert _agent(c, agent) == {"id": agent, "workspace": WS + agent,
                                "tools": {"allow": [f"{agent}__*"]}}
    # (2) Server mit Bearer eines gueltigen Event-Schluessels.
    server = c["mcp"]["servers"][agent]
    assert {k: v for k, v in server.items() if k != "headers"} == {
        "url": MCP, "transport": "streamable-http"}
    info = schluessel.pruefen(app, _bearer(c, agent))
    assert (info.art, info.event_id, info.firma_id) == ("event", welt["fest"], welt["a"])
    # (4) Binding an die Gruppe, dort ohne Erwaehnung.
    assert _bindungen(c, agent) == [{"agentId": agent, "match": {
        "channel": "telegram", "peer": {"kind": "group", "id": str(CHAT)}}}]
    assert c["channels"]["telegram"]["groups"][str(CHAT)] == {"requireMention": False}
    # (1) SOUL im Arbeitsbereich nennt das Event.
    assert "Sommerfest" in aus.dateien[f"{WS}{agent}/SOUL.md"]
    _fremdes_unveraendert(c)

    zeile = _auftrag(db_admin, auftrag_id)
    assert (zeile["status"], zeile["schritt"], zeile["fehler"]) == ("erfolg", "gruppe", None)
    assert zeile["schluessel_id"] == info.id
    assert zeile["gestartet"] is not None and zeile["beendet"] is not None


def test_leitung_direktchat_dmscope(db_admin, db_app, welt, telegram):
    app = db_app()
    ld = _direkt(app, welt["leitung"], telegram)
    bo = _direkt(app, welt["betreiber"], telegram, DIREKT_ANDERS)
    aus = _aus()
    assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == [ld, bo]
    c = aus.config
    for agent, chat, art, person, name in (
            ("ld-" + _hex8(welt["lea"]), DIREKT, "leitung", welt["lea"], "Person lea"),
            ("bo-" + _hex8(welt["bea"]), DIREKT_ANDERS, "betreiber", welt["bea"],
             "Person bea")):
        assert _agent(c, agent) == {"id": agent, "workspace": WS + agent,
                                    "tools": {"allow": [f"{agent}__*"]}}
        assert _bindungen(c, agent) == [{
            "agentId": agent,
            "match": {"channel": "telegram", "peer": {"kind": "direct", "id": str(chat)}},
            "session": {"dmScope": "per-peer"}}]
        info = schluessel.pruefen(app, _bearer(c, agent))
        assert (info.art, info.person_id, info.event_id) == (art, person, None)
        assert name in aus.dateien[f"{WS}{agent}/SOUL.md"]
        # Direktchats bekommen keinen Gruppen-Eintrag.
        assert str(chat) not in c["channels"]["telegram"]["groups"]
    assert "Betreiber" in aus.dateien[f"{WS}bo-{_hex8(welt['bea'])}/SOUL.md"]
    _fremdes_unveraendert(c)


def test_idempotent_zweiter_lauf_aendert_nichts(db_admin, db_app, welt, telegram):
    app = db_app()
    auftrag_id = _gruppe(app, welt, telegram)
    aus = _aus()
    bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    vorher = aus.config_lesen()
    aus.schreibvorgaenge.clear()
    assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == []
    assert aus.schreibvorgaenge == [] and aus.config == vorher

    # Jeder Schritt prueft den Ist-Zustand: dieselben Schritte noch einmal schreiben nichts.
    agent = "ev-" + _hex8(welt["fest"])
    zeile = _auftrag(db_admin, auftrag_id)
    ev = db_admin.execute("select e.*, f.name as firma_name from event.event e join"
                          " event.firma f on f.id = e.firma_id where e.id = %s",
                          (welt["fest"],)).fetchone()
    schritte = bereitstellung.schritte_anlegen(
        zeile, schluessel_klartext=_bearer(vorher, agent), mcp_url=MCP,
        schema=events.schema_fuer("fest", None), event=ev)
    assert [s.name for s in schritte] == [
        "arbeitsbereich", "server", "agent", "binding", "gruppe"]
    assert [s.ausfuehren(aus) for s in schritte] == [False] * 5
    assert aus.schreibvorgaenge == [] and aus.config == vorher
    # Der Schritt-Repr verraet den Schluessel nicht.
    assert "evk_" not in repr(schritte)


def test_fehler_mitten_drin_endet_fehler_und_widerruft_schluessel(
        db_admin, db_app, welt, telegram, caplog):
    app = db_app()
    auftrag_id = _gruppe(app, welt, telegram)
    aus = _aus()
    aus.scheitern.add("config_schreiben:bindings")
    with caplog.at_level(logging.DEBUG):
        assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == [auftrag_id]
    zeile = _auftrag(db_admin, auftrag_id)
    assert (zeile["status"], zeile["schritt"]) == ("fehler", "binding")
    assert zeile["fehler"] and "evk_" not in zeile["fehler"]
    # Der gerade ausgestellte Schluessel ist widerrufen, der Teilstand abgeraeumt; nur der
    # Arbeitsbereich bleibt (nicht umbenannt).
    assert _schluessel(db_admin, zeile["schluessel_id"])["widerrufen"] is not None
    agent = "ev-" + _hex8(welt["fest"])
    _teilstand_weg(aus, agent)
    assert f"{WS}{agent}/SOUL.md" in aus.dateien
    assert "evk_" not in caplog.text and str(CHAT) not in caplog.text

    # Kein blinder Neuversuch: der naechste Lauf laesst den fehlerhaften Auftrag liegen.
    aus.scheitern.clear()
    aus.schreibvorgaenge.clear()
    assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == []
    assert aus.schreibvorgaenge == []


def test_fehler_beim_schluessel_ausstellen(db_admin, db_app, welt, telegram):
    """Betreiber-Auftrag fuer eine Person, die kein Betreiber mehr ist (Rolle verloren
    zwischen Bestaetigen und Lauf): fehler, nichts in der Config, kein Schluessel."""
    app = db_app()
    auftrag_id = _direkt(app, welt["betreiber"], telegram)
    db_admin.execute("update event.person set betreiber = false where id = %s", (welt["bea"],))
    aus = _aus()
    bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    zeile = _auftrag(db_admin, auftrag_id)
    assert zeile["status"] == "fehler" and zeile["schluessel_id"] is None
    assert aus.config == GRUND_CONFIG
    assert db_admin.execute("select count(*) as n from event.schluessel").fetchone()["n"] == 0


def test_fehler_beim_gruppen_schritt_raeumt_teilstand_ab(db_admin, db_app, welt, telegram):
    """Scheitert der letzte Schritt, steht schon das Binding: es muss wieder weg, sonst
    bediente ein Agent mit widerrufenem Schluessel die Gruppe."""
    app = db_app()
    auftrag_id = _gruppe(app, welt, telegram)
    aus = _aus()
    aus.scheitern.add(f"config_schreiben:channels.telegram.groups.{CHAT}.requireMention")
    assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == [auftrag_id]
    zeile = _auftrag(db_admin, auftrag_id)
    assert (zeile["status"], zeile["schritt"]) == ("fehler", "gruppe")
    assert _schluessel(db_admin, zeile["schluessel_id"])["widerrufen"] is not None
    _teilstand_weg(aus, "ev-" + _hex8(welt["fest"]))
    assert str(CHAT) not in aus.config["channels"]["telegram"]["groups"]


class _BindingNurEinmal(SpeicherAusfuehrer):
    """Mit "bindings-zweites-mal" in `scheitern`: das erste Schreiben von `bindings` (das
    Anlegen) gelingt, jedes weitere (das Abraeumen) scheitert."""

    def config_schreiben(self, pfad: str, wert: object) -> None:
        if (pfad == "bindings" and "bindings-zweites-mal" in self.scheitern
                and ("config_schreiben", "bindings") in self.schreibvorgaenge):
            raise AusfuehrerFehler("config_schreiben fehlgeschlagen")
        super().config_schreiben(pfad, wert)


def test_scheitert_auch_das_abraeumen_bleibt_der_auftrag_fehler(
        db_admin, db_app, welt, telegram, caplog):
    """Das Abraeumen ist nur best effort: scheitert es, bleibt der urspruengliche Fehler
    stehen; den Rest raeumt der naechste Lauf als verwaisten Agenten ab."""
    app = db_app()
    auftrag_id = _gruppe(app, welt, telegram)
    aus = _BindingNurEinmal(config=GRUND_CONFIG)
    aus.scheitern.update({f"config_schreiben:channels.telegram.groups.{CHAT}.requireMention",
                          "bindings-zweites-mal"})
    with caplog.at_level(logging.DEBUG):
        bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    zeile = _auftrag(db_admin, auftrag_id)
    assert (zeile["status"], zeile["schritt"]) == ("fehler", "gruppe")
    agent = "ev-" + _hex8(welt["fest"])
    # Binding blieb (sein Entfernen scheiterte), Agent und Server sind trotzdem weg.
    assert _bindungen(aus.config, agent) != []
    assert _agent(aus.config, agent) is None and agent not in aus.config["mcp"]["servers"]
    assert "evk_" not in caplog.text and str(CHAT) not in caplog.text

    aus.scheitern.clear()
    [abbau] = bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    assert _auftrag(db_admin, abbau)["art"] == "abbauen"
    _abgebaut(aus, agent, CHAT)


def _event(admin: Any, welt: dict[str, Any]) -> dict[str, Any]:
    return admin.execute("select e.*, f.name as firma_name from event.event e join"
                         " event.firma f on f.id = e.firma_id where e.id = %s",
                         (welt["fest"],)).fetchone()


def test_haengender_auftrag_nach_binding_wird_abgeraeumt(db_admin, db_app, welt, telegram):
    app = db_app()
    auftrag_id = _gruppe(app, welt, telegram)
    sid, klartext = schluessel.ausstellen(db_admin, art="event", bezeichnung="haengt",
                                          event_id=welt["fest"])
    db_admin.execute("update event.bereitstellung set status = 'laeuft', schluessel_id = %s,"
                     " schritt = 'binding', gestartet = now() - interval '11 minutes'"
                     " where id = %s", (sid, auftrag_id))
    aus = _aus()
    # Der abgebrochene Lauf kam bis einschliesslich Binding.
    for s in bereitstellung.schritte_anlegen(
            _auftrag(db_admin, auftrag_id), schluessel_klartext=klartext, mcp_url=MCP,
            schema=events.schema_fuer("fest", None), event=_event(db_admin, welt))[:4]:
        s.ausfuehren(aus)
    agent = "ev-" + _hex8(welt["fest"])
    assert _bindungen(aus.config, agent) != []

    assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == []
    zeile = _auftrag(db_admin, auftrag_id)
    assert zeile["status"] == "fehler" and "10 Minuten" in zeile["fehler"]
    assert _schluessel(db_admin, sid)["widerrufen"] is not None
    _teilstand_weg(aus, agent)
    # Kein Abbau-Auftrag noetig, der Arbeitsbereich bleibt unberuehrt.
    assert [a["art"] for a in _auftraege(db_admin)] == ["anlegen"]
    assert f"{WS}{agent}/SOUL.md" in aus.dateien


def test_neues_anlegen_widerruft_schluessel_frueherer_anlegen(db_admin, db_app, welt, telegram):
    """Derselbe Agent ein zweites Mal angelegt (ohne Abbau dazwischen): der Schluessel des
    frueheren Anlegens wird nach dem Erfolg widerrufen, der neue gilt."""
    app = db_app()
    erstes = _gruppe(app, welt, telegram)
    aus = _aus()
    bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    alt = _auftrag(db_admin, erstes)
    zweites = _id(db_admin, "insert into event.bereitstellung (art, ziel, event_id, person_id,"
                            " chat_id, agent_id) values ('anlegen', %s, %s, %s, %s, %s)"
                            " returning id",
                  (alt["ziel"], alt["event_id"], alt["person_id"], alt["chat_id"],
                   alt["agent_id"]))
    assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == [zweites]
    neu = _auftrag(db_admin, zweites)
    assert neu["status"] == "erfolg"
    assert _schluessel(db_admin, alt["schluessel_id"])["widerrufen"] is not None
    assert _schluessel(db_admin, neu["schluessel_id"])["widerrufen"] is None
    assert schluessel.pruefen(app, _bearer(aus.config, alt["agent_id"])).id == neu[
        "schluessel_id"]


def test_gescheitertes_neues_anlegen_macht_den_agenten_verwaist(
        db_admin, db_app, welt, telegram):
    """Ein erneutes Anlegen scheitert, und auch das Abraeumen gelingt nur teilweise: das
    fruehere erfolgreiche Anlegen traegt den Agenten nicht mehr (nur das juengste zaehlt),
    der naechste Lauf baut ihn als verwaist ab. Der Schluessel des frueheren Anlegens ist
    schon nach dem Fehlschlag widerrufen."""
    app = db_app()
    erstes = _gruppe(app, welt, telegram)
    aus = _aus()
    bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    alt = _auftrag(db_admin, erstes)
    agent = alt["agent_id"]
    zweites = _id(db_admin, "insert into event.bereitstellung (art, ziel, event_id, person_id,"
                            " chat_id, agent_id) values ('anlegen', %s, %s, %s, %s, %s)"
                            " returning id",
                  (alt["ziel"], alt["event_id"], alt["person_id"], alt["chat_id"], agent))
    aus.scheitern.update({f"config_schreiben:mcp.servers.{agent}",
                          f"config_entfernen:mcp.servers.{agent}"})
    assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == [zweites]
    neu = _auftrag(db_admin, zweites)
    assert (neu["status"], neu["schritt"]) == ("fehler", "server")
    assert _schluessel(db_admin, neu["schluessel_id"])["widerrufen"] is not None
    assert _schluessel(db_admin, alt["schluessel_id"])["widerrufen"] is not None
    # Stehen geblieben ist der Server (mit dem alten, jetzt widerrufenen Schluessel).
    assert agent in aus.config["mcp"]["servers"] and _agent(aus.config, agent) is None

    aus.scheitern.clear()
    [abbau] = bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    zeile = _auftrag(db_admin, abbau)
    assert (zeile["art"], zeile["status"]) == ("abbauen", "erfolg")
    _abgebaut(aus, agent, CHAT)


def test_schluessel_und_bezug_nur_gemeinsam(db_admin, db_app, welt, telegram):
    """Scheitert das Setzen von schluessel_id, rollt auch das Ausstellen zurueck: kein
    Schluessel ohne Auftrag."""
    app = db_app()
    auftrag_id = _gruppe(app, welt, telegram)
    db_admin.execute("create function event.test_bezug_sperren() returns trigger"
                     " language plpgsql as $$ begin raise exception 'gesperrt'; end $$")
    db_admin.execute("create trigger test_bezug_sperren before update of schluessel_id"
                     " on event.bereitstellung for each row"
                     " when (new.schluessel_id is not null)"
                     " execute function event.test_bezug_sperren()")
    aus = _aus()
    assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == [auftrag_id]
    zeile = _auftrag(db_admin, auftrag_id)
    assert (zeile["status"], zeile["schritt"], zeile["schluessel_id"]) == (
        "fehler", "schluessel", None)
    assert db_admin.execute("select count(*) as n from event.schluessel").fetchone()["n"] == 0
    assert aus.config == GRUND_CONFIG


def test_fehler_beim_widerruf_frueherer_kippt_erfolg_nicht(
        db_admin, db_app, welt, telegram, monkeypatch, caplog):
    app = db_app()
    auftrag_id = _gruppe(app, welt, telegram)

    def kaputt(conn: Any, auftrag: Any) -> None:
        raise RuntimeError("evk_nie_im_log")

    monkeypatch.setattr(bereitstellung, "_schluessel_widerrufen", kaputt)
    aus = _aus()
    with caplog.at_level(logging.DEBUG):
        assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == [auftrag_id]
    zeile = _auftrag(db_admin, auftrag_id)
    assert (zeile["status"], zeile["fehler"]) == ("erfolg", None)
    assert _schluessel(db_admin, zeile["schluessel_id"])["widerrufen"] is None
    assert schluessel.pruefen(app, _bearer(aus.config, zeile["agent_id"])).id == zeile[
        "schluessel_id"]
    assert "RuntimeError" in caplog.text and "evk_" not in caplog.text


def test_zweiter_lauf_gleichzeitig_endet_still(db_admin, db_app, welt, telegram, pg_cluster):
    """Laeuft schon ein Arbeiter (Advisory-Lock gehalten), endet ein zweiter sofort."""
    app = db_app()
    auftrag_id = _gruppe(app, welt, telegram)
    andere = psycopg.connect(f"host=127.0.0.1 port={pg_cluster['port']} user=postgres"
                             f" dbname={db_admin.info.dbname}", autocommit=True)
    try:
        andere.execute("select pg_advisory_lock(%s)", (bereitstellung.SPERRE,))
        aus = _aus()
        assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == []
        assert aus.schreibvorgaenge == []
        assert _auftrag(db_admin, auftrag_id)["status"] == "offen"
        andere.execute("select pg_advisory_unlock(%s)", (bereitstellung.SPERRE,))
        assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == [auftrag_id]
    finally:
        andere.close()
    # Der Lauf gibt seine Sperre wieder frei.
    assert db_admin.execute("select count(*) as n from pg_locks where locktype = 'advisory'"
                            ).fetchone()["n"] == 0


# --- Abbauen ------------------------------------------------------------------------


def _abbau_einfuegen(admin: Any, anlegen: dict[str, Any]) -> UUID:
    return _id(admin, "insert into event.bereitstellung (art, ziel, event_id, person_id,"
                      " chat_id, agent_id, angelegt) values ('abbauen', %s, %s, %s, %s, %s,"
                      " clock_timestamp()) returning id",
               (anlegen["ziel"], anlegen["event_id"], anlegen["person_id"], anlegen["chat_id"],
                anlegen["agent_id"]))


def _abgebaut(aus: SpeicherAusfuehrer, agent: str, chat: int | None) -> None:
    c = aus.config
    assert _agent(c, agent) is None
    assert _bindungen(c, agent) == []
    assert agent not in c["mcp"]["servers"]
    if chat is not None:
        assert str(chat) not in c["channels"]["telegram"]["groups"]
    # Arbeitsbereich bleibt, nur umbenannt.
    assert not any(p.startswith(f"{WS}{agent}/") for p in aus.dateien)
    assert any(p.startswith(f"{WS}{agent}-abgebaut-") and p.endswith("/SOUL.md")
               for p in aus.dateien)
    _fremdes_unveraendert(c)


def test_abbauen_entfernt_auch_teilstaende(db_admin, db_app, welt, telegram):
    app = db_app()
    auftrag_id = _gruppe(app, welt, telegram)
    aus = _aus()
    # Teilstand: Arbeitsbereich, Server und Agent gesetzt, Binding gescheitert, und beim
    # Abraeumen danach scheiterte das Entfernen des Servers (er bleibt stehen).
    aus.scheitern.update({"config_schreiben:bindings", "config_entfernen"})
    bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    aus.scheitern.clear()
    anlegen = _auftrag(db_admin, auftrag_id)
    assert anlegen["status"] == "fehler"
    assert anlegen["agent_id"] in aus.config["mcp"]["servers"]

    abbau = _abbau_einfuegen(db_admin, anlegen)
    assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == [abbau]
    agent = anlegen["agent_id"]
    _abgebaut(aus, agent, CHAT)
    zeile = _auftrag(db_admin, abbau)
    assert (zeile["status"], zeile["schritt"]) == ("erfolg", "arbeitsbereich")

    # Ein zweiter Abbau auf leerem Stand ist harmlos (jeder Schritt prueft den Ist-Zustand).
    zweiter = _abbau_einfuegen(db_admin, anlegen)
    aus.schreibvorgaenge.clear()
    assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == [zweiter]
    assert _auftrag(db_admin, zweiter)["status"] == "erfolg"
    assert aus.schreibvorgaenge == []


def test_abbauen_laesst_gruppe_eines_anderen_agenten(db_admin, db_app, welt, telegram):
    """Der Gruppen-Eintrag bleibt, solange noch ein Binding auf diesen Chat zeigt."""
    app = db_app()
    auftrag_id = _gruppe(app, welt, telegram)
    aus = _aus()
    bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    aus.config["bindings"].append({"agentId": "ev-legacy", "match": {
        "channel": "telegram", "peer": {"kind": "group", "id": str(CHAT)}}})
    _abbau_einfuegen(db_admin, _auftrag(db_admin, auftrag_id))
    bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    assert aus.config["channels"]["telegram"]["groups"][str(CHAT)] == {"requireMention": False}


def test_anlegen_scheitert_wenn_chat_anderem_agenten_gehoert(db_admin, db_app, welt, telegram):
    app = db_app()
    auftrag_id = _gruppe(app, welt, telegram)
    aus = _aus()
    aus.config["bindings"].append({"agentId": "ev-legacy", "match": {
        "channel": "telegram", "peer": {"kind": "group", "id": str(CHAT)}}})
    bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    zeile = _auftrag(db_admin, auftrag_id)
    assert (zeile["status"], zeile["schritt"]) == ("fehler", "binding")
    assert _schluessel(db_admin, zeile["schluessel_id"])["widerrufen"] is not None


def test_haengender_auftrag_wird_fehler(db_admin, db_app, welt, telegram):
    app = db_app()
    alt = _gruppe(app, welt, telegram)
    sid, _ = schluessel.ausstellen(db_admin, art="event", bezeichnung="haengt",
                                   event_id=welt["fest"])
    db_admin.execute("update event.bereitstellung set status = 'laeuft', schluessel_id = %s,"
                     " gestartet = now() - interval '11 minutes' where id = %s", (sid, alt))
    frisch = _direkt(app, welt["leitung"], telegram)
    db_admin.execute("update event.bereitstellung set status = 'laeuft',"
                     " gestartet = now() - interval '2 minutes' where id = %s", (frisch,))
    aus = _aus()
    assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == []
    zeile = _auftrag(db_admin, alt)
    assert zeile["status"] == "fehler" and "10 Minuten" in zeile["fehler"]
    assert zeile["beendet"] is not None
    assert _schluessel(db_admin, sid)["widerrufen"] is not None
    assert _auftrag(db_admin, frisch)["status"] == "laeuft"
    # Mit `jetzt` in 15 Minuten haengt auch der zweite.
    bereitstellung.lauf(db_admin, aus, mcp_url=MCP,
                        jetzt=datetime.now(timezone.utc) + timedelta(minutes=15))
    assert _auftrag(db_admin, frisch)["status"] == "fehler"


# --- Archivieren und Loeschen (Carry Task 7) ---------------------------------------


def test_archivieren_legt_abbau_an(db_admin, db_app, welt, telegram):
    app = db_app()
    anlegen_id = _gruppe(app, welt, telegram)
    aus = _aus()
    bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    sid = _auftrag(db_admin, anlegen_id)["schluessel_id"]

    events.event_status(app, welt["leitung"], welt["fest"], "archiviert")
    # Zweimal archivieren legt keinen zweiten Abbau an.
    events.event_status(app, welt["leitung"], welt["fest"], "archiviert")
    abbau = [a for a in _auftraege(db_admin) if a["art"] == "abbauen"]
    agent = "ev-" + _hex8(welt["fest"])
    assert [(a["ziel"], a["event_id"], a["chat_id"], a["agent_id"], a["person_id"])
            for a in abbau] == [("gruppe", welt["fest"], CHAT, agent, welt["lea"])]

    assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == [abbau[0]["id"]]
    _abgebaut(aus, agent, CHAT)
    assert _schluessel(db_admin, sid)["widerrufen"] is not None


def test_archivieren_ohne_gruppe_kein_auftrag(db_admin, db_app, welt):
    events.event_status(db_app(), welt["leitung"], welt["fest"], "archiviert")
    assert _auftraege(db_admin) == []


def test_loeschen_legt_abbau_an_vor_dem_loeschen(db_admin, db_app, welt, telegram):
    app = db_app()
    _gruppe(app, welt, telegram)
    aus = _aus()
    bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    events.event_loeschen(app, welt["betreiber"], welt["fest"])
    # Die Kaskade hat die Verbindung entfernt, der Abbau-Auftrag hat ueberlebt.
    assert db_admin.execute("select count(*) as n from event.verbindung").fetchone()["n"] == 0
    [abbau] = [a for a in _auftraege(db_admin) if a["art"] == "abbauen"]
    assert (abbau["event_id"], abbau["chat_id"]) == (welt["fest"], CHAT)
    assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == [abbau["id"]]
    _abgebaut(aus, "ev-" + _hex8(welt["fest"]), CHAT)


def test_anlegen_uebersprungen_wenn_verbindung_nicht_mehr_bestaetigt(
        db_admin, db_app, welt, telegram):
    """Event geloescht, bevor der Arbeiter lief: kein Agent fuer ein geloeschtes Event."""
    app = db_app()
    anlegen_id = _gruppe(app, welt, telegram)
    events.event_loeschen(app, welt["leitung"], welt["fest"])
    aus = _aus()
    ids = bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    assert ids[0] == anlegen_id and len(ids) == 2
    zeile = _auftrag(db_admin, anlegen_id)
    assert (zeile["status"], zeile["schritt"]) == ("fehler", "pruefen")
    assert zeile["fehler"] == bereitstellung.NICHT_MEHR_BESTAETIGT
    assert zeile["schluessel_id"] is None
    assert db_admin.execute("select count(*) as n from event.schluessel").fetchone()["n"] == 0
    assert aus.config == GRUND_CONFIG

    # Ebenso fuer einen inzwischen verworfenen Direktchat.
    k = _direkt(app, welt["leitung"], telegram)
    db_admin.execute("update event.verbindung set status = 'verworfen' where art = 'direkt'")
    bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    assert _auftrag(db_admin, k)["status"] == "fehler"
    assert aus.config == GRUND_CONFIG


def test_reihenfolge_abbauen_vor_anlegen(db_admin, db_app, welt, telegram):
    """Neue Gruppe ersetzt die alte: abbauen ev-X (alter Chat) laeuft vor anlegen ev-X
    (neuer Chat), auch wenn beide im selben Lauf liegen."""
    app = db_app()
    erstes = _gruppe(app, welt, telegram)
    aus = _aus()
    bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    alter_schluessel = _auftrag(db_admin, erstes)["schluessel_id"]

    neues = _gruppe(app, welt, telegram, CHAT_NEU)
    [abbau] = [a["id"] for a in _auftraege(db_admin) if a["art"] == "abbauen"]
    # Beide in derselben Transaktion (bestaetigen), angelegt kommt aus dem Spalten-Default
    # clock_timestamp(): der Abbau liegt trotzdem vorn.
    assert _auftrag(db_admin, abbau)["angelegt"] < _auftrag(db_admin, neues)["angelegt"]
    aus.schreibvorgaenge.clear()
    assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == [abbau, neues]
    # Das erste Schreiben entfernt das alte Binding, erst danach entsteht das neue.
    ops = aus.schreibvorgaenge
    assert ops.index(("config_schreiben", "bindings")) < ops.index(
        ("datei_schreiben", f"{WS}ev-{_hex8(welt['fest'])}/SOUL.md"))
    agent = "ev-" + _hex8(welt["fest"])
    assert [b["match"]["peer"]["id"] for b in _bindungen(aus.config, agent)] == [str(CHAT_NEU)]
    gruppen = aus.config["channels"]["telegram"]["groups"]
    assert str(CHAT) not in gruppen and gruppen[str(CHAT_NEU)] == {"requireMention": False}
    assert _schluessel(db_admin, alter_schluessel)["widerrufen"] is not None
    neuer_schluessel = _auftrag(db_admin, neues)["schluessel_id"]
    assert _schluessel(db_admin, neuer_schluessel)["widerrufen"] is None
    assert schluessel.pruefen(app, _bearer(aus.config, agent)).id == neuer_schluessel


def test_strikt_nach_angelegt_nicht_nach_id(db_admin, db_app, welt, telegram):
    """Auch von Hand vertauschte Zeitstempel: der aeltere Auftrag desselben Agenten zuerst,
    ein juengerer wartet, solange ein aelterer noch offen ist."""
    app = db_app()
    anlegen = _gruppe(app, welt, telegram)
    abbau = _abbau_einfuegen(db_admin, _auftrag(db_admin, anlegen))
    db_admin.execute("update event.bereitstellung set angelegt = now() - interval '1 hour'"
                     " where id = %s", (abbau,))
    aus = _aus()
    assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == [abbau, anlegen]


# --- Verwaiste Agenten verlorener Rollen (Carry Task 7) ------------------------------


def test_verwaiste_agenten_verlorener_rollen_werden_abgebaut(db_admin, db_app, welt, telegram):
    app = db_app()
    p = _person(db_admin, "wechsel@example.org", betreiber=True)
    db_admin.execute("insert into event.mitgliedschaft values (%s, %s, 'leitung')",
                     (p, welt["a"]))
    vorher = Kontext(person_id=p, betreiber=True, rollen={welt["a"]: "leitung"})
    bo_auftrag = _direkt(app, vorher, telegram)
    aus = _aus()
    bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    bo, ld = "bo-" + _hex8(p), "ld-" + _hex8(p)
    assert _agent(aus.config, bo) is not None
    bo_schluessel = _auftrag(db_admin, bo_auftrag)["schluessel_id"]

    # Betreiber -> nur noch Leitung, derselbe Direktchat: RLS laesst den bo-Abbau beim
    # Bestaetigen nicht zu; der Arbeiter legt ihn VOR das ld-Anlegen (sonst CHAT_BELEGT).
    db_admin.execute("update event.person set betreiber = false where id = %s", (p,))
    nachher = Kontext(person_id=p, betreiber=False, rollen={welt["a"]: "leitung"})
    ld_auftrag = _direkt(app, nachher, telegram)
    assert not [a for a in _auftraege(db_admin) if a["art"] == "abbauen"]

    bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    [abbau] = [a for a in _auftraege(db_admin) if a["art"] == "abbauen"]
    assert (abbau["agent_id"], abbau["ziel"], abbau["chat_id"], abbau["status"]) == (
        bo, "betreiber", DIREKT, "erfolg")
    assert abbau["angelegt"] < _auftrag(db_admin, ld_auftrag)["angelegt"]
    _abgebaut(aus, bo, None)
    assert _schluessel(db_admin, bo_schluessel)["widerrufen"] is not None
    # Der neue Agent steht, am selben Chat, mit gueltigem Leitungs-Schluessel.
    assert _auftrag(db_admin, ld_auftrag)["status"] == "erfolg"
    assert [b["match"]["peer"]["id"] for b in _bindungen(aus.config, ld)] == [str(DIREKT)]
    info = schluessel.pruefen(app, _bearer(aus.config, ld))
    assert (info.art, info.person_id) == ("leitung", p)

    # Kein zweiter Abbau fuer denselben Agenten.
    bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    assert len([a for a in _auftraege(db_admin) if a["art"] == "abbauen"]) == 1

    # Leitung ganz verloren: auch der ld-Agent geht.
    db_admin.execute("delete from event.mitgliedschaft where person_id = %s", (p,))
    bereitstellung.lauf(db_admin, aus, mcp_url=MCP)
    _abgebaut(aus, ld, None)
    _fremdes_unveraendert(aus.config)


def test_verwaister_agent_ohne_auftrag_bleibt(db_admin, caplog):
    """Ein ev-/ld-/bo-Agent ohne jeden Auftrag ist nicht vom Arbeiter: nur Warnung."""
    aus = _aus()
    aus.config["agents"]["list"].append({"id": "ev-0123abcd"})
    with caplog.at_level(logging.WARNING):
        assert bereitstellung.lauf(db_admin, aus, mcp_url=MCP) == []
    assert _agent(aus.config, "ev-0123abcd") == {"id": "ev-0123abcd"}
    assert "ohne Auftrag" in caplog.text
    assert _auftraege(db_admin) == []


# --- Aufraeumen (Carry Task 2/3) ------------------------------------------------------


def test_lauf_raeumt_abgelaufene_marken_und_bestaetigungen_auf(db_admin, welt):
    sid, _ = schluessel.ausstellen(db_admin, art="leitung", bezeichnung="Test",
                                   person_id=welt["lea"])
    for wert, minuten in (("a", -1), ("b", 30)):
        h = wert * 64
        db_admin.execute("insert into event.marke values (%s, %s, %s, now() + make_interval("
                         "mins => %s))", (h, sid, welt["fest"], minuten))
        db_admin.execute("insert into event.bestaetigung (code_hash, schluessel_id, aktion,"
                         " ziel, ablauf) values (%s, %s, 'event_loeschen', '{}',"
                         " now() + make_interval(mins => %s))", (h, sid, minuten))
    bereitstellung.lauf(db_admin, _aus(), mcp_url=MCP)
    assert [z["wert_hash"][0] for z in db_admin.execute(
        "select wert_hash from event.marke").fetchall()] == ["b"]
    assert [z["code_hash"][0] for z in db_admin.execute(
        "select code_hash from event.bestaetigung").fetchall()] == ["b"]


# --- Zweites Tor: event_app setzt den Schluessel-Bezug nie selbst (007) ---------------


def test_event_app_darf_schluessel_bezug_nicht_setzen(db_admin, db_app, welt, telegram):
    app = db_app()
    auftrag = _auftrag(db_admin, _gruppe(app, welt, telegram))
    sid, _ = schluessel.ausstellen(db_admin, art="betreiber", bezeichnung="fremd",
                                   person_id=welt["bea"])
    leitung = db_app(firmen=[welt["a"]], person=welt["lea"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        leitung.execute(
            "insert into event.bereitstellung (art, ziel, event_id, person_id, chat_id,"
            " agent_id, schluessel_id) values ('anlegen', 'gruppe', %s, %s, %s, %s, %s)",
            (welt["fest"], welt["lea"], CHAT, auftrag["agent_id"], sid))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        leitung.execute(
            "insert into event.bereitstellung (art, ziel, event_id, person_id, chat_id,"
            " agent_id, status) values ('anlegen', 'gruppe', %s, %s, %s, %s, 'erfolg')",
            (welt["fest"], welt["lea"], CHAT, auftrag["agent_id"]))
    # Auch die Reihenfolge nicht: angelegt setzt allein der Default (clock_timestamp()).
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        leitung.execute(
            "insert into event.bereitstellung (art, ziel, event_id, person_id, chat_id,"
            " agent_id, angelegt) values ('anlegen', 'gruppe', %s, %s, %s, %s,"
            " now() - interval '1 day')",
            (welt["fest"], welt["lea"], CHAT, auftrag["agent_id"]))


# --- DockerAusfuehrer (ohne Docker) ---------------------------------------------------


class _FalschesRun:
    """Ersetzt subprocess.run: merkt sich argv/kwargs, antwortet je Befehl aus `antworten`."""

    def __init__(self, antworten: dict[str, bytes] | None = None, code: int = 0) -> None:
        self.aufrufe: list[dict[str, Any]] = []
        self.antworten = antworten or {}
        self.code = code

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.aufrufe.append({"argv": list(argv), **kwargs})
        import json
        auftrag = json.loads(kwargs["input"])
        stdout = kwargs["stdout"]
        if hasattr(stdout, "write"):
            stdout.write(self.antworten.get(auftrag["op"], b"{}"))
        return subprocess.CompletedProcess(argv, self.code)


def test_schluessel_nie_in_argv(monkeypatch, db_admin, db_app, welt, telegram):
    falsch = _FalschesRun({"lesen": b"{}", "datei_lesen": b'{"inhalt": null}'})
    monkeypatch.setattr(bereitstellung.subprocess, "run", falsch)
    klartext = "evk_" + "A" * 43
    auftrag = {"ziel": "gruppe", "agent_id": "ev-0123abcd", "chat_id": CHAT,
               "event_id": welt["fest"], "person_id": None}
    ev = db_admin.execute("select e.*, f.name as firma_name from event.event e join"
                          " event.firma f on f.id = e.firma_id where e.id = %s",
                          (welt["fest"],)).fetchone()
    aus = DockerAusfuehrer()
    for s in bereitstellung.schritte_anlegen(auftrag, schluessel_klartext=klartext,
                                             mcp_url=MCP, schema=events.schema_fuer("fest", None),
                                             event=ev):
        s.ausfuehren(aus)
    schreiben = [a for a in falsch.aufrufe if b"evk_" in a["input"]]
    assert schreiben, "der Schluessel muss ueber stdin kommen"
    for aufruf in falsch.aufrufe:
        assert aufruf["argv"][:5] == ["docker", "exec", "-i", "event-management-claw", "node"]
        assert not any("evk_" in teil for teil in aufruf["argv"])
        assert not any(str(CHAT) in teil for teil in aufruf["argv"])
        # Keine geerbten Pipes, fester Timeout.
        assert aufruf["stdout"] not in (subprocess.PIPE, None)
        assert aufruf["stderr"] == subprocess.DEVNULL
        assert aufruf["timeout"] == 60


def test_docker_ausfuehrer_fehler_ohne_werte(monkeypatch):
    falsch = _FalschesRun(code=1)
    monkeypatch.setattr(bereitstellung.subprocess, "run", falsch)
    aus = DockerAusfuehrer("event-management-claw")
    with pytest.raises(AusfuehrerFehler) as fehler:
        aus.config_schreiben("mcp.servers.ev-0123abcd", {"headers": {
            "Authorization": "Bearer evk_geheim"}})
    assert "evk_" not in str(fehler.value) and "evk_" not in repr(fehler.value)
    assert fehler.value.__cause__ is None and fehler.value.__context__ is None

    def zu_lange(argv: list[str], **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(argv, 60)

    monkeypatch.setattr(bereitstellung.subprocess, "run", zu_lange)
    with pytest.raises(AusfuehrerFehler):
        aus.config_lesen()


@pytest.mark.parametrize("pfad", ["", "a..b", "mcp.servers.x y", "a.$b", "agents/list",
                                  "__proto__.x", "a.constructor"])
def test_ausfuehrer_lehnt_kaputte_pfade_ab(pfad):
    for aus in (DockerAusfuehrer(), SpeicherAusfuehrer()):
        with pytest.raises(ValueError):
            aus.config_schreiben(pfad, 1)
        with pytest.raises(ValueError):
            aus.config_entfernen(pfad)


@pytest.mark.parametrize("pfad", ["/etc/passwd", "/home/node/.openclaw/openclaw.json",
                                  "/home/node/.openclaw/workspace-ev-0123abcd/../x",
                                  "workspace-ev-0123abcd/SOUL.md"])
def test_ausfuehrer_schreibt_nur_in_arbeitsbereiche(pfad):
    for aus in (DockerAusfuehrer(), SpeicherAusfuehrer()):
        with pytest.raises(ValueError):
            aus.datei_schreiben(pfad, "x")


def test_docker_container_name_geprueft():
    with pytest.raises(ValueError):
        DockerAusfuehrer("claw; rm -rf /")


# --- CLI und systemd-Dateien ---------------------------------------------------------


def test_systemd_dateien_nur_vorbereitet():
    from pathlib import Path

    deploy = Path(bereitstellung.__file__).resolve().parents[1] / "deploy"
    dienst = (deploy / "bereitstellung.service").read_text(encoding="utf-8")
    zeitgeber = (deploy / "bereitstellung.timer").read_text(encoding="utf-8")
    assert "Type=oneshot" in dienst
    assert "-m spaces.event_management.system.bereitstellung lauf" in dienst
    assert "EnvironmentFile=" in dienst and "evk_" not in dienst and "Bearer" not in dienst
    assert "OnUnitInactiveSec=20s" in zeitgeber and "Unit=bereitstellung.service" in zeitgeber


def test_cli_ohne_dsn_oder_mcp_url(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("EVENT_ADMIN_DSN_FILE", raising=False)
    monkeypatch.setenv("EVENT_MCP_URL", "http://event-mcp.example.org:8171")
    assert bereitstellung.main(["lauf"]) == 2
    datei = tmp_path / "dsn"
    datei.write_text("host=127.0.0.1 port=1 user=x dbname=y", encoding="utf-8")
    monkeypatch.setenv("EVENT_ADMIN_DSN_FILE", str(datei))
    monkeypatch.delenv("EVENT_MCP_URL", raising=False)
    assert bereitstellung.main(["lauf"]) == 2
    fehler = capsys.readouterr().err
    assert "EVENT_MCP_URL" in fehler and "host=" not in fehler
