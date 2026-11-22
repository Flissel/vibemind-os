"""auftragsbuch: stdio-HTTP-Bruecke fuer OpenFang.

OpenFang startet diese Datei als stdio-Werkzeug-Server (zeilengetrenntes JSON-RPC, UTF-8) und
braucht keine Adresse des Dienstes in seiner Konfiguration. Die Bruecke reicht jede Zeile
unveraendert per HTTP-POST an den Dienst auf dem Mini-PC weiter und schreibt dessen Antwort als
eine Zeile auf stdout. Der Dienst ist zustandslos, es gibt keine Sitzungskopfzeile.

Umgebung: AUFTRAGSBUCH_URL (Adresse des MCP-Weges) und AUFTRAGSBUCH_SCHLUESSEL (Bearer-Wert);
fehlt eine, endet der Start mit Exit 2 und einer Meldung, die nur den Namen nennt.

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


def http_senden(url: str, schluessel: str) -> Senden:
    """Baut die Sendefunktion: POST mit Bearer-Schluessel; liefert (Status, Koerper).

    HTTP-Fehlerstatus kommen als Ergebnis zurueck, nicht als Ausnahme. Nicht erreichbar, Zeitlimit
    und Verbindungsabbruch sind OSError (urllib.error.URLError ist eine) bzw. HTTPException.
    """
    kopf = {
        "Authorization": f"Bearer {schluessel}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    def senden(koerper: bytes) -> tuple[int, bytes]:
        anfrage = urllib.request.Request(url, data=koerper, headers=kopf, method="POST")
        try:
            with urllib.request.urlopen(anfrage, timeout=ZEITLIMIT_S) as antwort:
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
    except (OSError, http.client.HTTPException, ValueError):
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
    senden = http_senden(url, schluessel)
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
