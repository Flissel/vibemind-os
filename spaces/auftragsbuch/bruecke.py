"""auftragsbuch: stdio-HTTP-Bruecke fuer OpenFang.

OpenFang startet diese Datei als stdio-Werkzeug-Server (zeilengetrenntes JSON-RPC, UTF-8) und
braucht keine Adresse des Dienstes in seiner Konfiguration. Die Bruecke reicht jede Zeile
unveraendert per HTTP-POST an den Dienst auf dem Mini-PC weiter und schreibt dessen Antwort als
eine Zeile auf stdout. Der Dienst ist zustandslos, es gibt keine Sitzungskopfzeile.

Umgebung: AUFTRAGSBUCH_URL (Adresse des MCP-Weges) und AUFTRAGSBUCH_SCHLUESSEL (Bearer-Wert);
fehlt eine, endet der Start mit Exit 2 und einer Meldung, die nur den Namen nennt.
Optional AUFTRAGSBUCH_CA_DATEI: Pfad einer PEM-Datei mit CA-Zertifikaten. Ist sie gesetzt, prueft die
Bruecke TLS nur gegen diese Datei und nicht gegen den Zertifikatsspeicher des Systems (Windows kann
abgelaufene Zwischenzertifikate halten, die OpenSSL 1.1 dann waehlt). Ist die Datei nicht ladbar,
endet der Start mit Exit 2 und einer Meldung, die nur den Namen nennt.

Nur Standardbibliothek (laeuft unter dem Python des PC). Auf stdout stehen ausschliesslich
JSON-RPC-Zeilen. Auf stderr stehen nur Methode, Status und Dauer, nie Adresse, Schluessel,
Eingaben oder Ergebnisse. Fehler gehen als JSON-RPC-Fehlerantwort (Code -32000) an den Aufrufer,
nie als Absturz; auf eine Notification (ohne `id`) antwortet die Bruecke nie.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

ZEITLIMIT_S = 30
_FEHLER_CODE = -32000
_PARSE_FEHLER = -32700
_METHODE = re.compile(r"[A-Za-z0-9_./-]{1,64}")

Senden = Callable[[bytes], tuple[int, bytes]]


class _KeinRedirect(urllib.request.HTTPRedirectHandler):
    """Folgt keiner Weiterleitung: Der Schluessel darf nur an die konfigurierte Adresse gehen."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class CaDateiFehler(Exception):
    """AUFTRAGSBUCH_CA_DATEI ist nicht ladbar; der Text nennt nie den Pfad."""


def _ca_kontext(ca_datei: str) -> ssl.SSLContext:
    """TLS-Kontext, der nur die Zertifikate der Datei kennt (Pruefung und Hostname an).

    Bewusst weder `ssl.create_default_context()` noch `load_default_certs()`: Beide holten den
    Zertifikatsspeicher des Systems zurueck.
    """
    kontext = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    kontext.verify_mode = ssl.CERT_REQUIRED
    kontext.check_hostname = True
    try:
        kontext.load_verify_locations(cafile=ca_datei)
    except (OSError, ssl.SSLError, ValueError):
        raise CaDateiFehler("AUFTRAGSBUCH_CA_DATEI nicht ladbar") from None
    if kontext.cert_store_stats()["x509"] == 0:  # manche OpenSSL-Staende melden eine leere Datei nicht
        raise CaDateiFehler("AUFTRAGSBUCH_CA_DATEI nicht ladbar")
    return kontext


def _opener(ca_datei: str | None = None) -> urllib.request.OpenerDirector:
    """Eigener Opener ohne Proxy (Umgebung, Registry) und ohne Weiterleitung.

    Mit `ca_datei` (nicht leer) prueft er HTTPS nur gegen diese Datei; ohne bleibt es beim Standard.
    """
    handler: list[Any] = [urllib.request.ProxyHandler({}), _KeinRedirect()]
    if ca_datei:
        handler.append(urllib.request.HTTPSHandler(context=_ca_kontext(ca_datei)))
    return urllib.request.build_opener(*handler)


