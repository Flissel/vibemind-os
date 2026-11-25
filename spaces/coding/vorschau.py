"""Live-Vorschau: nach gruener Abnahme die Sandbox auf den Ordner des Auftrags setzen.

Hinter einem Schalter (Vorgabe aus): Umgebung ``CODING_VORSCHAU_AUTOSTART`` oder
die Datei ``%LOCALAPPDATA%\\VibeMind\\coding-dienst\\vorschau.an``. Es laeuft
immer hoechstens eine Vorschau-Sandbox (fester Name); ein neuer Auftrag
ersetzt den alten. Eingehaengt wird nur der Ordner ``out`` eines Auftrags
unterhalb der Arbeitswurzel, die Ports haengen nur an 127.0.0.1. Ist zu wenig
Speicher frei, startet nichts und das Ergebnis sagt es.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from spaces.coding import pipeline

logger = logging.getLogger("coding.vorschau")

NAME = "project-space-vorschau"
IMAGE = "coding-engine/sandbox:latest"
SPEICHER_GRENZE_GB = 3.3


def schalterdatei() -> Path:
    """Leere Datei neben der Schluesseldatei des Dienstes; ihr Dasein schaltet den Autostart ein."""
    basis = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(basis) / "VibeMind" / "coding-dienst" / "vorschau.an"


def an() -> bool:
    wert = os.environ.get("CODING_VORSCHAU_AUTOSTART", "").strip().lower()
    if wert in {"0", "false", "nein", "aus"}:
        return False
    return wert in {"1", "true", "ja", "an"} or schalterdatei().is_file()


def _port(variable: str, vorgabe: int) -> int:
    try:
        return int(os.environ.get(variable, vorgabe))
    except ValueError:
        return vorgabe


def freier_speicher_gb() -> Optional[float]:
    """Commit-Limit minus belegter Commit, wie die Speicherregel misst. Ohne Windows: None."""
    if sys.platform != "win32":
        return None

    class _Info(ctypes.Structure):
        _fields_ = [("groesse", ctypes.c_ulong)] + [(n, ctypes.c_size_t) for n in (
            "commit_total", "commit_limit", "commit_peak", "physisch_total", "physisch_frei",
            "cache", "kernel_total", "kernel_paged", "kernel_nonpaged", "seite")] + [
            ("handles", ctypes.c_ulong), ("prozesse", ctypes.c_ulong), ("faeden", ctypes.c_ulong)]

    info = _Info()
    info.groesse = ctypes.sizeof(info)
    if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(info), info.groesse):
        return None
    return (info.commit_limit - info.commit_total) * info.seite / 1024 ** 3


def befehl(ordner: Path) -> List[str]:
    vnc = _port("CODING_VORSCHAU_VNC_PORT", 6090)
    app = _port("CODING_VORSCHAU_APP_PORT", 3100)
    return [
        "docker", "run", "-d", "--name", NAME, "--memory", "1536m",
        "-v", f"{ordner}:/workspace/app", "-w", "/workspace/app",
        "-p", f"127.0.0.1:{vnc}:6090", "-p", f"127.0.0.1:{app}:3100",
        "-e", "ENABLE_VNC=true", "-e", "NOVNC_PORT=6090", "-e", "PREVIEW_MODE=auto", "-e", "MAX_FIX_ATTEMPTS=1",
        IMAGE,
    ]


def _ordner(auftrag: Dict[str, Any]) -> Optional[Path]:
    roh = auftrag.get("zielordner")
    if not roh:
        return None
    ordner = Path(roh).resolve()
    wurzel = pipeline.arbeitswurzel().resolve()
    if not ordner.is_dir() or wurzel not in ordner.parents:
        return None
    return ordner


def starten(auftrag: Dict[str, Any], ausfuehren: Callable[..., Any] = subprocess.run,
            speicher: Callable[[], Optional[float]] = freier_speicher_gb) -> Dict[str, Any]:
    """Setzt die Vorschau auf diesen Auftrag. Wirft nie; sagt, was passiert ist."""
    if not an():
        return {"gestartet": False, "grund": "aus"}
    ordner = _ordner(auftrag)
    if ordner is None:
        return {"gestartet": False, "grund": "kein Ordner des Auftrags unter der Arbeitswurzel"}
    frei = speicher()
    if frei is not None and frei < SPEICHER_GRENZE_GB:
        return {"gestartet": False, "grund": f"nur {frei:.1f} GB frei, unter {SPEICHER_GRENZE_GB} GB"}
    try:
        ausfuehren(["docker", "rm", "-f", NAME], capture_output=True, text=True, timeout=60)
        ergebnis = ausfuehren(befehl(ordner), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as fehler:
        return {"gestartet": False, "grund": f"docker liess sich nicht ausfuehren ({fehler})"}
    if ergebnis.returncode != 0:
        return {"gestartet": False, "grund": (ergebnis.stderr or "docker run scheiterte").strip()[:300]}
    return {"gestartet": True, "auftrag": auftrag.get("id"), "ordner": str(ordner)}


def nach_abnahme(auftrag_id: str, urteil: Dict[str, Any]) -> None:
    """Nur bei gruener Abnahme und eingeschaltetem Schalter."""
    if urteil.get("zustand") != pipeline.FERTIG or not an():
        return
    try:
        stand = starten(pipeline._laden(auftrag_id))
    except Exception:  # noqa: BLE001 - die Vorschau darf die Abnahme nie stoeren
        logger.exception("Vorschau fuer Auftrag %s nicht gestartet", auftrag_id)
        return
    logger.info("Vorschau fuer Auftrag %s: %s", auftrag_id, stand)
