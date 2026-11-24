"""Dienst des Coding-Space am Rechner: Seite, Lesen, Anstoss und Aufsicht in einem.

Nur Standardbibliothek. Gebunden an ``127.0.0.1``.

- Lesen (GET): ``/`` (Seite), ``/health`` (ohne Dateizugriff), ``/api/liste``,
  ``/api/stand``, ``/api/ergebnis``.
- Schreiben (POST): ``/api/auftrag``, ``/api/abbrechen``. Nur mit dem Kopf
  ``X-Coding-Schluessel``, dessen Wert der Dienst aus ``CODING_DIENST_SCHLUESSEL``
  liest. Ist die Variable nicht gesetzt, sind die POST-Wege aus.
- Anstoss: dieser Dienst ist die eine Stelle, die den Erzeuger der Engine
  startet - als Kindprozess mit sauberer Umgebung, hoechstens einer zugleich.
- Aufsicht: er kennt den Prozess. Endet der Erzeuger ohne Abschluss, gilt der
  Auftrag als fehlgeschlagen (unterbrochen); ``abbrechen`` haelt ihn samt
  Kindprozessen an.

Start:  python -m spaces.coding.dienst [--port 8163]
"""

from __future__ import annotations

import argparse
import collections
import hmac
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from spaces.coding import engine_huelle, pipeline

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
DEFAULT_PORT = 8163
ORDNER = Path(__file__).resolve().parent
STATISCH: Dict[str, Tuple[str, str]] = {
    "/": ("seite.html", "text/html; charset=utf-8"),
    "/seite.js": ("seite.js", "text/javascript; charset=utf-8"),
    "/seite.css": ("seite.css", "text/css; charset=utf-8"),
}
# Kein Inline-Skript, kein fremder Ursprung: die Seite laedt nur ihre eigenen Dateien.
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'"
KOPF_SCHLUESSEL = "X-Coding-Schluessel"
GROESSTER_KOERPER = 1_000_000

# Variablen von Windows, die ein Kindprozess zum Laufen braucht. Alles andere bleibt draussen.
SYSTEM = {
    "systemroot", "systemdrive", "windir", "path", "pathext", "comspec", "temp", "tmp", "userprofile",
    "username", "userdomain", "homedrive", "homepath", "appdata", "localappdata", "programdata",
    "programfiles", "programfiles(x86)", "programw6432", "commonprogramfiles", "commonprogramfiles(x86)",
    "commonprogramw6432", "public", "os", "computername", "number_of_processors",
    "processor_architecture", "processor_identifier", "home", "lang",
}


def _zahl(name: str, vorgabe: int) -> int:
    try:
        return int(os.environ.get(name, "") or vorgabe)
    except ValueError:
        return vorgabe


def saubere_umgebung(auftrag: Dict[str, Any]) -> Dict[str, str]:
    """Umgebung des Erzeugers: Systemvariablen, OpenFang und die Grenzen dieses Auftrags - sonst nichts."""
    umgebung = {k: v for k, v in os.environ.items() if k.lower() in SYSTEM}
    umgebung.update({
        "OPENFANG_URL": os.environ.get("OPENFANG_URL", "http://127.0.0.1:4200"),
        "OPENFANG_API_KEY": os.environ.get("OPENFANG_API_KEY", ""),
        "LLM_BACKEND": "openfang",
        "OPENFANG_MAX_CALLS": str(_zahl("CODING_DIENST_MAX_AUFRUFE", 40)),
        "VIBEMIND_CODING_AGENT": os.environ.get("VIBEMIND_CODING_AGENT", "brain-coder"),
        "ENGINE_AUDIT_LOG": str(Path(auftrag["projektordner"]) / "pruefspur.log"),
        "PYTHONIOENCODING": "utf-8",
        # npm install nur fuer Auftraege mit Paketen, und nur mit genug freiem Speicher.
        "ENGINE_NPM_INSTALL": "0" if auftrag.get("ohne_npm", True) else "1",
        "ENGINE_MIN_FREE_COMMIT_GB": str(_zahl("CODING_DIENST_MIN_FREI_GB", 8)),
    })
    return umgebung


