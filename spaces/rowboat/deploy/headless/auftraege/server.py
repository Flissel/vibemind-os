"""MCP-Dienst ``rowboat-auftraege`` (Container ``rowboat-auftraege`` auf dem Mini-PC).

Genau ein Werkzeug, ``video_auftrag_aus_konzept(pfad)``: liest ein fertiges Videokonzept
(``knowledge/Konzepte/**/*.html``, hoechstens 1 MB) selbst aus dem nur lesend eingehaengten Wissensordner, prueft den
Datenblock wie die Rowboat-App (``konzept_lesen``, Nachbau von ``readKonzept``/``checkVideoKonzept``) und legt in
Sales eine VORBEREITETE Uebergabe an (``POST /api/auftraege/video``). Es entsteht kein Auftrag: den legt Sales erst an,
wenn Felix die Uebergabe auf der Werkbank-Seite hinter seinem Login bestaetigt (Spec Abschnitt 14). Die Datei wird
genau einmal geoeffnet und gelesen; dieselben Bytes gehen in die Pruefung und in den Rumpf.

Sales antwortet ``200 {"status": "vorbereitet", "vorgang": "<8 hex>", "link": "<URL>"}`` oder ``{"fehler": "<Satz>"}``
mit 400/401/409/413/429. Durchgereicht werden nur diese drei Felder, geprueft und gedeckelt; Saetze von Sales hoechstens
300 Zeichen ohne Steuerzeichen. Alles andere ist "unerwartet".

Bindet nur ``127.0.0.1`` (Host-Netz), Pfad ``/mcp``, dazu ``GET /health`` ohne Anmeldung. Jede andere Anfrage braucht
``Authorization: Bearer <Dienstschluessel>`` (Datei aus ``AUFTRAEGE_ZUGANG_DATEI``). Der Sales-Schluessel kommt aus
der Datei in ``SALES_SCHLUESSEL_DATEI``. Beide Dateien werden beim START gelesen (neuer Schluessel = Neustart);
fehlt eine oder ist sie leer, startet der Dienst nicht. Kein Schluessel steht je im Log, im ``repr`` oder in einer
Antwort. Protokoll ``AUFTRAEGE_LOG``: je Aufruf eine Zeile mit Zeit, Pfad, Ergebnis (``vorbereitet <vorgang>`` oder
Fehlerart), nie Konzeptinhalt. Kein Wiederholen: "unklar" heisst es nur, wenn die Anfrage moeglicherweise gesendet
wurde (Zeitlimit oder Abbruch danach); jeder Fehler davor sagt, dass nichts vorbereitet wurde.
"""
from __future__ import annotations

import errno
import functools
import hmac
import http.client
import json
import logging
import logging.handlers
import os
import re
import stat
import sys
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
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
ZEITLIMIT_S = 20                 # Verbinden + Senden + Antwortbeginn
KOERPER_ZEIT_S = 5               # danach eigenes Limit fuer den Antwortkoerper
DATEI_MAX = 1_048_576
PFAD_MAX = 500                   # wie Sales (pfad/name hoechstens 500 Zeichen)
RUMPF_MAX = 64 * 1024            # Sales nimmt hoechstens 64 KiB
ANTWORT_MAX = 64 * 1024
SATZ_VON_SALES_MAX = 300
LINK_MAX = 300
WERKZEUG = "video_auftrag_aus_konzept"
BESCHREIBUNG = ("Bereitet die Übergabe eines Videokonzepts an die Video-Werkbank des Ladens {laden} vor. "
                "Der Auftrag entsteht erst, wenn die Übergabe in Sales bestätigt wird.")

