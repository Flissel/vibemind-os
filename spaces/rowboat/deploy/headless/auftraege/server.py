"""MCP-Dienst ``rowboat-auftraege`` (Container ``rowboat-auftraege`` auf dem Mini-PC).

Genau ein Werkzeug, ``video_auftrag_aus_konzept(pfad, stand)``: liest ein fertiges Videokonzept
(``knowledge/Konzepte/**/*.html``, hoechstens 1 MB) selbst aus dem nur lesend eingehaengten Wissensordner, prueft den
Datenblock wie die Rowboat-App (``konzept_lesen``, Nachbau von ``readKonzept``/``checkVideoKonzept``) und schickt
ihn an die Sales-Annahme ``POST /api/auftraege/video``. Die Datei wird genau einmal gelesen; dieselben Bytes gehen in
den Hash (``stand``), in die Pruefung und in den Rumpf. ``stand`` ist der sha256 der Dateibytes, den Rowboats
Rueckfrage gezeigt hat (die App setzt ihn nach dem Ja); passt er nicht, geht nichts hinaus.

Bindet nur ``127.0.0.1`` (Host-Netz), Pfad ``/mcp``, dazu ``GET /health`` ohne Anmeldung. Jede andere Anfrage braucht
``Authorization: Bearer <Dienstschluessel>`` (Datei aus ``AUFTRAEGE_ZUGANG_DATEI``). Der Sales-Schluessel kommt aus
der Datei in ``SALES_SCHLUESSEL_DATEI``. Beide Dateien werden beim START gelesen (neuer Schluessel = Neustart);
fehlt eine oder ist sie leer, startet der Dienst nicht. Kein Schluessel steht je im Log oder in einer Antwort.
Protokoll ``AUFTRAEGE_LOG``: je Aufruf eine Zeile mit Zeit, Pfad, Ergebnis (Auftragsnummer oder Fehlerart), nie
Konzeptinhalt. Kein Wiederholen: bleibt Sales 20 s stumm oder bricht die Verbindung nach dem Senden ab, ist offen,
ob ein Auftrag entstand, und genau das bekommt der Agent gesagt.
"""
from __future__ import annotations

import functools
import hashlib
import hmac
import http.client
import json
import logging
import logging.handlers
import os
import stat
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Callable, Mapping, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import konzept_lesen  # noqa: E402
# Abhaengigkeit von mcp; auf Modulebene, weil FastMCP die (Zeichenketten-)Annotationen erst spaeter aufloest.
from pydantic import Field  # noqa: E402

log = logging.getLogger("auftraege")

HOST = "127.0.0.1"
PORT_VORGABE = 8156
SALES_URL_VORGABE = "http://127.0.0.1:8791"
ANNAHME_PFAD = "/api/auftraege/video"
ZEITLIMIT_S = 20
DATEI_MAX = 1_048_576
ANTWORT_MAX = 64 * 1024
WERKZEUG = "video_auftrag_aus_konzept"
BESCHREIBUNG = "Übergibt ein Videokonzept als Auftrag an die Video-Werkbank des Ladens {laden}."

SATZ_PFAD_FEHLT = "Bitte den Pfad der Konzeptdatei angeben, z. B. knowledge/Konzepte/Name.html."
SATZ_PFAD_UNGUELTIG = "Der Pfad ist ungültig. Erwartet wird z. B. knowledge/Konzepte/Name.html."
SATZ_KANAL = "Konzepte unter knowledge/Kanal/ lassen sich nicht übergeben."
SATZ_NUR_KONZEPTE = "Übergeben lassen sich nur Konzepte unter knowledge/Konzepte/."
SATZ_ENDUNG = "Übergeben lassen sich nur .html-Dateien."
SATZ_NICHT_GEFUNDEN = "Die Konzeptdatei wurde nicht gefunden."
SATZ_NICHT_LESBAR = "Die Konzeptdatei lässt sich nicht lesen."
SATZ_ZU_GROSS = "Die Konzeptdatei ist größer als 1 MB."
SATZ_STAND = "Das Konzept hat sich seit der Rückfrage geändert. Bitte noch einmal übergeben."
SATZ_KEIN_BLOCK = "In der Datei steht kein Datenblock eines Videokonzepts."
SATZ_ZEICHEN = "Das Konzept enthält ungültige Zeichen (ein einzelnes Surrogat). Es wurde nichts übergeben."
SATZ_UNKLAR_ZEIT = ("Die Annahme hat nicht rechtzeitig geantwortet. Ob der Auftrag angelegt wurde, ist unklar – bitte "
                    "auf der Werkbank-Seite nachsehen, bevor du es noch einmal versuchst.")
