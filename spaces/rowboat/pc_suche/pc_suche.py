"""PC-Suche fuer Rowboat auf dem Mini-PC: kleiner, nur lesender Suchdienst am Windows-PC (nur stdlib).

Lauscht nur auf der Tailnet-IP des PCs (``100.84.127.84:8149``) und beantwortet nur Anfragen von der
VM (``100.67.177.45``) mit passender Kopfzeile ``X-PC-Suche-Key`` (Schluessel aus dem OpenFang-Tresor,
``ROWBOAT_PC_SUCHE_KEY``). Sucht in Desktop und Dokumente (Entscheid D2) nach Dateinamen oder Inhalten;
Geheimnis-Dateien und -Ordner sind nie Treffer, Inhaltsauszuege mit ``key=``/``token:`` werden
geschwaerzt. Keine Shell, keine Schreibzugriffe, Symlinks und Junctions werden nicht betreten.

Als Datei starten (nicht per ``-m``: ``spaces/rowboat/__init__.py`` bricht den Paket-Import ab):
    pythonw spaces/rowboat/pc_suche/pc_suche.py --log <VIBEMIND_ROOT>/logs/rowboat/pc_suche.log
Aufgabe anlegen: ``pc_suche_aufgabe.ps1 -Install`` (nur nach Betreiber-OK).
"""
from __future__ import annotations

import argparse
import fnmatch
import hmac
import importlib.util
import json
import logging
import os
import re
import stat
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping

WURZELN_VORGABE = (Path("C:/Users/User/Desktop"), Path("C:/Users/User/Documents"))

ORDNER_AUS = {".git", "node_modules", ".venv", "venv", "__pycache__", "AppData", ".rowboat", ".ssh", ".aws",
              ".openfang", ".claude", ".gnupg", ".docker", ".kube", "target", "dist", "build", ".next", ".cache",
              ".worktrees"}
DATEI_AUS = (".env*", "*.pem", "*.key", "*.p12", "*.pfx", "*.kdbx", "id_rsa*", "id_ed25519*", "*secret*",
             "*credential*", "*token*", "*passw*", "client_secret*", "server-key", "cipher-key", "claude-token",
             "rowboat-fern.json", "werkzeuge.env")
INHALT_ENDUNGEN = {".md", ".txt", ".py", ".ts", ".tsx", ".js", ".ps1", ".sh", ".html", ".css", ".csv"}
MAX_INHALT_BYTES = 2 * 1024 * 1024
MAX_ANFRAGE = 200
MAX_KOERPER = 10_000
ARTEN = ("name", "inhalt")

_ORDNER_AUS_KLEIN = {o.lower() for o in ORDNER_AUS}
# Wie im Plan, aber mit Wortrest nach dem Stichwort: sonst rutschten "password=" und "Passwort:" durch.
_SCHWAERZEN = re.compile(r"(?i)(api[_-]?key|token|secret|passw|bearer|authorization|private[_ -]?key)[A-Za-z0-9_-]*\s*[:=]")
# Bekannte Schluessel-Formen auch ohne "name=" davor (zusaetzlich zur Vorgabe des Plans)
_SCHLUESSEL_FORM = re.compile(r"sk-ant-|ghp_[A-Za-z0-9]|github_pat_|xox[abp]-|AKIA[0-9A-Z]{8}|-----BEGIN [A-Z ]*PRIVATE KEY")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

log = logging.getLogger("pc_suche")


@dataclass(frozen=True)
class Einstellungen:
    wurzeln: tuple[Path, ...]
    host: str = "100.84.127.84"
    port: int = 8149
    vm_ip: str = "100.67.177.45"
    zeitlimit_s: float = 10.0
    max_treffer: int = 50


def wurzeln_aus_umgebung(umgebung: Mapping[str, str]) -> tuple[Path, ...]:
    """``PC_SUCHE_WURZELN`` (``;``-getrennt) oder die Vorgabe Desktop + Dokumente."""
    roh = (umgebung.get("PC_SUCHE_WURZELN") or "").strip()
    teile = tuple(Path(t.strip()) for t in roh.split(";") if t.strip())
    return teile or WURZELN_VORGABE


def _name_ausgeschlossen(name: str) -> bool:
    klein = name.lower()
    return any(fnmatch.fnmatchcase(klein, muster) for muster in DATEI_AUS)


def ist_ausgeschlossen(pfad: Path) -> bool:
    """True, wenn ein Pfadteil ein ausgeschlossener Ordner ist oder der Name wie ein Geheimnis aussieht."""
    if any(teil.lower() in _ORDNER_AUS_KLEIN for teil in pfad.parts):
        return True
    return _name_ausgeschlossen(pfad.name)


def _innerhalb(pfad: Path, wurzel: Path) -> Path | None:
    """Relativer Teil von ``pfad`` unter ``wurzel`` (beide aufgeloest), sonst None."""
    try:
        return pfad.resolve().relative_to(wurzel.resolve())
    except (ValueError, OSError):
        return None


