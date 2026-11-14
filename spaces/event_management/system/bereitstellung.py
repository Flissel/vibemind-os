"""Bereitstellungs-Arbeiter: bestaetigte Telegram-Verbindungen -> OpenClaw-Agenten.

Ein `anlegen`-Auftrag (aus `verbinden.bestaetigen`) wird zu einem Agenten im Container
`event-management-claw`: Arbeitsbereich mit SOUL.md, MCP-Server `<agent>` mit eigenem
Schluessel, Agent mit nur den eigenen Werkzeugen (`<agent>__*`), Binding an den Chat und
(Gruppe) ``requireMention: false``. Ein `abbauen`-Auftrag (neue Gruppe ersetzt alte,
Archivieren/Loeschen des Events, verwaister Agent) widerruft den Schluessel und entfernt
Binding, Agent, Server und Gruppen-Eintrag; der Arbeitsbereich wird nur umbenannt
(``…-abgebaut-<datum>``), nie geloescht.

Ablauf von `lauf` (Admin-DSN, autocommit-Verbindung). Nur ein Lauf zugleich: haelt schon
ein anderer die Advisory-Sperre SPERRE, endet der Lauf sofort und still.

1. Haengende Auftraege (``laeuft`` seit mehr als HAENGT_MINUTEN) werden ``fehler``; ein
   dabei schon ausgestellter Schluessel wird widerrufen, der Teilstand eines `anlegen`
   abgeraeumt (wie unten).
2. Verwaiste Agenten: ``ev-/ld-/bo-``-Agenten in der Config, die kein bestaetigter Chat mehr
   traegt (Event archiviert/geloescht, Verbindung verworfen, Rolle verloren: Person nicht
   mehr aktiv, keine Leitung mehr bzw. kein Betreiber mehr; oder ihr `anlegen` scheiterte
   und das Abraeumen blieb unvollstaendig), bekommen einen
   `abbauen`-Auftrag, nach dem Vorbild ihres letzten `anlegen`. Einmal: gibt es schon einen
   juengeren Abbau (auch einen gescheiterten), wird kein weiterer angelegt. Wartet schon
   ein `anlegen` auf denselben Chat (Betreiber -> Leitung im selben Direktchat), liegt der
   Abbau direkt davor, sonst scheiterte das Anlegen am noch gebundenen Chat.
3. Offene Auftraege strikt nach ``angelegt`` (dann id); ein Auftrag wartet, solange ein
   aelterer desselben Agenten offen ist oder laeuft (``for update skip locked``). So laeuft
   der Abbau von ``ev-X`` immer vor dem Neuaufbau von ``ev-X``.
   - anlegen: nur wenn der Auftrag noch zu einer bestaetigten Verbindung gehoert und die
     Rolle noch passt (sonst ``fehler``, Schritt ``pruefen``, ohne Schluessel). Dann
     Schluessel ausstellen (Art event/leitung/betreiber, Bezug in ``schluessel_id``, beides
     in einer Transaktion), die Schritte ausfuehren; danach (Erfolg wie Fehler) die
     Schluessel frueherer `anlegen` desselben Agenten widerrufen, getrennt abgefangen (ein
     Fehler dort kippt keinen erfolgreichen Auftrag). Es zaehlt nur das juengste `anlegen`
     je Agent: scheitert es, ist der Agent verwaist. Scheitert ein Schritt: ``fehler`` mit
     Schrittname, der gerade ausgestellte Schluessel wird widerrufen und der Teilstand
     best effort abgeraeumt (Binding, Agent, Server, Gruppe; der Arbeitsbereich bleibt,
     unbenannt). Was dabei stehen bleibt, raeumt ein spaeterer Lauf als verwaisten Agenten
     ab. Kein automatischer Neuversuch.
   - abbauen: Schluessel aller frueheren `anlegen` dieses Agenten widerrufen (nur solche,
     die zum Ziel passen), dann die Schritte.
4. Abgelaufene Marken und Bestaetigungscodes werden geloescht.

Jeder Schritt hat einen Namen, liest den Ist-Zustand und schreibt nur bei Abweichung; der
Auftrag merkt sich den zuletzt erreichten Schritt (bei Fehler den gescheiterten).

Schluessel und Bot-Tokens stehen nie in argv, Logs, Fehlertexten oder einem repr: der
DockerAusfuehrer schickt Werte nur ueber stdin an ``node`` im Container (wie
``deploy/anbinden.sh``), Ausgaben gehen in eine temporaere Datei, nie in eine geerbte Pipe.

CLI (Admin-DSN aus der Datei in ``EVENT_ADMIN_DSN_FILE``, ``EVENT_MCP_URL`` = Basis von
event-mcp aus Sicht des Containers, ``EVENT_CLAW_CONTAINER`` Vorgabe event-management-claw)::

    python -m spaces.event_management.system.bereitstellung lauf
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from spaces.event_management.system import events, schluessel, soul
from spaces.event_management.system.kategorien import WirksamesSchema
from spaces.event_management.system.kontext import NichtGefunden

log = logging.getLogger(__name__)

CONTAINER = "event-management-claw"
ARBEITSBEREICH = "/home/node/.openclaw/workspace-"
TIMEOUT_SEKUNDEN = 60
HAENGT_MINUTEN = 10
NICHT_MEHR_BESTAETIGT = "Verbindung nicht mehr bestätigt oder Rolle verloren"
HAENGT = f"Abgebrochen: lief länger als {HAENGT_MINUTEN} Minuten"
CHAT_BELEGT = "Chat ist schon an einen anderen Agenten gebunden"
# Schluessel der Advisory-Sperre: nur ein Lauf zugleich (Timer und Handaufruf).
SPERRE = 7_406_315_001

AGENT_MUSTER = re.compile(r"(ev|ld|bo)-[0-9a-f]{8}")
_SEGMENT = re.compile(r"[A-Za-z0-9_*-]+")
_VERBOTENE_SEGMENTE = frozenset({"__proto__", "constructor", "prototype"})
_CONTAINER_MUSTER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_ARBEITSBEREICH_MUSTER = re.compile(r"/home/node/\.openclaw/workspace-(?:ev|ld|bo)-[0-9a-f]{8}")
_DATEI_MUSTER = re.compile(_ARBEITSBEREICH_MUSTER.pattern + r"/[A-Za-z0-9_-]{1,64}\.md")
_ABGEBAUT_MUSTER = re.compile(_ARBEITSBEREICH_MUSTER.pattern + r"-abgebaut-\d{8}-\d{6}")
_SCHLUESSEL_ART = {"gruppe": "event", "leitung": "leitung", "betreiber": "betreiber"}


class AusfuehrerFehler(Exception):
    """Ein Zugriff auf die OpenClaw-Config bzw. den Container scheiterte. Die Meldung ist fest
    und enthaelt nie Werte (Schluessel, Chat-IDs)."""


class BereitstellungFehler(Exception):
    """Fachlicher Abbruch eines Schritts (feste Meldung ohne Werte)."""


# --- Ausfuehrer ---------------------------------------------------------------------


class Ausfuehrer(Protocol):
    """Zugriff auf die OpenClaw-Config und die Arbeitsbereiche im Container. Pfade in der
    Config sind punktgetrennt (``mcp.servers.ev-0123abcd``); Werte gehen nie in argv."""

    def config_lesen(self) -> dict[str, Any]: ...

    def config_schreiben(self, pfad: str, wert: object) -> None: ...

    def config_entfernen(self, pfad: str) -> None: ...

    def datei_lesen(self, pfad_im_container: str) -> str | None: ...

    def datei_schreiben(self, pfad_im_container: str, inhalt: str) -> None: ...

    def umbenennen(self, alt: str, neu: str) -> bool: ...


def _teile(pfad: str) -> list[str]:
    """Config-Pfad in Segmente; nur [A-Za-z0-9_*-], keine leeren oder Prototyp-Segmente."""
    if not isinstance(pfad, str) or not pfad:
        raise ValueError("Config-Pfad ungültig")
    teile = pfad.split(".")
    for teil in teile:
        if not _SEGMENT.fullmatch(teil) or teil in _VERBOTENE_SEGMENTE:
            raise ValueError("Config-Pfad ungültig")
    return teile


def _datei_pruefen(pfad: str) -> None:
    if not isinstance(pfad, str) or not _DATEI_MUSTER.fullmatch(pfad):
        raise ValueError("Datei liegt nicht in einem Agenten-Arbeitsbereich")


def _umbenennen_pruefen(alt: str, neu: str) -> None:
    if (not isinstance(alt, str) or not isinstance(neu, str)
            or not _ARBEITSBEREICH_MUSTER.fullmatch(alt) or not _ABGEBAUT_MUSTER.fullmatch(neu)
            or not neu.startswith(alt + "-abgebaut-")):
        raise ValueError("Arbeitsbereich ungültig")


@dataclass
class SpeicherAusfuehrer:
    """In-Memory-Config fuer Tests. `scheitern` enthaelt Methodennamen
    (``config_schreiben``) oder ``<methode>:<pfad>`` (``config_schreiben:bindings``); ein
    Treffer wirft AusfuehrerFehler. `schreibvorgaenge` protokolliert (methode, pfad)."""

    # repr=False: die Config enthaelt die Bearer-Schluessel der Agenten.
    config: dict[str, Any] = field(default_factory=dict, repr=False)
    dateien: dict[str, str] = field(default_factory=dict, repr=False)
    scheitern: set[str] = field(default_factory=set)
    schreibvorgaenge: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.config = copy.deepcopy(self.config)
        self.dateien = dict(self.dateien)

    def _vielleicht_scheitern(self, methode: str, ziel: str) -> None:
        if methode in self.scheitern or f"{methode}:{ziel}" in self.scheitern:
            raise AusfuehrerFehler(f"{methode} fehlgeschlagen")

    def config_lesen(self) -> dict[str, Any]:
        self._vielleicht_scheitern("config_lesen", "")
        return copy.deepcopy(self.config)

    def config_schreiben(self, pfad: str, wert: object) -> None:
        teile = _teile(pfad)
        self._vielleicht_scheitern("config_schreiben", pfad)
        knoten = self.config
        for teil in teile[:-1]:
            weiter = knoten.setdefault(teil, {})
            if not isinstance(weiter, dict):
                raise AusfuehrerFehler("Config-Pfad fuehrt durch keinen Abschnitt")
            knoten = weiter
        knoten[teile[-1]] = copy.deepcopy(wert)
        self.schreibvorgaenge.append(("config_schreiben", pfad))

    def config_entfernen(self, pfad: str) -> None:
        teile = _teile(pfad)
        self._vielleicht_scheitern("config_entfernen", pfad)
        knoten: Any = self.config
        for teil in teile[:-1]:
            knoten = knoten.get(teil) if isinstance(knoten, dict) else None
        if isinstance(knoten, dict):
            knoten.pop(teile[-1], None)
        self.schreibvorgaenge.append(("config_entfernen", pfad))

    def datei_lesen(self, pfad_im_container: str) -> str | None:
        _datei_pruefen(pfad_im_container)
        self._vielleicht_scheitern("datei_lesen", pfad_im_container)
        return self.dateien.get(pfad_im_container)

    def datei_schreiben(self, pfad_im_container: str, inhalt: str) -> None:
        _datei_pruefen(pfad_im_container)
        self._vielleicht_scheitern("datei_schreiben", pfad_im_container)
        self.dateien[pfad_im_container] = inhalt
        self.schreibvorgaenge.append(("datei_schreiben", pfad_im_container))

    def umbenennen(self, alt: str, neu: str) -> bool:
        _umbenennen_pruefen(alt, neu)
        self._vielleicht_scheitern("umbenennen", alt)
        betroffen = [p for p in self.dateien if p.startswith(alt + "/")]
        if not betroffen:
            return False
        if any(p.startswith(neu + "/") for p in self.dateien):
            raise AusfuehrerFehler("Ziel des Umbenennens existiert schon")
        for p in betroffen:
            self.dateien[neu + p[len(alt):]] = self.dateien.pop(p)
        self.schreibvorgaenge.append(("umbenennen", alt))
        return True


# node im Container: liest einen JSON-Auftrag von stdin, schreibt die Config bzw. Datei,
# Antworten (nur lesen/datei_lesen/umbenennen) auf stdout. Exit != 0 bei Fehler, ohne
# Ausgabe von Werten. Die Pfade prueft Python vorher; node prueft sie noch einmal.
_NODE = r"""
const fs = require('fs');
const path = require('path');
const CFG = '/home/node/.openclaw/openclaw.json';
const VERBOTEN = new Set(['__proto__', 'constructor', 'prototype']);
const BEREICH = /^\/home\/node\/\.openclaw\/workspace-(ev|ld|bo)-[0-9a-f]{8}$/;
const DATEI = /^\/home\/node\/\.openclaw\/workspace-(ev|ld|bo)-[0-9a-f]{8}\/[A-Za-z0-9_-]{1,64}\.md$/;
const ABGEBAUT = /^\/home\/node\/\.openclaw\/workspace-(ev|ld|bo)-[0-9a-f]{8}-abgebaut-\d{8}-\d{6}$/;
function lesen() { return JSON.parse(fs.readFileSync(CFG, 'utf8')); }
// Atomar: Temp-Datei im selben Verzeichnis (0600), dann rename. Das Gateway liest
// openclaw.json beim Neuladen; es sieht so nie eine halb geschriebene Datei.
function schreiben(c) {
  const tmp = CFG + '.tmp-' + process.pid;
  try {
    fs.writeFileSync(tmp, JSON.stringify(c, null, 2) + '\n', { mode: 0o600 });
    fs.chmodSync(tmp, 0o600);
    fs.renameSync(tmp, CFG);
  } catch (e) {
    try { fs.unlinkSync(tmp); } catch (e2) { /* schon weg */ }
    throw e;
  }
}
function pfadOk(p) {
  return Array.isArray(p) && p.length > 0
    && p.every((k) => typeof k === 'string' && /^[A-Za-z0-9_*-]+$/.test(k) && !VERBOTEN.has(k));
}
function eigen(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
let a;
try { a = JSON.parse(fs.readFileSync(0, 'utf8')); } catch (e) { process.exit(3); }
try {
  if (a.op === 'lesen') {
    process.stdout.write(JSON.stringify(lesen()));
  } else if (a.op === 'setzen' || a.op === 'entfernen') {
    if (!pfadOk(a.pfad)) process.exit(4);
    const c = lesen();
    let n = c;
    for (const k of a.pfad.slice(0, -1)) {
      if (!eigen(n, k) || n[k] === null) {
        if (a.op === 'entfernen') { n = null; break; }
        n[k] = {};
      } else if (typeof n[k] !== 'object' || Array.isArray(n[k])) {
        process.exit(6);
      }
      n = n[k];
    }
    const letzter = a.pfad[a.pfad.length - 1];
    if (a.op === 'setzen') {
      n[letzter] = a.wert;
      schreiben(c);
    } else if (n && eigen(n, letzter)) {
      delete n[letzter];
      schreiben(c);
    }
  } else if (a.op === 'datei_lesen') {
    if (!DATEI.test(a.pfad)) process.exit(4);
    const inhalt = fs.existsSync(a.pfad) ? fs.readFileSync(a.pfad, 'utf8') : null;
    process.stdout.write(JSON.stringify({ inhalt }));
  } else if (a.op === 'datei_schreiben') {
    if (!DATEI.test(a.pfad) || typeof a.inhalt !== 'string') process.exit(4);
    fs.mkdirSync(path.dirname(a.pfad), { recursive: true });
    fs.writeFileSync(a.pfad, a.inhalt, { mode: 0o644 });
  } else if (a.op === 'umbenennen') {
    if (!BEREICH.test(a.alt) || !ABGEBAUT.test(a.neu) || !a.neu.startsWith(a.alt + '-')) process.exit(4);
    if (!fs.existsSync(a.alt)) {
      process.stdout.write(JSON.stringify({ umbenannt: false }));
    } else if (fs.existsSync(a.neu)) {
      process.exit(5);
    } else {
      fs.renameSync(a.alt, a.neu);
      process.stdout.write(JSON.stringify({ umbenannt: true }));
    }
  } else {
    process.exit(2);
  }
} catch (e) {
  process.exit(1);
}
"""


class DockerAusfuehrer:
    """``docker exec -i <container> node -e <festes Skript>``; der Auftrag samt Werten kommt
    als JSON ueber stdin. stdout geht in eine temporaere Datei, stderr nach DEVNULL (keine
    geerbte Pipe), Timeout TIMEOUT_SEKUNDEN."""

    def __init__(self, container: str = CONTAINER) -> None:
        if not isinstance(container, str) or not _CONTAINER_MUSTER.fullmatch(container):
            raise ValueError("Container-Name ungültig")
        self.container = container

    def __repr__(self) -> str:
        return f"DockerAusfuehrer(container={self.container!r})"

    def _ausfuehren(self, auftrag: Mapping[str, object]) -> str:
        daten = json.dumps(auftrag).encode("utf-8")
        argv = ["docker", "exec", "-i", self.container, "node", "-e", _NODE]
        with tempfile.TemporaryFile() as ausgabe:
            try:
                ergebnis = subprocess.run(
                    argv, input=daten, stdout=ausgabe, stderr=subprocess.DEVNULL,
                    timeout=TIMEOUT_SEKUNDEN, check=False)
            except subprocess.TimeoutExpired:
                raise AusfuehrerFehler("Container antwortet nicht (Zeitüberschreitung)") from None
            except OSError:
                raise AusfuehrerFehler("docker ist nicht ausführbar") from None
            if ergebnis.returncode != 0:
                raise AusfuehrerFehler(f"Container-Aufruf endete mit {ergebnis.returncode}")
            ausgabe.seek(0)
            return ausgabe.read().decode("utf-8", "replace")

    def _json(self, auftrag: Mapping[str, object]) -> Any:
        text = self._ausfuehren(auftrag)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise AusfuehrerFehler("Antwort des Containers unlesbar") from None

    def config_lesen(self) -> dict[str, Any]:
        wert = self._json({"op": "lesen"})
        if not isinstance(wert, dict):
            raise AusfuehrerFehler("Config ist kein Objekt")
        return wert

    def config_schreiben(self, pfad: str, wert: object) -> None:
        self._ausfuehren({"op": "setzen", "pfad": _teile(pfad), "wert": wert})

    def config_entfernen(self, pfad: str) -> None:
        self._ausfuehren({"op": "entfernen", "pfad": _teile(pfad)})

    def datei_lesen(self, pfad_im_container: str) -> str | None:
        _datei_pruefen(pfad_im_container)
        wert = self._json({"op": "datei_lesen", "pfad": pfad_im_container})
        inhalt = wert.get("inhalt") if isinstance(wert, dict) else None
        return inhalt if isinstance(inhalt, str) else None

    def datei_schreiben(self, pfad_im_container: str, inhalt: str) -> None:
        _datei_pruefen(pfad_im_container)
        self._ausfuehren({"op": "datei_schreiben", "pfad": pfad_im_container, "inhalt": inhalt})

    def umbenennen(self, alt: str, neu: str) -> bool:
        _umbenennen_pruefen(alt, neu)
        wert = self._json({"op": "umbenennen", "alt": alt, "neu": neu})
        return bool(isinstance(wert, dict) and wert.get("umbenannt"))


# --- Schritte -----------------------------------------------------------------------


@dataclass(frozen=True)
class Schritt:
    """Ein benannter, idempotenter Schritt; `ausfuehren` liefert True, wenn er etwas
    geaendert hat. Der repr nennt nur den Namen (der Schritt kann einen Schluessel kennen)."""

    name: str
    ausfuehren: Callable[[Ausfuehrer], bool] = field(repr=False, compare=False)


def _wert(config: Mapping[str, Any], *teile: str) -> Any:
    knoten: Any = config
    for teil in teile:
        if not isinstance(knoten, Mapping) or teil not in knoten:
            return None
        knoten = knoten[teil]
    return knoten


def _liste(config: Mapping[str, Any], *teile: str) -> list[Any]:
    wert = _wert(config, *teile)
    return list(wert) if isinstance(wert, list) else []


def _ist_agent(eintrag: object, agent: str, schluessel_name: str) -> bool:
    return isinstance(eintrag, Mapping) and eintrag.get(schluessel_name) == agent


def _peer(binding: object) -> tuple[str, str] | None:
    """(kind, id) eines Telegram-Bindings, sonst None."""
    match = binding.get("match") if isinstance(binding, Mapping) else None
    if not isinstance(match, Mapping) or match.get("channel") != "telegram":
        return None
    peer = match.get("peer")
    if not isinstance(peer, Mapping):
        return None
    return str(peer.get("kind")), str(peer.get("id"))


def _agent_und_chat(auftrag: Mapping[str, Any]) -> tuple[str, str, str]:
    agent = str(auftrag["agent_id"])
    if not AGENT_MUSTER.fullmatch(agent):
        raise ValueError("Agent-ID ungültig")
    ziel = str(auftrag["ziel"])
    if ziel not in _SCHLUESSEL_ART:
        raise ValueError("Ziel ungültig")
    chat = auftrag["chat_id"]
    if isinstance(chat, bool) or not isinstance(chat, int):
        raise ValueError("Chat-ID ungültig")
    return agent, ziel, str(chat)


def schritte_anlegen(auftrag: Mapping[str, Any], *, schluessel_klartext: str, mcp_url: str,
                     schema: WirksamesSchema | None,
                     event: Mapping[str, Any] | None) -> list[Schritt]:
    """Schritte fuer einen `anlegen`-Auftrag (ziel, agent_id, chat_id; bei leitung/betreiber
    optional person_name). Gruppe braucht `event` (mit firma_name) und `schema`.

    Reihenfolge: arbeitsbereich, server, agent, binding, (gruppe). Das Binding kommt erst,
    wenn Agent und Server stehen; ``requireMention: false`` erst danach, damit der Empfang
    in der Gruppe nie ohne Erwaehnung antwortet."""
    agent, ziel, chat = _agent_und_chat(auftrag)
    if not isinstance(mcp_url, str) or not mcp_url.startswith(("http://", "https://")):
        raise ValueError("MCP-Adresse ungültig")
    if not isinstance(schluessel_klartext, str) or not schluessel_klartext.startswith("evk_"):
        raise ValueError("Schlüssel ungültig")
    bereich = ARBEITSBEREICH + agent
    soul_datei = f"{bereich}/SOUL.md"
    if ziel == "gruppe":
        if event is None or schema is None:
            raise ValueError("Gruppe braucht Event und Schema")
        inhalt = soul.gruppe(event, schema, str(event.get("firma_name") or ""))
        art = "group"
    else:
        inhalt = soul.leitung(str(auftrag.get("person_name") or ""),
                              betreiber=ziel == "betreiber")
        art = "direct"
    server = {"url": mcp_url, "transport": "streamable-http",
              "headers": {"Authorization": f"Bearer {schluessel_klartext}"}}
    eintrag = {"id": agent, "workspace": bereich, "tools": {"allow": [f"{agent}__*"]}}
    binding: dict[str, Any] = {"agentId": agent, "match": {
        "channel": "telegram", "peer": {"kind": art, "id": chat}}}
    if ziel != "gruppe":
        binding["session"] = {"dmScope": "per-peer"}

    def arbeitsbereich(aus: Ausfuehrer) -> bool:
        if aus.datei_lesen(soul_datei) == inhalt:
            return False
        aus.datei_schreiben(soul_datei, inhalt)
        return True

    def server_setzen(aus: Ausfuehrer) -> bool:
        if _wert(aus.config_lesen(), "mcp", "servers", agent) == server:
            return False
        aus.config_schreiben(f"mcp.servers.{agent}", server)
        return True

    def agent_setzen(aus: Ausfuehrer) -> bool:
        liste = _liste(aus.config_lesen(), "agents", "list")
        eigene = [a for a in liste if _ist_agent(a, agent, "id")]
        if eigene == [eintrag]:
            return False
        aus.config_schreiben("agents.list",
                             [a for a in liste if not _ist_agent(a, agent, "id")] + [eintrag])
        return True

    def binding_setzen(aus: Ausfuehrer) -> bool:
        liste = _liste(aus.config_lesen(), "bindings")
        if any(_peer(b) == (art, chat) and not _ist_agent(b, agent, "agentId") for b in liste):
            raise BereitstellungFehler(CHAT_BELEGT)
        eigene = [b for b in liste if _ist_agent(b, agent, "agentId")]
        if eigene == [binding]:
            return False
        aus.config_schreiben("bindings",
                             [b for b in liste if not _ist_agent(b, agent, "agentId")] + [binding])
        return True

    def gruppe_setzen(aus: Ausfuehrer) -> bool:
        gruppe = _wert(aus.config_lesen(), "channels", "telegram", "groups", chat)
        if isinstance(gruppe, Mapping) and gruppe.get("requireMention") is False:
            return False
        aus.config_schreiben(f"channels.telegram.groups.{chat}.requireMention", False)
        return True

    schritte = [Schritt("arbeitsbereich", arbeitsbereich), Schritt("server", server_setzen),
                Schritt("agent", agent_setzen), Schritt("binding", binding_setzen)]
    if ziel == "gruppe":
        schritte.append(Schritt("gruppe", gruppe_setzen))
    return schritte


def schritte_abbauen(auftrag: Mapping[str, Any], *, datum: datetime) -> list[Schritt]:
    """Schritte fuer einen `abbauen`-Auftrag: binding, agent, server, (gruppe),
    arbeitsbereich. Entfernt auch Teilstaende; ein Gruppen-Eintrag bleibt, solange noch ein
    anderes Binding auf diesen Chat zeigt. Den Schluessel widerruft `lauf` vorher."""
    agent, ziel, chat = _agent_und_chat(auftrag)
    bereich = ARBEITSBEREICH + agent
    # Gruppen-Chats dieses Agenten: der des Auftrags und die seiner Bindings (falls ein
    # frueherer Lauf an einen anderen Chat gebunden hatte).
    chats: set[str] = {chat} if ziel == "gruppe" else set()

    def binding_entfernen(aus: Ausfuehrer) -> bool:
        liste = _liste(aus.config_lesen(), "bindings")
        eigene = [b for b in liste if _ist_agent(b, agent, "agentId")]
        if not eigene:
            return False
        for b in eigene:
            peer = _peer(b)
            if peer is not None and peer[0] == "group":
                chats.add(peer[1])
        aus.config_schreiben("bindings", [b for b in liste if not _ist_agent(b, agent, "agentId")])
        return True

    def agent_entfernen(aus: Ausfuehrer) -> bool:
        liste = _liste(aus.config_lesen(), "agents", "list")
        if not any(_ist_agent(a, agent, "id") for a in liste):
            return False
        aus.config_schreiben("agents.list", [a for a in liste if not _ist_agent(a, agent, "id")])
        return True

    def server_entfernen(aus: Ausfuehrer) -> bool:
        server = _wert(aus.config_lesen(), "mcp", "servers")
        if not isinstance(server, Mapping) or agent not in server:
            return False
        aus.config_entfernen(f"mcp.servers.{agent}")
        return True

    def gruppe_entfernen(aus: Ausfuehrer) -> bool:
        config = aus.config_lesen()
        gruppen = _wert(config, "channels", "telegram", "groups")
        if not isinstance(gruppen, Mapping):
            return False
        gebunden = {p[1] for p in map(_peer, _liste(config, "bindings"))
                    if p is not None and p[0] == "group"}
        geaendert = False
        for c in sorted(chats):
            if c in gruppen and c not in gebunden and c != "*":
                aus.config_entfernen(f"channels.telegram.groups.{c}")
                geaendert = True
        return geaendert

    def arbeitsbereich_umbenennen(aus: Ausfuehrer) -> bool:
        return aus.umbenennen(bereich, f"{bereich}-abgebaut-{datum:%Y%m%d-%H%M%S}")

    schritte = [Schritt("binding", binding_entfernen), Schritt("agent", agent_entfernen),
                Schritt("server", server_entfernen)]
    if ziel == "gruppe":
        schritte.append(Schritt("gruppe", gruppe_entfernen))
    schritte.append(Schritt("arbeitsbereich", arbeitsbereich_umbenennen))
    return schritte


# --- Datenbank ----------------------------------------------------------------------

# Soll-Zustand eines anlegen-Auftrags `b`: nicht gescheitert, eine bestaetigte Verbindung
# mit derselben Chat-ID zum selben Ziel, und die Rolle passt noch (Gruppe: Event nicht
# archiviert/Legacy; leitung: Person aktiv und irgendwo Leitung; betreiber: Person aktiv
# und Betreiber). Es zaehlt nur das juengste `anlegen` je Agent: scheitert ein erneutes
# Anlegen, traegt ein frueheres erfolgreiches den Agenten nicht mehr. Ein gescheitertes
# `anlegen` traegt keinen Agenten: was davon noch in der Config steht, ist verwaist.
_SOLL = """
    b.art = 'anlegen'
    and b.status in ('offen', 'laeuft', 'erfolg')
    and not exists (
        select 1 from event.bereitstellung n
        where n.agent_id = b.agent_id and n.art = 'anlegen'
          and (n.angelegt, n.id) > (b.angelegt, b.id))
    and exists (
        select 1 from event.verbindung v
        where v.status = 'bestaetigt' and v.chat_id = b.chat_id
          and case when b.ziel = 'gruppe'
                   then v.art = 'gruppe' and v.event_id = b.event_id
                   else v.art = 'direkt' and v.person_id = b.person_id
              end)
    and case b.ziel
        when 'gruppe' then exists (
            select 1 from event.event e
            where e.id = b.event_id and e.status <> 'archiviert' and e.quelle <> 'legacy')
        when 'leitung' then exists (
            select 1 from event.person p
            where p.id = b.person_id and p.aktiv and exists (
                select 1 from event.mitgliedschaft m
                where m.person_id = p.id and m.rolle = 'leitung'))
        else exists (
            select 1 from event.person p
            where p.id = b.person_id and p.aktiv and p.betreiber)
    end
"""


def _schritt_merken(conn: psycopg.Connection[Any], auftrag_id: UUID, schritt: str) -> None:
    conn.execute("update event.bereitstellung set schritt = %s where id = %s",
                 (schritt, auftrag_id))


def _beenden(conn: psycopg.Connection[Any], auftrag_id: UUID, status: str, schritt: str | None,
             fehler: str | None) -> None:
    conn.execute("update event.bereitstellung set status = %s, schritt = %s, fehler = %s,"
                 " beendet = now() where id = %s", (status, schritt, fehler, auftrag_id))


def _fehlertext(fehler: BaseException) -> str:
    """Nur eigene, feste Meldungen; alles andere nur als Typname (koennte Werte tragen)."""
    if isinstance(fehler, (AusfuehrerFehler, BereitstellungFehler)):
        return str(fehler)[:200]
    return type(fehler).__name__


def _widerrufen(conn: psycopg.Connection[Any], schluessel_id: UUID) -> None:
    try:
        schluessel.widerrufen(conn, schluessel_id)
    except NichtGefunden:
        pass  # mit dem Event geloescht (Kaskade): ohnehin ungueltig


# Schritte, nach denen ein gescheitertes `anlegen` schon etwas im Container hinterlassen
# haben kann (der gescheiterte Schritt selbst zaehlt mit).
_SCHRITTE_IM_CONTAINER = frozenset({"arbeitsbereich", "server", "agent", "binding", "gruppe"})


def _teilstand_abraeumen(ausfuehrer: Ausfuehrer, auftrag: Mapping[str, Any]) -> None:
    """Best effort nach gescheitertem oder haengendem `anlegen`: Binding, Agent, Server und
    (Gruppe) den Gruppen-Eintrag wieder entfernen, damit kein Agent mit widerrufenem
    Schluessel im Chat steht. Der Arbeitsbereich bleibt, wie er ist (nicht umbenannt). Jeder
    Schritt wird versucht, Fehler nur protokolliert (Typname); was stehen bleibt, raeumt ein
    spaeterer Lauf als verwaisten Agenten ab."""
    if auftrag.get("schritt") not in _SCHRITTE_IM_CONTAINER:
        return
    try:
        schritte = schritte_abbauen(auftrag, datum=datetime.now(timezone.utc))
    except ValueError:
        log.warning("Teilstand von Auftrag %s nicht abraeumbar (Daten ungueltig)",
                    auftrag["id"])
        return
    for s in schritte:
        if s.name == "arbeitsbereich":
            continue
        try:
            s.ausfuehren(ausfuehrer)
        except Exception as fehler:  # noqa: BLE001 - best effort, weiter mit dem naechsten
            log.warning("Teilstand von Auftrag %s: Entfernen (%s) gescheitert (%s)",
                        auftrag["id"], s.name, type(fehler).__name__)


def _haengende_beenden(conn: psycopg.Connection[Any], ausfuehrer: Ausfuehrer,
                       jetzt: datetime | None) -> None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "update event.bereitstellung set status = 'fehler', fehler = %s, beendet = now()"
            " where status = 'laeuft'"
            " and gestartet < coalesce(%s::timestamptz, now()) - make_interval(mins => %s)"
            " returning id, art, ziel, event_id, person_id, chat_id, agent_id, schritt,"
            " schluessel_id",
            (HAENGT, jetzt, HAENGT_MINUTEN))
        haengend = cur.fetchall()
    for zeile in haengend:
        log.warning("Auftrag %s haengt, auf fehler gesetzt", zeile["id"])
        if zeile["art"] == "anlegen":
            if zeile["schluessel_id"] is not None:
                _widerrufen(conn, zeile["schluessel_id"])
            _teilstand_abraeumen(ausfuehrer, zeile)


def _agenten_in_config(config: Mapping[str, Any]) -> set[str]:
    """ev-/ld-/bo-Agenten aus agents.list, mcp.servers und bindings (auch Teilstaende)."""
    namen: set[str] = set()
    for eintrag in _liste(config, "agents", "list"):
        if isinstance(eintrag, Mapping):
            namen.add(str(eintrag.get("id")))
    server = _wert(config, "mcp", "servers")
    if isinstance(server, Mapping):
        namen.update(str(n) for n in server)
    for binding in _liste(config, "bindings"):
        if isinstance(binding, Mapping):
            namen.add(str(binding.get("agentId")))
    return {n for n in namen if AGENT_MUSTER.fullmatch(n)}


def _verwaiste_abbauen(conn: psycopg.Connection[Any], ausfuehrer: Ausfuehrer) -> None:
    """Legt `abbauen` fuer Agenten an, die in der Config stehen, aber keinen Soll-Zustand
    mehr haben (Carry Task 7: verlorene Rollen, geloeschte/archivierte Events)."""
    try:
        config = ausfuehrer.config_lesen()
    except AusfuehrerFehler as fehler:
        log.warning("Config nicht lesbar, keine Suche nach verwaisten Agenten (%s)", fehler)
        return
    vorhanden = _agenten_in_config(config)
    if not vorhanden:
        return
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"select distinct b.agent_id from event.bereitstellung b where {_SOLL}")
        soll = {z["agent_id"] for z in cur.fetchall()}
        for agent in sorted(vorhanden - soll):
            cur.execute("select 1 from event.bereitstellung where agent_id = %s"
                        " and status in ('offen', 'laeuft') limit 1", (agent,))
            if cur.fetchone() is not None:
                continue  # ein Auftrag fuer diesen Agenten steht schon an
            cur.execute("select ziel, event_id, person_id, chat_id, angelegt"
                        " from event.bereitstellung where agent_id = %s and art = 'anlegen'"
                        " order by angelegt desc, id desc limit 1", (agent,))
            vorbild = cur.fetchone()
            if vorbild is None:
                log.warning("Agent %s steht ohne Auftrag in der Config; nicht angefasst", agent)
                continue
            cur.execute("select 1 from event.bereitstellung where agent_id = %s"
                        " and art = 'abbauen' and angelegt > %s limit 1",
                        (agent, vorbild["angelegt"]))
            if cur.fetchone() is not None:
                continue  # schon einmal abgebaut (oder versucht): kein zweiter Auftrag
            # Wartet schon ein `anlegen` auf denselben Chat (Betreiber -> Leitung im selben
            # Direktchat, neues Event in derselben Gruppe), muss der Abbau davor liegen: sonst
            # scheitert das Anlegen am noch gebundenen Chat (CHAT_BELEGT).
            cur.execute(
                "insert into event.bereitstellung (art, ziel, event_id, person_id, chat_id,"
                " agent_id, angelegt) values ('abbauen', %s, %s, %s, %s, %s, coalesce("
                "   (select min(o.angelegt) - interval '1 microsecond'"
                "    from event.bereitstellung o"
                "    where o.art = 'anlegen' and o.status = 'offen' and o.chat_id = %s),"
                "   clock_timestamp()))",
                (vorbild["ziel"], vorbild["event_id"], vorbild["person_id"], vorbild["chat_id"],
                 agent, vorbild["chat_id"]))
            log.info("Abbau fuer verwaisten Agenten %s angelegt", agent)


def _naechster(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
    """Aeltester offener Auftrag, dessen Agent keinen aelteren offenen/laufenden hat; wird
    in derselben Transaktion auf laeuft gesetzt."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select b.id from event.bereitstellung b"
            " where b.status = 'offen' and not exists ("
            "   select 1 from event.bereitstellung o"
            "   where o.agent_id = b.agent_id and o.status in ('offen', 'laeuft')"
            "     and (o.angelegt, o.id) < (b.angelegt, b.id))"
            " order by b.angelegt, b.id limit 1 for update of b skip locked")
        zeile = cur.fetchone()
        if zeile is None:
            return None
        cur.execute(
            "update event.bereitstellung b set status = 'laeuft', gestartet = now(),"
            " schritt = null, fehler = null where b.id = %s"
            " returning b.id, b.art, b.ziel, b.event_id, b.person_id, b.chat_id, b.agent_id,"
            " b.angelegt, b.schritt", (zeile["id"],))
        return cur.fetchone()