def erzeuger_befehl(auftrag: Dict[str, Any]) -> List[str]:
    """Der eine Befehl, mit dem der Erzeuger der Engine gestartet wird."""
    return [
        sys.executable, "run_generation.py",
        "--project-path", auftrag["projektordner"], "--output-dir", auftrag["zielordner"],
        "--parallelism", "1", "--no-loop", "--skip-task-gen",
        # Scheitert ein Paket, bauen die spaeteren darauf auf: die Kette endet dort.
        "--stop-on-failed-epic",
    ]


def engine_ordner() -> Path:
    roh = os.environ.get("CODING_ENGINE_PATH")
    return Path(roh) if roh else ORDNER.parents[1] / "coding-engine"


class Aufsicht:
    """Startet den Erzeuger, hoechstens einen zugleich, und weiss, wie er geendet hat."""

    def __init__(
        self,
        befehl: Callable[[Dict[str, Any]], List[str]] = erzeuger_befehl,
        arbeitsordner: Optional[Callable[[], Path]] = None,
        zeitgrenze_s: Optional[float] = None,
    ) -> None:
        self._befehl = befehl
        self._arbeitsordner = arbeitsordner or engine_ordner
        self.zeitgrenze_s = float(zeitgrenze_s if zeitgrenze_s is not None else _zahl("CODING_DIENST_ZEITGRENZE_S", 1800))
        self._sperre = threading.RLock()
        self._reihe: Deque[str] = collections.deque()
        self._laufend: Optional[Tuple[str, subprocess.Popen, float]] = None

    # ── Reihe ──────────────────────────────────────────────────────────────

    def anstoss(self, auftrag: Dict[str, Any]) -> bool:
        """Die eine Stelle: nimmt den Auftrag in die Reihe und startet, wenn nichts laeuft."""
        with self._sperre:
            self._reihe.append(auftrag["id"])
        self.pruefen()
        return True

    def lage(self) -> Dict[str, Any]:
        with self._sperre:
            return {"laeuft": self._laufend[0] if self._laufend else None, "reihe": list(self._reihe)}

    # ── Lebenslauf ─────────────────────────────────────────────────────────

    def _starten(self, auftrag_id: str) -> None:
        auftrag = pipeline._laden(auftrag_id)
        if auftrag["zustand"] in pipeline.ENDZUSTAENDE:
            return  # in der Reihe abgebrochen oder schon erledigt
        ziel = Path(auftrag["zielordner"])
        ziel.mkdir(parents=True, exist_ok=True)
        with open(Path(auftrag["projektordner"]) / "lauf.log", "ab") as protokoll:
            try:
                prozess = subprocess.Popen(  # noqa: S603 - fester Befehl, keine Shell
                    self._befehl(auftrag), cwd=str(self._arbeitsordner()), env=saubere_umgebung(auftrag),
                    stdin=subprocess.DEVNULL, stdout=protokoll, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
            except OSError as fehler:
                pipeline.als_unterbrochen_markieren(auftrag_id, f"der Erzeuger liess sich nicht starten ({fehler})")
                return
        self._laufend = (auftrag_id, prozess, time.time())
        logger.info("Erzeuger gestartet fuer Auftrag %s (pid %s)", auftrag_id, prozess.pid)

    def pruefen(self) -> None:
        """Einmal nachsehen: ist der Erzeuger zu Ende, ueber der Zeit, kann der naechste starten?"""
        with self._sperre:
            if self._laufend:
                auftrag_id, prozess, beginn = self._laufend
                if prozess.poll() is None:
                    if time.time() - beginn <= self.zeitgrenze_s:
                        return
                    self._anhalten(prozess)
                    self._abschliessen(auftrag_id, f"Zeitgrenze von {int(self.zeitgrenze_s)} s ueberschritten")
                else:
                    self._abschliessen(auftrag_id, f"der Erzeuger endete ohne Abschluss (Code {prozess.returncode})")
                self._laufend = None
            while self._laufend is None and self._reihe:
                self._starten(self._reihe.popleft())

    def _abschliessen(self, auftrag_id: str, grund: str) -> None:
        auftrag = pipeline._laden(auftrag_id)
        ziel = Path(auftrag["zielordner"])
        (ziel / engine_huelle.LAUF_DATEI).unlink(missing_ok=True)
        if not (ziel / engine_huelle.STATUS_DATEI).is_file() and auftrag["zustand"] != pipeline.ABGEBROCHEN:
            pipeline.als_unterbrochen_markieren(auftrag_id, grund)

    @staticmethod
    def _anhalten(prozess: subprocess.Popen) -> None:
        """Haelt den Erzeuger samt Kindprozessen an."""
        if prozess.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(prozess.pid), "/T", "/F"],  # noqa: S603, S607
                           capture_output=True, check=False)
        else:
            prozess.kill()
        try:
            prozess.wait(timeout=10)
        except subprocess.TimeoutExpired:
            prozess.kill()

    def abbrechen(self, auftrag_id: Optional[str]) -> Dict[str, Any]:
        with self._sperre:
            sicht = pipeline.abbrechen(auftrag_id)
            kennung = sicht["auftrag"]
            if kennung in self._reihe:
                self._reihe.remove(kennung)
            if self._laufend and self._laufend[0] == kennung:
                self._anhalten(self._laufend[1])
                self._abschliessen(kennung, "abgebrochen")
                self._laufend = None
        self.pruefen()
        return pipeline.stand(kennung)

    def verwaiste_schliessen(self) -> List[str]:
        """Beim Start des Dienstes: was noch auf ``laeuft`` steht, hat keinen Prozess mehr."""
        geschlossen: List[str] = []
        for auftrag in pipeline._alle():
            if auftrag.get("art") == "engine" and auftrag["zustand"] == pipeline.LAEUFT:
                (Path(auftrag["zielordner"]) / engine_huelle.LAUF_DATEI).unlink(missing_ok=True)
                pipeline.als_unterbrochen_markieren(auftrag["id"], "der Dienst wurde waehrend des Laufs neu gestartet")
                geschlossen.append(auftrag["id"])
        return geschlossen

    def wache(self, halt: threading.Event, takt_s: float = 1.0) -> None:
        while not halt.wait(takt_s):
            try:
                self.pruefen()
            except Exception:  # noqa: BLE001 - die Wache darf nie sterben
                logger.exception("Aufsicht: Pruefung gescheitert")


