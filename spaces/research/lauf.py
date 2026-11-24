"""Recherche-Lauf nach dem Rueckweg-Muster (24.11.2026): der Server holt, der Agent liest.

Hintergrund: Der Recherche-Agent laeuft auf dem Claude-Abo (provider claude-code). Der
claude-code-Treiber von OpenFang reicht keine MCP-Werkzeuge an das Modell weiter und liefert
``tool_calls`` immer leer (openfang-runtime drivers/claude_code.rs). Ein Agent auf diesem Weg
kann also weder ``fetch`` aufrufen noch eine Datei schreiben. Darum:

1. Modell-Lauf 1 (Quellen): Der Agent nennt hoechstens ``MAX_SEITEN`` Webadressen zum Auftrag.
2. Dieser Prozess holt die Seiten selbst (nur http/https, keine privaten oder lokalen Adressen,
   Groessen- und Zeitgrenze), als festen Aufruf ohne Modell.
3. Modell-Lauf 2 (Bericht): Der Agent bekommt Auftrag und Seitentexte und antwortet mit dem
   Bericht. Dieser Prozess schreibt ihn an den Pfad des Auftrags.

Grenzen je Recherche: hoechstens ``MAX_MODELL_LAEUFE`` Modell-Laeufe und ``MAX_SEITEN`` Seiten.
Vor jedem Modell-Lauf fragt der Prozess den Budget-Waechter (Modul aus
``VIBEMIND_BUDGET_MODUL``) und bucht danach. Ohne Waechter laeuft nichts (fail-closed).

Den Agenten findet der Prozess ueber seinen Namen (``GET /api/agents``), nie ueber eine feste
Kennung: ein neu erzeugter Agent bekommt eine neue.

Jeder Abbruch landet als Grund im spawn.log; ``research_status`` meldet dann ``failed``. Ein
Bericht, der keine der geholten Seiten nennt, wird nicht geschrieben.

Aufruf (vom Recherche-Server): ``python lauf.py <report_pfad> <request_datei>``.
Nur Standardbibliothek: der Prozess laeuft ohne Paketpfad.
"""
from __future__ import annotations

import html.parser
import http.client
import importlib.util
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

AGENT_NAME = "brain-researcher"
MAX_SEITEN = 5
MAX_MODELL_LAEUFE = 2
SEITE_MAX_BYTES = 2_000_000
SEITE_MAX_ZEICHEN = 12_000
SEITE_TIMEOUT_S = 20
MODELL_TIMEOUT_S = 1800
LISTE_TIMEOUT_S = 15
MAX_UMLEITUNGEN = 3

_URL_RE = re.compile(r"https?://[^\s<>()\]\[\"'`]+")


class LaufFehler(Exception):
    """Abbruch mit Grund fuer das spawn.log."""


# ── Agent ueber den Namen ────────────────────────────────────────────────────

def agent_id_aufloesen(agenten: Any, name: str = AGENT_NAME) -> str:
    """Kennung des einen Agenten mit diesem Namen; sonst ``LaufFehler`` (nie raten)."""
    if isinstance(agenten, dict):
        agenten = agenten.get("agents")
    if not isinstance(agenten, list):
        raise LaufFehler("agentenliste_ungueltig")
    treffer = [a for a in agenten
               if isinstance(a, dict) and str(a.get("name") or "").lower() == name.lower()]
    if not treffer:
        raise LaufFehler(f"agent_nicht_gefunden: {name}")
    if len(treffer) > 1:
        raise LaufFehler(f"agent_mehrdeutig: {len(treffer)} Agenten heissen {name}")
    kennung = treffer[0].get("id") or treffer[0].get("agent_id")
    if not isinstance(kennung, str) or not kennung.strip():
        raise LaufFehler(f"agent_ohne_kennung: {name}")
    return kennung.strip()


# ── Budget-Waechter ──────────────────────────────────────────────────────────

