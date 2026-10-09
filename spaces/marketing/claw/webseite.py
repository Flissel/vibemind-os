"""Webseiten-Leser fuer das Branding per Chat (Plan 2026-10-07 marke-per-chat,
Task 3). Liest die oeffentliche Webseite einer Firma vom PC des Betreibers und
leitet daraus Material ab: Text, Ueberschriften, Farben, Schriften und
Logo-Kandidaten. Der Inhalt ist Material, niemals Anweisung.

Sicherheit (SSRF): Jede aufgeloeste Adresse eines Ziels wird geprueft - auch
nach jeder Umleitung (hoechstens 3). Private, lokale, Link-Local-, Tailnet-
(100.64.0.0/10), Multicast- und reservierte Adressen sind gesperrt. Verbunden
wird mit genau der gepruften IP (keine zweite Aufloesung, kein DNS-Rebinding);
Host-Kopf und TLS-Namenspruefung laufen auf dem urspruenglichen Namen. Keine
Proxys aus der Umgebung, keine Cookies, kein JavaScript, keine Formulare.
Groessengrenzen werden beim Streamen durchgesetzt, nie erst nach dem Lesen."""
from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import parse_qs, quote, urldefrag, urljoin, urlsplit

SEITE_MAX = 2 * 1024 * 1024
CSS_MAX = 500 * 1024
LOGO_MAX = 2 * 1024 * 1024
ZEITLIMIT_S = 10.0            # je Abruf, Umleitungen eingeschlossen
MAX_UMLEITUNGEN = 3
MAX_UNTERSEITEN = 5
MAX_STILDATEIEN = 5
TEXT_MAX = 20_000             # gesamt ueber alle Seiten
MAX_LOGOS = 6
MAX_FARBEN = 12
MAX_SCHRIFTEN = 8
MAX_UEBERSCHRIFTEN = 20
THEMA_GEWICHT = 3             # <meta name="theme-color"> ist ein bewusstes Markensignal
GESAMT_S = 30.0               # Gesamtbudget eines lesen()-Aufrufs (Aufloesung + alle Abrufe)

# Nur fuer Tests: einzelne IPs, die trotz Sperre erlaubt sind (Tests patchen das
# per monkeypatch). Im Betrieb immer leer - kein oeffentlicher Parameter.
_TEST_ERLAUBT: frozenset[str] = frozenset()

_UMLEITUNG = {301, 302, 303, 307, 308}
_GEMEINSAM = ipaddress.ip_network("100.64.0.0/10")      # CGNAT / Tailscale
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_KOPF = {"User-Agent": "VibeMind-Markenleser/1.0", "Accept": "text/html,text/css,image/*;q=0.8,*/*;q=0.5",
         "Accept-Encoding": "identity", "Connection": "close"}
_KEINE_SEITE = re.compile(r"\.(pdf|zip|jpe?g|png|gif|webp|svg|ico|mp4|mp3|docx?|xlsx?|pptx?|css|js|xml|json)$",
                          re.IGNORECASE)
_DEKLARATION = re.compile(r":\s*([^;{}]+)")
_HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-fA-F])")
_RGB = re.compile(r"rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})")
_FAMILIE = re.compile(r"font-family\s*:\s*([^;{}]+)", re.IGNORECASE)
_GENERISCH = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-serif", "ui-sans-serif",
              "ui-monospace", "ui-rounded", "emoji", "math", "fangsong", "inherit", "initial", "unset", "revert",
              "revert-layer", "-apple-system", "blinkmacsystemfont", "apple color emoji", "segoe ui emoji",
              "segoe ui symbol", "noto color emoji"}
_TEXT_AUS = {"script", "style", "noscript", "template", "svg", "iframe", "object"}
_UEBERSCHRIFT = {"h1", "h2", "h3"}


@dataclass
class Seite:
    url: str
    text: str
    ueberschriften: list[str]


@dataclass
class Fund:
    seiten: list[Seite] = field(default_factory=list)
    farben: list[str] = field(default_factory=list)
    schriften: list[str] = field(default_factory=list)
    logos: list[str] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)


class LeseFehler(Exception):
    """Grund, warum ein Ziel nicht gelesen wurde - fuer den Hinweis an den Menschen."""


# --- Adresssperre --------------------------------------------------------------

