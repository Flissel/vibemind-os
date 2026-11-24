"""Abnahme eines Auftrags der Engine: das letzte Tor, unabhaengig von ihrer Selbstauskunft.

Die Engine prueft nur ihre eigenen Tests. Ob ein Auftrag ``fertig`` heisst, entscheidet erst
diese Abnahme, nachdem der Erzeuger seinen Abschluss geschrieben hat:

1. Allgemein (fuer jeden Auftrag):
   - jede gemeldete Datei liegt im Zielordner, ``package.json`` ist lesbar;
   - es gibt mindestens eine Testdatei, und keine ist leer oder auskommentiert;
   - jede Testdatei laeuft einzeln (``node --test``): mindestens ein Test, keiner rot,
     keiner uebersprungen;
   - ``npm test`` endet mit 0;
   - eine App (``startbefehl``) startet und beantwortet ``GET /health`` mit 200.
2. Aufgabe: hat der Auftrag einen Pruefer (``pruefer``) und ist ein Pruefskript eingerichtet
   (``CODING_DIENST_PRUEFER``), laeuft ``python <skript> pruefen <pruefer> --ziel <zielordner>``
   und antwortet mit einer JSON-Zeile ``{"richtig": bool, "gruende": [...]}``.

Ausgang:
- ``fertig``: beides gruen.
- ``ungeprueft``: allgemein gruen, aber kein (lauffaehiger) Pruefer der Aufgabe; der Satz sagt, was fehlt.
- ``fehlgeschlagen``: eine Regel oder der Pruefer ist rot; der Satz nennt den Grund.

Nur Standardbibliothek. Der gebaute Code laeuft in Unterprozessen mit Zeitgrenze; bei
Zeitgrenze endet der ganze Prozessbaum.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from spaces.coding.pipeline import FEHLGESCHLAGEN, FERTIG, UNGEPRUEFT

AUSLASSEN = {".git", "node_modules", "__pycache__", "coverage", ".venv"}
PRUEFER_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")

# Kommentare und Zeichenketten; Zeichenketten bleiben stehen, Kommentare fallen weg.
_KOMMENTARE = re.compile(r"(\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)|//[^\n]*|/\*.*?\*/", re.S)
_TESTAUFRUF = re.compile(r"\b(test|it)\s*(\.\s*(only)\s*)?\(")
_TAP = re.compile(r"^# (tests|pass|fail|skipped|todo|cancelled) (\d+)\s*$", re.M)


class Zeitgrenze(Exception):
    pass


def _zahl(name: str, vorgabe: float) -> float:
    try:
        return float(os.environ.get(name, "") or vorgabe)
    except ValueError:
        return vorgabe


def ausfuehren(befehl: List[str], ordner: Path, zeitgrenze_s: float,
               umgebung: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
    """Startet ``befehl`` ohne Eingabe; liefert (Code, Ausgabe). Bei Zeitgrenze endet der ganze Baum."""
    prozess = subprocess.Popen(  # noqa: S603 - feste Befehle, keine Shell
        befehl, cwd=str(ordner), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=umgebung)
    try:
        aus, _ = prozess.communicate(timeout=zeitgrenze_s)
        return prozess.returncode, aus or ""
    except subprocess.TimeoutExpired:
        _baum_beenden(prozess)
        raise Zeitgrenze(f"Zeitgrenze von {zeitgrenze_s:g} s überschritten") from None


def _baum_beenden(prozess: subprocess.Popen) -> None:
    if prozess.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(prozess.pid)],  # noqa: S603, S607
                           capture_output=True, check=False)
        else:
            prozess.kill()
    try:
        prozess.communicate(timeout=10)
    except (subprocess.TimeoutExpired, ValueError):
        pass


def _node() -> str:
    return shutil.which("node") or "node"


def _npm() -> str:
    return shutil.which("npm.cmd") or shutil.which("npm") or "npm"


def _letzte(text: str, muster: Optional[re.Pattern] = None) -> str:
    zeilen = [z.strip() for z in text.splitlines() if z.strip()]
    if muster is not None:
        zeilen = [z for z in zeilen if muster.search(z)] or zeilen
    return zeilen[-1][:200] if zeilen else "ohne Ausgabe"


# ── Allgemeine Regeln ─────────────────────────────────────────────────────


def testdateien(ziel: Path) -> List[Path]:
    """Alle Testdateien (``*.test.js`` oder unter ``test/``), ohne node_modules und Pruefer-Helfer."""
    gefunden = []
    for pfad in sorted(ziel.rglob("*.js")):
        teile = pfad.relative_to(ziel).parts
        if not pfad.is_file() or AUSLASSEN.intersection(teile) or "_pruefer_" in pfad.name:
            continue
        if ".test." in pfad.name or "test" in teile[:-1] or "tests" in teile[:-1]:
            gefunden.append(pfad)
    return gefunden


def ohne_kommentare(text: str) -> str:
    return _KOMMENTARE.sub(lambda m: m.group(1) or "", text)


def hat_test(text: str) -> bool:
    """Ruft die Datei ausserhalb von Kommentaren ``test(`` oder ``it(`` auf?"""
    return bool(_TESTAUFRUF.search(ohne_kommentare(text)))


def testdatei_laufen(datei: Path, ziel: Path, zeitgrenze_s: float = 60) -> Optional[str]:
    """None, wenn die Datei einzeln gruen laeuft; sonst der Grund."""
    name = datei.relative_to(ziel).as_posix()
    try:
        code, aus = ausfuehren([_node(), "--test", "--test-reporter=tap", str(datei)], ziel, zeitgrenze_s)
    except Zeitgrenze as grenze:
        return f"Testdatei {name}: {grenze}"
    zahlen = {k: int(v) for k, v in _TAP.findall(aus)}
    if code != 0 or zahlen.get("fail", 0) or zahlen.get("cancelled", 0):
        return f"Testdatei {name} ist rot: {_letzte(aus, re.compile(r'not ok|Error|error'))}"
    if zahlen.get("pass", 0) < 1:
        return f"Testdatei {name}: kein Test ist gelaufen"
    if zahlen.get("skipped", 0) or zahlen.get("todo", 0):
        return f"Testdatei {name}: {zahlen.get('skipped', 0) + zahlen.get('todo', 0)} Test(s) übersprungen"
    return None


def _freier_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def app_antwortet(ziel: Path, server: str = "src/server.js", zeitgrenze_s: float = 20) -> Optional[str]:
    """Startet die App mit eigenem Port; None, wenn ``GET /health`` mit 200 antwortet."""
    if not (ziel / server).is_file():
        return f"Die App-Datei {server} fehlt"
    port = _freier_port()
    umgebung = {**os.environ, "PORT": str(port)}
    prozess = subprocess.Popen(  # noqa: S603 - fester Befehl
        [_node(), server], cwd=str(ziel), env=umgebung, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ende = time.monotonic() + zeitgrenze_s
        letzter = "keine Antwort"
        while time.monotonic() < ende:
            if prozess.poll() is not None:
                return f"Die App ist beim Start beendet worden (Code {prozess.returncode})"
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as antwort:  # noqa: S310
                    if antwort.status == 200:
                        return None
                    letzter = f"Status {antwort.status}"
            except Exception as fehler:  # noqa: BLE001 - noch nicht bereit
                letzter = str(fehler)[:120]
            time.sleep(0.3)
        return f"Die App hat GET /health nicht mit 200 beantwortet ({letzter})"
    finally:
        _baum_beenden(prozess)


def allgemein(ziel: Path, gemeldet: List[str], mit_app: bool) -> List[str]:
    """Gruende gegen den Auftrag nach den allgemeinen Regeln; leer heisst gruen."""
    if not ziel.is_dir():
        return ["Der Zielordner fehlt"]
    gruende = [f"Gemeldete Datei fehlt: {f}" for f in gemeldet if not (ziel / f).is_file()]
    skripte: Dict[str, Any] = {}
    try:
        paket = json.loads((ziel / "package.json").read_text(encoding="utf-8"))
        skripte = paket.get("scripts") or {} if isinstance(paket, dict) else {}
    except OSError:
        gruende.append("package.json fehlt")
    except ValueError:
        gruende.append("package.json ist kein gültiges JSON")

    dateien = testdateien(ziel)
    if not dateien:
        gruende.append("Es gibt keine Testdatei")
    for datei in dateien:
        name = datei.relative_to(ziel).as_posix()
        if not hat_test(datei.read_text(encoding="utf-8", errors="replace")):
            gruende.append(f"Testdatei {name} enthält keinen Test (leer oder auskommentiert)")
            continue
        grund = testdatei_laufen(datei, ziel, _zahl("CODING_ABNAHME_TEST_S", 60))
        if grund:
            gruende.append(grund)

    if skripte.get("test"):
        try:
            code, aus = ausfuehren([_npm(), "test", "--silent"], ziel, _zahl("CODING_ABNAHME_NPM_S", 180))
            if code != 0:
                gruende.append(f"npm test endet mit Code {code}: {_letzte(aus)}")
        except Zeitgrenze as grenze:
            gruende.append(f"npm test: {grenze}")
    elif dateien:
        gruende.append("package.json hat kein Testskript")

    if mit_app:
        grund = app_antwortet(ziel)
        if grund:
            gruende.append(grund)
    return gruende


# ── Pruefer der Aufgabe ───────────────────────────────────────────────────


def aufgabe(name: Optional[str], ziel: Path) -> Tuple[str, List[str]]:
    """('gruen' | 'rot' | 'fehlt' | 'kaputt', Gruende) des Pruefers der Aufgabe."""
    if not name:
        return "fehlt", ["Für diese Aufgabe ist kein Prüfer genannt"]
    if not PRUEFER_NAME.match(name):
        return "fehlt", [f"Der Prüfername {name!r} ist ungültig"]
    skript = os.environ.get("CODING_DIENST_PRUEFER", "").strip()
    if not skript or not Path(skript).is_file():
        return "fehlt", [f"Der Prüfer „{name}“ ist genannt, aber im Dienst ist kein Prüfskript eingerichtet"]
    try:
        code, aus = ausfuehren([sys.executable, "-I", skript, "pruefen", name, "--ziel", str(ziel)],
                               Path(skript).parent, _zahl("CODING_ABNAHME_PRUEFER_S", 600))
    except Zeitgrenze as grenze:
        return "kaputt", [f"Der Prüfer „{name}“: {grenze}"]
    urteil = None
    for zeile in reversed(aus.splitlines()):
        if zeile.strip().startswith("{"):
            try:
                urteil = json.loads(zeile)
            except ValueError:
                urteil = None
            break
    if code != 0 or not isinstance(urteil, dict) or not isinstance(urteil.get("richtig"), bool):
        return "kaputt", [f"Der Prüfer „{name}“ lieferte kein Urteil (Code {code}): {_letzte(aus)}"]
    gruende = [str(g) for g in urteil.get("gruende") or []]
    if urteil["richtig"]:
        return "gruen", []
    return "rot", gruende or ["ohne Grund"]


# ── Zusammen ──────────────────────────────────────────────────────────────


def abnehmen(auftrag: Dict[str, Any]) -> Dict[str, Any]:
    """Abnahme eines Auftrags, dessen Erzeuger sich fertig gemeldet hat."""
    beginn = time.monotonic()
    ziel = Path(auftrag["zielordner"])
    gemeldet = sorted({f for p in auftrag.get("_pakete") or [] for f in p.get("gemeldet") or []})
    regeln = allgemein(ziel, gemeldet, mit_app=bool(auftrag.get("startbefehl")))
    name = auftrag.get("pruefer")
    pruefer, pruefer_gruende = aufgabe(name, ziel)
    return urteil(regeln, pruefer, pruefer_gruende, name, round(time.monotonic() - beginn, 2))


def urteil(regeln: List[str], pruefer: str, pruefer_gruende: List[str], name: Optional[str],
           dauer_s: Optional[float] = None) -> Dict[str, Any]:
    """Der Ausgang aus allgemeinen Regeln und Pruefer der Aufgabe, mit einem Satz fuer Menschen."""
    if regeln or pruefer == "rot":
        gruende = regeln + ([f"Prüfer der Aufgabe: {g}" for g in pruefer_gruende] if pruefer == "rot" else [])
        zustand = FEHLGESCHLAGEN
        satz = "Die Abnahme ist nicht bestanden: " + "; ".join(gruende[:3]) + (
            f" (und {len(gruende) - 3} weitere)" if len(gruende) > 3 else "") + "."
    elif pruefer == "gruen":
        gruende, zustand = [], FERTIG
        satz = "Die Abnahme ist bestanden: die allgemeinen Regeln und der Prüfer der Aufgabe sind grün."
    else:
        gruende, zustand = pruefer_gruende, UNGEPRUEFT
        satz = ("Gebaut und allgemein geprüft (Tests laufen und sind grün, Dateien da), aber ungeprüft: "
                + "; ".join(pruefer_gruende) + ".")
    return {"zustand": zustand, "satz": satz, "gruende": gruende, "allgemein": "rot" if regeln else "gruen",
            "pruefer": name, "pruefer_ausgang": pruefer, "dauer_s": dauer_s, "zeit": time.time()}