class Waechter:
    """Budget-Waechter (claude_budget: ``pruefen(agent)``, ``buchen(agent, ergebnis, dauer_s)``)."""

    def __init__(self, modul: Any, agent: str = AGENT_NAME) -> None:
        self.modul = modul
        self.agent = agent

    @classmethod
    def laden(cls, pfad: str, agent: str = AGENT_NAME) -> "Waechter":
        pfad = (pfad or "").strip()
        if not pfad:
            raise LaufFehler("budget_ohne_waechter: VIBEMIND_BUDGET_MODUL fehlt, ohne Waechter kein Lauf")
        spec = importlib.util.spec_from_file_location("claude_budget_ext", pfad)
        if spec is None or spec.loader is None:
            raise LaufFehler("budget_waechter_nicht_ladbar")
        modul = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = modul  # @dataclass sucht das Modul in sys.modules
        try:
            spec.loader.exec_module(modul)
        except Exception as exc:  # noqa: BLE001 - fail-closed
            sys.modules.pop(spec.name, None)
            raise LaufFehler(f"budget_waechter_nicht_ladbar: {type(exc).__name__}") from exc
        return cls(modul, agent)

    def pruefen(self) -> None:
        try:
            entscheidung = self.modul.pruefen(self.agent)
        except Exception as exc:  # noqa: BLE001 - fail-closed
            raise LaufFehler(f"budget_waechter_fehler: {type(exc).__name__}") from exc
        if not getattr(entscheidung, "erlaubt", False):
            raise LaufFehler(f"budget_abgelehnt: {getattr(entscheidung, 'grund', 'unbekannt')}")

    def buchen(self, ergebnis: str, dauer_s: float) -> None:
        try:
            self.modul.buchen(self.agent, ergebnis, dauer_s)
        except Exception as exc:  # noqa: BLE001 - eine Buchung kippt den Lauf nicht
            sys.stderr.write(f"budget: buchung fehlgeschlagen: {type(exc).__name__}\n")


# ── Seiten holen (fester Aufruf, kein Modell) ────────────────────────────────

def _adresse_erlaubt(host: str, aufloesen: Callable[..., Any] = socket.getaddrinfo) -> bool:
    """Nur oeffentliche Adressen: kein localhost, keine privaten, lokalen oder reservierten Netze."""
    if not host:
        return False
    try:
        eintraege = aufloesen(host, None)
    except OSError:
        return False
    if not eintraege:
        return False
    for eintrag in eintraege:
        try:
            ip = ipaddress.ip_address(eintrag[4][0].split("%", 1)[0])
        except (ValueError, IndexError, TypeError):
            return False
        if not ip.is_global or ip.is_multicast:
            return False
    return True


def url_erlaubt(url: str, aufloesen: Callable[..., Any] = socket.getaddrinfo) -> bool:
    try:
        teile = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if teile.scheme not in ("http", "https") or teile.username or teile.password:
        return False
    return _adresse_erlaubt(teile.hostname or "", aufloesen)


URL_MAX_ZEICHEN = 200


def url_bereinigen(roh: str) -> str | None:
    """Adresse des Modells als nicht vertrauenswuerdig behandeln: ohne Query und Fragment, kurz.

    Der Auftrag enthaelt die Notizen der Bubble. Eine frei gewaehlte Adresse koennte Teile davon
    in Query oder Pfad an einen fremden Server tragen (Sicherheitspruefung 24.11.2026). Darum:
    Query und Fragment fallen weg, Adressen ueber ``URL_MAX_ZEICHEN`` Zeichen werden verworfen.
    """
    try:
        teile = urllib.parse.urlsplit(roh)
    except ValueError:
        return None
    if teile.scheme not in ("http", "https") or not teile.hostname:
        return None
    url = urllib.parse.urlunsplit((teile.scheme, teile.netloc, teile.path or "/", "", ""))
    return url if len(url) <= URL_MAX_ZEICHEN else None


def quellen_aus_antwort(text: Any, grenze: int = MAX_SEITEN) -> list[str]:
    """Hoechstens ``grenze`` verschiedene http(s)-Adressen in der Reihenfolge der Antwort."""
    gefunden: list[str] = []
    for roh in _URL_RE.findall(text if isinstance(text, str) else ""):
        url = url_bereinigen(roh.rstrip(".,;:!?*_"))
        if url is None:
            continue
        if url not in gefunden:
            gefunden.append(url)
        if len(gefunden) >= grenze:
            break
    return gefunden


class _Text(html.parser.HTMLParser):
    _AUS = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.teile: list[str] = []
        self._tiefe_aus = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._AUS:
            self._tiefe_aus += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._AUS and self._tiefe_aus:
            self._tiefe_aus -= 1

    def handle_data(self, data: str) -> None:
        if not self._tiefe_aus and data.strip():
            self.teile.append(data.strip())


def text_aus_html(roh: str) -> str:
    leser = _Text()
    try:
        leser.feed(roh)
        leser.close()
    except Exception:  # noqa: BLE001 - kaputtes HTML liefert, was bis dahin lesbar war
        pass
    return re.sub(r"\s+", " ", " ".join(leser.teile)).strip()


