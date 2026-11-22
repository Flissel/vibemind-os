"""deploy/openfang_aufruf.py -- OpenFang-API-Aufrufe des Betreibers, ohne dass der Schluessel je auf einer Kommandozeile steht.

Nur Standardbibliothek (Python 3.11). Adresse und Schluessel kommen aus der Umgebung und werden von
scripts/mcp_mit_env.py aus der Root-.env des aeusseren Repos gesetzt, wie bei der Rauchprobe:

  python <aeusseres-repo>/scripts/mcp_mit_env.py --env OPENFANG_API_KEY --set OPENFANG_URL=http://127.0.0.1:4200 \\
    -- <vibemind-os>/spaces/auftragsbuch/deploy/openfang_aufruf.py <unterbefehl> ...

Unterbefehle (jeder gibt nur das Genannte aus, nie Schluessel, Adresse oder einen Antwortkoerper):
  werkzeuge-je-agent auftragsbuch      GET /api/agents; je Agent mit einem Werkzeug `mcp_auftragsbuch_*` eine Zeile
                                       `<Agent>: <Werkzeuge>`. Exit 0 nur, wenn genau `brain-sales` mit genau
                                       `mcp_auftragsbuch_auftrag_lesen` und `mcp_auftragsbuch_auftrag_probe` dasteht.
  gegenprobe brain-sales sales_post_pruefen
                                       tools/call von `mcp_auftragsbuch_sales_post_pruefen` an OpenFangs /mcp im Namen
                                       des Agenten (ohne Freigabe- und Kostenkopf: Ablehnung ist das Erwartete).
                                       `abgelehnt von OpenFang (erwartet)` (Exit 0), `DURCHGELASSEN: Unbekannte
                                       Auftragsart - anhalten` (Exit 1) oder `unerwartet (<Grund>)` (Exit 1).
  agent-entfernen brain-sales          DELETE /api/agents/<Kennung>; `entfernt`, `nicht vorhanden` oder
                                       `unerwartet (<Status>)`. Nur der Name `brain-sales` ist zulaessig.

Die Adresse muss auf 127.0.0.1 oder localhost zeigen; der Aufruf nutzt keinen Proxy und folgt keiner
Weiterleitung (sonst ginge der Schluessel mit). Exit 2 bei Bedienfehlern und fehlenden Einstellungen.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import TextIO

ZEITLIMIT_S = 15
MAX_ANTWORT = 4_000_000
ERLAUBTE_HOSTS = ("127.0.0.1", "localhost")
WERKZEUG_VORSATZ = "mcp_auftragsbuch_"
ERWARTET_AGENT = "brain-sales"
ERWARTETE_WERKZEUGE = ("mcp_auftragsbuch_auftrag_lesen", "mcp_auftragsbuch_auftrag_probe")
GEGENPROBE_WERKZEUG = "sales_post_pruefen"
# Gleiche gueltige Eingabe wie in deploy/rauchprobe.py (Werkzeug-Argumente der Bruecke).
GUELTIGE_EINGABE = {"zeitraum": "heute", "entwuerfe_anlegen": False}
DURCHGELASSEN_TEXT = "Unbekannte Auftragsart"
_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
_AUFRUF = ("Aufruf: werkzeuge-je-agent auftragsbuch | gegenprobe <agent> sales_post_pruefen"
           " | agent-entfernen brain-sales")


class Bedienfehler(ValueError):
    """Falsche Aufrufart oder fehlende Einstellung; die Meldung nennt nie einen Wert."""


class _KeinRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _opener() -> urllib.request.OpenerDirector:
    """Eigener Opener: kein Proxy aus der Umgebung, keine Weiterleitung."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _KeinRedirect())