SATZ_PFAD_FEHLT = "Bitte den Pfad der Konzeptdatei angeben, z. B. knowledge/Konzepte/Name.html."
SATZ_PFAD_UNGUELTIG = "Der Pfad ist ungültig. Erwartet wird z. B. knowledge/Konzepte/Name.html."
SATZ_KANAL = "Konzepte unter knowledge/Kanal/ lassen sich nicht übergeben."
SATZ_NUR_KONZEPTE = "Übergeben lassen sich nur Konzepte unter knowledge/Konzepte/."
SATZ_ENDUNG = "Übergeben lassen sich nur .html-Dateien."
SATZ_NICHT_GEFUNDEN = "Die Konzeptdatei wurde nicht gefunden."
SATZ_NICHT_LESBAR = "Die Konzeptdatei lässt sich nicht lesen."
SATZ_ZU_GROSS = "Die Konzeptdatei ist größer als 1 MB."
SATZ_KEIN_BLOCK = "In der Datei steht kein Datenblock eines Videokonzepts."
SATZ_ZEICHEN = "Das Konzept enthält ungültige Zeichen (ein einzelnes Surrogat). Es wurde nichts vorbereitet."
SATZ_RUMPF_GROSS = "Das Konzept ist zu groß für die Übergabe."
SATZ_UNKLAR_ZEIT = ("Die Annahme hat nicht rechtzeitig geantwortet. Ob die Übergabe vorbereitet wurde, ist unklar – "
                    "bitte auf der Werkbank-Seite nachsehen, bevor du es noch einmal versuchst.")
SATZ_UNERWARTET = "Die Annahme hat unerwartet geantwortet. Bitte auf der Werkbank-Seite nachsehen."
SATZ_NICHT_ERREICHBAR = "Die Annahme ist gerade nicht erreichbar. Es wurde nichts vorbereitet."
SATZ_SALES_401 = "Die Annahme hat den Schlüssel dieses Dienstes nicht angenommen. Es wurde nichts vorbereitet."
SATZ_ABGELEHNT = "Die Annahme hat die Übergabe abgelehnt. Es wurde nichts vorbereitet."
SATZ_INTERN = "Im Dienst ist ein Fehler aufgetreten. Es wurde nichts vorbereitet."
SATZ_INTERN_UNKLAR = ("Im Dienst ist ein Fehler aufgetreten. Ob die Übergabe vorbereitet wurde, ist unklar – bitte "
                      "auf der Werkbank-Seite nachsehen, bevor du es noch einmal versuchst.")

_VORGANG = re.compile(r"[0-9a-f]{8}")
_SALES_ABLEHNUNG = (400, 409, 413, 429)          # Sales hat nichts vorbereitet und sagt warum


class StartFehler(RuntimeError):
    """Der Dienst startet nicht; die Meldung nennt nur Variablennamen, nie Werte oder Pfade."""


class NichtVerbunden(Exception):
    """Keine Verbindung zu Sales: es wurde nichts gesendet."""


class Unklar(Exception):
    """Verbindung stand, aber keine (vollstaendige) Antwort: Sales koennte vorbereitet haben."""


# --- Einstellungen --------------------------------------------------------------------------------------------

Ursprung = tuple[str, str, int]


@dataclass(frozen=True)
class Einstellungen:
    wissen: Path
    sales_url: str
    sales_schluessel: str = field(repr=False)
    zugang: str = field(repr=False)
    laden_anzeige: str
    log_datei: Path
    port: int
    sales_anzeige: Ursprung | None = None        # Ursprung aus SALES_UI_ANZEIGE_URL; Links muessen dorthin zeigen


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


def _ursprung(teile: urllib.parse.SplitResult) -> Ursprung:
    """(Schema, Host klein, Port) einer schon zerlegten http(s)-Adresse; ValueError bei kaputtem Port."""
    port = teile.port or (443 if teile.scheme == "https" else 80)
    return teile.scheme, (teile.hostname or "").lower(), port


def _http_adresse(wert: str) -> urllib.parse.SplitResult | None:
    teile = urllib.parse.urlsplit(wert)
    try:
        teile.port                                   # wirft bei "http://h:abc"
    except ValueError:
        return None
    if (teile.scheme not in ("http", "https") or not teile.hostname or teile.username or teile.password
            or teile.query or teile.fragment):
        return None
    return teile


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
    if _http_adresse(sales_url) is None:
        raise StartFehler("SALES_UI_URL muss eine http(s)-Adresse sein.")
    anzeige_text = (umgebung.get("SALES_UI_ANZEIGE_URL") or "").strip()
    anzeige: Ursprung | None = None
    if anzeige_text:
        teile = _http_adresse(anzeige_text)
        if teile is None:
            raise StartFehler("SALES_UI_ANZEIGE_URL muss eine http(s)-Adresse sein.")
        anzeige = _ursprung(teile)
    log_datei = (umgebung.get("AUFTRAEGE_LOG") or "").strip()
    if not log_datei:
        raise StartFehler("AUFTRAEGE_LOG ist nicht gesetzt.")
    try:
        port = int((umgebung.get("AUFTRAEGE_PORT") or "").strip() or PORT_VORGABE)
    except ValueError:
        raise StartFehler("AUFTRAEGE_PORT ist keine Zahl.") from None
    return Einstellungen(Path(wissen), sales_url, sales_schluessel, zugang, laden, Path(log_datei), port, anzeige)


