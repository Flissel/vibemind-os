"""Aufgabe 5 -- die Verifikation eines Credentials beim Anbieter.

Das Tor, das verhindert, dass OpenFang je einen Blindgaenger uebernimmt
(Entwurf D3): bevor ein Wert von Supabase zu OpenFang uebergeht, ruft
`pruefe()` den Anbieter mit genau diesem Wert auf und meldet nur, ob es
funktioniert hat -- nie den Antwortkoerper, nie den Wert selbst.

Drei Pruefformen, aus der D3-Tabelle des Entwurfs
(docs/superpowers/specs/2026-09-08-plugin-setup-agent.md):

  bearer     GET  https://api.github.com/user           (Authorization: Bearer)
  oauth      POST <ziel>                                 MCP `initialize` (JSON-RPC)
  connector  POST https://api.openai.com/v1/responses     mit `connector_id` = ziel

Globale Regeln (siehe Brief, "Global Constraints"):
  - Ein Wert verlaesst nie seinen Pfad: er geht NUR in den Authorization-
    Header des einen Aufrufs an den Anbieter. Er kommt in keinem
    Rueckgabefeld und keiner Protokollzeile wieder heraus. Dieses Modul
    ruft nirgendwo `print`/`logging` mit dem Wert (oder ueberhaupt) auf.
  - Fail closed: jede unklare Antwort, jede unbekannte Pruefform, jeder
    Netzwerkfehler/jede Zeitueberschreitung endet in `gut = False`, nie in
    einem Absturz und nie in einem Teilzustand.
  - Genau ein Versuch pro Aufruf. Kein Wiederholen gegen fremde Anbieter --
    ein fremder Dienst entscheidet nicht durch Retries mit, wie oft wir bei
    ihm anklopfen.
  - Hartes Zeitlimit fuer jede Pruefung (siehe _TIMEOUT_SEKUNDEN).

Referenz fuer die Aufrufform (Header, Accept-Typen, User-Agent) und die
beiden dort bereits geloesten Fallen -- GitHub/Cloudflare weisen Pythons
Standard-User-Agent ab (403), manche Zertifikatsketten (z.B. Linear)
scheitern am Standard-Trust-Store, `certifi` heilt das --:
spaces/rowboat/rowboat/scripts/provision-oauth-token.py
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Callable, Mapping, Optional

# Siehe provision-oauth-token.py: manche Anbieter (z.B. Linear) chainen
# durch Wurzeln, die im Standard-Trust-Store aelterer Python-Installationen
# fehlen. certifi bevorzugen, wenn verfuegbar, sonst der Standardkontext.
try:
    import certifi

    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # pragma: no cover - certifi ist eine Projektabhaengigkeit
    _SSL_CONTEXT = ssl.create_default_context()

# GitHub (hinter Cloudflare-artigem Edge) und viele MCP-Ressourcen weisen
# Pythons Standard-User-Agent ab -- siehe provision-oauth-token.py.
_USER_AGENT = "vibemind-plugin-setup-pruefung/1.0"

# Hartes Zeitlimit je Pruefung. Kein Wiederholen -- ein einziger Versuch,
# und der bricht spaetestens hier ab statt haengen zu bleiben.
_TIMEOUT_SEKUNDEN = 10.0

_GITHUB_USER_URL = "https://api.github.com/user"
_RESPONSES_API_URL = "https://api.openai.com/v1/responses"

# fetch(method, url, *, headers, body, timeout) -> int (HTTP-Statuscode).
# Wirft bei Netzwerkfehlern/Zeitueberschreitung; ein regulaerer
# HTTP-Fehlerstatus (401 etc.) ist KEIN Wurf, sondern ein normaler
# Rueckgabewert -- siehe _echter_fetch.
FetchFn = Callable[..., int]


def _echter_fetch(method: str, url: str, *, headers: Mapping[str, str], body: Optional[bytes], timeout: float) -> int:
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        # Ein HTTP-Fehlerstatus (401, 403, ...) ist eine gueltige, geklaerte
        # Antwort des Anbieters -- kein Grund fuer "nicht gut wegen Absturz",
        # sondern der Statuscode selbst.
        return int(error.code)
    # Alles andere (DNS, Verbindungsabbruch, Zeitueberschreitung, TLS) ist
    # eine UNGEKLAERTE Antwort und wird von pruefe() als "nicht gut, kein
    # Absturz" behandelt -- hier einfach durchreichen lassen.


def _bearer_aufruf(wert: str) -> tuple[str, str, dict, Optional[bytes]]:
    headers = {
        "Authorization": f"Bearer {wert}",
        "Accept": "application/vnd.github+json",
        "User-Agent": _USER_AGENT,
    }
    return "GET", _GITHUB_USER_URL, headers, None


def _oauth_aufruf(wert: str, ziel: str) -> tuple[str, str, dict, Optional[bytes]]:
    headers = {
        "Authorization": f"Bearer {wert}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": _USER_AGENT,
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "vibemind-plugin-setup", "version": "1.0"},
        },
    }
    return "POST", ziel, headers, json.dumps(payload).encode("utf-8")


def _connector_aufruf(referenz: str, wert: str, ziel: str) -> tuple[str, str, dict, Optional[bytes]]:
    headers = {
        "Authorization": f"Bearer {wert}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }
    payload = {
        "model": "gpt-4o-mini",
        "input": "ping",
        "tools": [
            {
                "type": "mcp",
                "server_label": referenz,
                "connector_id": ziel,
                "require_approval": "never",
            }
        ],
    }
    return "POST", _RESPONSES_API_URL, headers, json.dumps(payload).encode("utf-8")


def pruefe(art: str, referenz: str, wert: str, ziel: str, *, fetch: Optional[FetchFn] = None) -> dict:
    """Ruft den Anbieter genau einmal auf und meldet nur, ob es
    funktioniert hat. Gibt NIE den Antwortkoerper und NIE `wert` zurueck --
    nur `{"gut": bool, "status": int}`.

    `fetch` wird fuer Tests eingespritzt; ohne Angabe ein echter
    HTTP-Aufruf. Fail closed: eine unbekannte Pruefform, ein Netzwerkfehler
    oder eine Zeitueberschreitung ergeben `gut = False`, nie einen Absturz.
    """
    aufrufen = fetch if fetch is not None else _echter_fetch

    if art == "bearer":
        method, url, headers, body = _bearer_aufruf(wert)
    elif art == "oauth":
        method, url, headers, body = _oauth_aufruf(wert, ziel)
    elif art == "connector":
        method, url, headers, body = _connector_aufruf(referenz, wert, ziel)
    else:
        # Unbekannte Pruefform: fail closed, ohne den Anbieter ueberhaupt
        # zu kontaktieren.
        return {"gut": False, "status": 0}

    try:
        status = aufrufen(method, url, headers=headers, body=body, timeout=_TIMEOUT_SEKUNDEN)
    except Exception:
        # Netzwerkfehler oder Zeitueberschreitung: ungeklaert, also nicht
        # gut -- aber kein Absturz und kein Teilzustand.
        return {"gut": False, "status": 0}

    return {"gut": status == 200, "status": status}
