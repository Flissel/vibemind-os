"""Signierter Datei-Link: Laura holt fertige Videomusik per `source_url`.

Laura hat keinen Upload-Endpunkt, also reicht der Musik-Dienst fertige Dateien ueber einen
winzigen HTTP-Server aus. Jede URL traegt eine HMAC-SHA256-Signatur und laeuft nach 900 s ab.
Es gibt keine Verzeichnisliste und keinen freien Pfad: erlaubt sind nur `ERLAUBT`-Namen
fertiger Auftraege, aufgeloest ueber die Auftragsablage.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import ValidationError

ERLAUBT = ("videomusik.wav", "videomusik.mp3")
GUELTIG_S = 900
_TYP = {"videomusik.wav": "audio/wav", "videomusik.mp3": "audio/mpeg"}
_FERTIG = ("fertig", "fertig_mit_befund")

log = logging.getLogger(__name__)


def signieren(schluessel: bytes, auftrag_id: str, name: str, ablauf: int) -> str:
    nachricht = f"{auftrag_id}/{name}/{ablauf}".encode("utf-8")
    return hmac.new(schluessel, nachricht, hashlib.sha256).hexdigest()


def link(basis: str, schluessel: bytes, auftrag_id: str, name: str, jetzt: float) -> str:
    ablauf = int(jetzt) + GUELTIG_S
    sig = signieren(schluessel, auftrag_id, name, ablauf)
    return f"{basis}/datei/{auftrag_id}/{name}?ablauf={ablauf}&sig={sig}"


def _einzelwert(abfrage: dict[str, list[str]], schluessel: str) -> str | None:
    werte = abfrage.get(schluessel)
    if not werte or len(werte) != 1:
        return None
    return werte[0]


def aufloesen(
    ablage, schluessel: bytes, pfad_mit_query: str, jetzt: float
) -> tuple[int, Path | None]:
    """Gibt (200, Datei), (403, None) bei Signatur/Ablauf oder (404, None) zurueck."""
    teile = urlsplit(pfad_mit_query)
    segmente = teile.path.split("/")
    # Genau /datei/<id>/<name>; ein Anteil mit Schraegstrich bleibt in seinem Segment.
    if len(segmente) != 4 or segmente[0] != "" or segmente[1] != "datei":
        return 404, None
    auftrag_id, name = unquote(segmente[2]), unquote(segmente[3])

    abfrage = parse_qs(teile.query, keep_blank_values=True)
    ablauf_text = _einzelwert(abfrage, "ablauf")
    sig = _einzelwert(abfrage, "sig")
    if ablauf_text is None or sig is None:
        return 403, None
    try:
        ablauf = int(ablauf_text)
    except ValueError:
        return 403, None

    erwartet = signieren(schluessel, auftrag_id, name, ablauf)
    if not hmac.compare_digest(erwartet.encode("ascii"), sig.encode("utf-8", "replace")):
        return 403, None
    if jetzt > ablauf:
        return 403, None

    # Ab hier ist die Anfrage signiert; jetzt erst die Ablage anfassen.
    if name not in ERLAUBT:
        return 404, None
    try:
        auftrag = ablage.hole(auftrag_id)
    except (KeyError, ValidationError, ValueError):
        return 404, None
    if auftrag.zustand not in _FERTIG or not auftrag.lauf:
        return 404, None
    lauf = auftrag.lauf
    if lauf in (".", "..") or "/" in lauf or "\\" in lauf or ":" in lauf:
        return 404, None
    datei = Path(ablage.daten) / "runs" / lauf / name
    if not datei.is_file():
        return 404, None
    return 200, datei


def _handler(ablage, schluessel: bytes):
    class Handler(BaseHTTPRequestHandler):
        server_version = "musik-link"

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            log.debug("%s", format % args)

        def _antwort(self, code: int) -> None:
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            status, datei = aufloesen(ablage, schluessel, self.path, time.time())
            if status != 200 or datei is None:
                self._antwort(status)
                return
            try:
                with datei.open("rb") as strom:
                    groesse = datei.stat().st_size
                    self.send_response(200)
                    self.send_header("Content-Type", _TYP[datei.name])
                    self.send_header("Content-Length", str(groesse))
                    self.end_headers()
                    shutil.copyfileobj(strom, self.wfile, 1024 * 1024)
            except OSError:
                # Gegenseite hat getrennt oder Datei verschwand: nichts mehr zu antworten.
                log.debug("Datei-Auslieferung abgebrochen", exc_info=True)

        def _nicht_gefunden(self) -> None:
            self._antwort(404)

        do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _nicht_gefunden

    return Handler


def starten(host: str, port: int, ablage, schluessel: bytes) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _handler(ablage, schluessel))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="musik-link", daemon=True).start()
    return server