def ordner_aufloesen(ordner: str, wurzeln: tuple[Path, ...] | list[Path]) -> list[Path] | None:
    """Leer = alle Wurzeln. Sonst muss der Ordner in einer Wurzel liegen (absolut oder relativ zu einer
    Wurzel), existieren und darf keinen ausgeschlossenen Teil haben. ``None`` = verboten."""
    ordner = (ordner or "").strip()
    if not ordner:
        return list(wurzeln)
    roh = Path(ordner)
    if ".." in roh.parts or ".." in ordner.replace("\\", "/").split("/"):
        return None
    kandidaten = [roh] if roh.is_absolute() else [w / roh for w in wurzeln]
    erlaubt: list[Path] = []
    for kandidat in kandidaten:
        for wurzel in wurzeln:
            relativ = _innerhalb(kandidat, wurzel)
            if relativ is None:
                continue
            if any(t.lower() in _ORDNER_AUS_KLEIN or _name_ausgeschlossen(t) for t in relativ.parts):
                return None
            if kandidat.is_dir():
                erlaubt.append(kandidat)
            break
    return erlaubt or None


def auszug_schwaerzen(zeile: str) -> str:
    if _SCHWAERZEN.search(zeile) or _SCHLUESSEL_FORM.search(zeile):
        return "[ausgeblendet]"
    return zeile.strip()[:200]


def _ist_umleitung(eintrag: os.DirEntry) -> bool:
    """Symlink oder Junction (Windows-Reparse-Punkt): nie betreten."""
    if eintrag.is_symlink():
        return True
    try:
        attribute = getattr(eintrag.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attribute & _REPARSE)


def _inhalt_treffer(pfad: str, nadel: str) -> tuple[int, str] | None:
    try:
        with open(pfad, encoding="utf-8", errors="ignore") as datei:
            for nummer, zeile in enumerate(datei, start=1):
                if nadel in zeile.lower():
                    return nummer, auszug_schwaerzen(zeile)
    except OSError:
        return None
    return None


def suchen(anfrage: str, art: str, ordner: str, max_treffer: int, einst: Einstellungen,
           uhr: Callable[[], float] = time.monotonic) -> dict:
    start = uhr()
    nadel = (anfrage or "").strip().lower()
    if not nadel or len(nadel) > MAX_ANFRAGE:
        return {"ok": False, "meldung": f"anfrage muss 1 bis {MAX_ANFRAGE} Zeichen haben"}
    if art not in ARTEN:
        return {"ok": False, "meldung": "art muss name oder inhalt sein"}
    startordner = ordner_aufloesen(ordner, einst.wurzeln)
    if startordner is None:
        return {"ok": False, "meldung": "ordner liegt nicht in Desktop/Dokumente oder ist gesperrt"}
    try:
        grenze = max(1, min(einst.max_treffer, int(max_treffer)))
    except (TypeError, ValueError):
        grenze = min(20, einst.max_treffer)

    treffer: list[dict[str, Any]] = []
    abgeschnitten = False
    stapel = list(reversed(startordner))
    while stapel and not abgeschnitten:
        aktuell = stapel.pop()
        try:
            with os.scandir(aktuell) as it:
                eintraege = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            continue
        unterordner: list[str] = []
        for eintrag in eintraege:
            if uhr() - start > einst.zeitlimit_s:
                abgeschnitten = True
                break
            if _ist_umleitung(eintrag):
                continue
            try:
                ist_ordner = eintrag.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if ist_ordner:
                if eintrag.name.lower() not in _ORDNER_AUS_KLEIN and not _name_ausgeschlossen(eintrag.name):
                    unterordner.append(eintrag.path)
                continue
            if _name_ausgeschlossen(eintrag.name):
                continue
            try:
                info = eintrag.stat(follow_symlinks=False)
            except OSError:
                continue
            fund: dict[str, Any] | None = None
            if art == "name":
                if nadel in eintrag.name.lower():
                    fund = {}
            elif Path(eintrag.name).suffix.lower() in INHALT_ENDUNGEN and info.st_size <= MAX_INHALT_BYTES:
                zeile = _inhalt_treffer(eintrag.path, nadel)
                if zeile is not None:
                    fund = {"zeile": zeile[0], "auszug": zeile[1]}
            if fund is None:
                continue
            if len(treffer) >= grenze:
                abgeschnitten = True
                break
            treffer.append({"pfad": eintrag.path, "groesse": info.st_size,
                            "geaendert": datetime.fromtimestamp(info.st_mtime).isoformat(timespec="seconds"),
                            **fund})
        stapel.extend(reversed(unterordner))
    return {"ok": True, "treffer": treffer, "abgeschnitten": abgeschnitten,
            "dauer_ms": int((uhr() - start) * 1000)}