SATZ_UNKLAR_ANTWORT = ("Die Annahme hat mit einem Fehler geantwortet. Ob der Auftrag angelegt wurde, ist unklar – bitte "
                       "auf der Werkbank-Seite nachsehen, bevor du es noch einmal versuchst.")
SATZ_NICHT_ERREICHBAR = "Die Annahme ist gerade nicht erreichbar. Es wurde nichts übergeben."
SATZ_SALES_401 = "Die Annahme hat den Schlüssel dieses Dienstes nicht angenommen. Es wurde nichts übergeben."
SATZ_ABGELEHNT = "Die Annahme hat den Auftrag abgelehnt. Es wurde nichts übergeben."
SATZ_INTERN = ("Im Dienst ist ein Fehler aufgetreten. Ob der Auftrag angelegt wurde, ist unklar – bitte auf der "
               "Werkbank-Seite nachsehen, bevor du es noch einmal versuchst.")


class StartFehler(RuntimeError):
    """Der Dienst startet nicht; die Meldung nennt nur Variablennamen, nie Werte oder Pfade."""


class NichtVerbunden(Exception):
    """Keine Verbindung zu Sales: es wurde nichts gesendet."""


class Unklar(Exception):
    """Verbindung stand, aber keine (vollstaendige) Antwort: Sales koennte angelegt haben."""


# --- Einstellungen --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Einstellungen:
    wissen: Path
    sales_url: str
    sales_schluessel: str
    zugang: str
    laden_anzeige: str
    log_datei: Path
    port: int


def _schluessel_aus_datei(umgebung: Mapping[str, str], name: str) -> str:
    pfad = (umgebung.get(name) or "").strip()
    if not pfad:
        raise StartFehler(f"{name} ist nicht gesetzt.")
    try:
        wert = Path(pfad).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise StartFehler(f"Die Schlüsseldatei aus {name} fehlt oder ist nicht lesbar.") from None
    if not wert:
        raise StartFehler(f"Die Schlüsseldatei aus {name} ist leer.")
    if not all("!" <= z <= "~" for z in wert):
        raise StartFehler(f"Die Schlüsseldatei aus {name} enthält unzulässige Zeichen (erlaubt: druckbares ASCII).")
    return wert


def einstellungen_aus(umgebung: Mapping[str, str]) -> Einstellungen:
    zugang = _schluessel_aus_datei(umgebung, "AUFTRAEGE_ZUGANG_DATEI")
    sales_schluessel = _schluessel_aus_datei(umgebung, "SALES_SCHLUESSEL_DATEI")
    laden = (umgebung.get("AUFTRAEGE_LADEN_ANZEIGE") or "").strip()
    if not laden:
        raise StartFehler("AUFTRAEGE_LADEN_ANZEIGE ist nicht gesetzt.")
    if len(laden) > 100 or any(ord(z) < 0x20 or ord(z) == 0x7F for z in laden):
        raise StartFehler("AUFTRAEGE_LADEN_ANZEIGE muss eine Zeile mit höchstens 100 Zeichen sein.")
    wissen = (umgebung.get("WISSEN_ORDNER") or "").strip()
    if not wissen or not Path(wissen).is_dir():
        raise StartFehler("WISSEN_ORDNER ist nicht gesetzt oder kein Ordner.")
    sales_url = ((umgebung.get("SALES_UI_URL") or "").strip() or SALES_URL_VORGABE).rstrip("/")
    teile = urllib.parse.urlsplit(sales_url)
    if teile.scheme not in ("http", "https") or not teile.hostname or teile.query or teile.fragment:
        raise StartFehler("SALES_UI_URL muss eine http(s)-Adresse sein.")
    log_datei = (umgebung.get("AUFTRAEGE_LOG") or "").strip()
    if not log_datei:
        raise StartFehler("AUFTRAEGE_LOG ist nicht gesetzt.")
    try:
        port = int((umgebung.get("AUFTRAEGE_PORT") or "").strip() or PORT_VORGABE)
    except ValueError:
        raise StartFehler("AUFTRAEGE_PORT ist keine Zahl.") from None
    return Einstellungen(Path(wissen), sales_url, sales_schluessel, zugang, laden, Path(log_datei), port)


# --- Pfad und Datei ------------------------------------------------------------------------------------------

def _seg_name(s: str) -> str:
    return s.rstrip(". ").lower()           # Windows ignoriert Punkte und Leerzeichen am Segmentende


