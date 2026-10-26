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
import io
import json
import logging
import os
import re
import stat
import sys
import threading
import time
from array import array
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

WURZELN_VORGABE = (Path("C:/Users/User/Desktop"), Path("C:/Users/User/Documents"))

ORDNER_AUS = {".git", "node_modules", ".venv", "venv", "__pycache__", "AppData", ".rowboat", ".ssh", ".aws",
              ".openfang", ".claude", ".gnupg", ".docker", ".kube", "target", "dist", "build", ".next", ".cache",
              ".worktrees"}
DATEI_AUS = (".env*", "*.pem", "*.key", "*.p12", "*.pfx", "*.kdbx", "id_rsa*", "id_ed25519*", "id_ecdsa*",
             "id_dsa*", "*.ppk", ".netrc", ".npmrc", ".pypirc", ".pgpass", "*.ovpn", "*secret*", "*credential*",
             "*token*", "*passw*", "client_secret*", "server-key", "cipher-key", "claude-token",
             "rowboat-fern.json", "werkzeuge.env")
INHALT_ENDUNGEN = {".md", ".txt", ".py", ".ts", ".tsx", ".js", ".ps1", ".sh", ".html", ".css", ".csv"}
MAX_INHALT_BYTES = 2 * 1024 * 1024
MAX_ANFRAGE = 200
MAX_KOERPER = 10_000
MAX_PARALLEL = 3           # gleichzeitige Suchen; die vierte bekommt sofort 503
HANDLER_TIMEOUT_S = 15     # Lese-Zeitlimit je Verbindung (langsame/haengende Absender)
ARTEN = ("name", "inhalt")
INDEX_ABSTAND_S = 30 * 60  # Namensindex alle 30 min neu (Desktop + Dokumente ~700k Dateien, ~45 s Lauf)