def handler_bauen(einst: Einstellungen, schluessel: str) -> type[BaseHTTPRequestHandler]:
    erwartet = schluessel.encode("utf-8")

    class PcSucheHandler(BaseHTTPRequestHandler):
        server_version = "pc-suche"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - kein Zugriffslog
            pass

        def _antworten(self, status: int, daten: Any = None) -> None:
            roh = b"" if daten is None else json.dumps(daten, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            if roh:
                self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(roh)))
            self.end_headers()
            if roh:
                self.wfile.write(roh)

        def _zugelassen(self) -> bool:
            if self.client_address[0] != einst.vm_ip:
                return False
            gegeben = (self.headers.get("X-PC-Suche-Key") or "").encode("utf-8")
            return bool(gegeben) and hmac.compare_digest(gegeben, erwartet)

        def _abweisen(self) -> bool:
            if not self._zugelassen():
                self._antworten(403)
                return True
            return False

        def do_GET(self) -> None:  # noqa: N802
            if self._abweisen():
                return
            if self.path == "/health":
                self._antworten(200, {"status": "ok"})
            else:
                self._antworten(405)

        def do_POST(self) -> None:  # noqa: N802
            if self._abweisen():
                return
            if self.path != "/suchen":
                self._antworten(405)
                return
            try:
                laenge = int(self.headers.get("Content-Length") or "0")
                if not 0 < laenge <= MAX_KOERPER:
                    raise ValueError("Laenge")
                koerper = json.loads(self.rfile.read(laenge).decode("utf-8"))
                if not isinstance(koerper, dict):
                    raise ValueError("kein Objekt")
            except (ValueError, UnicodeDecodeError):
                self._antworten(400, {"ok": False, "meldung": "Anfrage nicht lesbar"})
                return
            try:
                ergebnis = suchen(str(koerper.get("anfrage", "")), str(koerper.get("art", "name")),
                                  str(koerper.get("ordner", "")), koerper.get("max_treffer", 20), einst)
            except Exception as e:  # noqa: BLE001 - nur der Typ, nie Inhalte
                log.warning("Suche fehlgeschlagen: %s", type(e).__name__)
                ergebnis = {"ok": False, "meldung": f"Suche fehlgeschlagen: {type(e).__name__}"}
            self._antworten(200, ergebnis)

        def _nicht_erlaubt(self) -> None:
            if self._abweisen():
                return
            self._antworten(405)

        do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _nicht_erlaubt  # noqa: N815

    return PcSucheHandler


def _tresor_laden() -> Callable[[str], str | None]:
    """``rowboat_zugang.tresor_wert`` aus ``<VIBEMIND_ROOT>/scripts`` (aeusseres Repo)."""
    wurzel = Path(os.environ.get("VIBEMIND_ROOT") or "C:/Users/User/Desktop/Vibemind_V1")
    pfad = wurzel / "scripts" / "rowboat_zugang.py"
    spec = importlib.util.spec_from_file_location("rowboat_zugang", pfad)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(str(pfad))
    modul = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(pfad.parent))
    spec.loader.exec_module(modul)
    return modul.tresor_wert


def _log_einrichten(datei: str | None) -> None:
    if datei:
        Path(datei).parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(datei, encoding="utf-8")
    else:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    for alt in list(log.handlers):
        log.removeHandler(alt)
        alt.close()
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False


def main(argv: list[str] | None = None, *, tresor: Callable[[str], str | None] | None = None,
         schlafen: Callable[[float], None] = time.sleep, server_klasse: Any = ThreadingHTTPServer) -> int:
    parser = argparse.ArgumentParser(prog="pc_suche", description="Nur lesender Suchdienst fuer Rowboat")
    parser.add_argument("--log", help="Logdatei (nur Start, Stopp, Fehlertyp)")
    args = parser.parse_args(argv)
    _log_einrichten(args.log)
    einst = Einstellungen(wurzeln=wurzeln_aus_umgebung(os.environ))
    try:
        holen = tresor or _tresor_laden()
    except Exception as e:  # noqa: BLE001
        log.error("Tresor-Zugriff nicht ladbar: %s", type(e).__name__)
        return 2
    # OpenFang startet bei der Anmeldung evtl. spaeter: bis zu 10 Versuche im Abstand von 30 s.
    schluessel = None
    for versuch in range(10):
        schluessel = (holen("PC_SUCHE_KEY") or "").strip() or None
        if schluessel:
            break
        if versuch < 9:
            schlafen(30)
    if not schluessel:
        log.error("Schlüssel fehlt im Tresor (ROWBOAT_PC_SUCHE_KEY; rowboat-zugang pc-schluessel) – beendet.")
        return 2
    handler = handler_bauen(einst, schluessel)
    del schluessel
    # Tailscale kann bei der Anmeldung spaeter kommen als dieser Dienst: Bindung bis zu 1 h wiederholen.
    server = None
    for versuch in range(120):
        try:
            server = server_klasse((einst.host, einst.port), handler)
            break
        except OSError as e:
            if versuch == 0:
                log.warning("Bindung an %s:%d noch nicht moeglich (%s), versuche weiter", einst.host, einst.port,
                            type(e).__name__)
            schlafen(30)
    if server is None:
        log.error("Bindung an %s:%d gescheitert – beendet.", einst.host, einst.port)
        return 3
    log.info("PC-Suche gestartet auf %s:%d (nur von %s), Wurzeln: %s", einst.host, einst.port, einst.vm_ip,
             "; ".join(str(w) for w in einst.wurzeln))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log.info("PC-Suche beendet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