# ── HTTP ───────────────────────────────────────────────────────────────────


def host_erlaubt(kopf: Optional[str], port: int) -> bool:
    """Nur der eigene Name: lokal und der Name, unter dem ein Container den Rechner sieht."""
    weitere = [h.strip() for h in os.environ.get("CODING_DIENST_HOSTS", "host.docker.internal").split(",") if h.strip()]
    return kopf in {f"{name}:{port}" for name in (HOST, "localhost", *weitere)}


def _json(status: int, inhalt: Any) -> Tuple[int, str, bytes]:
    return status, "application/json; charset=utf-8", json.dumps(inhalt, ensure_ascii=False).encode("utf-8")


def _fehler(fehler: pipeline.PipelineFehler) -> Tuple[int, str, bytes]:
    text = str(fehler)
    unbekannt = "gibt es nicht" in text or "keinen passenden" in text
    return _json(404 if unbekannt else 400, {"error": text})


def lesen(pfad: str) -> Tuple[int, str, bytes]:
    teile = urllib.parse.urlsplit(pfad)
    frage = {k: v[0] for k, v in urllib.parse.parse_qs(teile.query).items()}
    weg = teile.path.rstrip("/") or "/"
    if weg in STATISCH:
        name, art = STATISCH[weg]
        try:
            return 200, art, (ORDNER / "seite" / name).read_bytes()
        except OSError:
            return _json(500, {"error": "seite_fehlt"})
    if weg == "/health":
        # Nur Lebenszeichen, kein Dateizugriff.
        return _json(200, {"status": "ok", "service": "coding-dienst", "stufen": list(pipeline.STUFEN),
                           "schreiben": bool(os.environ.get("CODING_DIENST_SCHLUESSEL"))})
    try:
        if weg == "/api/liste":
            return _json(200, pipeline.liste(int(frage.get("grenze", "20"))))
        if weg == "/api/stand":
            return _json(200, pipeline.stand(frage.get("auftrag")))
        if weg == "/api/ergebnis":
            return _json(200, pipeline.ergebnis(frage.get("auftrag")))
    except pipeline.PipelineFehler as fehler:
        return _fehler(fehler)
    except ValueError:
        return _json(400, {"error": "ungueltige_angabe"})
    return _json(404, {"error": "unbekannter_pfad"})