def _gesperrt(text: str) -> bool:
    try:
        a = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return True
    if a.version == 6:
        eingebettet = a.ipv4_mapped or a.sixtofour or (
            ipaddress.IPv4Address(int(a) & 0xFFFFFFFF) if a in _NAT64 else None)
        if eingebettet is not None:
            return _gesperrt(str(eingebettet))
        if a.is_site_local:
            return True
    elif a in _GEMEINSAM:
        return True
    return (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved
            or a.is_unspecified or not a.is_global)


def _aufloesen_bis(aufloesen, host: str, port: int, frist: float) -> list:
    """getaddrinfo kennt kein Zeitlimit - darum in einem Hilfsfaden mit join(rest)."""
    ergebnis: dict = {}

    def lauf():
        try:
            ergebnis["infos"] = aufloesen(host, port, 0, socket.SOCK_STREAM)
        except Exception as e:  # noqa: BLE001 - jeder Aufloesungsfehler ist "nicht aufloesbar"
            ergebnis["fehler"] = e

    faden = threading.Thread(target=lauf, name="webseite-dns", daemon=True)
    faden.start()
    faden.join(_rest(frist))
    if faden.is_alive():
        raise LeseFehler("Zeitlimit überschritten (Namensauflösung)")
    if "fehler" in ergebnis:
        raise LeseFehler("Adresse nicht auflösbar")
    return list(ergebnis.get("infos") or [])


def _gepruefte_adressen(host: str, port: int, aufloesen, frist: float) -> list[str]:
    """Alle Adressen des Ziels; gesperrt, sobald auch nur eine gesperrt ist."""
    host = host.strip("[]")
    try:
        kandidaten = [str(ipaddress.ip_address(host.split("%", 1)[0]))]
    except ValueError:
        kandidaten = [str(info[4][0]) for info in _aufloesen_bis(aufloesen, host, port, frist)]
    if not kandidaten:
        raise LeseFehler("Adresse nicht auflösbar")
    for ip in kandidaten:
        if ip not in _TEST_ERLAUBT and _gesperrt(ip):
            raise LeseFehler("Adresse gesperrt (privat oder lokal)")
    return list(dict.fromkeys(kandidaten))


def adresse_erlaubt(host: str, aufloesen) -> bool:
    try:
        _gepruefte_adressen(host, 0, aufloesen, time.monotonic() + ZEITLIMIT_S)
    except LeseFehler:
        return False
    return True


# --- Abruf an einer festen IP ----------------------------------------------------

class _Wache:
    """Harte Frist fuer einen Abruf. Socket-Timeouts gelten nur je recv - ein
    Server, der Kopfzeilen oder Chunk-Groessen troepfelt, haelt sie ewig offen.
    Darum schliesst ein Timer den Socket zur Frist (shutdown), egal wo der
    Leser gerade blockiert: Verbindungsaufbau, TLS, Kopf, Chunk oder Koerper."""

    def __init__(self, rest: float):
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self.abgelaufen = False
        self._timer = threading.Timer(rest, self._ausloesen)
        self._timer.daemon = True
        self._timer.start()

    def _zu(self) -> None:
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _ausloesen(self) -> None:
        with self._lock:
            self.abgelaufen = True
            self._zu()

    def bewachen(self, sock: socket.socket) -> None:
        with self._lock:
            self._sock = sock
            if self.abgelaufen:
                self._zu()

    def beenden(self) -> None:
        self._timer.cancel()


class _FesteHTTP(http.client.HTTPConnection):
    """Verbindet zur vorab geprueften IP; Host-Kopf bleibt der Name."""

    def __init__(self, host: str, ip: str, port: int, timeout: float, wache: _Wache):
        super().__init__(host, port, timeout=timeout)
        self._ip, self._wache = ip, wache

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)
        self._wache.bewachen(self.sock)


class _FesteHTTPS(http.client.HTTPSConnection):
    """Wie _FesteHTTP; TLS-SNI und Zertifikatspruefung auf dem Namen."""

    def __init__(self, host: str, ip: str, port: int, timeout: float, wache: _Wache,
                 context: ssl.SSLContext):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip, self._wache = ip, wache

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self._wache.bewachen(sock)                # Verbindungsaufbau bis hier
        try:
            # Handshake erst, wenn die Wache den TLS-Socket kennt: wrap_socket loest den rohen
            # Socket ab, ein shutdown auf ihm traefe waehrend des Handshakes ins Leere.
            tls = self._context.wrap_socket(sock, server_hostname=self.host, do_handshake_on_connect=False)
        except BaseException:
            sock.close()
            raise
        self._wache.bewachen(tls)                 # auch ein troepfelnder TLS-Handshake endet zur Frist
        try:
            tls.do_handshake()
        except BaseException:
            tls.close()
            raise
        self.sock = tls