class OpenFang:
    """Schlanker Client: liefert (Status, Rohtext), Status None bei Netzfehler."""

    def __init__(self, basis: str, schluessel: str) -> None:
        self._basis = basis
        self._schluessel = schluessel
        self._opener = _opener()

    def senden(self, methode: str, pfad: str, nutzlast: object | None = None,
               kopf: Mapping[str, str] | None = None) -> tuple[int | None, str]:
        koerper = None if nutzlast is None else json.dumps(nutzlast).encode("utf-8")
        header = {"Accept": "application/json", "Authorization": f"Bearer {self._schluessel}"}
        if koerper is not None:
            header["Content-Type"] = "application/json"
        header.update(kopf or {})
        anfrage = urllib.request.Request(f"{self._basis}{pfad}", data=koerper, headers=header, method=methode)
        try:
            with self._opener.open(anfrage, timeout=ZEITLIMIT_S) as antwort:
                return antwort.status, antwort.read(MAX_ANTWORT).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as fehler:
            try:
                return fehler.code, fehler.read(MAX_ANTWORT).decode("utf-8", errors="replace")
            finally:
                fehler.close()
        except (OSError, ValueError):
            return None, ""


def konfiguration(umgebung: Mapping[str, str]) -> tuple[str, str]:
    """(Basisadresse, Schluessel) aus der Umgebung; Meldungen nennen nur den Variablennamen."""
    for name in ("OPENFANG_URL", "OPENFANG_API_KEY"):
        if not umgebung.get(name, "").strip():
            raise Bedienfehler(f"{name} fehlt (Start ueber scripts/mcp_mit_env.py)")
    adresse = umgebung["OPENFANG_URL"].strip().rstrip("/")
    try:
        teile = urllib.parse.urlsplit(adresse)
        host = teile.hostname
        _ = teile.port  # loest bei ungueltigem Port ValueError aus
    except ValueError:
        raise Bedienfehler("OPENFANG_URL ist keine gueltige Adresse") from None
    if (teile.scheme not in ("http", "https") or host not in ERLAUBTE_HOSTS or teile.username is not None
            or teile.password is not None or teile.path or teile.query or teile.fragment):
        raise Bedienfehler("OPENFANG_URL: nur http(s)://127.0.0.1[:Port] oder localhost[:Port] ohne Pfad ist erlaubt")
    return adresse, umgebung["OPENFANG_API_KEY"].strip()


def _agentenliste(client: OpenFang) -> tuple[list[dict[str, object]] | None, str]:
    """(Agenten, Grund); Agenten ist None bei Fehler. Bare Liste oder Objekt mit `agents`."""
    status, text = client.senden("GET", "/api/agents")
    if status is None:
        return None, "nicht erreichbar"
    if status in (401, 403):
        return None, "Schluessel abgelehnt"
    if status != 200:
        return None, f"Status {status}"
    try:
        koerper = json.loads(text)
    except ValueError:
        return None, "keine JSON-Antwort"
    agenten = koerper.get("agents") if isinstance(koerper, dict) else koerper
    if not isinstance(agenten, list):
        return None, "Agentenliste ungueltig"
    return [a for a in agenten if isinstance(a, dict)], ""


def _kennung(agenten: list[dict[str, object]], name: str) -> str | None:
    for agent in agenten:
        if str(agent.get("name") or "").lower() == name.lower():
            kennung = agent.get("id") or agent.get("agent_id")
            if isinstance(kennung, str) and kennung.strip():
                return kennung.strip()
    return None


def _werkzeugnamen(agent: Mapping[str, object]) -> set[str]:
    """Werkzeugnamen eines Agenten, defensiv aus `capabilities.tools` und `tools` (Text oder Objekt mit `name`)."""
    quellen: list[object] = [agent.get("tools")]
    faehigkeiten = agent.get("capabilities")
    if isinstance(faehigkeiten, dict):
        quellen.append(faehigkeiten.get("tools"))
    namen: set[str] = set()
    for quelle in quellen:
        if not isinstance(quelle, list):
            continue
        for eintrag in quelle:
            name = eintrag.get("name") if isinstance(eintrag, dict) else eintrag
            if isinstance(name, str):
                namen.add(name)
    return namen


def werkzeuge_je_agent(client: OpenFang, aus: TextIO, fehler: TextIO) -> int:
    agenten, grund = _agentenliste(client)
    if agenten is None:
        print(f"unerwartet ({grund})", file=fehler)
        return 1
    gefunden: dict[str, list[str]] = {}
    for agent in agenten:
        treffer = sorted(n for n in _werkzeugnamen(agent) if n.startswith(WERKZEUG_VORSATZ))
        if treffer:
            gefunden[str(agent.get("name") or "?")] = treffer
    for name, treffer in gefunden.items():
        print(f"{name}: {', '.join(treffer)}", file=aus)
    if gefunden == {ERWARTET_AGENT: list(ERWARTETE_WERKZEUGE)}:
        return 0
    print(f"abweichend: erwartet genau {ERWARTET_AGENT} mit {', '.join(ERWARTETE_WERKZEUGE)}", file=fehler)
    return 1