# --- Pfad und Datei ------------------------------------------------------------------------------------------

def _seg_name(s: str) -> str:
    return s.rstrip(". ").lower()           # Windows ignoriert Punkte und Leerzeichen am Segmentende


def pfad_pruefen(pfad: object) -> tuple[list[str] | None, str | None]:
    """-> (Segmente ab ``Konzepte``, None) oder (None, Satz). Nur ``knowledge/Konzepte/**/*.html``, streng."""
    if not isinstance(pfad, str) or not pfad.strip():
        return None, SATZ_PFAD_FEHLT
    if (len(pfad) > PFAD_MAX or "\\" in pfad or ":" in pfad or pfad.startswith("/")
            or any(ord(z) < 0x20 or ord(z) == 0x7F or 0xD800 <= ord(z) <= 0xDFFF for z in pfad)):
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


def _ist_link(st: os.stat_result) -> bool:
    # Windows: jeder Reparse-Punkt (Symlink, Junction) zaehlt als Link; unter Linux gibt es das Feld nicht.
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_reparse_tag", 0))


_OEFFNEN = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOINHERIT", 0))


def datei_lesen(wissen: Path, teile: list[str]) -> tuple[bytes | None, str | None]:
    """Liest die Datei EINMAL (hoechstens DATEI_MAX Byte).

    Jedes Segment ab ``Konzepte`` (einschliesslich ``Konzepte`` selbst) wird mit ``lstat`` geprueft: kein Link, alle
    Zwischenstufen Ordner, das Ende eine regulaere Datei. Dann EIN ``open`` mit ``O_NOFOLLOW|O_NONBLOCK`` (eine FIFO
    blockiert so nicht) und die Pruefung am offenen Griff: regulaere Datei, Groesse, und dieselbe Datei (Geraet und
    Inode) wie beim Gang. Was gelesen wird, ist damit genau die geprueft erreichte Datei, auch wenn jemand dazwischen
    einen Ordner gegen einen Link tauscht.
    """
    ort = os.fspath(wissen)
    st: os.stat_result | None = None
    for i, teil in enumerate(teile):
        ort = os.path.join(ort, teil)
        try:
            st = os.lstat(ort)
        except (FileNotFoundError, NotADirectoryError):
            return None, SATZ_NICHT_GEFUNDEN
        except (OSError, ValueError):
            return None, SATZ_NICHT_LESBAR
        if _ist_link(st):
            return None, SATZ_PFAD_UNGUELTIG
        if i < len(teile) - 1 and not stat.S_ISDIR(st.st_mode):
            return None, SATZ_NICHT_GEFUNDEN
    if st is None or not stat.S_ISREG(st.st_mode):
        return None, SATZ_NICHT_GEFUNDEN
    if st.st_size > DATEI_MAX:
        return None, SATZ_ZU_GROSS
    try:
        fd = os.open(ort, _OEFFNEN)
    except FileNotFoundError:
        return None, SATZ_NICHT_GEFUNDEN
    except OSError as fehler:
        return None, SATZ_PFAD_UNGUELTIG if fehler.errno == errno.ELOOP else SATZ_NICHT_LESBAR
    with os.fdopen(fd, "rb") as f:
        offen = os.fstat(f.fileno())
        if not stat.S_ISREG(offen.st_mode):
            return None, SATZ_NICHT_GEFUNDEN
        if (offen.st_dev, offen.st_ino) != (st.st_dev, st.st_ino):
            return None, SATZ_PFAD_UNGUELTIG
        if offen.st_size > DATEI_MAX:
            return None, SATZ_ZU_GROSS
        roh = f.read(DATEI_MAX + 1)
    if len(roh) > DATEI_MAX:
        return None, SATZ_ZU_GROSS
    return roh, None


