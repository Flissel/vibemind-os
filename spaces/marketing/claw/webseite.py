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
import time
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Iterable
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


def _gepruefte_adressen(host: str, port: int, aufloesen, test_erlaubt: Iterable[str]) -> list[str]:
    """Alle Adressen des Ziels; gesperrt, sobald auch nur eine gesperrt ist."""
    host = host.strip("[]")
    try:
        kandidaten = [str(ipaddress.ip_address(host.split("%", 1)[0]))]
    except ValueError:
        try:
            infos = aufloesen(host, port, 0, socket.SOCK_STREAM)
        except Exception:
            raise LeseFehler("Adresse nicht auflösbar") from None
        kandidaten = [str(info[4][0]) for info in infos]
    if not kandidaten:
        raise LeseFehler("Adresse nicht auflösbar")
    erlaubt = set(test_erlaubt)
    for ip in kandidaten:
        if ip not in erlaubt and _gesperrt(ip):
            raise LeseFehler("Adresse gesperrt (privat oder lokal)")
    return list(dict.fromkeys(kandidaten))


def adresse_erlaubt(host: str, aufloesen, *, _test_erlaubt: Iterable[str] = ()) -> bool:
    try:
        _gepruefte_adressen(host, 0, aufloesen, _test_erlaubt)
    except LeseFehler:
        return False
    return True


# --- Abruf an einer festen IP ----------------------------------------------------

