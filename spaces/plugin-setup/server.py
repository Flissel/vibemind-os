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

Der TATSAECHLICHE Grund: der Agent hat kein Werkzeug, das eine beliebige
HTTP-Anfrage stellen kann. `config/openclaw.json` hat `browser.enabled` UND
den `browser`-Plugin-Eintrag auf `false` (diese Aenderung, Review Runde 3
-- vorher war das die tatsaechliche Luecke: ein eigener Chrome im
Container haette den Link, den der Agent selbst als `url` von
`eingabe_anfordern` bekommt, oeffnen und das Formular absenden koennen),
`tools.web.search`/`tools.web.fetch` waren schon `false`, und keines der
fuenf Werkzeuge in `server.WERKZEUGE` nimmt eine URL oder stellt selbst
einen Netzaufruf. Wird dem Agenten je wieder ein Browser- oder Fetch-
Werkzeug gegeben, reicht die Loopback-Wache auf einem Docker-Desktop-Host
wie diesem NICHT aus, das zu kompensieren -- das braucht dann einen
Mechanismus, der nicht auf der Client-Adresse beruht.

`spaces/plugin-setup` traegt bewusst KEIN `__init__.py` und ist per
Bindestrich kein gueltiger Python-Modulname -- dieser Server (wie
werkzeuge.py, ablage.py, pruefung.py) wird darum als eigenstaendiges
Skript gestartet (`python server.py`), nicht als `-m spaces.plugin-setup...`.
"""
import os
import sys
from pathlib import Path

_HIER = Path(__file__).resolve().parent
if str(_HIER) not in sys.path:
    sys.path.insert(0, str(_HIER))

import anfragen  # noqa: E402
import fenster  # noqa: E402
import werkzeuge  # noqa: E402
from starlette.responses import HTMLResponse  # noqa: E402

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
_OAUTH_NOCH_NICHT_VERFUEGBAR = (
    f"{_KOPF}<h1>Noch nicht verfuegbar</h1>"
    "<p>Die Anmeldung per OAuth ueber dieses Fenster ist noch nicht angebunden. "
    "Dein Link bleibt gueltig -- versuch es in Kuerze erneut oder wende dich an "
    "den Agenten.</p>"
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
    #   - `art == "oauth"`: der Provisioner fuer den echten Consent-Flow
    #     landet erst in Aufgabe 6. Ohne diese Abfrage wuerde "Anmeldung
    #     starten" (die einzige Schaltflaeche der oauth-Seite, kein
    #     `wert`-Feld) das Token verbrauchen und einen LEEREN Wert an
    #     `schluessel_entgegennehmen` uebergeben -- ein echter Aufruf beim
    #     Anbieter mit leerem Bearer, ein Fehlschlag, und die Referenz auf
    #     `fehlgeschlagen`, fuer nichts. Bis Aufgabe 6 landet: Token bleibt
    #     GUELTIG, keine Schreibaktion.
    #   - jede andere `art`: ein leerer `wert` (kaputtes/leeres POST) wuerde
    #     ebenfalls das Token verbrauchen und einen leeren Wert vaulten.
    vorschau = anfragen.holen(token)
    if vorschau is not None and vorschau.art == "oauth":
        return HTMLResponse(_OAUTH_NOCH_NICHT_VERFUEGBAR, status_code=200)

    formular = await request.form()
    wert = str(formular.get("wert", ""))
    if vorschau is not None and not wert:
        return HTMLResponse(_WERT_FEHLT, status_code=400)

    status, html = fenster.entgegennehmen(token, wert, werkzeuge.schluessel_entgegennehmen)
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
    return server


def main() -> None:
    _load_env_fallback()
    _baue_server().run(transport="streamable-http")


if __name__ == "__main__":
    main()