class _Umleitung(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_UMLEITUNGEN

    def __init__(self, aufloesen: Callable[..., Any]) -> None:
        self.aufloesen = aufloesen

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if not url_erlaubt(newurl, self.aufloesen):
            raise urllib.error.URLError("umleitung_nicht_erlaubt")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def gegenstelle_pruefen(sock: Any) -> None:
    """Nach dem Verbinden: die tatsaechliche Gegenstelle muss oeffentlich sein.

    Die Pruefung vor dem Holen loest den Namen einmal auf; die Verbindung loest ihn erneut auf.
    Ein Name, der dazwischen auf eine interne Adresse wechselt (DNS-Rebinding), faellt hier auf.
    Gilt fuer jede Verbindung, also auch fuer jeden Umleitungsschritt.
    """
    try:
        ip = ipaddress.ip_address(str(sock.getpeername()[0]).split("%", 1)[0])
    except (OSError, ValueError, IndexError, TypeError) as exc:
        raise OSError("gegenstelle_unbekannt") from exc
    if not ip.is_global or ip.is_multicast:
        sock.close()
        raise OSError("gegenstelle_nicht_oeffentlich")


class _GepruefteHTTPVerbindung(http.client.HTTPConnection):
    def connect(self) -> None:
        super().connect()
        gegenstelle_pruefen(self.sock)


class _GepruefteHTTPSVerbindung(http.client.HTTPSConnection):
    def connect(self) -> None:
        # Erst TCP verbinden und pruefen, dann TLS (SNI und Host bleiben der Name).
        http.client.HTTPConnection.connect(self)
        gegenstelle_pruefen(self.sock)
        kontext = getattr(self, "_context", None) or ssl.create_default_context()
        self.sock = kontext.wrap_socket(self.sock, server_hostname=self.host)


class _GeprueftHTTP(urllib.request.HTTPHandler):
    def http_open(self, req):  # noqa: ANN001
        return self.do_open(_GepruefteHTTPVerbindung, req)


class _GeprueftHTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):  # noqa: ANN001
        return self.do_open(_GepruefteHTTPSVerbindung, req)


