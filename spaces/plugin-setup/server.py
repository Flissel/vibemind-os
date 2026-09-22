"""plugin-setup MCP-Server -- Host-Sidecar auf 0.0.0.0:8131 (Aufgabe 6).

WARUM HOST-SIDECAR UND NICHT CONTAINER (Muster spaces/marketing/claw/
server.py): sowohl Rowboat als auch der ISOLIERTE OpenFang-Daemon dieses
Tasks (127.0.0.1:4273, eigener OPENFANG_HOME -- s. Global Constraints,
NIE :4200/~/.openfang/) binden Loopback; aus einem Container ist Loopback
des Hosts nicht ohne Weiteres erreichbar. Der Sidecar sitzt daneben und
bindet selbst 0.0.0.0, damit der openclaw-Container ihn ueber
host.docker.internal erreicht (s. config/openclaw.json).

LOOPBACK-WACHE AUF DEN FORMULAR-ROUTEN (Review Runde 3, C-Kritisch) -- UND
IHRE GEMESSENE GRENZE: `HOST` bleibt `0.0.0.0`, weil der Container `/mcp`
erreichen muss. `_ist_loopback` prueft in BEIDEN `/fenster/{token}`-Routen
die Client-Adresse, bevor irgendetwas am Token oder am `wert` passiert.

Das ist auf DIESEM Host (Docker Desktop + WSL Mirrored-Networking) KEINE
Trennung zwischen Betreiber und Container -- gemessen, nicht nur gelesen:
ein Request von `marketing-claw` an `http://host.docker.internal:<port>`
kam beim Python-Prozess mit `request.client.host == "127.0.0.1"` an, UND
ZWAR AUCH GEGEN EINEN LISTENER, DER NUR AUF `127.0.0.1` GEBUNDEN WAR (nicht
nur `0.0.0.0`) -- Docker Desktops `host.docker.internal`-Bruecke terminiert
Container-Traffic fuer den Zielprozess wie eine lokale Loopback-Verbindung,
unabhaengig von dessen Bind-Adresse. `_ist_loopback` blockt darum auf
diesem Host weder den `marketing-claw`- noch einen anderen so erreichten
Container -- s. Task-5-Report, Abschnitt "Mutation checks", fuer die
rohen Messwerte. Sie bleibt sinnvolle Tiefenverteidigung gegen andere
Quellen (LAN, ein Container in echtem Bridge-Networking ohne Desktops
Bruecke) und ist darum nicht umsonst, aber sie ist NICHT der Grund, warum
der `plugin-setup`-Agent diese Route heute nicht erreichen kann.

FRUEHERE FASSUNG DIESES ABSCHNITTS WAR SELBST EINE UNVOLLSTAENDIGE
AUFZAEHLUNG (Review Runde 3, Fix-Runde 2) -- sie nannte `browser`,
`tools.web.search`/`tools.web.fetch` und die fuenf MCP-Werkzeuge und schloss
daraus, der Agent habe "kein Werkzeug, das eine beliebige HTTP-Anfrage
stellen kann". Sie verschwieg die SHELL: `config/openclaw.json` setzte
weder `tools.profile` noch `tools.deny` noch einen Sandbox-Modus, und ohne
das ist das gebaute Tool-Profil `full` ("No restriction"), das laut
openclaw's eigener Doku `group:runtime` (`exec`/`process`/`code_execution`)
einschliesst -- Sandboxing ist dort ebenfalls als standardmaessig AUS
dokumentiert. Ein einziger `exec`-Aufruf im Container
(`curl -X POST http://host.docker.internal:8131/fenster/<token>
--data-urlencode "wert=..."`) waere derselbe Weg wie der Browser-Fund
gewesen, nur durch eine andere Faehigkeit, die die Aufzaehlung nicht
mitgezaehlt hatte -- und die Loopback-Wache haette ihn nachweislich nicht
gestoppt (s. oben).

Darum die ehrliche, NICHT aufzaehlende Fassung: kein MCP-Werkzeug, das dem
Agenten zur Verfuegung steht, nimmt einen Credential-Wert an, und die
Tool-Policy des Agenten entzieht ihm Shell-, Dateisystem-, Web-,
Automations- und Session-Faehigkeiten (`config/openclaw.json`: `tools.deny`
= `group:runtime`, `group:fs`, `group:web`, `group:ui`, `group:automation`,
`group:sessions`) -- der Agent haelt trotzdem den Einmal-Link, und die
Loopback-Wache trennt auf diesem Host den Container nicht vom Betreiber.

DIESE FASSUNG WAR EBENFALLS UNVOLLSTAENDIG (Review Runde 3, Fix-Runde 3,
C-Kritisch): `group:automation` fehlte in der Deny-Liste. Der FIX bleibt
richtig, aber die BEGRUENDUNG dafuer war falsch, korrigiert in Fix-Runde 4
-- und das ist selbst die Lehre hier: Fix-Runde 3 las openclaw's PROSA-Doku
("das owner-only Werkzeug `gateway` schuetzt nur `tools.exec.ask`/
`tools.exec.security` vor sich selbst") und las das als kurze Denyliste,
die `tools.deny` fuer `config.patch` offen liesse -- und schloss daraus
eine zweistufige Eskalationskette (`gateway config.patch` -> `tools.deny`
loeschen -> Neustart -> `exec` -> `curl`). GEGEN DEN KOMPILIERTEN CODE
GEPRUEFT (Review Runde 4) ist das falsch: `ALLOWED_GATEWAY_CONFIG_PATHS` in
diesem Image (2026.7.1) ist eine 18-Muster-ALLOWLIST, und
`assertGatewayConfigMutationAllowed` wirft fuer alles, was nicht darauf
passt -- nichts unter `tools.` passt. Die beschriebene Eskalationskette
existiert in diesem Image schlicht nicht; die Doku las sich wie eine
Denyliste und war eine Allowlist.

`group:automation` bleibt trotzdem zu Recht denied, aus dem tatsaechlichen
Grund: es enthaelt `cron` (kann Agenten-Turns zeitgesteuert ausloesen) und
`gateway`s Neustart-/`update.run`-Flaeche -- beides Faehigkeiten, die dieser
Agent nicht braucht, UNABHAENGIG davon, ob `gateway` `tools.deny` je aendern
koennte. Und die Allowlist selbst ist eine Eigenschaft DIESER Image-Version,
kein Vertrag, den wir kontrollieren -- sie kann bei einem Upgrade in beide
Richtungen kippen. `group:sessions` (Subagent-/Session-Werkzeuge, kein
Bedarf fuer diesen Agenten) bleibt aus derselben Vorsicht denied, ebenfalls
ohne einen belegten Weg dorthin.

Der Schreibweg ist also durch KONFIGURATION verschlossen, nicht durch
STRUKTUR. `tests/test_openclaw_tool_policy.py` haelt fest, dass diese
sechs Gruppen im EINGECHECKTEN `config/openclaw.json` stehen -- das ist ein
Tripwire fuer eine Aenderung an DIESER Datei, ausdruecklich KEIN Beweis,
dass (a) der tatsaechlich laufende Container diese Datei so geladen hat
(unverifiziert, s. Task-5-Report) oder (b) keine hier nicht genannte
Gruppe/Kombination denselben Weg auf einem anderen Pfad oeffnet -- geprueft
ist nur, was hier aufgefuehrt ist. Zu (b) hat Review Runde 4 gezielt
nachgesehen: sechs Gruppen bleiben ungeprueft denied (`group:plugins`,
`group:nodes`, `group:messaging`, `group:agents`, `group:media`,
`group:memory` -- `memory_search`/`memory_get`, lokales Read-only-Recall,
kein Schreib- oder Exfiltrationspfad, das Risiko dort ist nach heutigem
Stand nahe null, aber ungeprueft ist ungeprueft), und
dieses Image aktiviert `file-transfer` standardmaessig mit einer
`file_write`-Primitive AUSSERHALB von `group:fs` -- die zwei Kandidaten mit
plausibel neuem HTTP-/Config-Schreibweg (`file_fetch`, `skill_workshop`)
sind gegen den kompilierten Code geprueft und beide durch das IMAGE SELBST
verschlossen (`file_fetch`s URL ist hart auf das lokale Gateway gebunden,
`skill_workshop` kann seinen Workspace nicht verlassen), nicht durch unsere
Liste -- die Deckung ist also teils zufaellig, und "auf `profile: minimal`
mit expliziter `alsoAllow`-Liste statt Denyliste-ueber-`full` umstellen"
ist der richtig geformte, aber hier bewusst NICHT umgesetzte naechste
Schritt (gehoert in dieselbe Folgerunde, nicht in eine schliessende Runde).

N7-FIX (2026-09-22): der strukturelle Fix, der die urspruengliche
Zusicherung wiederherstellt, ist jetzt umgesetzt -- `eingabe_anfordern`
haendigt dem Agenten die Adresse nicht mehr aus. Er bekommt nur noch den
Hinweis, die feste, tokenlose Listen-Seite (`/anfragen`, `_anfragen_zeigen`
unten) zu nennen; der Mensch oeffnet sie selbst und findet dort seinen
echten Einmal-Link. Die Listen-Seite sitzt hinter derselben Loopback-Wache
wie die Formular-Routen -- die oben beschriebene Grenze dieser Wache
(Docker-Desktop/WSL-Bruecke) gilt darum unveraendert auch fuer sie: der
Agent hat aber ohnehin keine Faehigkeit, die eine HTTP-Anfrage stellen
koennte (s. oben), die Listen-Seite ist also nicht der schwaechere Punkt.
Details: `docs/superpowers/specs/2026-09-12-eingabefenster-design.md` E3,
`docs/superpowers/specs/2026-09-12-eingabefenster-nachzieher.md` N7.

`spaces/plugin-setup` traegt bewusst KEIN `__init__.py` und ist per
Bindestrich kein gueltiger Python-Modulname -- dieser Server (wie
werkzeuge.py, ablage.py, pruefung.py) wird darum als eigenstaendiges
Skript gestartet (`python server.py`), nicht als `-m spaces.plugin-setup...`.
"""
import importlib.util
import os
import sys
from pathlib import Path