class _FesteHTTP(http.client.HTTPConnection):
    """Verbindet zur vorab geprueften IP; Host-Kopf bleibt der Name."""

    def __init__(self, host: str, ip: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip
        self.roh: socket.socket | None = None

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.roh = self.sock


class _FesteHTTPS(http.client.HTTPSConnection):
    """Wie _FesteHTTP; TLS-SNI und Zertifikatspruefung auf dem Namen."""

    def __init__(self, host: str, ip: str, port: int, timeout: float, context: ssl.SSLContext):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip = ip
        self.roh: socket.socket | None = None

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise
        self.roh = self.sock


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
    conn = (_FesteHTTPS(host, ip, port, _rest(frist), _tls()) if https
            else _FesteHTTP(host, ip, port, _rest(frist)))
    try:
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
            rest = _rest(frist)
            if conn.roh is not None:
                conn.roh.settimeout(rest)
            stueck = antwort.read1(65536)
            if not stueck:
                break
            gelesen += len(stueck)
            if gelesen > grenze:
                raise LeseFehler(f"zu groß (über {_groesse(grenze)})")
            stuecke.append(stueck)
        return antwort.status, kopf, b"".join(stuecke)
    except (socket.timeout, TimeoutError):
        raise LeseFehler("Zeitlimit überschritten") from None
    finally:
        conn.close()


Oeffnen = Callable[[str, str, int, float], "tuple[int, dict, bytes]"]


def _holen(url: str, aufloesen, oeffnen: Oeffnen, grenze: int,
           test_erlaubt: Iterable[str]) -> tuple[str, dict, bytes]:
    """Laedt `url` mit Umleitungen; jedes Ziel wird vor dem Verbinden geprueft."""
    frist = time.monotonic() + ZEITLIMIT_S
    for sprung in range(MAX_UMLEITUNGEN + 1):
        teile = urlsplit(url)
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
        for ip in _gepruefte_adressen(host, port, aufloesen, test_erlaubt):
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
            url = urldefrag(urljoin(url, ziel))[0]
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


def _dekodieren(body: bytes, kopf: dict) -> str:
    m = re.search(r"charset=([\w-]+)", kopf.get("content-type", ""), re.IGNORECASE)
    try:
        return body.decode(m.group(1) if m else "utf-8", errors="replace")
    except LookupError:
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
    return host[4:] if host.startswith("www.") else host


def _absolut(basis: str, ziele: list[str]) -> list[str]:
    out = []
    for z in ziele:
        voll = urldefrag(urljoin(basis, z.strip()))[0]
        if urlsplit(voll).scheme in ("http", "https"):
            out.append(voll)
    return out


def _hinweis(url: str, grund: object) -> str:
    return f"Webseite {url} nicht lesbar: {grund}"


def _grund(e: Exception) -> str:
    return str(e) if isinstance(e, LeseFehler) else f"unerwarteter Fehler ({type(e).__name__})"


def _seite_lesen(url, aufloesen, oeffnen, test_erlaubt) -> tuple[str, _Leser]:
    endurl, kopf, body = _holen(url, aufloesen, oeffnen, SEITE_MAX, test_erlaubt)
    art = kopf.get("content-type", "").lower()
    if art and "html" not in art:
        raise LeseFehler("keine HTML-Seite")
    leser = _Leser()
    leser.feed(_dekodieren(body, kopf))
    leser.close()
    return endurl, leser


def lesen(url: str, *, aufloesen=socket.getaddrinfo, oeffnen=None,
          _test_erlaubt: Iterable[str] = ()) -> Fund:
    """Startseite + hoechstens 5 Unterseiten derselben Domain. Wirft nie; Fehler
    stehen als "Webseite <url> nicht lesbar: <grund>" in `hinweise`.
    `_test_erlaubt` gibt einzelne IPs frei - nur fuer Tests, nie im Betrieb."""
    fund = Fund()
    oeffnen = oeffnen or _oeffnen
    test_erlaubt = frozenset(_test_erlaubt)
    start = url.strip()
    if "://" not in start:
        start = "https://" + start
    try:
        basis, leser = _seite_lesen(start, aufloesen, oeffnen, test_erlaubt)
    except Exception as e:
        fund.hinweise.append(_hinweis(url, _grund(e)))
        return fund

    budget = TEXT_MAX
    farben: Counter = Counter()
    schriften: Counter = Counter()
    schriftnamen: dict[str, str] = {}

    def aufnehmen(seiten_url: str, l: _Leser) -> None:
        nonlocal budget
        text = " ".join(" ".join(l.text).split())[:budget]
        budget -= len(text)
        fund.seiten.append(Seite(url=seiten_url, text=text, ueberschriften=l.ueberschriften))
        for css in l.css:
            _farben(css, farben)
            _schriften(css, schriften, schriftnamen)

    aufnehmen(basis, leser)
    for farbe in leser.themenfarben:
        _farben(":" + farbe, farben, THEMA_GEWICHT)

    google: list[str] = []        # Google-Fonts-Links: Namen aus der URL, nicht laden
    geladen = 0
    for stil in _absolut(basis, leser.stildateien):
        if urlsplit(stil).hostname == "fonts.googleapis.com":
            google += _google_schriften(stil)
            continue
        if geladen >= MAX_STILDATEIEN:
            continue
        geladen += 1
        try:
            _, kopf, body = _holen(stil, aufloesen, oeffnen, CSS_MAX, test_erlaubt)
        except Exception:
            continue            # fehlendes Stylesheet: weniger Material, kein Abbruch
        css = _dekodieren(body, kopf)
        _farben(css, farben)
        _schriften(css, schriften, schriftnamen)
    for name in google:
        _schrift_zaehlen(name, schriften, schriftnamen)

    logos = _absolut(basis, leser.logo_bilder + leser.touch_icons + leser.og_bilder + leser.icons)
    fund.logos = list(dict.fromkeys(logos))[:MAX_LOGOS]

    domain = _domain(urlsplit(basis).hostname)
    unterseiten = []
    for link in _absolut(basis, leser.links):
        teile = urlsplit(link)
        if (_domain(teile.hostname) == domain and link != basis and link not in unterseiten
                and not _KEINE_SEITE.search(teile.path)):
            unterseiten.append(link)
    for unter in unterseiten[:MAX_UNTERSEITEN]:
        if budget <= 0:
            break
        try:
            endurl, l = _seite_lesen(unter, aufloesen, oeffnen, test_erlaubt)
        except Exception as e:
            fund.hinweise.append(_hinweis(unter, _grund(e)))
            continue
        if _domain(urlsplit(endurl).hostname) == domain:
            aufnehmen(endurl, l)

    fund.farben = [f for f, _ in farben.most_common(MAX_FARBEN)]
    fund.schriften = [schriftnamen[s] for s, _ in schriften.most_common(MAX_SCHRIFTEN)]
    return fund


def logo_laden(url: str, *, aufloesen=socket.getaddrinfo,
               _test_erlaubt: Iterable[str] = ()) -> tuple[bytes, str] | None:
    """Logo-Datei mit denselben Sperren; nur PNG/JPEG bis 2 MB, sonst None."""
    try:
        _, _, body = _holen(url.strip(), aufloesen, _oeffnen, LOGO_MAX, frozenset(_test_erlaubt))
    except Exception:
        return None
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return body, "image/png"
    if body.startswith(b"\xff\xd8\xff"):
        return body, "image/jpeg"
    return None