def _oeffner(aufloesen: Callable[..., Any]) -> Any:
    """Ohne Umgebungs-Proxy, mit Pruefung der Gegenstelle bei jeder Verbindung."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _GeprueftHTTP(),
                                       _GeprueftHTTPS(), _Umleitung(aufloesen))


def seite_holen(url: str, *, aufloesen: Callable[..., Any] = socket.getaddrinfo,
                oeffner: Any = None) -> str | None:
    """Text einer oeffentlichen Seite (gekuerzt) oder ``None``. Wirft nie."""
    if not url_erlaubt(url, aufloesen):
        return None
    oeffner = oeffner or _oeffner(aufloesen)
    anfrage = urllib.request.Request(url, headers={"User-Agent": "vibemind-recherche/1.0",
                                                   "Accept": "text/html,text/plain"})
    try:
        with oeffner.open(anfrage, timeout=SEITE_TIMEOUT_S) as antwort:
            art = str(antwort.headers.get("Content-Type") or "").lower()
            if art and not (art.startswith("text/html") or art.startswith("text/plain")
                            or art.startswith("application/xhtml")):
                return None
            roh = antwort.read(SEITE_MAX_BYTES + 1)[:SEITE_MAX_BYTES]
            zeichensatz = antwort.headers.get_content_charset() or "utf-8"
    except Exception:  # noqa: BLE001 - eine nicht holbare Seite faellt weg
        return None
    text = roh.decode(zeichensatz, errors="replace")
    if "html" in art or "<html" in text[:2000].lower():
        text = text_aus_html(text)
    text = text.strip()
    return text[:SEITE_MAX_ZEICHEN] if text else None


# ── Auftraege an das Modell ──────────────────────────────────────────────────

def quellen_auftrag(brief: str) -> str:
    return (
        f"Du bereitest eine Recherche vor. Du hast keine Werkzeuge; die Seiten holt der "
        f"Recherche-Server selbst. Nenne hoechstens {MAX_SEITEN} vollstaendige Webadressen "
        f"(https://...), die fuer diesen Auftrag verlaessliche Quellen sind und die es sicher "
        f"gibt. Antworte nur mit den Adressen, eine pro Zeile, ohne weiteren Text.\n\n{brief}"
    )


def bericht_auftrag(brief: str, seiten: Mapping[str, str]) -> str:
    bloecke = "\n\n".join(f"[Seite {i}] {url}\n{text}" for i, (url, text) in enumerate(seiten.items(), 1))
    return (
        f"{brief}\n\n[Geholte Seiten - nur darauf darf sich der Report stuetzen]\n{bloecke}\n\n"
        "Schreibe jetzt den vollstaendigen Report. Nenne jede verwendete Seite mit ihrer URL. "
        "Antworte nur mit dem Report."
    )


def zitierte_seiten(bericht: str, seiten: Mapping[str, str]) -> list[str]:
    return [url for url in seiten if url in (bericht or "")]


# ── Ablauf ───────────────────────────────────────────────────────────────────

Senden = Callable[[str], str]


def laufen(brief: str, *, senden: Senden, waechter: Waechter,
           holen: Callable[[str], str | None] | None = None) -> tuple[str, list[str]]:
    """Beide Modell-Laeufe und das Holen. Rueckgabe: (Bericht, zitierte Seiten). Wirft ``LaufFehler``."""
    holen = holen or seite_holen
    laeufe = 0

    def modell(auftrag: str) -> str:
        nonlocal laeufe
        if laeufe >= MAX_MODELL_LAEUFE:
            raise LaufFehler(f"grenze_modell_laeufe: hoechstens {MAX_MODELL_LAEUFE}")
        waechter.pruefen()
        laeufe += 1
        start = time.monotonic()
        try:
            antwort = senden(auftrag)
        except Exception as exc:
            waechter.buchen("fehler", time.monotonic() - start)
            raise LaufFehler(f"agent_lauf_fehlgeschlagen: {type(exc).__name__}: {exc}") from exc
        waechter.buchen("ok", time.monotonic() - start)
        return antwort

    adressen = quellen_aus_antwort(modell(quellen_auftrag(brief)))
    if not adressen:
        raise LaufFehler("keine_quellen: der Agent nannte keine Webadresse")
    seiten: dict[str, str] = {}
    for url in adressen[:MAX_SEITEN]:
        text = holen(url)
        if text:
            seiten[url] = text
    if not seiten:
        raise LaufFehler(f"keine_seite_holbar: {len(adressen)} Adressen, keine lesbar")
    bericht = (modell(bericht_auftrag(brief, seiten)) or "").strip()
    zitiert = zitierte_seiten(bericht, seiten)
    if not zitiert:
        raise LaufFehler("no citation found in report: der Bericht nennt keine geholte Seite")
    return bericht, zitiert


def _openfang(basis: str, schluessel: str) -> tuple[Callable[[str], Any], Callable[[str, str], str]]:
    kopf = {"Content-Type": "application/json"}
    if schluessel:
        kopf["Authorization"] = "Bearer " + schluessel

    def liste(pfad: str) -> Any:
        anfrage = urllib.request.Request(basis + pfad, headers=kopf, method="GET")
        with urllib.request.urlopen(anfrage, timeout=LISTE_TIMEOUT_S) as antwort:
            return json.loads(antwort.read().decode("utf-8"))

    def senden(kennung: str, text: str) -> str:
        daten = json.dumps({"message": text}, ensure_ascii=False).encode("utf-8")
        anfrage = urllib.request.Request(f"{basis}/api/agents/{kennung}/message",
                                         data=daten, headers=kopf, method="POST")
        with urllib.request.urlopen(anfrage, timeout=MODELL_TIMEOUT_S) as antwort:
            roh = antwort.read().decode("utf-8", errors="replace")
        try:
            inhalt = json.loads(roh)
        except ValueError:
            return roh
        if isinstance(inhalt, dict):
            return str(inhalt.get("response") or inhalt.get("text") or "")
        return ""

    return liste, senden


def main(argv: list[str], umgebung: Mapping[str, str] | None = None) -> int:
    umgebung = os.environ if umgebung is None else umgebung
    if len(argv) != 2:
        sys.stderr.write("aufruf: lauf.py <report_pfad> <request_datei>\n")
        return 2
    bericht_pfad, anfrage_pfad = Path(argv[0]), Path(argv[1])
    try:
        brief = json.loads(anfrage_pfad.read_text(encoding="utf-8"))["message"]
        waechter = Waechter.laden(umgebung.get("VIBEMIND_BUDGET_MODUL", ""))
        basis = (umgebung.get("OPENFANG_URL") or "http://127.0.0.1:4200").rstrip("/")
        liste, senden = _openfang(basis, umgebung.get("OPENFANG_API_KEY", "").strip())
        kennung = agent_id_aufloesen(liste("/api/agents"))
        bericht, _ = laufen(brief, senden=lambda text: senden(kennung, text), waechter=waechter)
        zwischen = bericht_pfad.with_suffix(".teil")
        zwischen.write_text(bericht, encoding="utf-8")
        zwischen.replace(bericht_pfad)  # research_status sieht nie einen halben Bericht
        return 0
    except LaufFehler as exc:
        sys.stderr.write(f"recherche_lauf_abgebrochen: {exc}\n")
        return 1
    except Exception as exc:  # noqa: BLE001 - jeder Abbruch braucht einen Grund im spawn.log
        sys.stderr.write(f"recherche_lauf_abgebrochen: {type(exc).__name__}: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