def _anlegen(conn: psycopg.Connection[Any], ausfuehrer: Ausfuehrer, auftrag: dict[str, Any],
             *, mcp_url: str) -> None:
    auftrag_id: UUID = auftrag["id"]
    schritt = "pruefen"
    sid: UUID | None = None
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select exists (select 1 from event.bereitstellung b where b.id = %s"
                        f" and {_SOLL}) as ok", (auftrag_id,))
            gueltig = cur.fetchone()
            if gueltig is None or not gueltig["ok"]:
                _beenden(conn, auftrag_id, "fehler", schritt, NICHT_MEHR_BESTAETIGT)
                log.info("Auftrag %s uebersprungen: Verbindung oder Rolle weg", auftrag_id)
                return
            event: dict[str, Any] | None = None
            schema: WirksamesSchema | None = None
            if auftrag["ziel"] == "gruppe":
                cur.execute("select e.*, f.name as firma_name from event.event e"
                            " join event.firma f on f.id = e.firma_id where e.id = %s",
                            (auftrag["event_id"],))
                event = cur.fetchone()
                if event is None:
                    raise BereitstellungFehler("Event nicht gefunden")
                schema = events.schema_fuer(event["kategorie"], None)
            else:
                cur.execute("select name from event.person where id = %s",
                            (auftrag["person_id"],))
                person = cur.fetchone()
                auftrag = dict(auftrag, person_name=person["name"] if person else "")
        schritt = "schluessel"
        try:
            # Ausstellen und Bezug in einer Transaktion: nie ein Schluessel ohne Auftrag.
            with conn.transaction():
                neu_id, klartext = schluessel.ausstellen(
                    conn, art=_SCHLUESSEL_ART[auftrag["ziel"]],
                    bezeichnung=f"Bereitstellung {auftrag['agent_id']}",
                    gueltig_tage=schluessel.MAX_TAGE,
                    event_id=auftrag["event_id"] if auftrag["ziel"] == "gruppe" else None,
                    person_id=None if auftrag["ziel"] == "gruppe" else auftrag["person_id"])
                conn.execute("update event.bereitstellung set schluessel_id = %s,"
                             " schritt = %s where id = %s", (neu_id, schritt, auftrag_id))
        except (ValueError, NichtGefunden) as fehler:
            raise BereitstellungFehler(str(fehler)) from None
        sid = neu_id
        for s in schritte_anlegen(auftrag, schluessel_klartext=klartext, mcp_url=mcp_url,
                                  schema=schema, event=event):
            schritt = s.name
            s.ausfuehren(ausfuehrer)
            _schritt_merken(conn, auftrag_id, schritt)
        _beenden(conn, auftrag_id, "erfolg", schritt, None)
        log.info("Agent %s angelegt", auftrag["agent_id"])
    except Exception as fehler:  # noqa: BLE001 - jeder Fehler beendet den Auftrag
        if sid is not None:
            _widerrufen(conn, sid)
        _beenden(conn, auftrag_id, "fehler", schritt, _fehlertext(fehler))
        log.warning("Auftrag %s gescheitert bei %s (%s)", auftrag_id, schritt,
                    type(fehler).__name__)
        _teilstand_abraeumen(ausfuehrer, dict(auftrag, schritt=schritt))
    # Erfolg: der Agent traegt jetzt den neuen Schluessel. Fehler: der Agent ist abgeraeumt
    # oder verwaist. In beiden Faellen gelten die Schluessel frueherer Anlegen nicht mehr.
    # Getrennt abgefangen, damit ein Fehler hier einen erfolgreichen Auftrag nicht kippt.
    _fruehere_widerrufen(conn, auftrag)


