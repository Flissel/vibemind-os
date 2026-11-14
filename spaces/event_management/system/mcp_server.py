"""event-mcp: MCP-Adapter (Streamable HTTP, stateless, JSON-Antworten) ueber werkzeuge.py.

Der Adapter uebersetzt nur. Bei JEDEM `tools/list` und `tools/call` liest er den
Bearer-Schluessel aus der HTTP-Anfrage dieses Aufrufs (`server.request_context.request`),
prueft ihn per Hash-Lookup (`schluessel.pruefen`) und reicht die SchluesselInfo nur als
Argument weiter: kein Zustand ueber die Anfrage hinaus. Der Werkzeug-Cache des
Low-Level-Servers wird bewusst nicht benutzt (eigene Request-Handler statt der
Dekoratoren), denn er wuerde Kataloge verschiedener Schluessel vermischen.

Vor der MCP-Verarbeitung lehnt eine ASGI-Pruefung fehlende oder ungueltige Schluessel mit
HTTP 401 ab. Werkzeugfehler gehen als Text mit `isError` zurueck (`_fehlertext`):
NichtGefunden "nicht gefunden" (ausser der Marken-Meldung), KeinRecht "keine Berechtigung",
ValueError mit eigener (datenfreier) Meldung, alles andere "interner Fehler" (nur mit
Typname geloggt). Geloggt werden nie Schluessel, Authorization-Header, Argumente oder
Ergebnisse.

CLI: ``python -m spaces.event_management.system.mcp_server`` (DSN aus
``EVENT_APP_DSN_FILE`` oder ``EVENT_APP_DSN``, ``EVENT_MCP_PORT`` Vorgabe 8171,
``EVENT_MCP_HOST`` Vorgabe 127.0.0.1, ``EVENT_MCP_ZUSATZ_HOSTS`` weitere erlaubte
Host-Kopfzeilen (siehe `zusatz_hosts_pruefen`); fuer `einladen` ``EVENT_BASIS_URL`` und die
``EVENT_SMTP_*``-Werte wie event-api, ohne sie laeuft der Dienst ohne Einladen; fuer
`nachricht_an_gruppe` ``TELEGRAM_BOT_TOKEN_FILE``, ohne sie lehnt das Werkzeug ab).
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import McpError
from psycopg_pool import ConnectionPool
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from spaces.event_management.system import marke, schluessel, werkzeuge
from spaces.event_management.system.kategorien import Kategorie, lade_kategorien
from spaces.event_management.system.kontext import NichtGefunden
from spaces.event_management.system.mailer import Mailer, SmtpMailer
from spaces.event_management.system.telegram import Telegram, telegram_aus_umgebung

log = logging.getLogger(__name__)

PORT_VORGABE = 8171
HOST_VORGABE = "127.0.0.1"
ERLAUBTE_HOSTS = [
    "127.0.0.1", "localhost", "event-mcp", "host.docker.internal",
    "host.docker.internal:*", "127.0.0.1:*", "localhost:*", "event-mcp:*",
]
_SCHLUESSEL_UNGUELTIG = "Schlüssel fehlt oder ist ungültig"
_ZUSATZ_UNGUELTIG = "EVENT_MCP_ZUSATZ_HOSTS ungueltig"
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_ZUSATZ_HOST = re.compile(rf"(?P<name>{_LABEL}(?:\.{_LABEL})*)(?::(?P<port>[1-9][0-9]{{0,4}}))?")
_INTERN = "interner Fehler"


def zusatz_hosts_pruefen(eintraege: Sequence[str]) -> tuple[str, ...]:
    """Prueft weitere erlaubte Host-Kopfzeilen (DNS-Rebinding-Schutz) fuer den Netzweg des
    Brain, z. B. den Tailnet-Namen der VM (Abschluss-Review F-4).

    Jeder Eintrag ist `hostname` oder `hostname:port` (Port 1-65535, keine Muster, kein
    Schema, kein Pfad, kein IPv6); Grossbuchstaben werden klein. ValueError mit festem Text
    ohne den Wert, sobald ein Eintrag nicht passt.
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


def _json_wert(wert: object) -> object:
    if isinstance(wert, UUID):
        return str(wert)
    if isinstance(wert, (datetime, date)):
        return wert.isoformat()
    if isinstance(wert, Decimal):
        return int(wert) if wert == wert.to_integral_value() else float(wert)
    raise TypeError(type(wert).__name__)