def pfad_pruefen(pfad: object) -> tuple[list[str] | None, str | None]:
    """-> (Segmente ab ``Konzepte``, None) oder (None, Satz). Nur ``knowledge/Konzepte/**/*.html``, streng."""
    if not isinstance(pfad, str) or not pfad.strip():
        return None, SATZ_PFAD_FEHLT
    if (len(pfad) > 1000 or "\\" in pfad or ":" in pfad or pfad.startswith("/")
            or any(ord(z) < 0x20 or ord(z) == 0x7F for z in pfad)):
        return None, SATZ_PFAD_UNGUELTIG
    teile = pfad.split("/")
    if ".." in teile:
        return None, SATZ_PFAD_UNGUELTIG
    if len(teile) >= 2 and _seg_name(teile[0]) == "knowledge" and _seg_name(teile[1]) == "kanal":
        return None, SATZ_KANAL
    if any(t in ("", ".") or t != t.rstrip(". ") for t in teile):
        return None, SATZ_PFAD_UNGUELTIG
    if len(teile) < 3 or teile[0] != "knowledge" or teile[1] != "Konzepte":
        return None, SATZ_NUR_KONZEPTE
    if not teile[-1].lower().endswith(".html") or len(teile[-1]) <= len(".html"):
        return None, SATZ_ENDUNG
    return teile[1:], None


