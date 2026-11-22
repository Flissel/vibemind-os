"""deploy/openfang_aufruf.py -- OpenFang-API-Aufrufe des Betreibers, ohne dass der Schluessel je auf einer Kommandozeile steht.

Nur Standardbibliothek (Python 3.11). Adresse und Schluessel kommen aus der Umgebung und werden von
scripts/mcp_mit_env.py aus der Root-.env des aeusseren Repos gesetzt, wie bei der Rauchprobe:

  python <aeusseres-repo>/scripts/mcp_mit_env.py --env OPENFANG_API_KEY --set OPENFANG_URL=http://127.0.0.1:4200 \\
    -- <vibemind-os>/spaces/auftragsbuch/deploy/openfang_aufruf.py <unterbefehl> ...

Unterbefehle (jeder gibt nur das Genannte aus, nie Schluessel, Adresse oder einen Antwortkoerper):
  werkzeuge-je-agent auftragsbuch      GET /api/agents (Liste ohne Werkzeuge), dann je Agent GET /api/agents/<Kennung>.
                                       Gruppe 1: je Agent mit `mcp_auftragsbuch_*` in `capabilities.tools` eine Zeile
                                       `<Agent>: <Werkzeuge>`; Exit 0 nur, wenn genau `brain-sales` mit genau
                                       `mcp_auftragsbuch_auftrag_lesen` und `..._auftrag_probe` dasteht. Gruppe 2 unter
                                       `offener Zugang ...:`: Agenten mit leeren oder `*`-Werkzeugen, die den Server
                                       sehen (nur Namen; aendert den Exit nicht). Scheitert ein Detail: Exit 1.
  gegenprobe brain-sales sales_post_pruefen
                                       Vorab GET /api/mcp/servers: Server `auftragsbuch` muss verbunden sein und das
                                       Werkzeug fuehren (sonst `unerwartet (...)`, ohne Aufruf). Dann tools/call von
                                       `mcp_auftragsbuch_sales_post_pruefen` an OpenFangs /mcp im Namen des Agenten
                                       (ohne Freigabe- und Kostenkopf). Nur JSON-RPC-Fehler -32602 mit der Nachricht
                                       `Tool not permitted for caller agent: <Werkzeug>` ist die erwartete Ablehnung
                                       (`abgelehnt von OpenFang (erwartet)`, Exit 0). `DURCHGELASSEN: ...` (Exit 1):
                                       Datenbanktext `Unbekannte Auftragsart` oder Fehler -32040 (Recht vorhanden, nur
                                       die Freigabe fehlte). Alles andere: `unerwartet (<fester Grund>)` (Exit 1).
  agent-entfernen brain-sales          DELETE /api/agents/<Kennung>; `entfernt`, `nicht vorhanden` oder
                                       `unerwartet (<Status>)`. Nur der Name `brain-sales` ist zulaessig.

Die Adresse muss auf 127.0.0.1 oder localhost zeigen; der Aufruf nutzt keinen Proxy und folgt keiner
Weiterleitung (sonst ginge der Schluessel mit). Exit 2 bei Bedienfehlern und fehlenden Einstellungen.
"""
from __future__ import annotations

import http.client
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
# Antworten von OpenFangs /mcp (routes.rs): Werkzeug nicht im Werkzeugsatz des Aufrufers / Recht da, Freigabe fehlt.
ABLEHNUNG_CODE = -32602
ABLEHNUNG_NACHRICHT = "Tool not permitted for caller agent: "
FREIGABE_CODE = -32040
FREIGABE_NACHRICHT = "Runtime authority denied tool execution"
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
        except (OSError, ValueError, http.client.HTTPException):
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


def _detail(client: OpenFang, kennung: str) -> tuple[dict[str, object] | None, str]:
    """(Detail, Grund) von GET /api/agents/<kennung>; Detail ist None bei Fehler."""
    status, text = client.senden("GET", f"/api/agents/{urllib.parse.quote(kennung, safe='')}")
    if status is None:
        return None, "nicht erreichbar"
    if status in (401, 403):
        return None, "Schluessel abgelehnt"
    if status != 200:
        return None, f"Status {status}"
    try:
        detail = json.loads(text)
    except ValueError:
        return None, "keine JSON-Antwort"
    if not isinstance(detail, dict):
        return None, "Agentendetail ungueltig"
    return detail, ""


def _werkzeugnamen(detail: Mapping[str, object]) -> list[str]:
    """`capabilities.tools` des Agenten-Details (Namen); alles andere zaehlt als leer."""
    faehigkeiten = detail.get("capabilities")
    werkzeuge = faehigkeiten.get("tools") if isinstance(faehigkeiten, dict) else None
    return [w for w in werkzeuge if isinstance(w, str)] if isinstance(werkzeuge, list) else []


def _sieht_server(detail: Mapping[str, object], server: str) -> bool:
    """`mcp_servers_mode == all`, der Server steht in `mcp_servers`, oder `mcp_servers` ist leer."""
    liste = detail.get("mcp_servers")
    namen = [n for n in liste if isinstance(n, str)] if isinstance(liste, list) else []
    return detail.get("mcp_servers_mode") == "all" or server in namen or not namen


