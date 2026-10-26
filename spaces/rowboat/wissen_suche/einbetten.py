"""Einbetter: holt Vektoren per HTTP von Ollama am PC (auf dem Mini-PC laeuft kein Modell).

Jede Stoerung auf dem Weg zum PC (kein Aufbau, Zeitueberschreitung, zurueckgesetzte Verbindung,
HTTP 5xx, HTTP 404 = Modell fehlt, unlesbare Antwort, falsche Dimension) wird zu
``PcNichtErreichbar``. Nur ein HTTP 400 auf einen Text wird zu ``EingabeAbgelehnt``.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Protocol

FRAGE_PRAEFIX = "Instruct: Given a question, retrieve knowledge-base notes that answer it\nQuery: "


class PcNichtErreichbar(Exception):
    """Ollama am PC ist nicht nutzbar (aus, Tailnet weg, Modell fehlt, Antwort unbrauchbar)."""


class EingabeAbgelehnt(Exception):
    """Ollama lehnt diesen Text ab (HTTP 400)."""


class Einbetter(Protocol):
    modell: str
    dim: int

    def dokumente(self, texte: list[str]) -> list[list[float]]: ...

    def frage(self, text: str) -> list[float]: ...


def _normieren(v: list[float]) -> list[float]:
    laenge = math.sqrt(sum(x * x for x in v))
    return [x / laenge for x in v] if laenge > 0 else list(v)


class _Antwort:
    """Antwort von ``_oeffnen`` mit derselben Form wie die von ``urlopen`` (``read`` + ``with``)."""

    def __init__(self, daten: bytes) -> None:
        self._daten = daten

    def read(self) -> bytes:
        return self._daten

    def __enter__(self) -> "_Antwort":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _oeffnen(req: urllib.request.Request, timeout: float, verbinden_s: float = 3.0) -> _Antwort:
    """Wie ``urlopen``, aber mit eigener Grenze fuer den Verbindungsaufbau.

    ``urlopen`` kennt nur eine Zeitgrenze je Socket-Operation. Ein ausgeschalteter PC im Tailnet
    beantwortet den Aufbau nie; dafuer gilt ``verbinden_s``. Fuer Senden und Lesen gilt ``timeout``,
    denn das erste Einbetten nach dem Laden des Modells dauert laenger als der Aufbau.
    """
    teile = urllib.parse.urlsplit(req.full_url)
    klasse = http.client.HTTPSConnection if teile.scheme == "https" else http.client.HTTPConnection
    verbindung = klasse(teile.hostname or "", teile.port, timeout=verbinden_s)
    try:
        verbindung.connect()
        if verbindung.sock is not None:
            verbindung.sock.settimeout(timeout)
        pfad = teile.path + (f"?{teile.query}" if teile.query else "")
        verbindung.request(req.get_method(), pfad or "/", body=req.data, headers=dict(req.header_items()))
        antwort = verbindung.getresponse()
        daten = antwort.read()
        if antwort.status >= 400:
            raise urllib.error.HTTPError(req.full_url, antwort.status, antwort.reason, antwort.headers, None)
        return _Antwort(daten)
    finally:
        verbindung.close()


class OllamaEinbetter:
    """Fragt ``POST {url}/api/embed`` und liefert L2-normierte Vektoren."""

    def __init__(self, url: str, modell: str, dim: int, num_ctx: int = 2048,
                 verbinden_s: float = 3.0, gesamt_s: float = 15.0,
                 oeffner: Callable[..., Any] | None = None) -> None:
        self.url = url.rstrip("/")
        self.modell = modell
        self.dim = dim
        self.num_ctx = num_ctx
        self.verbinden_s = verbinden_s
        self.gesamt_s = gesamt_s
        # Vorgabe: urlopen-gleicher Oeffner mit eigener Aufbau-Grenze; Tests reichen einen Fake hinein.
        self._oeffner = oeffner or (lambda req, timeout: _oeffnen(req, timeout, verbinden_s=self.verbinden_s))

    def dokumente(self, texte: list[str]) -> list[list[float]]:
        if not texte:
            return []
        return self._einbetten(list(texte), keep_alive="60s")

    def frage(self, text: str) -> list[float]:
        return self._einbetten([FRAGE_PRAEFIX + text], keep_alive="5m")[0]

    def _einbetten(self, texte: list[str], keep_alive: str) -> list[list[float]]:
        koerper = json.dumps({
            "model": self.modell,
            "input": texte,
            "truncate": True,
            "options": {"num_ctx": self.num_ctx},
            "keep_alive": keep_alive,
        }).encode("utf-8")
        req = urllib.request.Request(f"{self.url}/api/embed", data=koerper, method="POST",
                                     headers={"Content-Type": "application/json"})
        daten = self._mit_gesamtgrenze(req)
        try:
            vektoren = json.loads(daten.decode("utf-8"))["embeddings"]
        except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
            raise PcNichtErreichbar(f"unlesbare Antwort von Ollama: {type(exc).__name__}") from None
        if not isinstance(vektoren, list) or len(vektoren) != len(texte):
            raise PcNichtErreichbar("Ollama lieferte nicht je Text einen Vektor")
        ergebnis = []
        for v in vektoren:
            if not isinstance(v, list) or len(v) != self.dim:
                laenge = len(v) if isinstance(v, list) else "?"
                raise PcNichtErreichbar(f"falsche Dimension {laenge} statt {self.dim} ({self.modell})")
            try:
                ergebnis.append(_normieren([float(x) for x in v]))
            except (TypeError, ValueError):
                raise PcNichtErreichbar("Vektor enthaelt keine Zahlen") from None
        return ergebnis

    def _mit_gesamtgrenze(self, req: urllib.request.Request) -> bytes:
        """Fuehrt die Anfrage in einem Hilfsfaden aus und gibt nach ``gesamt_s`` auf.

        So haengt keine Frage laenger als ``gesamt_s``, auch wenn der Server Byte fuer Byte
        tropfen laesst. Der Faden laeuft dann im Hintergrund aus. Das "Leck" ist begrenzt: der
        Standard-Oeffner setzt dieselbe Grenze als Socket-Zeitgrenze (Aufbau ``verbinden_s``, jede
        Lese-Operation ``gesamt_s``), der Faden ist ``daemon`` und haelt keine Sperren; bei
        dauerhaft totem PC laeuft also hoechstens etwa ein Faden je Anfrage fuer kurze Zeit nach.
        """
        ergebnis: dict[str, Any] = {}

        def lauf() -> None:
            try:
                with self._oeffner(req, timeout=self.gesamt_s) as antwort:
                    ergebnis["daten"] = antwort.read()
            except BaseException as exc:  # noqa: BLE001 - wird unten eingeordnet
                ergebnis["fehler"] = exc

        faden = threading.Thread(target=lauf, name="ollama-embed", daemon=True)
        faden.start()
        faden.join(self.gesamt_s)
        if faden.is_alive():
            raise PcNichtErreichbar(f"keine Antwort von Ollama binnen {self.gesamt_s:g} s")
        fehler = ergebnis.get("fehler")
        if fehler is None:
            return ergebnis["daten"]
        if isinstance(fehler, urllib.error.HTTPError):
            if fehler.code == 400:
                raise EingabeAbgelehnt(f"Ollama lehnt den Text ab (HTTP 400, {self.modell})") from None
            raise PcNichtErreichbar(f"Ollama antwortet mit HTTP {fehler.code}") from None
        if isinstance(fehler, (urllib.error.URLError, OSError, http.client.HTTPException)):
            # OSError deckt Zeitueberschreitung, ConnectionReset und RemoteDisconnected ab.
            raise PcNichtErreichbar(f"Ollama nicht erreichbar: {type(fehler).__name__}") from None
        raise fehler


_BEGRIFFE = {"altersvorsorge": "rente", "rente": "rente", "pension": "rente", "rentenplanung": "rente",
             "hund": "tier", "katze": "tier"}


class FakeEinbetter:
    """Einbetter fuer Tests: Bag-of-Words in 64 Dimensionen mit kleinem Begriffsplan (Synonyme)."""

    modell = "fake"
    dim = 64

    def __init__(self, erreichbar: bool = True) -> None:
        self.erreichbar = erreichbar
        self.aufrufe = 0

    def _vektor(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for wort in re.findall(r"\w+", text.lower()):
            begriff = _BEGRIFFE.get(wort, wort)
            v[int(hashlib.sha256(begriff.encode("utf-8")).hexdigest(), 16) % self.dim] += 1.0
        return _normieren(v)

    def dokumente(self, texte: list[str]) -> list[list[float]]:
        self.aufrufe += 1
        if not self.erreichbar:
            raise PcNichtErreichbar("Fake: PC aus")
        return [self._vektor(t) for t in texte]

    def frage(self, text: str) -> list[float]:
        self.aufrufe += 1
        if not self.erreichbar:
            raise PcNichtErreichbar("Fake: PC aus")
        return self._vektor(text)