_HIER = Path(__file__).resolve().parent
if str(_HIER) not in sys.path:
    sys.path.insert(0, str(_HIER))

import anfragen  # noqa: E402
import fenster  # noqa: E402
import werkzeuge  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402
from starlette.responses import HTMLResponse  # noqa: E402

# `provision-oauth-token.py` liegt in spaces/rowboat, nicht hier, UND sein
# Dateiname ist per Bindestrich kein gueltiger Modulname -- ein normaler
# `import` geht also so oder so nicht. Geladen per `spec_from_file_location`,
# derselbe Kniff wie in spaces/rowboat/tests/test_openai_plugin_runtime_contract.py.
# Aufgabe 6: das Eingabefenster ruft dieselbe Beschaffung auf, statt sie
# abzuschreiben (s. fenster.oauth_entgegennehmen).
_PROVISIONER_PATH = (_HIER.parent / "rowboat" / "rowboat" / "scripts"
                     / "provision-oauth-token.py")
# N10-FIX (2026-09-15): Verhalten bleibt fail-closed -- ohne spaces/rowboat/
# soll dieser Server NICHT starten, weil der oauth-Zweig des Eingabefensters
# (fenster.oauth_entgegennehmen) den ECHTEN Provisioner dort braucht, statt
# ihn abzuschreiben. Vorher war die Meldung dabei ein nackter
# `FileNotFoundError` auf den rohen Pfad, ohne zu sagen, WAS fehlt oder
# WARUM das den Start verhindert -- derselbe Ausnahmetyp, aber mit Kontext.
if not _PROVISIONER_PATH.exists():
    raise FileNotFoundError(
        "plugin-setup braucht spaces/rowboat/ im selben Checkout, um zu "
        f"starten -- es fehlt: {_PROVISIONER_PATH}. Der oauth-Zweig des "
        "Eingabefensters (fenster.oauth_entgegennehmen) ruft den ECHTEN "
        "Provisioner aus spaces/rowboat/rowboat/scripts/"
        "provision-oauth-token.py auf, statt ihn abzuschreiben (s. Kommentar "
        "direkt ueber dieser Pruefung) -- ohne diese Datei startet dieser "
        "Server nicht (fail closed: lieber gar nicht laufen als der "
        "oauth-Zweig ohne echten Provisioner). Abhilfe: spaces/rowboat/ in "
        "diesem Checkout mit auschecken, dann den Server erneut starten.")