_TLS: ssl.SSLContext | None = None


def _tls() -> ssl.SSLContext:
    """certifi, wenn vorhanden (Begruendung: workers/bild_worker.tls_kontext)."""
    global _TLS
    if _TLS is None:
        try:
            import certifi
            _TLS = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            _TLS = ssl.create_default_context()
    return _TLS


def _groesse(grenze: int) -> str:
    return f"{grenze // (1024 * 1024)} MB" if grenze >= 1024 * 1024 else f"{grenze // 1024} KB"


def _rest(frist: float) -> float:
    rest = frist - time.monotonic()
    if rest <= 0:
        raise LeseFehler("Zeitlimit überschritten")
    return rest


def _oeffnen(url: str, ip: str, grenze: int, frist: float) -> tuple[int, dict, bytes]:
    """Ein GET an `ip`; Koerper nur bei 2xx, hoechstens `grenze` Bytes, bis `frist`."""
    teile = urlsplit(url)
    host = teile.hostname or ""
    https = teile.scheme == "https"
    port = teile.port or (443 if https else 80)
    pfad = quote(teile.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if teile.query:
        pfad += "?" + quote(teile.query, safe="/%:@!$&'()*+,;=-._~?")
    rest = _rest(frist)
    wache, conn = None, None
    try:
        wache = _Wache(rest)                      # im try: scheitert die Verbindung, endet der Timer trotzdem
        conn = (_FesteHTTPS(host, ip, port, rest, wache, _tls()) if https
                else _FesteHTTP(host, ip, port, rest, wache))
        conn.request("GET", pfad, headers=_KOPF)
        antwort = conn.getresponse()
        kopf = {k.lower(): v for k, v in antwort.getheaders()}
        if not 200 <= antwort.status < 300:
            return antwort.status, kopf, b""
        laenge = kopf.get("content-length", "").strip()
        if laenge.isdigit() and int(laenge) > grenze:
            raise LeseFehler(f"zu groß (über {_groesse(grenze)})")
        stuecke, gelesen = [], 0
        while True:
            _rest(frist)
            stueck = antwort.read1(65536)
            if not stueck:
                break
            gelesen += len(stueck)
            if gelesen > grenze:
                raise LeseFehler(f"zu groß (über {_groesse(grenze)})")
            stuecke.append(stueck)
        if wache.abgelaufen:                      # shutdown sieht fuer read1 wie ein Ende aus
            raise LeseFehler("Zeitlimit überschritten")
        return antwort.status, kopf, b"".join(stuecke)
    except LeseFehler:
        raise
    except (socket.timeout, TimeoutError):
        raise LeseFehler("Zeitlimit überschritten") from None
    except Exception:
        if wache is not None and wache.abgelaufen:
            raise LeseFehler("Zeitlimit überschritten") from None
        raise
    finally:
        if wache is not None:
            wache.beenden()
        if conn is not None:
            conn.close()


Oeffnen = Callable[[str, str, int, float], "tuple[int, dict, bytes]"]


def _holen(url: str, aufloesen, oeffnen: Oeffnen, grenze: int,
           gesamt_frist: float | None = None) -> tuple[str, dict, bytes]:
    """Laedt `url` mit Umleitungen; jedes Ziel wird vor dem Verbinden geprueft.
    Frist = ZEITLIMIT_S fuer diesen Abruf, hoechstens bis `gesamt_frist`."""
    frist = time.monotonic() + ZEITLIMIT_S
    if gesamt_frist is not None:
        frist = min(frist, gesamt_frist)
    for sprung in range(MAX_UMLEITUNGEN + 1):
        try:
            teile = urlsplit(url)
        except ValueError:
            raise LeseFehler("ungültige Adresse") from None
        if teile.scheme not in ("http", "https"):
            raise LeseFehler("nur http und https erlaubt")
        try:
            host = (teile.hostname or "").encode("idna").decode("ascii")
            port = teile.port or (443 if teile.scheme == "https" else 80)
        except (UnicodeError, ValueError):
            raise LeseFehler("ungültige Adresse") from None
        if not host:
            raise LeseFehler("ungültige Adresse")
        # Netloc neu bauen: Zugangsdaten fallen weg, Umlaut-Domains werden IDNA.
        netloc = (f"[{host}]" if ":" in host else host) + (f":{teile.port}" if teile.port else "")
        url = teile._replace(netloc=netloc).geturl()
        letzter: Exception | None = None
        for ip in _gepruefte_adressen(host, port, aufloesen, frist):
            try:
                status, kopf, body = oeffnen(url, ip, grenze, frist)
                break
            except LeseFehler:
                raise
            except OSError as e:          # naechste geprufte Adresse versuchen
                letzter = e
        else:
            raise LeseFehler(f"keine Verbindung ({type(letzter).__name__})")
        if status in _UMLEITUNG:
            ziel = kopf.get("location", "").strip()
            if not ziel:
                raise LeseFehler("Umleitung ohne Ziel")
            if sprung == MAX_UMLEITUNGEN:
                raise LeseFehler(f"zu viele Umleitungen (mehr als {MAX_UMLEITUNGEN})")
            try:
                neu = urldefrag(urljoin(url, ziel))[0]
            except ValueError:
                raise LeseFehler("Umleitung auf ungültige Adresse") from None
            if teile.scheme == "https" and urlsplit(neu).scheme == "http":
                raise LeseFehler("Umleitung von https auf http abgelehnt")
            url = neu
            continue
        if not 200 <= status < 300:
            raise LeseFehler(f"HTTP {status}")
        if len(body) > grenze:
            raise LeseFehler(f"zu groß (über {_groesse(grenze)})")
        return url, kopf, body
    raise LeseFehler(f"zu viele Umleitungen (mehr als {MAX_UMLEITUNGEN})")  # pragma: no cover


# --- HTML und CSS auswerten ------------------------------------------------------

class _Leser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.ueberschriften: list[str] = []
        self.links: list[str] = []
        self.stildateien: list[str] = []
        self.css: list[str] = []
        self.logo_bilder: list[str] = []
        self.touch_icons: list[str] = []
        self.og_bilder: list[str] = []
        self.icons: list[str] = []
        self.themenfarben: list[str] = []
        self._aus = 0
        self._im_stil = False
        self._ueberschrift: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if a.get("style"):
            self.css.append(a["style"])
        if tag in _TEXT_AUS:
            self._aus += 1
            self._im_stil = tag == "style"
        elif tag in _UEBERSCHRIFT:
            self._ueberschrift = []
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "link" and a.get("href"):
            rel = a.get("rel", "").lower().split()
            if "stylesheet" in rel:
                self.stildateien.append(a["href"])
            elif "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel:
                self.touch_icons.append(a["href"])
            elif "icon" in rel:
                self.icons.append(a["href"])
        elif tag == "meta":
            name = (a.get("property") or a.get("name") or "").lower()
            if name in ("og:image", "og:image:url") and a.get("content"):
                self.og_bilder.append(a["content"])
            elif name == "theme-color" and a.get("content"):
                self.themenfarben.append(a["content"])
        elif tag == "img" and a.get("src"):
            merkmale = " ".join(a.get(k, "") for k in ("src", "alt", "class", "id")).lower()
            if "logo" in merkmale:
                self.logo_bilder.append(a["src"])

    def handle_endtag(self, tag):
        if tag in _TEXT_AUS and self._aus:
            self._aus -= 1
            self._im_stil = False
        elif tag in _UEBERSCHRIFT and self._ueberschrift is not None:
            text = " ".join(" ".join(self._ueberschrift).split())[:200]
            if text and len(self.ueberschriften) < MAX_UEBERSCHRIFTEN:
                self.ueberschriften.append(text)
            self._ueberschrift = None

    def handle_data(self, data):
        if self._aus:
            if self._im_stil:
                self.css.append(data)
            return
        self.text.append(data)
        if self._ueberschrift is not None:
            self._ueberschrift.append(data)


_META_CHARSET = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([\w-]+)", re.IGNORECASE)


def _dekodieren(body: bytes, kopf: dict) -> str:
    """Zeichensatz aus dem Kopf, sonst aus <meta charset> (erste 4 KB), sonst UTF-8."""
    m = re.search(r"charset=([\w-]+)", kopf.get("content-type", ""), re.IGNORECASE)
    zeichensatz = m.group(1) if m else None
    if zeichensatz is None:
        mm = _META_CHARSET.search(body[:4096])
        zeichensatz = mm.group(1).decode("ascii") if mm else "utf-8"
    try:
        return body.decode(zeichensatz, errors="replace")
    except (LookupError, UnicodeError, TypeError):   # unbekannt, nur strict (idna) oder kein Text-Codec
        return body.decode("utf-8", errors="replace")


def _farben(css: str, zaehler: Counter, gewicht: int = 1) -> None:
    for wert in _DEKLARATION.findall(css):
        for h in _HEX.findall(wert):
            h = "".join(c * 2 for c in h) if len(h) == 3 else h
            zaehler["#" + h.upper()] += gewicht
        for r, g, b in _RGB.findall(wert):
            if max(int(r), int(g), int(b)) <= 255:
                zaehler[f"#{int(r):02X}{int(g):02X}{int(b):02X}"] += gewicht


def _schriften(css: str, zaehler: Counter, namen: dict) -> None:
    for liste in _FAMILIE.findall(css):
        for teil in liste.replace("!important", "").split(","):
            _schrift_zaehlen(teil, zaehler, namen)


def _schrift_zaehlen(teil: str, zaehler: Counter, namen: dict) -> None:
    name = " ".join(teil.strip().strip("'\"").split())
    schluessel = name.lower()
    if not name or schluessel in _GENERISCH or "var(" in schluessel or len(name) > 60:
        return
    namen.setdefault(schluessel, name)
    zaehler[schluessel] += 1


def _google_schriften(url: str) -> list[str]:
    teile = urlsplit(url)
    if teile.hostname != "fonts.googleapis.com":
        return []
    out = []
    for familie in parse_qs(teile.query).get("family", []):
        out += [f.split(":", 1)[0] for f in familie.split("|")]
    return out


def _domain(host: str | None) -> str:
    host = (host or "").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    return host[4:] if host.startswith("www.") else host


def _absolut(basis: str, ziele: list[str]) -> list[str]:
    """Absolute http(s)-URLs ohne Fragment; kaputte Eintraege fallen still weg."""
    out = []
    for z in ziele:
        try:
            voll = urldefrag(urljoin(basis, z.strip()))[0]
            teile = urlsplit(voll)
            teile.port                     # wirft ValueError bei kaputtem Port
        except ValueError:
            continue
        if teile.scheme in ("http", "https") and teile.hostname:
            out.append(voll)
    return out


def _hinweis(url: str, grund: object) -> str:
    return f"Webseite {url} nicht lesbar: {grund}"


def _grund(e: Exception) -> str:
    return str(e) if isinstance(e, LeseFehler) else f"unerwarteter Fehler ({type(e).__name__})"


def _seite_lesen(url, aufloesen, oeffnen, gesamt_frist) -> tuple[str, _Leser]:
    endurl, kopf, body = _holen(url, aufloesen, oeffnen, SEITE_MAX, gesamt_frist)
    art = kopf.get("content-type", "").lower()
    if art and "html" not in art:
        raise LeseFehler("keine HTML-Seite")
    leser = _Leser()
    leser.feed(_dekodieren(body, kopf))
    leser.close()
    return endurl, leser


class _Sammlung:
    """Zwischenstand eines lesen()-Aufrufs; Text und Ueberschriften teilen sich TEXT_MAX."""

    def __init__(self, fund: Fund):
        self.fund = fund
        self.budget = TEXT_MAX
        self.farben: Counter = Counter()
        self.schriften: Counter = Counter()
        self.schriftnamen: dict[str, str] = {}

    def seite(self, seiten_url: str, leser: _Leser) -> None:
        ueberschriften = []
        for u in leser.ueberschriften:
            if len(u) > self.budget:
                break
            ueberschriften.append(u)
            self.budget -= len(u)
        text = " ".join(" ".join(leser.text).split())[:self.budget]
        self.budget -= len(text)
        self.fund.seiten.append(Seite(url=seiten_url, text=text, ueberschriften=ueberschriften))
        for css in leser.css:
            self.css(css)

    def css(self, css: str) -> None:
        _farben(css, self.farben)
        _schriften(css, self.schriften, self.schriftnamen)


def _auswerten(url: str, basis: str, leser: _Leser, aufloesen, oeffnen, gesamt_frist: float,
               s: _Sammlung) -> None:
    fund = s.fund
    s.seite(basis, leser)
    for farbe in leser.themenfarben:
        _farben(":" + farbe, s.farben, THEMA_GEWICHT)
    fund.logos = list(dict.fromkeys(_absolut(
        basis, leser.logo_bilder + leser.touch_icons + leser.og_bilder + leser.icons)))[:MAX_LOGOS]

    google: list[str] = []        # Google-Fonts-Links: Namen aus der URL, nicht laden
    stildateien = []
    for stil in _absolut(basis, leser.stildateien):
        if urlsplit(stil).hostname == "fonts.googleapis.com":
            google += _google_schriften(stil)
        elif len(stildateien) < MAX_STILDATEIEN:
            stildateien.append(stil)

    domain = _domain(urlsplit(basis).hostname)
    unterseiten: list[str] = []
    for link in _absolut(basis, leser.links):
        teile = urlsplit(link)
        if (_domain(teile.hostname) == domain and link != basis and link not in unterseiten
                and not _KEINE_SEITE.search(teile.path)):
            unterseiten.append(link)
    unterseiten = unterseiten[:MAX_UNTERSEITEN]

    uebersprungen = 0
    for stil in stildateien:
        if time.monotonic() >= gesamt_frist:
            uebersprungen += 1
            continue
        try:
            _, kopf, body = _holen(stil, aufloesen, oeffnen, CSS_MAX, gesamt_frist)
        except Exception:
            continue            # fehlendes Stylesheet: weniger Material, kein Abbruch
        s.css(_dekodieren(body, kopf))
    for name in google:
        _schrift_zaehlen(name, s.schriften, s.schriftnamen)

    for unter in unterseiten:
        if s.budget <= 0:
            break
        if time.monotonic() >= gesamt_frist:
            uebersprungen += 1
            continue
        try:
            endurl, l = _seite_lesen(unter, aufloesen, oeffnen, gesamt_frist)
        except Exception as e:
            fund.hinweise.append(_hinweis(unter, _grund(e)))
            continue
        if _domain(urlsplit(endurl).hostname) == domain:
            s.seite(endurl, l)
    if uebersprungen:
        fund.hinweise.append(_hinweis(
            url, f"Zeitbudget von {GESAMT_S:g} s aufgebraucht, {uebersprungen} Abrufe übersprungen"))


def lesen(url: str, *, aufloesen=socket.getaddrinfo, oeffnen=None) -> Fund:
    """Startseite + hoechstens 5 Unterseiten derselben Domain, alles zusammen in
    hoechstens GESAMT_S. Wirft nie; Fehler stehen als
    "Webseite <url> nicht lesbar: <grund>" in `hinweise`."""
    fund = Fund()
    oeffnen = oeffnen or _oeffnen
    gesamt_frist = time.monotonic() + GESAMT_S
    s = _Sammlung(fund)
    try:
        start = url.strip()
        if "://" not in start:
            start = "https://" + start
        basis, leser = _seite_lesen(start, aufloesen, oeffnen, gesamt_frist)
    except Exception as e:
        fund.hinweise.append(_hinweis(url, _grund(e)))
        return fund
    try:
        _auswerten(url, basis, leser, aufloesen, oeffnen, gesamt_frist, s)
    except Exception as e:      # wirft nie: was bis hier gesammelt ist, bleibt
        fund.hinweise.append(_hinweis(url, _grund(e)))
    fund.farben = [f for f, _ in s.farben.most_common(MAX_FARBEN)]
    fund.schriften = [s.schriftnamen[k] for k, _ in s.schriften.most_common(MAX_SCHRIFTEN)]
    return fund


def einzelseite(url: str, *, aufloesen=socket.getaddrinfo, oeffnen=None) -> Fund:
    """Nur diese eine Seite (fuer `lesen` des Marken-Agenten): Text und Ueberschriften, keine Unterseiten,
    keine Stildateien. Gleiche Adresssperre wie lesen; wirft nie."""
    fund = Fund()
    try:
        basis, leser = _seite_lesen(url.strip(), aufloesen, oeffnen or _oeffnen, time.monotonic() + GESAMT_S)
    except Exception as e:
        fund.hinweise.append(_hinweis(url, _grund(e)))
        return fund
    _Sammlung(fund).seite(basis, leser)
    return fund


def logo_laden(url: str, *, aufloesen=socket.getaddrinfo) -> tuple[bytes, str] | None:
    """Logo-Datei mit denselben Sperren; nur PNG/JPEG bis 2 MB, sonst None."""
    try:
        _, _, body = _holen(url.strip(), aufloesen, _oeffnen, LOGO_MAX)
    except Exception:
        return None
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return body, "image/png"
    if body.startswith(b"\xff\xd8\xff"):
        return body, "image/jpeg"
    return None