def werkzeuge_je_agent(client: OpenFang, aus: TextIO, fehler: TextIO) -> int:
    """Gruppe 1 `<Agent>: <Werkzeuge>` (ausdruecklich, bestimmt den Exit); Gruppe 2 nur Namen (offener Zugang)."""
    agenten, grund = _agentenliste(client)
    if agenten is None:
        print(f"unerwartet ({grund})", file=fehler)
        return 1
    ausdruecklich: list[tuple[str, str, list[str]]] = []  # (Kennung, Name, Werkzeuge): nach Kennung, nicht Name
    offen: list[str] = []
    for agent in agenten:  # die Liste traegt keine Werkzeuge: je Agent das Detail holen
        kennung = agent.get("id") or agent.get("agent_id")
        if not isinstance(kennung, str) or not kennung.strip():
            print("unerwartet (Agentenliste ungueltig)", file=fehler)
            return 1
        detail, grund = _detail(client, kennung.strip())
        if detail is None:
            print(f"unerwartet ({grund})", file=fehler)
            return 1
        name = str(detail.get("name") or agent.get("name") or "?")
        werkzeuge = _werkzeugnamen(detail)
        treffer = sorted(w for w in werkzeuge if w.startswith(WERKZEUG_VORSATZ))
        if treffer:
            ausdruecklich.append((kennung.strip(), name, treffer))
        if (not werkzeuge or "*" in werkzeuge) and _sieht_server(detail, "auftragsbuch"):
            offen.append(name)
    namen = [n for _, n, _ in ausdruecklich]
    for kennung, name, treffer in ausdruecklich:
        titel = f"{name} (Kennung {kennung})" if namen.count(name) > 1 else name
        print(f"{titel}: {', '.join(treffer)}", file=aus)
    if offen:
        print("offener Zugang (sieht alle Werkzeuge des Servers):", file=aus)
        for name in offen:
            print(name, file=aus)
    if [(n, t) for _, n, t in ausdruecklich] == [(ERWARTET_AGENT, list(ERWARTETE_WERKZEUGE))]:
        return 0
    print(f"abweichend: erwartet genau {ERWARTET_AGENT} mit {', '.join(ERWARTETE_WERKZEUGE)}", file=fehler)
    return 1


def _server_kontrolle(client: OpenFang, werkzeug: str) -> str | None:
    """Positive Kontrolle: Server `auftragsbuch` verbunden und fuehrt das Werkzeug. None = in Ordnung, sonst Grund."""
    status, text = client.senden("GET", "/api/mcp/servers")
    if status is None:
        return "nicht erreichbar"
    if status in (401, 403):
        return "Schluessel abgelehnt"
    if status != 200:
        return f"Status {status}"
    try:
        koerper = json.loads(text)
    except ValueError:
        return "Serverliste ungueltig"
    verbunden = koerper.get("connected") if isinstance(koerper, dict) else None
    if not isinstance(verbunden, list):
        return "Serverliste ungueltig"
    eintraege = [e for e in verbunden if isinstance(e, dict) and e.get("name") == "auftragsbuch"
                 and e.get("connected") is True]
    if not eintraege:
        return "Server auftragsbuch nicht verbunden"
    for eintrag in eintraege:
        werkzeuge = eintrag.get("tools")
        if isinstance(werkzeuge, list) and any(isinstance(w, dict) and w.get("name") == werkzeug for w in werkzeuge):
            return None
    return "Werkzeug am Server nicht vorhanden"


def _fehlercode(antwort: object) -> tuple[bool, object, object]:
    """(ist JSON-RPC-Fehler ohne result, code, message)."""
    if not isinstance(antwort, dict) or "error" not in antwort or "result" in antwort:
        return False, None, None
    fehler = antwort["error"]
    if not isinstance(fehler, dict):
        return True, None, None
    return True, fehler.get("code"), fehler.get("message")


def gegenprobe(client: OpenFang, agent: str, aus: TextIO) -> int:
    werkzeug = WERKZEUG_VORSATZ + GEGENPROBE_WERKZEUG
    agenten, grund = _agentenliste(client)
    if agenten is None:
        print(f"unerwartet ({grund})", file=aus)
        return 1
    kennung = _kennung(agenten, agent)
    if kennung is None:
        print("unerwartet (Agent nicht registriert)", file=aus)
        return 1
    # Ohne verbundenen Server fehlt das Werkzeug ueberall, und OpenFang antwortete ebenfalls "nicht erlaubt":
    # das darf nicht wie die Ablehnung aussehen.
    grund_server = _server_kontrolle(client, werkzeug)
    if grund_server is not None:
        print(f"unerwartet ({grund_server})", file=aus)
        return 1
    status, text = client.senden(
        "POST", "/mcp",
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": werkzeug, "arguments": {"eingabe": GUELTIGE_EINGABE}}},
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
    if status != 200:
        print(f"unerwartet (Status {status})", file=aus)
        return 1
    try:
        antwort = json.loads(text)
    except ValueError:
        print("unerwartet (keine JSON-Antwort)", file=aus)
        return 1
    ist_fehler, code, nachricht = _fehlercode(antwort)
    if not ist_fehler:
        print("unerwartet (angenommen ohne Ablehnung)", file=aus)
        return 1
    if code == ABLEHNUNG_CODE and nachricht == ABLEHNUNG_NACHRICHT + werkzeug:
        print("abgelehnt von OpenFang (erwartet)", file=aus)
        return 0
    if code == FREIGABE_CODE and nachricht == FREIGABE_NACHRICHT:
        print("DURCHGELASSEN: Recht vorhanden, nur die Freigabe fehlte - anhalten", file=aus)
        return 1
    # Nur ein ganzzahliger Code wird genannt; alles andere bleibt ungenannt.
    nenne = f" {code}" if isinstance(code, int) and not isinstance(code, bool) else ""
    print(f"unerwartet (JSON-RPC-Fehler{nenne})", file=aus)
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
