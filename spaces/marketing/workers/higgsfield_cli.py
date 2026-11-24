"""Huelle um das Kommandozeilen-Programm `higgsfield` (Abo-Anmeldung am PC) fuer die Video-Werkbank (24.11.2026).

Vier Dinge: Preis abfragen (kostet nichts), bestellen (kostet Credits), nach einer Auftragsnummer fragen und eine
verlorene Nummer in der Auftragsliste wiederfinden. Bestellt wird OHNE Warten: das Programm gibt die Nummer sofort
zurueck, der Arbeiter vermerkt sie und fragt danach nur noch nach ihr.

Regeln:
- Der Auftragstext geht als EIN Argument (`--prompt=<text>`) direkt an das Programm, nie durch eine Shell. Unter
  Windows ist `higgsfield` ein .cmd-Start ueber cmd.exe; darum wird das eigentliche Programm (`hf.exe` im npm-Paket)
  gesucht und direkt gestartet. Ein .cmd oder .bat wird abgelehnt.
- Ausgaben gehen in eine Datei, nie in eine Pipe. Zeitlimits beenden den Prozessbaum ueber die PID.
- Fehlertexte sind kurze deutsche Saetze ohne Pfade, Auftragstexte oder Ausgaben des Programms.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from spaces.marketing.workers.video_rendern import _baum_beenden

LOG = logging.getLogger("higgsfield_cli")

MODELL = "kling3_0"
FORMAT = "16:9"
MODUS = "pro"                    # 1080p
NUMMER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{5,79}")   # nie mit einem Strich am Anfang (waere ein Schalter)
FERTIG = ("completed", "succeeded", "success", "done")
GESCHEITERT = ("failed", "error", "canceled", "cancelled", "nsfw", "rejected", "expired")
ZEIT_KURZ_S = 180                # Preis, Nachfragen, Liste
ZEIT_BESTELLEN_S = 600           # die Bilder werden beim Bestellen hochgeladen


class HfFehler(Exception):
    """Das Programm fehlt, ist nicht angemeldet oder gab keine brauchbare Antwort; `grund` ist ein kurzer Satz."""

    def __init__(self, grund: str):
        super().__init__(grund)
        self.grund = grund


class HfUnklar(HfFehler):
    """Nach dem Bestellen kam keine Auftragsnummer zurueck: ob Higgsfield den Auftrag hat, ist offen. Es darf
    NICHT neu bestellt werden."""


def programm() -> str:
    """Pfad des eigentlichen Programms: HIGGSFIELD_EXE, sonst hf(.exe) im npm-Paket neben dem Start `higgsfield`."""
    kandidaten: list[Path] = []
    gesetzt = os.environ.get("HIGGSFIELD_EXE", "").strip()
    if gesetzt:
        kandidaten.append(Path(gesetzt))
    else:
        start = shutil.which("higgsfield")
        if start:
            name = "hf.exe" if os.name == "nt" else "hf"
            kandidaten.append(Path(start).resolve().parent / "node_modules" / "@higgsfield" / "cli" / "vendor" / name)
            if os.name != "nt":
                kandidaten.append(Path(start).resolve().parent.parent / "vendor" / name)   # Start ist ein Link ins Paket
    for k in kandidaten:
        if k.suffix.lower() in (".cmd", ".bat", ".ps1"):
            continue                       # liefe ueber eine Shell
        if k.is_file():
            return str(k)
    raise HfFehler("Das Higgsfield-Programm wurde nicht gefunden.")


def _lauf(args: list[str], zeitlimit_s: float) -> tuple[int, str]:
    """Startet das Programm mit `args` (Liste, keine Shell); liefert (Exit-Code, Ausgabe)."""
    exe = programm()
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    with tempfile.TemporaryFile() as aus, tempfile.TemporaryFile() as fehler:
        try:
            proc = subprocess.Popen([exe, *args], stdout=aus, stderr=fehler, stdin=subprocess.DEVNULL, shell=False,
                                    env={**os.environ, "NO_COLOR": "1"}, **flags)
        except OSError as e:
            raise HfFehler(f"Das Higgsfield-Programm ließ sich nicht starten ({type(e).__name__}).") from None
        try:
            code = proc.wait(timeout=zeitlimit_s)
        except subprocess.TimeoutExpired:
            _baum_beenden(proc.pid)
            raise HfFehler("Higgsfield hat nicht rechtzeitig geantwortet.") from None
        except BaseException:
            _baum_beenden(proc.pid)
            raise
        aus.seek(0)
        return code, aus.read().decode("utf-8", errors="replace")


def _json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return None


def _medien_argumente(prompt: str, start_bild: Path, end_bild: Path | None, dauer_s: int, modell: str) -> list[str]:
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 1000 \
            or any(ord(c) < 32 or ord(c) == 127 for c in prompt):
        raise HfFehler("Der Auftragstext ist ungültig.")
    if not re.fullmatch(r"[a-z0-9_]{1,40}", modell or ""):
        raise HfFehler("Das Modell ist ungültig.")
    a = [modell, "--prompt=" + prompt, "--start-image", str(start_bild)]
    if end_bild is not None:
        a += ["--end-image", str(end_bild)]
    return a + ["--aspect_ratio", FORMAT, "--duration", str(int(dauer_s)), "--mode", MODUS, "--sound", "off", "--json"]


def preis(prompt: str, start_bild: Path, end_bild: Path | None, dauer_s: int, modell: str = MODELL) -> float:
    """Preis in Credits fuer genau diesen Auftrag. Kostet nichts."""
    code, text = _lauf(["generate", "cost", *_medien_argumente(prompt, start_bild, end_bild, dauer_s, modell)], ZEIT_KURZ_S)
    daten = _json(text)
    wert = daten.get("credits") if isinstance(daten, dict) else None
    if code != 0 or isinstance(wert, bool) or not isinstance(wert, (int, float)) or wert < 0:
        raise HfFehler("Higgsfield nannte keinen Preis. Bitte die Anmeldung des Programms prüfen.")
    return float(wert)


def _nummer_finden(daten) -> str | None:
    """Erste Auftragsnummer (`id`) in der Antwort: Objekt, Liste oder ein Objekt mit einer Liste darin."""
    if isinstance(daten, dict):
        wert = daten.get("id") or daten.get("job_id")
        if isinstance(wert, str) and NUMMER.fullmatch(wert):
            return wert
        for k in ("jobs", "items", "data", "results", "job"):
            gefunden = _nummer_finden(daten.get(k))
            if gefunden:
                return gefunden
    elif isinstance(daten, list):
        for x in daten:
            gefunden = _nummer_finden(x)
            if gefunden:
                return gefunden
    return None


def bestellen(prompt: str, start_bild: Path, end_bild: Path | None, dauer_s: int, modell: str = MODELL) -> str:
    """Bestellt den Clip (KOSTET CREDITS) und gibt Higgsfields Auftragsnummer zurueck, ohne auf den Clip zu warten.
    `HfFehler`: sicher nicht bestellt (das Programm lief nicht an). `HfUnklar`: das Programm lief, aber es kam keine
    Nummer - dann zuerst `nummer_suchen`, und nie neu bestellen."""
    args = ["generate", "create", *_medien_argumente(prompt, start_bild, end_bild, dauer_s, modell)]
    programm()                             # fehlt das Programm, ist sicher nichts bestellt
    try:
        code, text = _lauf(args, ZEIT_BESTELLEN_S)
    except HfFehler as e:
        raise HfUnklar(e.grund) from None  # gestartet und abgebrochen: offen, ob der Auftrag ankam
    nummer = _nummer_finden(_json(text))
    if nummer:
        return nummer
    LOG.warning("Bestellung ohne Auftragsnummer (Exit %s)", code)
    raise HfUnklar("Higgsfield gab nach dem Bestellen keine Auftragsnummer zurück.")


def nummer_suchen(prompt: str, seit_iso: str, modell: str = MODELL) -> str | None:
    """Sucht in Higgsfields Auftragsliste den juengsten Auftrag mit genau diesem Auftragstext und Modell, der nicht
    vor `seit_iso` (UTC, ISO 8601) angelegt wurde. None, wenn keiner da ist oder die Liste nicht lesbar war."""
    try:
        code, text = _lauf(["generate", "list", "--json"], ZEIT_KURZ_S)
    except HfFehler:
        return None
    daten = _json(text)
    liste = daten if isinstance(daten, list) else (daten.get("items") or daten.get("jobs") if isinstance(daten, dict) else None)
    if code != 0 or not isinstance(liste, list):
        return None
    treffer = [j for j in liste if isinstance(j, dict) and j.get("job_type") == modell
               and isinstance(j.get("params"), dict) and j["params"].get("prompt") == prompt
               and isinstance(j.get("created_at"), str) and j["created_at"][:19] >= seit_iso[:19]
               and isinstance(j.get("id"), str) and NUMMER.fullmatch(j["id"])]
    if not treffer:
        return None
    return max(treffer, key=lambda j: j["created_at"])["id"]


def stand(nummer: str) -> dict:
    """{zustand: fertig|laeuft|gescheitert, url}: Zustand eines bestellten Auftrags. Kostet nichts."""
    if not isinstance(nummer, str) or not NUMMER.fullmatch(nummer):
        raise HfFehler("Die Auftragsnummer ist ungültig.")
    code, text = _lauf(["generate", "get", nummer, "--json"], ZEIT_KURZ_S)
    daten = _json(text)
    if isinstance(daten, list) and daten:
        daten = daten[0]
    if code != 0 or not isinstance(daten, dict):
        raise HfFehler("Higgsfield gab keine Auskunft zum Auftrag.")
    status = str(daten.get("status") or "").lower()
    url = daten.get("result_url")
    if status in FERTIG and isinstance(url, str) and url:
        return {"zustand": "fertig", "url": url}
    if status in GESCHEITERT:
        return {"zustand": "gescheitert", "url": None}
    return {"zustand": "laeuft", "url": None}