_PROVISIONER_SPEC = importlib.util.spec_from_file_location(
    "provision_oauth_token", _PROVISIONER_PATH)
assert _PROVISIONER_SPEC is not None and _PROVISIONER_SPEC.loader is not None
provision_oauth_token = importlib.util.module_from_spec(_PROVISIONER_SPEC)
_PROVISIONER_SPEC.loader.exec_module(provision_oauth_token)

REPO_ROOT = next((p for p in (_HIER, *_HIER.parents) if (p / "vibemind-os").is_dir()), _HIER)

# Schluessel wie die Nachbar-Sidecars: aus der repo-.env nachladen, nie
# ueberschreiben, kein Key-Material im Launcher-Skript. Siehe README.md
# in diesem Verzeichnis fuer, was jede Variable bedeutet.
_ENV_KEYS = ("ROWBOAT_URL", "ROWBOAT_API_KEY", "ROWBOAT_CATALOG_DIGEST",
             "PLUGIN_SETUP_OPENFANG_URL", "PLUGIN_SETUP_OPENFANG_API_KEY",
             "PLUGIN_SETUP_DB_CONTAINER")


def _load_env_fallback() -> None:
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    missing = [k for k in _ENV_KEYS if not os.environ.get(k)]
    if not missing:
        return
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        for k in missing:
            if line.startswith(k + "="):
                os.environ[k] = line.split("=", 1)[1].strip().strip('"').strip("'")


