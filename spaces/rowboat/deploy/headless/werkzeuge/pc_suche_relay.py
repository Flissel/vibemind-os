"""Weiterleitung ``pc_suchen``: VM -> Suchdienst am PC (Tailnet ``100.84.127.84:8149``).

Ist der PC aus oder Tailscale am PC weg, antwortet die Weiterleitung nach hoechstens 3 s
Verbindungsaufbau mit der festen Meldung ``PC_AUS_MELDUNG``. Steht die Verbindung, aber der Dienst
antwortet nicht binnen 20 s, heisst es ``PC_ANTWORTET_NICHT`` (der PC ist dann an). Beides haengt den
Rowboat-Lauf nicht. Der Schluessel geht nur als Kopfzeile ``X-PC-Suche-Key`` hinaus
und erscheint nie im Ergebnis; Fehler nennen nur den Ausnahme-Typ.
"""
from __future__ import annotations

import http.client
import json
import urllib.parse
from typing import Any, Callable

PC_AUS_MELDUNG = "PC ist aus – auf dem PC kann ich gerade nicht suchen."
PC_ANTWORTET_NICHT = "PC antwortet nicht – die Suche dauerte zu lange oder der Suchdienst hängt."
NICHT_EINGERICHTET = "PC-Suche ist nicht eingerichtet (kein Schlüssel im Werkzeug-Container)."
ABGELEHNT = "PC-Suche lehnt ab (Schlüssel oder Absender)."

HttpPostJson = Callable[[str, dict[str, str], dict, float, float], tuple[int, Any]]

CONNECT_TIMEOUT_S = 3.0
READ_TIMEOUT_S = 20.0
ARTEN = ("name", "inhalt")
MAX_ANFRAGE = 200
MAX_ORDNER = 500
MAX_TREFFER = 50


class KeineAntwort(TimeoutError):
    """Verbindung stand, aber keine Antwort binnen Lese-Zeitlimit."""


def http_post_json(url: str, kopf: dict[str, str], koerper: dict, connect_timeout: float,
                   read_timeout: float) -> tuple[int, Any]:
    """POST mit getrennten Zeitlimits: ``connect_timeout`` fuer den Aufbau, danach ``read_timeout``."""
    teile = urllib.parse.urlsplit(url)
    if teile.scheme != "http" or not teile.hostname:
        raise ValueError("nur http://host:port erlaubt")
    verbindung = http.client.HTTPConnection(teile.hostname, teile.port or 80, timeout=connect_timeout)
    try:
        verbindung.connect()   # Fehler hier = PC aus (OSError)
        if verbindung.sock is None:
            raise ConnectionError("keine Verbindung")
        verbindung.sock.settimeout(read_timeout)
        daten = json.dumps(koerper).encode("utf-8")
        try:
            verbindung.request("POST", teile.path or "/", body=daten,
                               headers={**kopf, "Content-Type": "application/json"})
            antwort = verbindung.getresponse()
            roh = antwort.read()
        except TimeoutError as e:   # socket.timeout ist seit 3.10 TimeoutError
            raise KeineAntwort() from e
        try:
            inhalt = json.loads(roh.decode("utf-8")) if roh else None
        except ValueError:
            inhalt = None
        return antwort.status, inhalt
    finally:
        verbindung.close()


def pc_suchen(anfrage: str, art: str = "name", ordner: str = "", max_treffer: int = 20, *,
              url: str, schluessel: str | None, post: HttpPostJson = http_post_json) -> dict:
    """Sucht auf dem PC nach Dateinamen (``art="name"``) oder Inhalten (``art="inhalt"``)."""
    schluessel = (schluessel or "").strip()
    if not schluessel:
        return {"ok": False, "meldung": NICHT_EINGERICHTET}
    anfrage = (anfrage or "").strip()
    if not anfrage or len(anfrage) > MAX_ANFRAGE:
        return {"ok": False, "meldung": f"anfrage muss 1 bis {MAX_ANFRAGE} Zeichen haben"}
    if art not in ARTEN:
        return {"ok": False, "meldung": "art muss name oder inhalt sein"}
    ordner = (ordner or "").strip()
    if len(ordner) > MAX_ORDNER:
        return {"ok": False, "meldung": "ordner ist zu lang"}
    try:
        anzahl = max(1, min(MAX_TREFFER, int(max_treffer)))
    except (TypeError, ValueError):
        anzahl = 20
    koerper = {"anfrage": anfrage, "art": art, "ordner": ordner, "max_treffer": anzahl}
    try:
        status, daten = post(f"{url.rstrip('/')}/suchen", {"X-PC-Suche-Key": schluessel}, koerper,
                             CONNECT_TIMEOUT_S, READ_TIMEOUT_S)
    except KeineAntwort:
        return {"ok": False, "pc_aus": False, "meldung": PC_ANTWORTET_NICHT}
    except OSError:
        # Verbindung abgelehnt/zurueckgesetzt, Zeitlimit, kein Weg zum Host (URLError ist OSError)
        return {"ok": False, "pc_aus": True, "meldung": PC_AUS_MELDUNG}
    except Exception as e:  # noqa: BLE001 - nur der Typ, nie der Text
        return {"ok": False, "meldung": f"PC-Suche fehlgeschlagen: {type(e).__name__}"}
    if status == 403:
        return {"ok": False, "meldung": ABGELEHNT}
    if status != 200 or not isinstance(daten, dict):
        return {"ok": False, "meldung": f"PC-Suche antwortet {status}."}
    if not daten.get("ok"):
        return {"ok": False, "meldung": str(daten.get("meldung") or "PC-Suche meldet einen Fehler.")}
    return {"ok": True, "treffer": daten.get("treffer") or [], "abgeschnitten": bool(daten.get("abgeschnitten")),
            "dauer_ms": daten.get("dauer_ms")}