_ORDNER_AUS_KLEIN = {o.lower() for o in ORDNER_AUS}
# Zeilen mit diesen Mustern werden ganz durch "[ausgeblendet]" ersetzt (lieber zu viel als zu wenig):
_SCHWAERZEN = (
    # Schluessel-Namen mit Zuweisung, auch in Anfuehrungszeichen und mit Wortrest:
    # API_KEY=, "password": , 'api_key': , ANTHROPIC_KEY=, client_secret:, Passwort=
    re.compile(r"(?i)(api[_-]?key|token|secret|passw|bearer|authorization|private[_ -]?key|credential|"
               r"[a-z0-9]+_key)[A-Za-z0-9_-]*[\"']?\s*[:=]"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{6,}"),                       # Bearer <token>
    re.compile(r"(?i)[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"),                  # ://user:pass@
    re.compile(r"(?i)(^|\s)--?(token|password|passwd|pass|api[-_]?key|secret|key|auth)(=|\s+)\S"),  # --token x
    re.compile(r"sk-[A-Za-z0-9_-]{10,}|AIza[0-9A-Za-z_-]{20,}|ghp_[A-Za-z0-9]|github_pat_|xox[abp]-|"
               r"AKIA[0-9A-Z]{8}|-----BEGIN [A-Z ]*PRIVATE KEY"),                # bekannte Schluessel-Formen
)
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


def _lexikalisch_unter(kandidat: str, wurzel: Path) -> bool:
    """Reiner Text-Vergleich (normcase(abspath)), ohne das Dateisystem anzufassen."""
    k = os.path.normcase(os.path.abspath(kandidat))
    w = os.path.normcase(os.path.abspath(str(wurzel))).rstrip("\\/")
    if k.startswith(("\\\\", "//")):
        return False
    return k == w or k.startswith(w + os.sep) or k.startswith(w + "/")


def ordner_aufloesen(ordner: str, wurzeln: tuple[Path, ...] | list[Path]) -> list[Path] | None:
    """Leer = alle Wurzeln. Sonst muss der Ordner in einer Wurzel liegen (absolut oder relativ zu einer
    Wurzel), existieren und darf keinen ausgeschlossenen Teil haben. ``None`` = verboten.

    UNC-Pfade (``\\\\host\\share``, ``//host/share``, ``\\\\?\\``) und alles, was nicht schon als Text unter
    einer Wurzel liegt, werden abgelehnt, BEVOR das Dateisystem angefasst wird (sonst loeste schon
    ``resolve()`` eine SMB-Verbindung aus)."""
    ordner = (ordner or "").strip()
    if not ordner:
        return list(wurzeln)
    if ordner.startswith(("\\\\", "//")):
        return None
    roh = Path(ordner)
    if ".." in roh.parts or ".." in ordner.replace("\\", "/").split("/"):
        return None
    if roh.is_absolute():
        kandidaten = [(roh, w) for w in wurzeln if _lexikalisch_unter(ordner, w)]
    else:
        kandidaten = [(w / roh, w) for w in wurzeln if _lexikalisch_unter(os.path.join(str(w), ordner), w)]
    erlaubt: list[Path] = []
    for kandidat, wurzel in kandidaten:
        # Erst jetzt das Dateisystem: Symlink/Junction darf nicht aus der Wurzel herausfuehren.
        relativ = _innerhalb(kandidat, wurzel)
        if relativ is None:
            continue
        if any(t.lower() in _ORDNER_AUS_KLEIN or _name_ausgeschlossen(t) for t in relativ.parts):
            return None
        if kandidat.is_dir():
            erlaubt.append(kandidat)
    return erlaubt or None


def auszug_schwaerzen(zeile: str) -> str:
    if any(muster.search(zeile) for muster in _SCHWAERZEN):
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


class NamensIndex:
    """Dateinamen unter den Wurzeln, nach denselben Regeln wie der Suchlauf (ausgeschlossene Ordner und
    Geheimnis-Namen fehlen, Symlinks/Junctions werden nicht betreten). Kompakt gespeichert: alle Namen
    klein geschrieben in einer Zeichenkette (durch Zeilenumbrueche getrennt) plus Versatz- und Ordner-Tabellen, damit
    ~700k Dateien nur einige zehn MB belegen. Der Index liefert nur Kandidaten; ``suchen`` prueft jeden
    Treffer beim Abruf noch einmal auf dem Dateisystem (geloescht, ausgeschlossen)."""

    def __init__(self, wurzeln: tuple[Path, ...] | list[Path]) -> None:
        self.wurzeln = tuple(wurzeln)
        self._stand: tuple[bytes, array, array, list[str], bytes, array] | None = None
        self.erstellt: float | None = None

    @property
    def bereit(self) -> bool:
        return self._stand is not None

    def aufbauen(self) -> None:
        # UTF-8-Bytes statt Listen von str: 700k einzelne Objekte, oder ein einziges Emoji im Namen (dann
        # 4 Byte je Zeichen fuer den ganzen Text), kosteten ~100 MB.
        klein_text = io.BytesIO()
        namen_text = io.BytesIO()
        versatz = array("L")
        namen_versatz = array("L")
        ordner_nr = array("L")
        ordner: list[str] = []
        pos = 0
        npos = 0
        stapel = [str(w) for w in reversed(self.wurzeln)]
        while stapel:
            aktuell = stapel.pop()
            try:
                with os.scandir(aktuell) as it:
                    eintraege = list(it)
            except OSError:
                continue
            nr = len(ordner)
            ordner.append(aktuell)
            unterordner: list[str] = []
            for eintrag in eintraege:
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
                name = eintrag.name.replace("\n", " ").encode("utf-8", "surrogatepass")
                klein = eintrag.name.replace("\n", " ").lower().encode("utf-8", "surrogatepass")
                versatz.append(pos)
                namen_versatz.append(npos)
                ordner_nr.append(nr)
                klein_text.write(klein + b"\n")
                namen_text.write(name + b"\n")
                pos += len(klein) + 1
                npos += len(name) + 1
            stapel.extend(reversed(unterordner))
        self._stand = (klein_text.getvalue(), versatz, ordner_nr, ordner, namen_text.getvalue(), namen_versatz)
        self.erstellt = time.time()

    def kandidaten(self, nadel: str, startordner: list[Path]) -> "Iterator[str]":
        """Pfade, deren Name ``nadel`` enthaelt und die unter einem der ``startordner`` liegen."""
        stand = self._stand
        if stand is None:
            return
        text, versatz, ordner_nr, ordner, namen, namen_versatz = stand
        praefixe = [os.path.normcase(os.path.abspath(str(o))).rstrip("\\/") for o in startordner]
        zuletzt = -1
        nadel_b = nadel.encode("utf-8", "surrogatepass")
        pos = text.find(nadel_b)
        while pos != -1:
            i = bisect_right(versatz, pos) - 1
            if i != zuletzt:
                zuletzt = i
                verz = ordner[ordner_nr[i]]
                v = os.path.normcase(verz)
                if any(v == pr or v.startswith(pr + os.sep) for pr in praefixe):
                    ende = namen_versatz[i + 1] - 1 if i + 1 < len(namen_versatz) else len(namen) - 1
                    yield os.path.join(verz, namen[namen_versatz[i]:ende].decode("utf-8", "surrogatepass"))
            naechster = versatz[i + 1] if i + 1 < len(versatz) else len(text)
            pos = text.find(nadel_b, max(pos + 1, naechster))


def index_pflegen(index: NamensIndex, abstand_s: float = INDEX_ABSTAND_S,
                  schlafen: Callable[[float], None] = time.sleep) -> None:
    """Endlosschleife fuer einen Hintergrund-Thread: Index bauen, warten, neu bauen."""
    while True:
        try:
            start = time.monotonic()
            index.aufbauen()
            log.info("Namensindex gebaut (%.0f s)", time.monotonic() - start)
        except Exception as e:  # noqa: BLE001 - nur der Typ
            log.warning("Namensindex fehlgeschlagen: %s", type(e).__name__)
        schlafen(abstand_s)


def _aus_index(index: NamensIndex, nadel: str, startordner: list[Path], grenze: int, start: float,
               uhr: Callable[[], float]) -> dict:
    treffer: list[dict[str, Any]] = []
    abgeschnitten = False
    for pfad in index.kandidaten(nadel, startordner):
        if _name_ausgeschlossen(os.path.basename(pfad)):  # Ordner filtert schon der Index
            continue
        try:
            info = os.stat(pfad, follow_symlinks=False)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        if len(treffer) >= grenze:
            abgeschnitten = True
            break
        treffer.append({"pfad": pfad, "groesse": info.st_size,
                        "geaendert": datetime.fromtimestamp(info.st_mtime).isoformat(timespec="seconds")})
    stand = datetime.fromtimestamp(index.erstellt).isoformat(timespec="minutes") if index.erstellt else None
    return {"ok": True, "treffer": treffer, "abgeschnitten": abgeschnitten,
            "dauer_ms": int((uhr() - start) * 1000), "index_stand": stand}


def suchen(anfrage: str, art: str, ordner: str, max_treffer: int, einst: Einstellungen,
           uhr: Callable[[], float] = time.monotonic, index: NamensIndex | None = None) -> dict:
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
    if art == "name" and index is not None and index.bereit:
        return _aus_index(index, nadel, startordner, grenze, start, uhr)

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


def handler_bauen(einst: Einstellungen, schluessel: str,
                  index: NamensIndex | None = None) -> type[BaseHTTPRequestHandler]:
    erwartet = schluessel.encode("utf-8")

    class PcSucheHandler(BaseHTTPRequestHandler):
        server_version = "pc-suche"
        sys_version = ""
        timeout = HANDLER_TIMEOUT_S
        suche_sperre = threading.BoundedSemaphore(MAX_PARALLEL)

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

        def _koerper_verwerfen(self) -> None:
            # Ungelesener Koerper im Empfangspuffer laesst Windows die Verbindung beim Schliessen per RST
            # abbrechen (WinError 10053): der Absender saehe dann "PC ist aus" statt 403.
            try:
                laenge = min(int(self.headers.get("Content-Length") or "0"), MAX_KOERPER)
            except ValueError:
                laenge = 0
            if laenge > 0:
                try:
                    self.rfile.read(laenge)
                except OSError:
                    pass

        def _abweisen(self) -> bool:
            if not self._zugelassen():
                self._koerper_verwerfen()
                self.close_connection = True
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
                self._koerper_verwerfen()
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
            if not self.suche_sperre.acquire(blocking=False):
                self._antworten(503, {"ok": False, "meldung": "PC-Suche ist ausgelastet – gleich noch einmal versuchen."})
                return
            try:
                ergebnis = suchen(str(koerper.get("anfrage", "")), str(koerper.get("art", "name")),
                                  str(koerper.get("ordner", "")), koerper.get("max_treffer", 20), einst,
                                  index=index)
            except Exception as e:  # noqa: BLE001 - nur der Typ, nie Inhalte
                log.warning("Suche fehlgeschlagen: %s", type(e).__name__)
                ergebnis = {"ok": False, "meldung": f"Suche fehlgeschlagen: {type(e).__name__}"}
            finally:
                self.suche_sperre.release()
            self._antworten(200, ergebnis)

        def _nicht_erlaubt(self) -> None:
            if self._abweisen():
                return
            self._koerper_verwerfen()
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
    index = NamensIndex(einst.wurzeln)
    threading.Thread(target=index_pflegen, args=(index,), name="namensindex", daemon=True).start()
    handler = handler_bauen(einst, schluessel, index)
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