def gegenprobe(client: OpenFang, agent: str, aus: TextIO) -> int:
    agenten, grund = _agentenliste(client)
    if agenten is None:
        print(f"unerwartet ({grund})", file=aus)
        return 1
    kennung = _kennung(agenten, agent)
    if kennung is None:
        print("unerwartet (Agent nicht registriert)", file=aus)
        return 1
    status, text = client.senden(
        "POST", "/mcp",
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": WERKZEUG_VORSATZ + GEGENPROBE_WERKZEUG, "arguments": {"eingabe": GUELTIGE_EINGABE}}},
        {"X-OpenFang-Agent-Id": kennung})
    if DURCHGELASSEN_TEXT in text:
        print(f"DURCHGELASSEN: {DURCHGELASSEN_TEXT} - anhalten", file=aus)
        return 1
    if status is None:
        print("unerwartet (nicht erreichbar)", file=aus)
        return 1
    if status == 401:
        print("unerwartet (Schluessel abgelehnt)", file=aus)
        return 1
    if status == 403:
        print("abgelehnt von OpenFang (erwartet)", file=aus)
        return 0
    if status != 200:
        print(f"unerwartet (Status {status})", file=aus)
        return 1
    try:
        antwort = json.loads(text)
    except ValueError:
        print("unerwartet (keine JSON-Antwort)", file=aus)
        return 1
    if isinstance(antwort, dict) and "error" in antwort and "result" not in antwort:
        print("abgelehnt von OpenFang (erwartet)", file=aus)
        return 0
    print("unerwartet (angenommen ohne Ablehnung)", file=aus)
    return 1


def agent_entfernen(client: OpenFang, agent: str, aus: TextIO) -> int:
    agenten, grund = _agentenliste(client)
    if agenten is None:
        print(f"unerwartet ({grund})", file=aus)
        return 1
    kennung = _kennung(agenten, agent)
    if kennung is None:
        print("nicht vorhanden", file=aus)
        return 0
    status, _ = client.senden("DELETE", f"/api/agents/{urllib.parse.quote(kennung, safe='')}")
    if status in (200, 202, 204):
        print("entfernt", file=aus)
        return 0
    if status == 404:
        print("nicht vorhanden", file=aus)
        return 0
    print(f"unerwartet ({'nicht erreichbar' if status is None else status})", file=aus)
    return 1


def _pruefe_aufruf(argv: list[str]) -> None:
    """Aufrufart pruefen, bevor irgendeine Einstellung gelesen oder etwas gesendet wird."""
    if len(argv) == 2 and argv[0] == "werkzeuge-je-agent" and argv[1] == "auftragsbuch":
        return
    if len(argv) == 3 and argv[0] == "gegenprobe" and _NAME.fullmatch(argv[1]) and argv[2] == GEGENPROBE_WERKZEUG:
        return
    if len(argv) == 2 and argv[0] == "agent-entfernen":
        if argv[1] != ERWARTET_AGENT:
            raise Bedienfehler(f"agent-entfernen: nur {ERWARTET_AGENT} ist zulaessig")
        return
    raise Bedienfehler(_AUFRUF)


def main(argv: list[str], umgebung: Mapping[str, str] | None = None,
         aus: TextIO | None = None, fehler: TextIO | None = None) -> int:
    ausgabe = sys.stdout if aus is None else aus
    meldung = sys.stderr if fehler is None else fehler
    try:
        _pruefe_aufruf(argv)
        basis, schluessel = konfiguration(os.environ if umgebung is None else umgebung)
        client = OpenFang(basis, schluessel)
        if argv[0] == "werkzeuge-je-agent":
            return werkzeuge_je_agent(client, ausgabe, meldung)
        if argv[0] == "gegenprobe":
            return gegenprobe(client, argv[1], ausgabe)
        return agent_entfernen(client, argv[1], ausgabe)
    except Bedienfehler as problem:
        print(f"openfang_aufruf: {problem}", file=meldung)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