def _fruehere_widerrufen(conn: psycopg.Connection[Any], auftrag: Mapping[str, Any]) -> None:
    try:
        _schluessel_widerrufen(conn, auftrag)
    except Exception as fehler:  # noqa: BLE001 - best effort, nur Typname ins Log
        log.warning("Fruehere Schluessel von %s nicht widerrufen (%s)", auftrag["agent_id"],
                    type(fehler).__name__)


def _schluessel_widerrufen(conn: psycopg.Connection[Any], auftrag: Mapping[str, Any]) -> None:
    """Widerruft die Schluessel aller frueheren `anlegen` dieses Agenten, die zum Ziel des
    Auftrags passen (Art und Event bzw. Person)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select distinct s.id from event.bereitstellung b"
            " join event.schluessel s on s.id = b.schluessel_id"
            " where b.agent_id = %s and b.art = 'anlegen' and b.angelegt < %s"
            "   and s.widerrufen is null"
            "   and case b.ziel"
            "       when 'gruppe' then s.art = 'event' and s.event_id = b.event_id"
            "       when 'leitung' then s.art = 'leitung' and s.person_id = b.person_id"
            "       else s.art = 'betreiber' and s.person_id = b.person_id end",
            (auftrag["agent_id"], auftrag["angelegt"]))
        ids = [z["id"] for z in cur.fetchall()]
    for sid in ids:
        _widerrufen(conn, sid)


def _abbauen(conn: psycopg.Connection[Any], ausfuehrer: Ausfuehrer, auftrag: dict[str, Any],
             *, jetzt: datetime | None) -> None:
    auftrag_id: UUID = auftrag["id"]
    schritt = "schluessel"
    try:
        _schluessel_widerrufen(conn, auftrag)
        _schritt_merken(conn, auftrag_id, schritt)
        datum = jetzt or datetime.now(timezone.utc)
        for s in schritte_abbauen(auftrag, datum=datum):
            schritt = s.name
            s.ausfuehren(ausfuehrer)
            _schritt_merken(conn, auftrag_id, schritt)
        _beenden(conn, auftrag_id, "erfolg", schritt, None)
        log.info("Agent %s abgebaut", auftrag["agent_id"])
    except Exception as fehler:  # noqa: BLE001 - jeder Fehler beendet den Auftrag
        _beenden(conn, auftrag_id, "fehler", schritt, _fehlertext(fehler))
        log.warning("Abbau %s gescheitert bei %s (%s)", auftrag_id, schritt,
                    type(fehler).__name__)


def _aufraeumen(conn: psycopg.Connection[Any], jetzt: datetime | None) -> None:
    """Abgelaufene Marken und Bestaetigungscodes loeschen (sie loesen ohnehin nichts mehr
    auf); Carry Task 2/3."""
    marken = conn.execute("delete from event.marke where ablauf < coalesce(%s::timestamptz,"
                          " now())", (jetzt,)).rowcount
    codes = conn.execute("delete from event.bestaetigung where ablauf < coalesce("
                         "%s::timestamptz, now())", (jetzt,)).rowcount
    if marken or codes:
        log.info("Aufgeraeumt: %s Marken, %s Bestaetigungscodes", marken, codes)


def lauf(conn_admin: psycopg.Connection[Any], ausfuehrer: Ausfuehrer, *, mcp_url: str,
         jetzt: datetime | None = None) -> list[UUID]:
    """Ein Lauf des Arbeiters (siehe Moduldoku). `conn_admin`: Admin-DSN, autocommit.
    Rueckgabe: ids der in diesem Lauf bearbeiteten Auftraege in Bearbeitungsreihenfolge.
    `jetzt` verschiebt nur die Zeitgrenzen (haengend, abgelaufen) fuer Tests. Haelt schon
    ein anderer Lauf die Sperre SPERRE, Rueckgabe [] ohne jede Aenderung."""
    with conn_admin.cursor(row_factory=dict_row) as cur:
        cur.execute("select pg_try_advisory_lock(%s) as frei", (SPERRE,))
        zeile = cur.fetchone()
    if zeile is None or not zeile["frei"]:
        log.info("Ein anderer Lauf ist aktiv; nichts zu tun")
        return []
    try:
        _haengende_beenden(conn_admin, ausfuehrer, jetzt)
        _verwaiste_abbauen(conn_admin, ausfuehrer)
        erledigt: list[UUID] = []
        while (auftrag := _naechster(conn_admin)) is not None:
            if auftrag["art"] == "anlegen":
                _anlegen(conn_admin, ausfuehrer, auftrag, mcp_url=mcp_url)
            else:
                _abbauen(conn_admin, ausfuehrer, auftrag, jetzt=jetzt)
            erledigt.append(auftrag["id"])
        _aufraeumen(conn_admin, jetzt)
        return erledigt
    finally:
        conn_admin.execute("select pg_advisory_unlock(%s)", (SPERRE,))


# --- CLI ----------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m spaces.event_management.system.bereitstellung")
    befehle = parser.add_subparsers(dest="befehl", required=True)
    befehle.add_parser("lauf", help="offene Bereitstellungs-Auftraege einmal abarbeiten")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _parser().parse_args(argv)
    try:
        dsn = schluessel._dsn_lesen()
    except schluessel._DsnFehlt as fehler:
        print(str(fehler), file=sys.stderr)
        return 2
    basis = os.environ.get("EVENT_MCP_URL", "").strip()
    if not basis.startswith(("http://", "https://")):
        print("EVENT_MCP_URL fehlt oder ist keine http(s)-Adresse.", file=sys.stderr)
        return 2
    try:
        ausfuehrer = DockerAusfuehrer(os.environ.get("EVENT_CLAW_CONTAINER", "").strip()
                                      or CONTAINER)
    except ValueError:
        print("EVENT_CLAW_CONTAINER ist kein gültiger Container-Name.", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    except psycopg.Error as fehler:
        # Ohne Details: die Meldung koennte Verbindungsangaben enthalten.
        print(f"Verbindung fehlgeschlagen ({type(fehler).__name__}).", file=sys.stderr)
        return 1
    with conn:
        erledigt = lauf(conn, ausfuehrer, mcp_url=basis.rstrip("/") + "/mcp")
    print(f"{len(erledigt)} Auftraege bearbeitet.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