def _fehlertext(e: Exception) -> str | None:
    """Fehlertext fuer den Client (gemeinsam.md, Ruling T4-a); None = interner Fehler.

    NichtGefunden -> "nicht gefunden" (nur die Marken-Meldung bleibt, damit der Bot neu
    waehlt), KeinRecht/PermissionError -> "keine Berechtigung", ValueError -> eigener
    Text (datenfrei), ungueltige Zeichen -> "Ungültige Zeichen".
    """
    if isinstance(e, NichtGefunden):
        return marke.MARKE_UNGUELTIG if str(e) == marke.MARKE_UNGUELTIG else "nicht gefunden"
    if isinstance(e, PermissionError):
        return "keine Berechtigung"
    if isinstance(e, UnicodeError):
        return "Ungültige Zeichen"
    if isinstance(e, ValueError):
        return str(e)
    return None


def _fehler(text: str) -> types.ServerResult:
    return types.ServerResult(types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], isError=True))


def _tool(w: werkzeuge.Werkzeug) -> types.Tool:
    return types.Tool(name=w.name, description=w.beschreibung, inputSchema=w.eingabe,
                      annotations=types.ToolAnnotations(readOnlyHint=not w.schreibend))


def erstelle_mcp_app(
    dsn: str, *, mailer: Mailer | None = None, basis_url: str | None = None,
    telegram: Telegram | None = None, kategorien_ordner: Path | None = None,
    zusatz_hosts: Sequence[str] = (),
) -> Starlette:
    """Baut die event-mcp-App. Kategorien werden hier einmal geladen; der Pool und der
    Session-Manager laufen im Lifespan (uvicorn, ``with TestClient(app)``).

    `mailer` und `basis_url` (fuer Einladungslinks, nur aus der Konfiguration) braucht
    das Werkzeug `einladen` (Ruling S6); fehlt eines, lehnt es sauber ab. `telegram`
    braucht `nachricht_an_gruppe`; ohne lehnt es mit "Telegram ist nicht eingerichtet" ab.
    `zusatz_hosts` erweitert die erlaubten Host-Kopfzeilen (`zusatz_hosts_pruefen`).
    """
    if basis_url is not None and not (
            isinstance(basis_url, str) and basis_url.startswith(("https://", "http://"))):
        raise ValueError("basis_url muss mit http:// oder https:// beginnen")
    erlaubte_hosts = _erlaubte_hosts(zusatz_hosts)
    kats: dict[str, Kategorie] = lade_kategorien(kategorien_ordner)
    pool: ConnectionPool[Any] = ConnectionPool(
        dsn, min_size=1, max_size=10, open=False, kwargs={"autocommit": True},
        name="event-mcp",
    )
    server: Server[Any, Any] = Server("event-mcp")

    def klartext_der_anfrage() -> str:
        """Bearer-Schluessel der HTTP-Anfrage, die diesen MCP-Aufruf traegt."""
        anfrage = server.request_context.request
        klartext = None
        if isinstance(anfrage, Request):
            klartext = _bearer(anfrage.headers.get("authorization"))
        if klartext is None:
            raise NichtGefunden(_SCHLUESSEL_UNGUELTIG)
        return klartext

    def katalog_holen(klartext: str) -> list[werkzeuge.Werkzeug]:
        with pool.connection() as conn:
            info = schluessel.pruefen(conn, klartext)
            return werkzeuge.katalog(conn, info, kategorien=kats)

    def werkzeug_ausfuehren(klartext: str, name: str, argumente: Mapping[str, object]) -> str:
        with pool.connection() as conn:
            info = schluessel.pruefen(conn, klartext)
            ergebnis = werkzeuge.ausfuehren(conn, info, name, argumente, kategorien=kats,
                                            mailer=mailer, basis_url=basis_url,
                                            telegram=telegram)
        return json.dumps(ergebnis, ensure_ascii=False, default=_json_wert)

    async def tools_list(_anfrage: types.ListToolsRequest) -> types.ServerResult:
        try:
            klartext = klartext_der_anfrage()
            liste = await anyio.to_thread.run_sync(katalog_holen, klartext)
        except Exception as e:  # noqa: BLE001 - Abbildung nach Vorgabe, nie Inhalt
            text = _fehlertext(e)
            if text is None:
                log.error("tools/list fehlgeschlagen: %s", type(e).__name__)
                raise McpError(types.ErrorData(code=types.INTERNAL_ERROR, message=_INTERN)) from None
            raise McpError(types.ErrorData(code=types.INVALID_REQUEST, message=text)) from None
        return types.ServerResult(types.ListToolsResult(tools=[_tool(w) for w in liste]))

    async def tools_call(anfrage: types.CallToolRequest) -> types.ServerResult:
        name = anfrage.params.name
        argumente = anfrage.params.arguments or {}
        try:
            klartext = klartext_der_anfrage()
            text = await anyio.to_thread.run_sync(werkzeug_ausfuehren, klartext, name, argumente)
        except Exception as e:  # noqa: BLE001 - Abbildung nach Vorgabe, nie Inhalt
            text = _fehlertext(e)
            if text is None:
                log.error("Werkzeug fehlgeschlagen: %s", type(e).__name__)
                text = _INTERN
            return _fehler(text)
        return types.ServerResult(types.CallToolResult(
            content=[types.TextContent(type="text", text=text)], isError=False))

    # Eigene Handler statt @server.list_tools()/@server.call_tool(): die Dekoratoren
    # fuellen und nutzen einen Werkzeug-Cache auf der Server-Instanz (ueber Anfragen und
    # Schluessel hinweg).
    server.request_handlers[types.ListToolsRequest] = tools_list
    server.request_handlers[types.CallToolRequest] = tools_call

    manager = StreamableHTTPSessionManager(
        app=server, stateless=True, json_response=True,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=erlaubte_hosts),
    )

    def schluessel_gueltig(klartext: str) -> bool:
        with pool.connection() as conn:
            try:
                schluessel.pruefen(conn, klartext)
            except NichtGefunden:
                return False
        return True

    async def mcp_eingang(scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI-Pruefung vor dem MCP: ohne gueltigen Bearer-Schluessel HTTP 401."""
        klartext = _bearer(Request(scope).headers.get("authorization"))
        try:
            gueltig = klartext is not None and await anyio.to_thread.run_sync(
                schluessel_gueltig, klartext)
        except Exception as e:  # noqa: BLE001 - z. B. Datenbank weg; nur Typname
            log.error("Schluesselpruefung fehlgeschlagen: %s", type(e).__name__)
            await JSONResponse({"fehler": _INTERN}, status_code=503)(scope, receive, send)
            return
        if not gueltig:
            antwort = JSONResponse({"fehler": _SCHLUESSEL_UNGUELTIG}, status_code=401,
                                   headers={"WWW-Authenticate": 'Bearer realm="event-mcp"'})
            await antwort(scope, receive, send)
            return
        await manager.handle_request(scope, receive, send)

    async def gesund(_anfrage: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    @asynccontextmanager
    async def lebenszyklus(_app: Starlette) -> AsyncIterator[None]:
        await anyio.to_thread.run_sync(lambda: pool.open(wait=True, timeout=30))
        try:
            async with manager.run():
                yield
        finally:
            await anyio.to_thread.run_sync(pool.close)

    app = Starlette(
        routes=[Route("/gesund", gesund, methods=["GET"]), Route("/mcp", _AsgiRoute(mcp_eingang))],
        lifespan=lebenszyklus,
    )
    app.state.mailer = mailer
    app.state.basis_url = basis_url
    app.state.telegram = telegram
    return app


class _AsgiRoute:
    """Huelle, damit Starlette den Endpunkt als ASGI-App statt als Request-Funktion nimmt."""

    def __init__(self, ziel: Any) -> None:
        self._ziel = ziel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._ziel(scope, receive, send)


# --- Kommandozeile -------------------------------------------------------------------


@dataclass(frozen=True)
class Konfiguration:
    dsn: str
    host: str
    port: int
    basis_url: str | None = None  # fuer Einladungslinks; ohne: Einladen aus
    zusatz_hosts: tuple[str, ...] = ()  # weitere erlaubte Host-Kopfzeilen (F-4)

    def __repr__(self) -> str:  # DSN nie ausgeben, Zusatz-Hosts nur als Anzahl
        return (f"Konfiguration(host={self.host!r}, port={self.port},"
                f" basis_url={self.basis_url!r}, zusatz_hosts={len(self.zusatz_hosts)})")


def _datei_lesen(pfad: str, name: str) -> str:
    try:
        # strip(): Dateien von Windows enden oft mit CRLF.
        wert = Path(pfad).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise ValueError(f"{name} nicht lesbar") from None
    if not wert:
        raise ValueError(f"{name} ist leer")
    return wert


def konfiguration(umgebung: Mapping[str, str] | None = None) -> Konfiguration:
    """Liest die EVENT_*-Werte; Fehlermeldungen nennen nur den Variablennamen."""
    env = os.environ if umgebung is None else umgebung
    dsn_datei = env.get("EVENT_APP_DSN_FILE", "").strip()
    if dsn_datei:
        dsn = _datei_lesen(dsn_datei, "EVENT_APP_DSN_FILE")
    else:
        dsn = env.get("EVENT_APP_DSN", "").strip()
        if not dsn:
            raise ValueError("EVENT_APP_DSN_FILE oder EVENT_APP_DSN fehlt")
    try:
        port = int(env.get("EVENT_MCP_PORT", "").strip() or PORT_VORGABE)
    except ValueError:
        raise ValueError("EVENT_MCP_PORT ungueltig") from None
    if not 0 < port < 65536:
        raise ValueError("EVENT_MCP_PORT ungueltig")
    host = env.get("EVENT_MCP_HOST", "").strip() or HOST_VORGABE
    basis_url = env.get("EVENT_BASIS_URL", "").strip() or None
    if basis_url is not None and not basis_url.startswith(("https://", "http://")):
        raise ValueError("EVENT_BASIS_URL ungueltig")
    zusatz_roh = env.get("EVENT_MCP_ZUSATZ_HOSTS", "").strip()
    zusatz = zusatz_hosts_pruefen(zusatz_roh.split(",")) if zusatz_roh else ()
    return Konfiguration(dsn, host, port, basis_url, zusatz)


SMTP_UMGEBUNG = ("EVENT_SMTP_HOST", "EVENT_SMTP_PORT", "EVENT_SMTP_USER",
                 "EVENT_SMTP_ABSENDER", "EVENT_SMTP_PASSWORT_FILE")


def _mailer_aus_umgebung(umgebung: Mapping[str, str] | None = None) -> Mailer | None:
    """Derselbe Mailer wie event-api (`SmtpMailer.aus_umgebung`, EVENT_SMTP_*).

    Ist KEINE EVENT_SMTP_*-Variable gesetzt, laeuft der Dienst ohne Einladen (das Werkzeug
    lehnt dann ab). Eine teilweise oder ungueltige Konfiguration bricht den Start ab
    (ValueError, die Meldung nennt nur den Variablennamen) – wie EVENT_BASIS_URL.
    """
    env = os.environ if umgebung is None else umgebung
    if not any(env.get(name, "").strip() for name in SMTP_UMGEBUNG):
        log.warning("Einladen per Bot aus: keine EVENT_SMTP_*-Konfiguration")
        return None
    return SmtpMailer.aus_umgebung(env)


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    # Das mcp-Paket loggt auf DEBUG Nachrichteninhalte; nie darunter, auch wenn die
    # Wurzel spaeter auf DEBUG steht.
    logging.getLogger("mcp").setLevel(logging.INFO)
    k = konfiguration()
    if k.basis_url is None:
        log.warning("Einladen per Bot aus: EVENT_BASIS_URL fehlt")
    telegram = telegram_aus_umgebung()
    if telegram is None:
        log.warning("Nachrichten an die Gruppe aus: TELEGRAM_BOT_TOKEN_FILE fehlt")
    if k.zusatz_hosts:
        log.info("Zusatz-Hosts erlaubt: %d", len(k.zusatz_hosts))
    app = erstelle_mcp_app(k.dsn, mailer=_mailer_aus_umgebung(), basis_url=k.basis_url,
                           telegram=telegram, zusatz_hosts=k.zusatz_hosts)
    # Kein Access-Log (Anfragen tragen den Schluessel im Header; sicher ist sicher).
    uvicorn.run(app, host=k.host, port=k.port, access_log=False)


if __name__ == "__main__":
    main()
