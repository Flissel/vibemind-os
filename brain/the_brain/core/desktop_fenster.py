"""Fester Lesesatz des Desktop-Space: "Welche Fenster sind offen?".

Gelesen wird ueber den festen Aufruf ``desktop_fenster`` (Ereignis ``desktop.fenster``) mit
einem eigenen Laufzeit-Kontext, ohne Modell. Der Werkzeug-Dienst filtert durch dieselbe
Sperrliste wie der Blick: gesperrte Fenster kommen nur als ``{"gesperrt": true}``.

Was gespeichert wird (wie 10(a), core/desktop_sicht.py): Das Ergebnis des Schritts (HopResult,
Plan-Recorder, Verlauf, Lernspeicher) enthaelt nur Zahlen und eine Kennung. Der Satz mit den
Fenstertiteln liegt nur fluechtig im Speicher dieses Moduls und wird genau einmal fuer die
Antwort an die App abgeholt (:func:`antwort`). Ins Protokoll geht :func:`protokoll` (ohne Titel).
"""
from __future__ import annotations

import itertools
import time
from typing import Any, Mapping, Optional

from .fluechtige_saetze import abholen as _abholen, merken as _merken
from .desktop_schleife import FreigabeAusstehend, WerkzeugFehler, fester_aufruf
from .desktop_schleife_contract import AGENT, ZIEL_SERVER

WERKZEUG = "desktop_fenster"
EREIGNIS = "desktop.fenster"
CAPABILITY = "desktop_fenster"
IM_SATZ = 8               # so viele Fenster nennt der Satz, der Rest ist "und K weitere"
MAX_TITEL_IM_SATZ = 50
_ZAEHLER = itertools.count(1)


def kontext() -> Any:
    """Eigener Laufzeit-Kontext fuer EINEN Aufruf, gebunden ueber ``desktop.fenster``."""
    from .capability_targets import find_registered_mcp_authority
    from .openfang_runtime_authority import RuntimeInvocationContext, runtime_invocation_id

    autoritaet = find_registered_mcp_authority(AGENT, ZIEL_SERVER, WERKZEUG)
    if autoritaet is None or autoritaet.event_id != EREIGNIS or not autoritaet.space_id:
        raise WerkzeugFehler(f"nicht_gebunden: {WERKZEUG} ist im Verzeichnis nicht ueber {EREIGNIS} gebunden")
    nummer = next(_ZAEHLER)
    plan_id = f"fenster-{int(time.time() * 1000)}-{nummer:04d}"
    schritt = f"s1/fenster/{nummer:04d}/{WERKZEUG}"
    return RuntimeInvocationContext(
        correlation_id=plan_id, plan_id=plan_id, plan_revision=1, step_id=schritt,
        space_id=autoritaet.space_id, agent_name=autoritaet.agent,
        invocation_id=runtime_invocation_id(plan_id, 1, schritt),
    )


