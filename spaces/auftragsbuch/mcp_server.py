"""auftragsbuch: MCP-Adapter (Streamable HTTP, stateless, JSON-Antworten) ueber werkzeuge.py.

Der Adapter uebersetzt nur. Eine ASGI-Schicht vor der MCP-Verarbeitung prueft den Bearer-
Schluessel (sha256 des vorgelegten Schluessels gegen den Hash aus der Datei, Vergleich mit
`hmac.compare_digest`) und antwortet ohne gueltigen Schluessel mit HTTP 401. Eigene
Request-Handler statt der Dekoratoren des Low-Level-Servers (kein Werkzeug-Cache).

Werkzeugfehler gehen als Text mit `isError` zurueck: FormFehler "Eingabe passt nicht zum
Vertrag" plus Regel und Feldpfad, Abgelehnt der feste Text, NichtGefunden "nicht gefunden",
alles andere "interner Fehler". Je Aufruf genau eine INFO-Zeile (Logger `auftragsbuch`) mit
Werkzeugname, Ergebnis und Dauer, bei `intern` zusaetzlich der Typname; nie Schluessel,
Argumente oder Ergebnisse.

CLI: ``python -m spaces.auftragsbuch.mcp_server``. Umgebung: AUFTRAGSBUCH_DSN_FILE,
AUFTRAGSBUCH_SCHLUESSEL_HASH_FILE, AUFTRAGSBUCH_LADEN, AUFTRAGSBUCH_MANDANT,
AUFTRAGSBUCH_VERTRAG_ORDNER (alle Pflicht), AUFTRAGSBUCH_PORT (Vorgabe 8172),
AUFTRAGSBUCH_HOST (Vorgabe 127.0.0.1), AUFTRAGSBUCH_ZUSATZ_HOSTS (weitere erlaubte Hosts).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from spaces.auftragsbuch import werkzeuge
from spaces.auftragsbuch.db import Abgelehnt, Buch, PgBuch
from spaces.auftragsbuch.vertrag import FormFehler, lade_auftragsarten

log = logging.getLogger("auftragsbuch")

PORT_VORGABE = 8172
HOST_VORGABE = "127.0.0.1"
ERLAUBTE_HOSTS = ["127.0.0.1", "localhost", "auftragsbuch",
                  "127.0.0.1:*", "localhost:*", "auftragsbuch:*"]
_SCHLUESSEL_UNGUELTIG = "Schlüssel fehlt oder ist ungültig"
_ZUSATZ_UNGUELTIG = "AUFTRAGSBUCH_ZUSATZ_HOSTS ungueltig"
_INTERN = "interner Fehler"
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_ZUSATZ_HOST = re.compile(rf"(?P<name>{_LABEL}(?:\.{_LABEL})*)(?::(?P<port>[1-9][0-9]{{0,4}}))?")
_HASH = re.compile(r"[0-9a-f]{64}")


def zusatz_hosts_pruefen(eintraege: Sequence[str]) -> tuple[str, ...]:
    """Prueft weitere erlaubte Host-Kopfzeilen: `hostname` oder `hostname:port`, klein geschrieben.

    ValueError mit festem Text ohne den Wert, sobald ein Eintrag nicht passt.
    """
    geprueft: list[str] = []
    for roh in eintraege:
        eintrag = roh.strip().lower() if isinstance(roh, str) else ""
        treffer = _ZUSATZ_HOST.fullmatch(eintrag)
        if treffer is None or len(treffer.group("name")) > 253:
            raise ValueError(_ZUSATZ_UNGUELTIG)
        port = treffer.group("port")
        if port is not None and int(port) > 65535:
            raise ValueError(_ZUSATZ_UNGUELTIG)
        geprueft.append(eintrag)
    return tuple(geprueft)


def _erlaubte_hosts(zusatz: Sequence[str]) -> list[str]:
    """Feste Hosts plus Zusatz-Hosts; ein Zusatz ohne Port gilt mit jedem Port."""
    hosts = list(ERLAUBTE_HOSTS)
    for eintrag in zusatz_hosts_pruefen(zusatz):
        hosts.append(eintrag)
        if ":" not in eintrag:
            hosts.append(f"{eintrag}:*")
    return hosts


def _bearer(wert: str | None) -> str | None:
    """Schluessel aus ``Authorization: Bearer <schluessel>``; sonst None."""
    if not wert:
        return None
    teile = wert.strip().split(None, 1)
    if len(teile) != 2 or teile[0].lower() != "bearer":
        return None
    return teile[1].strip() or None


def _fehlertext(e: Exception) -> tuple[str, str]:
    """(Ergebnis fuers Log, Text fuer den Client)."""
    if isinstance(e, FormFehler):
        return "form", f"Eingabe passt nicht zum Vertrag: {e}"
    if isinstance(e, Abgelehnt):
        return "abgelehnt", str(e)
    if isinstance(e, werkzeuge.NichtGefunden):
        return "nicht_gefunden", "nicht gefunden"
    return "intern", _INTERN


def _ergebnis(text: str, fehler: bool) -> types.ServerResult:
    return types.ServerResult(types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], isError=fehler))


class _AsgiRoute:
    """Huelle, damit Starlette den Endpunkt als ASGI-App statt als Request-Funktion nimmt."""

    def __init__(self, ziel: Any) -> None:
        self._ziel = ziel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._ziel(scope, receive, send)


def app_bauen(buch: Buch, arten: dict, schluessel_hash: str, zusatz_hosts: Sequence[str]) -> Starlette:
    """Baut die App. `schluessel_hash` ist der sha256-Hex des erlaubten Bearer-Schluessels."""
    soll_hash = schluessel_hash.strip().lower()
    if _HASH.fullmatch(soll_hash) is None:
        raise ValueError("Schluesselhash ungueltig")
    soll = soll_hash.encode("ascii")
    erlaubte_hosts = _erlaubte_hosts(zusatz_hosts)
    bekannte = {w["name"] for w in werkzeuge.katalog(arten)}
    server: Server[Any, Any] = Server("auftragsbuch")

    def ausfuehren(name: str, argumente: dict) -> str:
        return json.dumps(werkzeuge.ausfuehren(name, argumente, buch, arten),
                          ensure_ascii=False, default=str)

    async def tools_list(_anfrage: types.ListToolsRequest) -> types.ServerResult:
        return types.ServerResult(types.ListToolsResult(tools=[
            types.Tool(name=w["name"], description=w["description"], inputSchema=w["inputSchema"],
                       annotations=types.ToolAnnotations(readOnlyHint=w["name"] == "auftrag_lesen"))
            for w in werkzeuge.katalog(arten)]))

    async def tools_call(anfrage: types.CallToolRequest) -> types.ServerResult:
        name = anfrage.params.name
        argumente = anfrage.params.arguments or {}
        start = time.monotonic()
        fehlertyp = ""
        try:
            text = await anyio.to_thread.run_sync(ausfuehren, name, argumente)
            ergebnis, fehler = "ok", False
        except Exception as e:  # noqa: BLE001 - Abbildung nach Vorgabe, nie Inhalt
            ergebnis, text = _fehlertext(e)
            fehler = True
            fehlertyp = type(e).__name__ if ergebnis == "intern" else ""
        dauer_ms = int((time.monotonic() - start) * 1000)
        # Der Name kommt vom Client: nur bekannte Namen ins Log.
        logname = name if name in bekannte else "(unbekannt)"
        if fehlertyp:
            log.info("werkzeug=%s ergebnis=%s dauer_ms=%d fehlertyp=%s", logname, ergebnis, dauer_ms, fehlertyp)
        else:
            log.info("werkzeug=%s ergebnis=%s dauer_ms=%d", logname, ergebnis, dauer_ms)
        return _ergebnis(text, fehler)

    server.request_handlers[types.ListToolsRequest] = tools_list
    server.request_handlers[types.CallToolRequest] = tools_call

    manager = StreamableHTTPSessionManager(
        app=server, stateless=True, json_response=True,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=erlaubte_hosts),
    )

    def schluessel_gueltig(klartext: str | None) -> bool:
        if klartext is None:
            return False
        ist = hashlib.sha256(klartext.encode("utf-8")).hexdigest().encode("ascii")
        return hmac.compare_digest(ist, soll)

    async def mcp_eingang(scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI-Pruefung vor dem MCP: ohne gueltigen Bearer-Schluessel HTTP 401."""
        if not schluessel_gueltig(_bearer(Request(scope).headers.get("authorization"))):
            antwort = JSONResponse({"fehler": _SCHLUESSEL_UNGUELTIG}, status_code=401,
                                   headers={"WWW-Authenticate": 'Bearer realm="auftragsbuch"'})
            await antwort(scope, receive, send)
            return
        await manager.handle_request(scope, receive, send)

    async def gesund(_anfrage: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    @asynccontextmanager
    async def lebenszyklus(_app: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            yield

    return Starlette(
        routes=[Route("/gesund", gesund, methods=["GET"]), Route("/mcp", _AsgiRoute(mcp_eingang))],
        lifespan=lebenszyklus,
    )


# --- Kommandozeile -------------------------------------------------------------------


@dataclass(frozen=True)
class Konfiguration:
    dsn: str
    schluessel_hash: str
    laden: str
    mandant: str
    vertrag_ordner: Path
    host: str
    port: int
    zusatz_hosts: tuple[str, ...] = ()

    def __repr__(self) -> str:  # DSN und Hash nie ausgeben
        return (f"Konfiguration(laden={self.laden!r}, mandant={self.mandant!r},"
                f" host={self.host!r}, port={self.port}, zusatz_hosts={len(self.zusatz_hosts)})")


def _datei_lesen(pfad: str, name: str) -> str:
    try:
        wert = Path(pfad).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise ValueError(f"{name} nicht lesbar") from None
    if not wert:
        raise ValueError(f"{name} ist leer")
    return wert


def _pflicht(env: Mapping[str, str], name: str) -> str:
    wert = env.get(name, "").strip()
    if not wert:
        raise ValueError(f"{name} fehlt")
    return wert


def konfiguration(umgebung: Mapping[str, str] | None = None) -> Konfiguration:
    """Liest die AUFTRAGSBUCH_*-Werte; Fehlermeldungen nennen nur den Variablennamen."""
    env = os.environ if umgebung is None else umgebung
    dsn = _datei_lesen(_pflicht(env, "AUFTRAGSBUCH_DSN_FILE"), "AUFTRAGSBUCH_DSN_FILE")
    hash_wert = _datei_lesen(_pflicht(env, "AUFTRAGSBUCH_SCHLUESSEL_HASH_FILE"),
                             "AUFTRAGSBUCH_SCHLUESSEL_HASH_FILE").lower()
    if _HASH.fullmatch(hash_wert) is None:
        raise ValueError("AUFTRAGSBUCH_SCHLUESSEL_HASH_FILE ungueltig")
    laden = _pflicht(env, "AUFTRAGSBUCH_LADEN")
    mandant = _pflicht(env, "AUFTRAGSBUCH_MANDANT")
    ordner = Path(_pflicht(env, "AUFTRAGSBUCH_VERTRAG_ORDNER"))
    try:
        port = int(env.get("AUFTRAGSBUCH_PORT", "").strip() or PORT_VORGABE)
    except ValueError:
        raise ValueError("AUFTRAGSBUCH_PORT ungueltig") from None
    if not 0 < port < 65536:
        raise ValueError("AUFTRAGSBUCH_PORT ungueltig")
    host = env.get("AUFTRAGSBUCH_HOST", "").strip() or HOST_VORGABE
    zusatz_roh = env.get("AUFTRAGSBUCH_ZUSATZ_HOSTS", "").strip()
    zusatz = zusatz_hosts_pruefen(zusatz_roh.split(",")) if zusatz_roh else ()
    return Konfiguration(dsn, hash_wert, laden, mandant, ordner, host, port, zusatz)


def main(umgebung: Mapping[str, str] | None = None) -> None:
    """Startet den Dienst; eine fehlende oder ungueltige Einstellung beendet mit einer Meldung."""
    logging.basicConfig(level=logging.INFO)
    # Das mcp-Paket loggt auf DEBUG Nachrichteninhalte; nie darunter.
    logging.getLogger("mcp").setLevel(logging.INFO)
    try:
        k = konfiguration(umgebung)
        arten = lade_auftragsarten(k.vertrag_ordner)
        from psycopg_pool import ConnectionPool

        pool: Any = ConnectionPool(k.dsn, min_size=1, max_size=5, open=False, name="auftragsbuch")
        app = app_bauen(PgBuch(pool, k.laden, k.mandant), arten, k.schluessel_hash, k.zusatz_hosts)
    except ValueError as e:
        raise SystemExit(f"auftragsbuch: {e}") from None
    import uvicorn

    pool.open(wait=True, timeout=30)
    try:
        # Kein Access-Log (Anfragen tragen den Schluessel im Header).
        uvicorn.run(app, host=k.host, port=k.port, access_log=False)
    finally:
        pool.close()


if __name__ == "__main__":
    main()