# --- Rumpf fuer Sales ----------------------------------------------------------------------------------------

def rumpf_bauen(video: dict, pfad: str) -> dict:
    """Rumpf von ``POST /api/auftraege/video``: nur bekannte Felder. Getrimmt wird wie in der App
    (``konzept_lesen.js_trim`` = ``String.prototype.trim``): ``beschreibung`` = trim(kernaussage) + Leerzeile +
    trim(abschluss)."""
    trim = konzept_lesen.js_trim
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
    return {"thema": trim(video["thema"]), "minuten": konzept["minuten"], "zielgruppe": trim(video["zielgruppe"]),
            "beschreibung": trim(video["kernaussage"]) + "\n\n" + trim(video["abschluss"]), "konzept": konzept}


# --- HTTP zu Sales -----------------------------------------------------------------------------------------

HttpSenden = Callable[[str, dict, bytes, float], tuple[int, bytes]]


def http_senden(url: str, kopf: dict, rumpf: bytes, zeitlimit: float) -> tuple[int, bytes]:
    """Ein POST ohne Wiederholen. NichtVerbunden: keine Verbindung (nichts gesendet). Unklar: Verbindung stand, aber
    kein Antwortbeginn binnen ``zeitlimit`` (ab Beginn gemessen), Abbruch, oder der Koerper kam nicht binnen
    ``KOERPER_ZEIT_S`` vollstaendig. Ein vollstaendig empfangener Koerper wird nie verworfen. Gelesen werden
    hoechstens ``ANTWORT_MAX + 1`` Byte (mehr deutet ``antwort_deuten`` als unerwartet)."""
    teile = urllib.parse.urlsplit(url)
    frist = time.monotonic() + zeitlimit
    klasse = http.client.HTTPSConnection if teile.scheme == "https" else http.client.HTTPConnection
    verbindung = klasse(teile.hostname, teile.port, timeout=zeitlimit)
    try:
        try:
            verbindung.connect()
        except OSError:
            raise NichtVerbunden() from None
        sock = verbindung.sock           # bleibt gueltig, auch wenn http.client die Verbindung nach der Antwort abgibt
        try:
            def rest(bis: float) -> float:
                r = bis - time.monotonic()
                if r <= 0:
                    raise TimeoutError()
                return r

            sock.settimeout(rest(frist))
            verbindung.request("POST", teile.path or "/", body=rumpf, headers=kopf)
            sock.settimeout(rest(frist))
            antwort = verbindung.getresponse()
            koerper_frist = time.monotonic() + KOERPER_ZEIT_S
            stuecke: list[bytes] = []
            n = 0
            while n <= ANTWORT_MAX:
                sock.settimeout(rest(koerper_frist))
                stueck = antwort.read1(ANTWORT_MAX + 1 - n)
                if not stueck:
                    break
                stuecke.append(stueck)
                n += len(stueck)
            return antwort.status, b"".join(stuecke)
        except (OSError, http.client.HTTPException):
            raise Unklar() from None
    finally:
        verbindung.close()


_WEG = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}


def satz_saeubern(s: object) -> str | None:
    """Satz von Sales fuer das Modell: Steuer-, Format- (Bidi, unsichtbare) und Trennzeichen raus, Leerraum zu einem
    Leerzeichen, hoechstens SATZ_VON_SALES_MAX Zeichen. None, wenn nichts Lesbares bleibt."""
    if not isinstance(s, str):
        return None
    sauber = "".join(" " if z.isspace() else z for z in s if z.isspace() or unicodedata.category(z) not in _WEG)
    sauber = " ".join(sauber.split())[:SATZ_VON_SALES_MAX].strip()
    return sauber or None


def link_gueltig(link: object, anzeige: Ursprung | None) -> bool:
    """http(s)-Adresse, hoechstens LINK_MAX Zeichen, nur druckbares ASCII, ohne Zugangsdaten; ist
    SALES_UI_ANZEIGE_URL gesetzt, muss der Ursprung genau dorthin zeigen."""
    if not isinstance(link, str) or not 0 < len(link) <= LINK_MAX or not all("!" <= z <= "~" for z in link):
        return False
    teile = urllib.parse.urlsplit(link)
    try:
        ursprung = _ursprung(teile)
    except ValueError:
        return False
    if teile.scheme not in ("http", "https") or not teile.hostname or teile.username or teile.password:
        return False
    return anzeige is None or ursprung == anzeige