WERKZEUGE = (
    werkzeuge.plugin_bedarf,
    werkzeuge.eingabe_anfordern,
    werkzeuge.einrichtung_status,
    werkzeuge.plugin_installieren,
    werkzeuge.plugin_werkzeug_binden,
)

HOST = os.environ.get("PLUGIN_SETUP_MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("PLUGIN_SETUP_MCP_PORT", "8131"))

_KOPF = (
    "<!doctype html><meta charset='utf-8'><title>Plugin-Einrichtung</title>"
    "<style>body{font:16px system-ui;margin:3rem auto;max-width:34rem}</style>"
)
_NUR_VOM_HOST = (
    f"{_KOPF}<h1>Nur vom Host aus erreichbar</h1>"
    "<p>Dieses Formular nimmt keine Verbindung von ausserhalb des Hosts an.</p>"
)
_WERT_FEHLT = (
    f"{_KOPF}<h1>Wert fehlt</h1>"
    "<p>Bitte einen Wert eintragen und das Formular erneut absenden. Dein Link "
    "bleibt gueltig.</p>"
)

# "127.0.0.1"/"::1" reichen auf DIESER Maschine aus (gemessen, s. Moduldoku
# und Task-5-Report) -- ein IPv4-gemapptes "::ffff:127.0.0.1" wird von
# manchen Stacks statt "127.0.0.1" gemeldet, darum zusaetzlich abgedeckt.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def _ist_loopback(request) -> bool:
    """Gate NUR fuer die Formular-Routen -- s. Moduldoku oben, EINSCHLIESSLICH
    der dort gemessenen Grenze: auf einem Docker-Desktop/WSL-Mirrored-Host
    trennt diese Pruefung den Container NICHT vom Host. `request.client` ist
    `None` fuer manche Transports (z.B. ein ASGI-Test ohne Client-Scope);
    das zaehlt als NICHT loopback, nie als Freifahrtschein."""
    client = request.client
    return client is not None and client.host in _LOOPBACK_HOSTS


async def _anfragen_zeigen(request):
    """N7: die Listen-Seite -- tokenlos, hinter derselben Loopback-Wache wie
    die Formular-Routen. Der Agent bekommt diese feste Adresse als Hinweis
    von `werkzeuge.eingabe_anfordern` mit, nie einen Einmal-Link direkt."""
    if not _ist_loopback(request):
        return HTMLResponse(_NUR_VOM_HOST, status_code=403)
    return HTMLResponse(fenster.listenseite(anfragen.alle_offenen()))


async def _fenster_zeigen(request):
    if not _ist_loopback(request):
        return HTMLResponse(_NUR_VOM_HOST, status_code=403)
    a = anfragen.holen(request.path_params["token"])
    if a is None:
        return HTMLResponse("Link ungueltig.", status_code=404)
    return HTMLResponse(fenster.seite_fuer(a))


async def _fenster_annehmen(request):
    if not _ist_loopback(request):
        return HTMLResponse(_NUR_VOM_HOST, status_code=403)
    token = request.path_params["token"]

    # C-Kritisch (Review Runde 3, Punkt 2): VOR jedem `verbrauchen()` erst
    # ansehen (`anfragen.holen`, konsumiert NICHT), was fuer dieses Token
    # ueberhaupt anfaellt.
    #   - `art == "oauth"`: die oauth-Seite hat kein `wert`-Feld -- ohne
    #     diese Abfrage wuerde `request.form()` unten einen LEEREN Wert
    #     lesen und einen echten Aufruf beim Anbieter mit leerem Bearer
    #     ausloesen, fuer nichts. Aufgabe 6 zweigt hier stattdessen auf den
    #     echten Provisioner ab (`fenster.oauth_entgegennehmen`), der das
    #     Token selbst verbraucht -- unabhaengig davon, ob die Beschaffung
    #     gelingt (kein Retry auf demselben Link) -- und den Formular-Pfad
    #     unten fuer diese Anfrage nie erreicht.
    #   - jede andere `art`: ein leerer `wert` (kaputtes/leeres POST) wuerde
    #     ebenfalls das Token verbrauchen und einen leeren Wert vaulten.
    #
    # Beide Schreibwege unten laufen per `run_in_threadpool` (Fix-Runde 1,
    # Befund 1): FastMCP faehrt eine einzige uvicorn-Event-Loop, und ein
    # synchroner Aufruf -- beim oauth-Zweig bis zu `timeout_seconds=300` --
    # wuerde sonst jede andere Anfrage dieses Prozesses blockieren.
    vorschau = anfragen.holen(token)
    if vorschau is not None and vorschau.art == "oauth":
        status, html = await run_in_threadpool(
            fenster.oauth_entgegennehmen, token,
            provision_oauth_token.token_holen, werkzeuge.schluessel_entgegennehmen)
        return HTMLResponse(html, status_code=status)

    formular = await request.form()
    # .strip() VOR der Leer-Pruefung (Review Runde 3, Fix-Runde 2): ohne das
    # waere ein Wert aus nur Leerzeichen (Copy-Paste-Artefakt, kein echter
    # Leerstring) `truthy` und erreichte den Tresor und den Anbieter --
    # also praktisch immer ein Fehlschlag, nur schwerer zu diagnostizieren
    # als ein ehrliches "Wert fehlt". Der gestrippte Wert geht auch an den
    # Schreiber, nicht nur in die Pruefung: fuehrende/nachlaufende
    # Leerzeichen sind nie Teil eines echten Credentials.
    wert = str(formular.get("wert", "")).strip()
    if vorschau is not None and not wert:
        return HTMLResponse(_WERT_FEHLT, status_code=400)

    status, html = await run_in_threadpool(
        fenster.entgegennehmen, token, wert, werkzeuge.schluessel_entgegennehmen)
    return HTMLResponse(html, status_code=status)


def _baue_server():
    """Baut den FastMCP-Server -- Werkzeuge und Formular-Routen registriert,
    aber noch NICHT gestartet. Eigene Funktion statt Inline-Code in `main()`,
    damit Tests pruefen koennen, was der Server TATSAECHLICH registriert
    (`server._tool_manager.list_tools()`), statt nur das `WERKZEUGE`-Tupel
    zu lesen -- ein `server.tool()(...)`-Aufruf ausserhalb der Schleife
    wuerde das Tupel nicht aendern, wohl aber die echte Registrierung
    (Review Runde 3, Important).

    Nur noch EIN Server-Typ: `mcp.server.mcpserver.MCPServer` existiert auf
    dieser Codebasis nicht (gemessen), und der fruehere Fallback-Zweig
    dorthin liess sich nicht importieren, nicht testen und widersprach der
    tatsaechlich laufenden FastMCP-Route -- und war der eine Ort, an dem ein
    kuenftiger Editor den Schreibweg wieder haette registrieren koennen,
    ohne dass ein Sicherheitstest rot wird (Review Runde 3, Minor). Taucht
    `mcp.server.mcpserver` je in einer anderen Umgebung auf, ist DAS der
    Moment, einen verifizierten Zweig dafuer zu schreiben -- nicht vorher
    raten.
    """
    from mcp.server.fastmcp import FastMCP
    server = FastMCP("plugin-setup", host=HOST, port=PORT)
    for fn in WERKZEUGE:
        server.tool()(fn)
    server.custom_route("/fenster/{token}", methods=["GET"])(_fenster_zeigen)
    server.custom_route("/fenster/{token}", methods=["POST"])(_fenster_annehmen)
    server.custom_route("/anfragen", methods=["GET"])(_anfragen_zeigen)
    return server


def main() -> None:
    _load_env_fallback()
    _baue_server().run(transport="streamable-http")


if __name__ == "__main__":
    main()