def _kurz(text: Any, hoechstens: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= hoechstens else text[: hoechstens - 1].rstrip() + "…"


def satz(liste: Any) -> Optional[str]:
    """"3 Fenster offen: notepad „Unbenannt - Editor“, ein gesperrtes Fenster, … (und 2 weitere)."

    ``None``, wenn die Antwort des Werkzeugs nicht lesbar ist.
    """
    if not isinstance(liste, Mapping) or not isinstance(liste.get("fenster"), list):
        return None
    anzahl = liste.get("anzahl")
    fenster = [f for f in liste["fenster"] if isinstance(f, Mapping)]
    if not isinstance(anzahl, int) or isinstance(anzahl, bool) or anzahl < len(fenster):
        anzahl = len(fenster)
    if anzahl == 0:
        return "Es ist kein Fenster offen."
    teile = []
    for f in fenster[:IM_SATZ]:
        if f.get("gesperrt") is not False:
            teile.append("ein gesperrtes Fenster")
            continue
        titel = _kurz(f.get("titel"), MAX_TITEL_IM_SATZ)
        programm = _kurz(f.get("programm"), 30) or "unbekanntes Programm"
        teile.append(f"{programm} „{titel}“" if titel else programm)
    kopf = "Ein Fenster offen" if anzahl == 1 else f"{anzahl} Fenster offen"
    rest = f" (und {anzahl - len(teile)} weitere)" if anzahl > len(teile) else ""
    return f"{kopf}: " + ", ".join(teile) + rest + "."


def fenster_lesen(value: Any = None, **_: Any) -> dict:
    """Ziel ``direct:core.desktop_fenster:fenster_lesen``. Gibt nur Zahlen und eine Kennung zurueck."""
    try:
        liste = fester_aufruf(WERKZEUG, {}, kontext())
    except FreigabeAusstehend:
        return {"success": False, "error": "freigabe_ausstehend"}
    except WerkzeugFehler as fehler:
        return {"success": False, "error": fehlerart(str(fehler))}
    text = satz(liste)
    if text is None:
        return {"success": False, "error": "unlesbar"}
    fenster = [f for f in liste["fenster"] if isinstance(f, Mapping)]
    anzahl = liste.get("anzahl") if isinstance(liste.get("anzahl"), int) else len(fenster)
    gesperrt = sum(1 for f in fenster if f.get("gesperrt") is not False)
    return {"success": True, "anzahl": anzahl, "gesperrt": gesperrt, "satz_id": _merken(text)}


# Was fehlt, aus der Fehlermeldung des festen Aufrufs (ohne Bildschirminhalt: der Aufruf
# scheiterte, bevor ein Titel gelesen wurde).
_FEHLERARTEN = (
    ("nicht_freigegeben", ("not permitted", "nicht freigegeben", "not allowed")),
    ("freigabe_ausstehend", ("approval was not approved", "pending", "ausstehend")),
    ("nicht_gebunden", ("nicht_gebunden",)),
    ("fenster_unlesbar", ("fenster_unlesbar",)),
    ("dienst_aus", ("connection", "refused", "verweigert", "unreachable", "not reachable", "timed out",
                    "timeout", "openfang_url", "no mcp server", "not connected", "unknown server")),
)

FEHLERSAETZE = {
    "nicht_freigegeben": ("Die Fensterliste kann ich noch nicht lesen: OpenFang hat dem Desktop-Agenten das "
                          "Werkzeug desktop_fenster nicht freigegeben."),
    "freigabe_ausstehend": "Die Fensterliste kann ich gerade nicht lesen: die Freigabe für den Aufruf fehlt.",
    "nicht_gebunden": "Die Fensterliste kann ich noch nicht lesen: das Werkzeug ist im Verzeichnis nicht gebunden.",
    "fenster_unlesbar": "Die Fensterliste ist auf diesem Rechner nicht lesbar.",
    "dienst_aus": "Die Fensterliste kann ich gerade nicht lesen: der Werkzeug-Dienst des Desktop antwortet nicht.",
}
ALLGEMEIN = "Die Fensterliste kann ich gerade nicht lesen. Geändert habe ich nichts."


def fehlerart(meldung: str) -> str:
    text = str(meldung or "").lower()
    for art, teile in _FEHLERARTEN:
        if any(teil in text for teil in teile):
            return art
    return "werkzeug_fehler"


def fehlersatz(ergebnis: Any, fehler: Any = None) -> str:
    """Genauer Satz, wenn die Fensterliste nicht gelesen werden konnte."""
    ergebnis = _ergebnis(ergebnis)
    art = ergebnis.get("error") if isinstance(ergebnis, Mapping) else None
    if art not in FEHLERSAETZE and fehler:
        art = fehlerart(str(fehler))
    return FEHLERSAETZE.get(str(art), ALLGEMEIN)


def _ergebnis(ergebnis: Any) -> Any:
    if isinstance(ergebnis, Mapping) and isinstance(ergebnis.get("result"), Mapping) and "satz_id" not in ergebnis:
        return ergebnis["result"]
    return ergebnis


def antwort(ergebnis: Any) -> Optional[str]:
    """Der Satz mit den Titeln fuer die App; einmal abgeholt, dann weg. ``None``: nicht lesbar."""
    try:
        ergebnis = _ergebnis(ergebnis)
        if not isinstance(ergebnis, Mapping) or ergebnis.get("success") is not True:
            return None
        return _abholen(ergebnis.get("satz_id"))
    except Exception:  # noqa: BLE001
        return None


def protokoll(ergebnis: Any) -> Optional[str]:
    """Der Satz fuer Plan-Verlauf und Protokolle: nur Zahlen, kein Titel."""
    try:
        ergebnis = _ergebnis(ergebnis)
        if not isinstance(ergebnis, Mapping) or ergebnis.get("success") is not True:
            return None
        return (f"{int(ergebnis.get('anzahl') or 0)} Fenster offen, davon {int(ergebnis.get('gesperrt') or 0)} "
                "gesperrt (Titel nur in der Antwort an die App).")
    except Exception:  # noqa: BLE001
        return None