def datei_lesen(wissen: Path, teile: list[str]) -> tuple[bytes | None, str | None]:
    """Liest die Datei EINMAL (hoechstens DATEI_MAX Byte). Der aufgeloeste Pfad muss unter ``Konzepte/`` bleiben."""
    basis = os.path.realpath(wissen / "Konzepte")
    ziel = os.path.realpath(os.path.join(wissen, *teile))
    try:
        drinnen = os.path.commonpath([basis, ziel]) == basis and ziel != basis
    except ValueError:                      # anderes Laufwerk
        drinnen = False
    if not drinnen or not ziel.lower().endswith(".html"):
        return None, SATZ_PFAD_UNGUELTIG
    if not os.path.isfile(ziel):
        return None, SATZ_NICHT_GEFUNDEN
    try:
        fd = os.open(ziel, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    except FileNotFoundError:
        return None, SATZ_NICHT_GEFUNDEN
    except OSError:
        return None, SATZ_NICHT_LESBAR
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            return None, SATZ_NICHT_GEFUNDEN
        if st.st_size > DATEI_MAX:
            return None, SATZ_ZU_GROSS
        roh = f.read(DATEI_MAX + 1)
    if len(roh) > DATEI_MAX:
        return None, SATZ_ZU_GROSS
    return roh, None


def stand_passt(stand: object, roh: bytes) -> bool:
    if not isinstance(stand, str) or not stand.strip():
        return False
    return hmac.compare_digest(stand.strip().lower().encode("utf-8"), hashlib.sha256(roh).hexdigest().encode("ascii"))


# --- Rumpf fuer Sales ----------------------------------------------------------------------------------------

def rumpf_bauen(video: dict, pfad: str) -> dict:
    """Rumpf von ``POST /api/auftraege/video``: nur bekannte Felder. ``beschreibung`` genau so, wie Sales sie
    nachrechnet (``video_konzept.beschreibung_aus``: Python-``strip``)."""
    datei = pfad.rsplit("/", 1)[-1]
    konzept: dict[str, Any] = {
        "thema": video["thema"], "zielgruppe": video["zielgruppe"], "minuten": int(video["minuten"]),
        "hook": video["hook"], "kernaussage": video["kernaussage"],
        "stufen": [{"name": s["name"],
                    "szenen": [{"kurz": sz["kurz"], "bildart": sz["bildart"],
                                **({"notiz": sz["notiz"]} if "notiz" in sz else {})} for sz in s["szenen"]]}
                   for s in video["stufen"]],
        "abschluss": video["abschluss"], "auftrag": video["auftrag"],
    }
    if "firma" in video:
        konzept["firma"] = video["firma"]
    konzept["pfad"] = pfad
    konzept["name"] = datei[: -len(".html")]
    return {"thema": video["thema"].strip(), "minuten": konzept["minuten"], "zielgruppe": video["zielgruppe"].strip(),
            "beschreibung": video["kernaussage"].strip() + "\n\n" + video["abschluss"].strip(), "konzept": konzept}


# --- HTTP zu Sales -----------------------------------------------------------------------------------------

HttpSenden = Callable[[str, dict, bytes, float], tuple[int, bytes]]


def http_senden(url: str, kopf: dict, rumpf: bytes, zeitlimit: float) -> tuple[int, bytes]:
    """Ein POST ohne Wiederholen. NichtVerbunden: keine Verbindung (nichts gesendet). Unklar: Verbindung stand,
    aber keine Antwort binnen ``zeitlimit`` (ab Beginn gemessen) oder Abbruch."""
    teile = urllib.parse.urlsplit(url)
    frist = time.monotonic() + zeitlimit
    klasse = http.client.HTTPSConnection if teile.scheme == "https" else http.client.HTTPConnection
    verbindung = klasse(teile.hostname, teile.port, timeout=zeitlimit)
    try:
        try:
            verbindung.connect()
        except OSError:
            raise NichtVerbunden() from None
        try:
            def rest() -> float:
                r = frist - time.monotonic()
                if r <= 0:
                    raise TimeoutError()
                return r

            verbindung.sock.settimeout(rest())
            verbindung.request("POST", teile.path or "/", body=rumpf, headers=kopf)
            verbindung.sock.settimeout(rest())
            antwort = verbindung.getresponse()
            rest()
            daten = antwort.read(ANTWORT_MAX)
            rest()
            return antwort.status, daten
        except (OSError, http.client.HTTPException):
            raise Unklar() from None
    finally:
        verbindung.close()


def antwort_deuten(status: int, daten: bytes) -> tuple[dict, str]:
    """-> (Ergebnis fuer den Agenten, Fehlerart fuers Protokoll)."""
    try:
        j = json.loads(daten.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        j = None
    if status == 200:
        if (isinstance(j, dict) and konzept_lesen._ist_auftragsnummer(j.get("auftrag"))
                and isinstance(j.get("id"), str) and isinstance(j.get("link"), str)):
            return {"auftrag": j["auftrag"], "id": j["id"], "link": j["link"]}, f"auftrag {j['auftrag']}"
        return {"fehler": SATZ_UNKLAR_ANTWORT}, "fehler unklar 200"
    if status == 401:
        return {"fehler": SATZ_SALES_401}, "fehler sales 401"
    if 400 <= status < 500:
        satz = j.get("fehler") if isinstance(j, dict) else None
        satz = satz.strip()[:500] if isinstance(satz, str) and satz.strip() else SATZ_ABGELEHNT
        return {"fehler": satz}, f"fehler sales {status}"
    return {"fehler": SATZ_UNKLAR_ANTWORT}, f"fehler unklar {status}"


# --- Der Dienst ----------------------------------------------------------------------------------------------

class Dienst:
    def __init__(self, e: Einstellungen, senden: HttpSenden = http_senden, zeitlimit: float = ZEITLIMIT_S) -> None:
        self.e = e
        self.senden = senden
        self.zeitlimit = zeitlimit
        self.protokoll = logging.Logger("auftraege.uebergaben")      # nicht im globalen Register
        try:
            handler = logging.handlers.RotatingFileHandler(e.log_datei, maxBytes=1_000_000, backupCount=3,
                                                           encoding="utf-8")
        except OSError:
            raise StartFehler("Die Protokolldatei aus AUFTRAEGE_LOG lässt sich nicht öffnen.") from None
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        self.protokoll.addHandler(handler)

    def schliessen(self) -> None:
        for h in list(self.protokoll.handlers):
            h.close()
            self.protokoll.removeHandler(h)

    def uebergeben(self, pfad: str, stand: str) -> dict:
        try:
            ergebnis, art = self._uebergeben(pfad, stand)
        except Exception as fehler:  # noqa: BLE001 - nie eine Ausnahme (mit Text) an den Agenten
            ergebnis, art = {"fehler": SATZ_INTERN}, f"fehler intern {type(fehler).__name__}"
        zeigen = pfad[:300] if isinstance(pfad, str) else type(pfad).__name__
        self.protokoll.info("pfad=%s ergebnis=%s", json.dumps(zeigen), art)    # json.dumps: eine Zeile, ASCII
        return ergebnis

    def _uebergeben(self, pfad: str, stand: str) -> tuple[dict, str]:
        teile, satz = pfad_pruefen(pfad)
        if satz:
            return {"fehler": satz}, "fehler pfad"
        roh, satz = datei_lesen(self.e.wissen, teile)
        if satz:
            return {"fehler": satz}, "fehler pfad" if satz == SATZ_PFAD_UNGUELTIG else "fehler datei"
        if not stand_passt(stand, roh):
            return {"fehler": SATZ_STAND}, "fehler stand"
        # Wie die App: UTF-8, kaputte Folgen als U+FFFD, BOM bleibt Teil des Textes.
        daten, satz = konzept_lesen.lesen(roh.decode("utf-8", errors="replace"))
        satz = (satz or SATZ_KEIN_BLOCK) if daten is None else konzept_lesen.video_pruefen(daten)
        if satz:
            return {"fehler": satz}, "fehler konzept"
        try:
            rumpf = json.dumps(rumpf_bauen(daten["video"], pfad), ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            return {"fehler": SATZ_ZEICHEN}, "fehler zeichen"
        kopf = {"Authorization": f"Bearer {self.e.sales_schluessel}", "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json"}
        try:
            status, antwort = self.senden(self.e.sales_url + ANNAHME_PFAD, kopf, rumpf, self.zeitlimit)
        except NichtVerbunden:
            return {"fehler": SATZ_NICHT_ERREICHBAR}, "fehler nicht_erreichbar"
        except Unklar:
            return {"fehler": SATZ_UNKLAR_ZEIT}, "fehler unklar zeit"
        return antwort_deuten(status, antwort)


# --- Zugang am MCP-Endpunkt ------------------------------------------------------------------------------------

class Zugangswache:
    """ASGI-Huelle: ausser ``GET /health`` braucht jede Anfrage ``Authorization: Bearer <Dienstschluessel>``."""

    def __init__(self, app: Any, zugang: str) -> None:
        self.app = app
        self._erwartet = zugang.encode("ascii")

    def _erlaubt(self, kopfzeilen: list) -> bool:
        werte = [v for k, v in kopfzeilen if k.lower() == b"authorization"]
        if len(werte) != 1:
            return False
        art, _, token = werte[0].partition(b" ")
        return art.lower() == b"bearer" and hmac.compare_digest(token.strip(), self._erwartet)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            offen = scope.get("path") == "/health" and scope.get("method") == "GET"
            if not offen and not self._erlaubt(scope.get("headers") or []):
                from starlette.responses import JSONResponse
                antwort = JSONResponse({"fehler": "Nicht berechtigt."}, status_code=401,
                                       headers={"WWW-Authenticate": "Bearer"})
                await antwort(scope, receive, send)
                return
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await self.app(scope, receive, send)


def im_thread(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Synchrones Werkzeug als async-Huelle im Arbeiter-Thread (Muster werkzeuge/server.py)."""
    import anyio

    @functools.wraps(fn)
    async def huelle(**kwargs: Any) -> Any:
        return await anyio.to_thread.run_sync(functools.partial(fn, **kwargs))

    return huelle


class Aufbau(NamedTuple):
    mcp: Any
    app: Any
    dienst: Dienst


def bauen(umgebung: Mapping[str, str] = os.environ, *, senden: HttpSenden = http_senden,
          zeitlimit: float = ZEITLIMIT_S) -> Aufbau:
    """Server mit genau einem Werkzeug. StartFehler, wenn eine Einstellung oder Schluesseldatei fehlt."""
    from mcp.server.fastmcp import FastMCP
    from starlette.responses import JSONResponse

    e = einstellungen_aus(umgebung)
    dienst = Dienst(e, senden, zeitlimit)

    def video_auftrag_aus_konzept(
        pfad: Annotated[str, Field(description="Pfad der Konzeptdatei im Arbeitsordner, z. B. "
                                               "knowledge/Konzepte/Name.html")],
        stand: Annotated[str, Field(description="Leer lassen; die App setzt den Stand nach dem Ja selbst.")],
    ) -> dict:
        return dienst.uebergeben(pfad, stand)

    mcp = FastMCP("rowboat-auftraege", host=HOST, port=e.port, log_level="WARNING",
                  stateless_http=True, json_response=True, streamable_http_path="/mcp")
    mcp.tool(name=WERKZEUG, description=BESCHREIBUNG.format(laden=e.laden_anzeige))(im_thread(video_auftrag_aus_konzept))

    @mcp.custom_route("/health", methods=["GET"])
    async def _gesund(_request):  # noqa: ANN001
        return JSONResponse({"status": "ok"})

    return Aufbau(mcp, Zugangswache(mcp.streamable_http_app(), e.zugang), dienst)


def main() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    try:
        aufbau = bauen(os.environ)
    except StartFehler as e:
        log.error("rowboat-auftraege startet nicht: %s", e)
        sys.exit(2)
    import uvicorn

    log.info("rowboat-auftraege: %s:%d/mcp", HOST, aufbau.mcp.settings.port)
    uvicorn.run(aufbau.app, host=HOST, port=aufbau.mcp.settings.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