def http_senden(url: str, schluessel: str, ca_datei: str | None = None) -> Senden:
    """Baut die Sendefunktion: POST mit Bearer-Schluessel; liefert (Status, Koerper).

    Weder Proxy noch Weiterleitung (3xx kommt als Status zurueck). HTTP-Fehlerstatus kommen als
    Ergebnis zurueck, nicht als Ausnahme. Nicht erreichbar, Zeitlimit und Verbindungsabbruch
    sind OSError (urllib.error.URLError ist eine) bzw. HTTPException. Mit `ca_datei` siehe _opener;
    eine nicht ladbare Datei wirft CaDateiFehler schon hier, vor der ersten Anfrage.
    """
    kopf = {
        "Authorization": f"Bearer {schluessel}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    opener = _opener(ca_datei)

    def senden(koerper: bytes) -> tuple[int, bytes]:
        anfrage = urllib.request.Request(url, data=koerper, headers=kopf, method="POST")
        try:
            with opener.open(anfrage, timeout=ZEITLIMIT_S) as antwort:
                return antwort.status, antwort.read()
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read()
            finally:
                e.close()

    return senden


def _fehler(id_: Any, code: int, text: str) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": text}})


def _json_aus(koerper: bytes) -> Any | None:
    """Liest eine JSON-Antwort; auch aus einem Event-Stream (letzte `data:`-Zeile mit JSON)."""
    text = koerper.decode("utf-8", errors="replace").strip()
    kandidaten = [text]
    kandidaten += [z[5:].strip() for z in reversed(text.splitlines()) if z.startswith("data:")]
    for kandidat in kandidaten:
        try:
            wert = json.loads(kandidat)
        except ValueError:
            continue
        if isinstance(wert, dict):
            return wert
    return None


def _ist_zertifikatsfehler(fehler: BaseException) -> bool:
    """Wahr bei einem TLS-Pruefungsfehler, auch eingewickelt in urllib.error.URLError."""
    if isinstance(fehler, urllib.error.URLError):
        fehler = fehler.reason if isinstance(fehler.reason, BaseException) else fehler
    return isinstance(fehler, ssl.SSLCertVerificationError)


def _methode(nachricht: Any) -> str:
    wert = nachricht.get("method") if isinstance(nachricht, dict) else None
    return wert if isinstance(wert, str) and _METHODE.fullmatch(wert) else "?"


def _melden(methode: str, status: str, start: float) -> None:
    dauer = int((time.monotonic() - start) * 1000)
    print(f"auftragsbuch-bruecke methode={methode} status={status} dauer_ms={dauer}",
          file=sys.stderr, flush=True)


def weiterreichen(zeile: str, senden: Senden) -> str | None:
    """Reicht eine JSON-RPC-Zeile an den Dienst und liefert die Antwortzeile (None: keine Antwort)."""
    try:
        nachricht = json.loads(zeile)
    except ValueError:
        return _fehler(None, _PARSE_FEHLER, "auftragsbuch: ungültige Zeile")
    ist_anfrage = isinstance(nachricht, dict) and "id" in nachricht
    id_ = nachricht["id"] if ist_anfrage else None
    start = time.monotonic()
    methode = _methode(nachricht)
    try:
        status, koerper = senden(zeile.encode("utf-8"))
    except (OSError, http.client.HTTPException, ValueError) as fehler:
        if _ist_zertifikatsfehler(fehler):
            _melden(methode, "zertifikat", start)
            return _fehler(id_, _FEHLER_CODE, "auftragsbuch: Zertifikat nicht pruefbar") if ist_anfrage else None
        _melden(methode, "nicht-erreichbar", start)
        return _fehler(id_, _FEHLER_CODE, "auftragsbuch nicht erreichbar") if ist_anfrage else None
    _melden(methode, str(status), start)
    if status == 401:
        return _fehler(id_, _FEHLER_CODE, "auftragsbuch: Schlüssel abgelehnt") if ist_anfrage else None
    if not ist_anfrage:
        return None
    antwort = _json_aus(koerper) if status == 200 else None
    if antwort is None:
        return _fehler(id_, _FEHLER_CODE, f"auftragsbuch: unerwartete Antwort ({status})")
    return json.dumps(antwort, separators=(",", ":"))


def main() -> int:
    """Liest Zeilen von stdin, bis stdin endet; Exit 2 bei fehlender Einstellung."""
    url = os.environ.get("AUFTRAGSBUCH_URL", "").strip()
    schluessel = os.environ.get("AUFTRAGSBUCH_SCHLUESSEL", "").strip()
    for name, wert in (("AUFTRAGSBUCH_URL", url), ("AUFTRAGSBUCH_SCHLUESSEL", schluessel)):
        if not wert:
            print(f"auftragsbuch-bruecke: {name} fehlt", file=sys.stderr)
            return 2
    if not url.lower().startswith(("http://", "https://")):
        print("auftragsbuch-bruecke: AUFTRAGSBUCH_URL ungueltig", file=sys.stderr)
        return 2
    ca_datei = os.environ.get("AUFTRAGSBUCH_CA_DATEI", "").strip()
    try:
        senden = http_senden(url, schluessel, ca_datei or None)
    except CaDateiFehler as fehler:
        print(f"auftragsbuch-bruecke: {fehler}", file=sys.stderr)
        return 2
    for roh in sys.stdin.buffer:
        zeile = roh.decode("utf-8", errors="replace").strip()
        if not zeile:
            continue
        antwort = weiterreichen(zeile, senden)
        if antwort is not None:
            sys.stdout.buffer.write(antwort.encode("ascii") + b"\n")  # json.dumps maskiert Nicht-ASCII
            sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
