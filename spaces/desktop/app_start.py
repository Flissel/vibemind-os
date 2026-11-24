"""App starten oder ihr Fenster nach vorn holen (Werkzeug ``desktop_app`` in ``mcp_server.py``).

Nur eine kleine feste Liste von Programmen (``PROGRAMME``): Editor, Chrome, Explorer. Gestartet
wird immer die Programmdatei selbst, ohne Argumente: nichts aus dem Satz und kein Pfad von
aussen erreicht ``CreateProcess``. Gesperrte Fenster (dieselbe Sperrliste wie der Blick:
Passwort-Manager, Banking, private Browserfenster, Anmeldung) werden nie nach vorn geholt.

Nur Windows und Standardbibliothek. Die Aufrufe von Windows liegen in kleinen Funktionen, damit
die Tests sie ersetzen koennen, ohne den echten Bildschirm zu beruehren.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import blick

# Schluessel -> Anzeigename, Programmdatei (wie blick.programm_aus_pid: klein, ohne ".exe"),
# Fensterklasse (nur wenn das Programm auch Fenster hat, die keine App-Fenster sind).
PROGRAMME: dict[str, dict[str, Any]] = {
    "editor": {"name": "Editor", "programm": "notepad", "klasse": None},
    "chrome": {"name": "Chrome", "programm": "chrome", "klasse": None},
    # explorer.exe ist auch die Taskleiste und der Desktop; ein Ordnerfenster hat diese Klasse.
    "explorer": {"name": "Explorer", "programm": "explorer", "klasse": "CabinetWClass"},
}
MODI = ("oeffnen", "vorholen")
WARTEN_AUF_FENSTER_SEKUNDEN = 8.0
WARTEN_TAKT_SEKUNDEN = 0.25


class AppFehler(RuntimeError):
    pass


# ─── Programmdateien (nur feste Orte) ────────────────────────────────────────


def _windows() -> Path:
    return Path(os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows")


def _app_pfad_aus_registry(datei: str) -> Optional[Path]:
    """Eintrag unter "App Paths" (so findet auch "Ausfuehren" Chrome). ``None``, wenn keiner."""
    if sys.platform != "win32":
        return None
    import winreg

    schluessel = r"Software\Microsoft\Windows\CurrentVersion\App Paths" + "\\" + datei
    for wurzel in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(wurzel, schluessel) as k:
                wert, _ = winreg.QueryValueEx(k, None)
        except OSError:
            continue
        if isinstance(wert, str) and wert.strip():
            return Path(os.path.expandvars(wert.strip().strip('"')))
    return None


def programmdatei(schluessel: str) -> Optional[Path]:
    """Der feste Ort der Programmdatei; ``None``, wenn sie auf diesem Rechner fehlt."""
    if schluessel == "editor":
        kandidaten = [_windows() / "System32" / "notepad.exe"]
    elif schluessel == "explorer":
        kandidaten = [_windows() / "explorer.exe"]
    elif schluessel == "chrome":
        kandidaten = [_app_pfad_aus_registry("chrome.exe")]
        for umgebung in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
            basis = os.environ.get(umgebung)
            if basis:
                kandidaten.append(Path(basis) / "Google" / "Chrome" / "Application" / "chrome.exe")
    else:
        return None
    for pfad in kandidaten:
        # Nur eine Datei mit genau dem erwarteten Namen, nie etwas anderes aus der Registry.
        if pfad is not None and pfad.name.lower() == PROGRAMME[schluessel]["programm"] + ".exe" and pfad.is_file():
            return pfad
    return None


# ─── Windows ─────────────────────────────────────────────────────────────────


def fensterklasse(hwnd: int) -> str:
    if sys.platform != "win32":
        return ""
    try:
        puffer = ctypes.create_unicode_buffer(256)
        ctypes.WinDLL("user32").GetClassNameW(hwnd, puffer, 256)
        return puffer.value
    except Exception:  # noqa: BLE001
        return ""


def vordergrund_hwnd() -> int:
    if sys.platform != "win32":
        return 0
    try:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32")
        user32.GetForegroundWindow.restype = wintypes.HWND
        return int(user32.GetForegroundWindow() or 0)
    except Exception:  # noqa: BLE001
        return 0


def nach_vorn(hwnd: int) -> bool:
    """Holt das Fenster nach vorn (aus der Taskleiste wiederhergestellt). True, wenn es vorn ist.

    Ein Hintergrundprozess darf ein fremdes Fenster nur nach vorn holen, wenn er sich kurz an
    den Eingabe-Thread des jetzigen Vordergrunds haengt (AttachThreadInput). Keine Taste, kein Klick.
    """
    if sys.platform != "win32":
        return False
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    user32.GetForegroundWindow.restype = wintypes.HWND
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    vorn = user32.GetForegroundWindow()
    fremd = user32.GetWindowThreadProcessId(vorn, None) if vorn else 0
    eigen = kernel32.GetCurrentThreadId()
    angehaengt = bool(fremd and fremd != eigen and user32.AttachThreadInput(eigen, fremd, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if angehaengt:
            user32.AttachThreadInput(eigen, fremd, False)
    return int(user32.GetForegroundWindow() or 0) == int(hwnd)


def starten(pfad: Path) -> int:
    """Startet die Programmdatei ohne Argumente und ohne Shell. Gibt die Prozessnummer zurueck."""
    prozess = subprocess.Popen(  # noqa: S603 - fester Pfad aus programmdatei(), keine Argumente
        [str(pfad)], cwd=str(Path.home()), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    return int(prozess.pid)


# ─── Ablauf ──────────────────────────────────────────────────────────────────


def _passend(f: Mapping[str, Any], schluessel: str, klasse: Callable[[int], str]) -> bool:
    eintrag = PROGRAMME[schluessel]
    if str(f.get("programm") or "") != eintrag["programm"]:
        return False
    return eintrag["klasse"] is None or klasse(int(f.get("hwnd") or 0)) == eintrag["klasse"]


def fenster_von(schluessel: str, *, liste: Callable[[], Any] = None, klasse: Callable[[int], str] = None
                ) -> tuple[Optional[int], int]:
    """(hwnd des obersten freien Fensters des Programms oder None, Zahl gesperrter Fenster des Programms)."""
    liste = liste or blick.sichtbare_fenster
    klasse = klasse or fensterklasse
    roh = liste()
    if roh is None:
        raise AppFehler("fenster_unlesbar: die Fensterliste ist auf diesem Rechner nicht lesbar")
    gesperrt = 0
    for f in roh:
        if not isinstance(f, Mapping) or not _passend(f, schluessel, klasse):
            continue
        titel = " ".join(str(f.get("titel") or "").split())
        if blick.gesperrt(f.get("programm"), titel):
            gesperrt += 1
            continue
        return int(f["hwnd"]), gesperrt
    return None, gesperrt


def ausfuehren(schluessel: str, modus: str, *, schlafen: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Fuehrt die Aktion wirklich aus. Ergebnis ohne Fenstertitel.

    ``getan``: "vorgeholt" (Fenster war offen und ist jetzt vorn), "gestartet" (neu gestartet,
    ``fenster_da`` sagt, ob binnen weniger Sekunden ein Fenster erschien), "nicht_offen" (nur
    vorholen verlangt, kein freies Fenster), "nicht_vorn" (Windows hat das Fenster nicht nach
    vorn gelassen).
    """
    if schluessel not in PROGRAMME or modus not in MODI:
        raise AppFehler("eingabe_ungueltig: unbekanntes Programm oder unbekannter Modus")
    hwnd, gesperrt = fenster_von(schluessel)
    if hwnd is not None:
        if nach_vorn(hwnd):
            return {"getan": "vorgeholt", "fenster_da": True, "gesperrt_uebersprungen": gesperrt}
        return {"getan": "nicht_vorn", "fenster_da": True, "gesperrt_uebersprungen": gesperrt}
    if modus == "vorholen":
        return {"getan": "nicht_offen", "fenster_da": False, "gesperrt_uebersprungen": gesperrt}
    pfad = programmdatei(schluessel)
    if pfad is None:
        raise AppFehler(f"programm_fehlt: {PROGRAMME[schluessel]['name']} ist auf diesem Rechner nicht installiert")
    starten(pfad)
    ende = time.monotonic() + WARTEN_AUF_FENSTER_SEKUNDEN
    while time.monotonic() < ende:
        schlafen(WARTEN_TAKT_SEKUNDEN)
        neu, _ = fenster_von(schluessel)
        if neu is not None:
            # Ein frisch gestartetes Programm kommt meist selbst nach vorn; sonst nachhelfen.
            if vordergrund_hwnd() != neu:
                nach_vorn(neu)
            return {"getan": "gestartet", "fenster_da": True, "gesperrt_uebersprungen": gesperrt}
    return {"getan": "gestartet", "fenster_da": False, "gesperrt_uebersprungen": gesperrt}