def schreiben(pfad: str, koerper: bytes, schluessel: Optional[str], aufsicht: Aufsicht) -> Tuple[int, str, bytes]:
    soll = os.environ.get("CODING_DIENST_SCHLUESSEL", "")
    if not soll:
        return _json(403, {"error": "schreiben_aus"})
    if not schluessel or not hmac.compare_digest(schluessel.encode("utf-8"), soll.encode("utf-8")):
        return _json(401, {"error": "schluessel_fehlt_oder_falsch"})
    try:
        angaben = json.loads(koerper.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return _json(400, {"error": "kein_json"})
    if not isinstance(angaben, dict):
        return _json(400, {"error": "kein_json_objekt"})
    weg = urllib.parse.urlsplit(pfad).path.rstrip("/")
    try:
        if weg == "/api/auftrag":
            return _json(200, pipeline.auftrag_starten(
                aufgabe=angaben.get("aufgabe"), entwurf=angaben.get("entwurf"), titel=angaben.get("titel"),
                arbeitspaket=angaben.get("arbeitspaket"), ohne_npm=angaben.get("ohne_npm", True) is not False,
                bestehend=angaben.get("bestehend"),
                anstoss=aufsicht.anstoss))
        if weg == "/api/abbrechen":
            return _json(200, aufsicht.abbrechen(angaben.get("auftrag")))
    except pipeline.PipelineFehler as fehler:
        return _fehler(fehler)
    return _json(404, {"error": "unbekannter_pfad"})


class Handler(BaseHTTPRequestHandler):
    server_version = "coding-dienst/1"
    aufsicht: Aufsicht

    def _antwort(self, status: int, art: str, koerper: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", art)
        self.send_header("Content-Length", str(len(koerper)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(koerper)

    def _eigener_host(self) -> bool:
        return host_erlaubt(self.headers.get("Host"), self.server.server_address[1])

    def do_GET(self) -> None:  # noqa: N802
        self._antwort(*(lesen(self.path) if self._eigener_host() else _json(403, {"error": "fremder_host"})))

    def do_POST(self) -> None:  # noqa: N802
        if not self._eigener_host():
            return self._antwort(*_json(403, {"error": "fremder_host"}))
        # Eine Seite in einem Browser schickt "Origin" mit; dieser Weg ist fuer Dienste, nicht fuer Seiten.
        if self.headers.get("Origin") or "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._antwort(*_json(403, {"error": "nur_json_ohne_origin"}))
        try:
            laenge = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            laenge = -1
        if laenge < 0 or laenge > GROESSTER_KOERPER:
            return self._antwort(*_json(413, {"error": "koerper_zu_gross"}))
        self._antwort(*schreiben(self.path, self.rfile.read(laenge), self.headers.get(KOPF_SCHLUESSEL), self.aufsicht))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return  # still: die Seite fragt alle zwei Sekunden


class _EinzigerServer(ThreadingHTTPServer):
    """Belegt den Port allein.

    HTTPServer setzt SO_REUSEADDR; unter Windows darf damit ein zweiter Prozess denselben Port
    binden. Zwei Dienste hiessen zwei Aufsichten, die je einen Erzeuger starten koennen.
    """

    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def server(port: int, aufsicht: Aufsicht) -> ThreadingHTTPServer:
    behandler = type("CodingHandler", (Handler,), {"aufsicht": aufsicht})
    return _EinzigerServer((HOST, port), behandler)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Dienst des Coding-Space: Seite, Lesen, Anstoss, Aufsicht")
    parser.add_argument("--port", type=int, default=_zahl("CODING_DIENST_PORT", DEFAULT_PORT))
    port = parser.parse_args(argv).port
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    aufsicht = Aufsicht()
    verwaist = aufsicht.verwaiste_schliessen()
    if verwaist:
        logger.info("Beim Start als unterbrochen markiert: %s", ", ".join(verwaist))
    halt = threading.Event()
    threading.Thread(target=aufsicht.wache, args=(halt,), name="coding-aufsicht", daemon=True).start()
    dienst = server(port, aufsicht)
    print(f"coding-dienst bereit auf http://{HOST}:{port}", file=sys.stderr, flush=True)
    try:
        dienst.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        halt.set()
        dienst.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
