"""Der schnelle Blick des Desktop-Space: aktives Programm, Fenstertitel, Monitor, Bedienelemente.

Ohne Texterkennung. Fenster, Programm und Monitor liest dieser Dienst selbst über die
Windows-Schnittstelle (user32/kernel32), die Bedienelemente kommen vom Desktop-Dienst.

Sperrliste: Ein Fenster von Passwort-Managern, Banking, privaten Browserfenstern oder mit
Anmeldefeldern liefert nur ``gesperrt``, nie Titel, Programm oder Elemente. Geprüft wird
zweimal: am Fenster, BEVOR der Desktop-Dienst nach Elementen gefragt wird, und an den
gelieferten Elementen (Anmeldefelder sieht man oft erst dort).
"""

from __future__ import annotations

import ctypes
import re
import sys
from typing import Any, Iterable, Mapping, Optional

# ─── Sperrliste ──────────────────────────────────────────────────────────────
# Woran ein Fenster erkannt wird, steht in ANZEIGE.md ("Blick: Sperrliste"), auch was durchrutscht.

# Programmdatei ohne ".exe", klein geschrieben; Treffer, wenn der Name mit dem Stamm beginnt.
GESPERRTE_PROGRAMME = (
    # Passwort-Manager
    "keepass", "1password", "bitwarden", "dashlane", "lastpass", "enpass", "roboform", "keeper",
    "nordpass", "protonpass", "passwordsafe", "pwsafe",
    # Banking-Programme
    "starmoney", "banking", "finanzmanager", "moneymoney", "hibiscus", "subsembly", "outbank",
    # Windows: Anmeldung, Kennwortabfrage, Benutzerkontensteuerung, Sperrbildschirm
    "credentialuibroker", "consent", "logonui", "lockapp", "credwiz",
)

# Teile des Fenstertitels, klein geschrieben.
GESPERRTE_TITELTEILE = (
    # private Browserfenster (Edge, Firefox, Brave, Opera, Vivaldi)
    "inprivate", "inkognito", "incognito", "privates surfen", "private browsing", "privates fenster",
    "private window", "privater modus", "private modus", "privat-modus",
    # Passwort-Manager und gespeicherte Kennwörter, auch im Browser
    "keepass", "1password", "bitwarden", "lastpass", "dashlane", "passwortmanager", "passwort-manager",
    "password manager", "passwords", "passwörter", "kennwörter", "kennwort", "passwort", "password",
    # Banking und Zahlungen
    "banking", "sparkasse", "volksbank", "raiffeisen", "postbank", "commerzbank", "deutsche bank",
    "comdirect", "ing-diba", "ing.de", "dkb", "n26", "consorsbank", "targobank", "hypovereinsbank",
    "santander", "revolut", "bunq", "trade republic", "scalable capital", "paypal", "klarna",
    "kontostand", "überweisung", "ueberweisung", "pushtan", "phototan", "chiptan", "sepa",
    # Anmeldung
    "anmelden", "anmeldung", "einloggen", "login", "log in", "sign in", "signin", "sign-in",
    "zwei-faktor", "two-factor", "2fa", "bestätigungscode", "verification code", "einmalcode",
    "windows-sicherheit", "windows security",
)

# In Namen oder Kennungen von Bedienelementen: ein Anmeldefeld. Kurze Wörter nur als ganzes Wort.
GESPERRTE_ELEMENTTEILE = (
    "passwort", "password", "kennwort", "passphrase", "sicherheitscode", "einmalcode", "one-time",
    "bestätigungscode", "verification code", "geheimzahl", "kreditkarte", "card number", "kartennummer",
    "iban", "prüfziffer", "cvc", "cvv",
)
GESPERRTE_ELEMENTWOERTER = ("pin", "tan", "otp", "pwd", "passwd")

_WORT = re.compile(r"[a-z0-9äöüß]+")


def _klein(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def programm_gesperrt(programm: Any) -> bool:
    name = _klein(programm).removesuffix(".exe")
    return any(name.startswith(stamm) for stamm in GESPERRTE_PROGRAMME)


def titel_gesperrt(titel: Any) -> bool:
    text = _klein(titel)
    return any(teil in text for teil in GESPERRTE_TITELTEILE)


def element_gesperrt(element: Mapping[str, Any]) -> bool:
    # Kurze Wörter ("PIN", "TAN") nur an Eingabefeldern: sonst sperrte "Pin to Quick access" den Explorer.
    eingabe = _klein(element.get("control_type") or element.get("typ")).removesuffix("control") in ("edit", "combobox")
    for feld in ("name", "automation_id", "class_name"):
        text = _klein(element.get(feld))
        if not text:
            continue
        if any(teil in text for teil in GESPERRTE_ELEMENTTEILE):
            return True
        if eingabe and set(_WORT.findall(text)) & set(GESPERRTE_ELEMENTWOERTER):
            return True
    return False


def gesperrt(programm: Any, titel: Any, elemente: Iterable[Mapping[str, Any]] = ()) -> bool:
    """True, wenn dieses Fenster nichts von sich zeigen darf. Wirft nie; im Zweifel True."""
    try:
        if programm_gesperrt(programm) or titel_gesperrt(titel):
            return True
        return any(element_gesperrt(e) for e in elemente if isinstance(e, Mapping))
    except Exception:  # noqa: BLE001 - lieber zu viel sperren als zu viel zeigen
        return True


# ─── Fenster am eigenen Rechner (nur Windows) ────────────────────────────────


def programm_aus_pid(pid: Any) -> str:
    """Programmdatei ohne Pfad und ohne ".exe", klein; "" wenn nicht lesbar."""
    if sys.platform != "win32" or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return ""
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            puffer = ctypes.create_unicode_buffer(1024)
            groesse = wintypes.DWORD(len(puffer))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, puffer, ctypes.byref(groesse)):
                return ""
            pfad = puffer.value
        finally:
            kernel32.CloseHandle(handle)
        return pfad.replace("/", "\\").rsplit("\\", 1)[-1].lower().removesuffix(".exe")
    except Exception:  # noqa: BLE001
        return ""


def _monitor_nummer(user32: Any, hwnd: int) -> Optional[int]:
    """1-basiert in der Reihenfolge von EnumDisplayMonitors (wie mss, also wie desktop_screenshot)."""
    from ctypes import wintypes

    ziel = user32.MonitorFromWindow(wintypes.HWND(hwnd), 2)  # MONITOR_DEFAULTTONEAREST
    if not ziel:
        return None
    liste: list[int] = []
    rueckruf_typ = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )

    def sammeln(hmonitor, _hdc, _rect, _param):
        liste.append(int(hmonitor or 0))
        return True

    user32.EnumDisplayMonitors(None, None, rueckruf_typ(sammeln), 0)
    return liste.index(int(ziel)) + 1 if int(ziel) in liste else None


def vordergrund() -> Optional[dict[str, Any]]:
    """Das aktive Fenster: hwnd, titel, pid, programm, monitor. None, wenn nicht lesbar."""
    if sys.platform != "win32":
        return None
    try:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        laenge = user32.GetWindowTextLengthW(hwnd)
        puffer = ctypes.create_unicode_buffer(laenge + 1)
        user32.GetWindowTextW(hwnd, puffer, laenge + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            monitor = _monitor_nummer(user32, int(hwnd))
        except Exception:  # noqa: BLE001
            monitor = None
        return {
            "hwnd": int(hwnd), "titel": puffer.value, "pid": int(pid.value),
            "programm": programm_aus_pid(int(pid.value)), "monitor": monitor,
        }
    except Exception:  # noqa: BLE001
        return None
