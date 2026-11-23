"""Rowboat-RPC-Client: `POST /rpc/<kanal>` des headless Servers.

Nur Loopback (`http://127.0.0.1:<port>` oder `http://localhost:<port>`), kein Proxy, keine
Weiterleitung. Der Schluessel wird beim Start aus einer Datei gelesen und erscheint nie in
`repr`, Log oder Fehlertext. Fehler tragen nur einen festen Grund.
"""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path

GRUND_NICHT_ERREICHBAR = "Rowboat nicht erreichbar"
GRUND_ABGELEHNT = "Rowboat: Schlüssel abgelehnt"
GRUND_UNERWARTET = "Rowboat: unerwartete Antwort"

_URL = re.compile(r"http://(?:127\.0\.0\.1|localhost):(\d{1,5})")
_KANAL = re.compile(r"[A-Za-z][A-Za-z0-9]*:[A-Za-z][A-Za-z0-9]*")
_ANTWORT_MAX = 4 * 1024 * 1024


class RowboatFehler(Exception):
    """Der Aufruf scheiterte; `grund` ist einer der festen Texte."""

    def __init__(self, grund: str) -> None:
        super().__init__(grund)
        self.grund = grund


class _KeineWeiterleitung(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:  # type: ignore[override]
        return None


class RowboatRpc:
    def __init__(self, url: str, schluessel_datei: Path, timeout_s: float = 10) -> None:
        treffer = _URL.fullmatch((url or "").strip())
        if treffer is None or not 0 < int(treffer.group(1)) < 65536:
            raise ValueError("Rowboat-URL ungueltig")
        port = int(treffer.group(1))
        self._basis = f"http://127.0.0.1:{port}" if "127.0.0.1" in url else f"http://localhost:{port}"
        self._host = f"127.0.0.1:{port}"
        try:
            schluessel = Path(schluessel_datei).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            raise ValueError("Rowboat-Schluesseldatei nicht lesbar") from None
        if not schluessel or any(c in schluessel for c in "\r\n"):
            raise ValueError("Rowboat-Schluessel ungueltig")
        self._schluessel = schluessel
        self._timeout = timeout_s
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _KeineWeiterleitung())

    def __repr__(self) -> str:
        return f"RowboatRpc(port={self._host.rpartition(':')[2]})"

    def aufruf(self, kanal: str, koerper: object) -> object:
        if not isinstance(kanal, str) or _KANAL.fullmatch(kanal) is None:
            raise RowboatFehler(GRUND_UNERWARTET)
        anfrage = urllib.request.Request(
            f"{self._basis}/rpc/{kanal}",
            data=json.dumps(koerper).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._schluessel}", "Content-Type": "application/json",
                     "Host": self._host},
            method="POST",
        )
        try:
            with self._opener.open(anfrage, timeout=self._timeout) as antwort:
                roh = antwort.read(_ANTWORT_MAX + 1)
        except urllib.error.HTTPError as e:
            status = e.code
            e.close()
            raise RowboatFehler(GRUND_ABGELEHNT if status in (401, 403) else GRUND_UNERWARTET) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError):
            raise RowboatFehler(GRUND_NICHT_ERREICHBAR) from None
        if len(roh) > _ANTWORT_MAX:
            raise RowboatFehler(GRUND_UNERWARTET)
        try:
            return json.loads(roh.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise RowboatFehler(GRUND_UNERWARTET) from None