def antwort_deuten(status: int, daten: bytes, anzeige: Ursprung | None = None) -> tuple[dict, str]:
    """-> (Ergebnis fuer den Agenten, Ergebnis fuers Protokoll)."""
    j: object = None
    if len(daten) <= ANTWORT_MAX:
        try:
            j = json.loads(daten.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            j = None
    if status == 200:
        if (isinstance(j, dict) and j.get("status") == "vorbereitet"
                and isinstance(j.get("vorgang"), str) and _VORGANG.fullmatch(j["vorgang"])
                and link_gueltig(j.get("link"), anzeige)):
            return ({"status": "vorbereitet", "vorgang": j["vorgang"], "link": j["link"]},
                    f"vorbereitet {j['vorgang']}")
        return {"fehler": SATZ_UNERWARTET}, "fehler unerwartet 200"
    if status == 401:
        return {"fehler": SATZ_SALES_401}, "fehler sales 401"
    if status in _SALES_ABLEHNUNG:
        satz = satz_saeubern(j.get("fehler")) if isinstance(j, dict) else None
        return {"fehler": satz or SATZ_ABGELEHNT}, f"fehler sales {status}"
    return {"fehler": SATZ_UNERWARTET}, f"fehler unerwartet {status}"


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

    def uebergeben(self, pfad: str) -> dict:
        lauf = {"gesendet": False}
        try:
            ergebnis, art = self._uebergeben(pfad, lauf)
        except Exception as fehler:  # noqa: BLE001 - nie eine Ausnahme (mit Text) an den Agenten
            if lauf["gesendet"]:
                ergebnis, art = {"fehler": SATZ_INTERN_UNKLAR}, f"fehler intern unklar {type(fehler).__name__}"
            else:
                ergebnis, art = {"fehler": SATZ_INTERN}, f"fehler intern {type(fehler).__name__}"
        zeigen = pfad[:300] if isinstance(pfad, str) else type(pfad).__name__
        self.protokoll.info("pfad=%s ergebnis=%s", json.dumps(zeigen), art)    # json.dumps: eine Zeile, ASCII
        return ergebnis

    def _uebergeben(self, pfad: str, lauf: dict) -> tuple[dict, str]:
        teile, satz = pfad_pruefen(pfad)
        if satz:
            return {"fehler": satz}, "fehler pfad"
        roh, satz = datei_lesen(self.e.wissen, teile)
        if satz:
            return {"fehler": satz}, "fehler pfad" if satz == SATZ_PFAD_UNGUELTIG else "fehler datei"
        # Wie die App: UTF-8, kaputte Folgen als U+FFFD, BOM bleibt Teil des Textes.
        daten, satz = konzept_lesen.lesen(roh.decode("utf-8", errors="replace"))
        satz = (satz or SATZ_KEIN_BLOCK) if daten is None else konzept_lesen.video_pruefen(daten)
        if satz:
            return {"fehler": satz}, "fehler konzept"
        try:
            rumpf = json.dumps(rumpf_bauen(daten["video"], pfad), ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            return {"fehler": SATZ_ZEICHEN}, "fehler zeichen"
        if len(rumpf) > RUMPF_MAX:
            return {"fehler": SATZ_RUMPF_GROSS}, "fehler zu_gross"
        kopf = {"Authorization": f"Bearer {self.e.sales_schluessel}", "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json"}
        lauf["gesendet"] = True              # ab hier koennte Sales etwas vorbereitet haben
        try:
            status, antwort = self.senden(self.e.sales_url + ANNAHME_PFAD, kopf, rumpf, self.zeitlimit)
        except NichtVerbunden:
            return {"fehler": SATZ_NICHT_ERREICHBAR}, "fehler nicht_erreichbar"
        except Unklar:
            return {"fehler": SATZ_UNKLAR_ZEIT}, "fehler unklar"
        return antwort_deuten(status, antwort, self.e.sales_anzeige)


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
    ) -> dict:
        return dienst.uebergeben(pfad)

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
