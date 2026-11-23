"""Feste deutsche Antwortsaetze fuer die Desktop-Faehigkeiten.

Nach dem Muster der Ideen (``ideen_abkuerzung.antwort_fuer_schritt``): Der Satz entsteht aus
dem Ergebnis des Werkzeugs, kein Modell formuliert. Im Satz stehen keine Interna (``_meta``,
Freigaben, Pfade auf dem Rechner) und keine Kennungen ausser der Aufgaben-Kennung beim Start.
Text vom Bildschirm erscheint nur als Zitat und nur gekuerzt.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

DESKTOP_FAEHIGKEITEN = (
    "desktop_status", "desktop_task_status", "desktop_screen_read", "desktop_screenshot", "desktop_task_start",
)
MAX_BILDSCHIRM_ZITAT = 300
MAX_AUFTRAG_ZITAT = 80

_ZUSTAND = {
    "laeuft": "läuft", "fertig": "ist fertig", "fehlgeschlagen": "ist fehlgeschlagen",
    "abgebrochen": "ist abgebrochen", "abgelaufen": "ist abgelaufen",
}
_ZUSTAND_KURZ = {
    "laeuft": "läuft", "fertig": "fertig", "fehlgeschlagen": "fehlgeschlagen",
    "abgebrochen": "abgebrochen", "abgelaufen": "abgelaufen",
}

# Fehlertext -> Satz. Der erste passende Teil gewinnt; Codes des Werkzeug-Dienstes und
# Meldungen des Plan-Ausfuehrers. Werte aus dem Fehlertext kommen nie in den Satz.
_FEHLER = (
    ("nur aus getippter Eingabe", "Das geht nur aus getippter Eingabe."),
    ('mit "Desktop-Aufgabe:" beginnen', "Eine Desktop-Aufgabe beginnt mit „Desktop-Aufgabe:“."),
    ("schon eine Desktop-Aufgabe", "Es läuft schon eine Desktop-Aufgabe. Warte, bis sie fertig ist, oder halte sie an."),
    ("aufgabe_laeuft", "Es läuft schon eine Desktop-Aufgabe. Warte, bis sie fertig ist, oder halte sie an."),
    ("not_aus", "Der Not-Aus ist gesetzt, deshalb wird nichts ausgeführt."),
    ("dienst_nicht_erreichbar", "Der Desktop-Dienst läuft gerade nicht."),
    ("dienst_veraltet", "Der Desktop-Dienst ist zu alt für diese Anfrage."),
    ("dienst_fehler", "Der Desktop-Dienst hat keine brauchbare Antwort gegeben."),
    ("aufgabe_unbekannt", "Diese Desktop-Aufgabe gibt es nicht."),
    ("aufgabenbuch_unlesbar", "Das Aufgabenbuch des Desktops ist nicht lesbar."),
    ("budget_erschoepft", "Das Tagesbudget des Desktop-Agenten ist aufgebraucht."),
    ("freigabe_ausstehend", "Das wartet noch auf eine Freigabe."),
    ("approval pending", "Das wartet noch auf eine Freigabe."),
    ("OpenFang", "Der Desktop-Agent ist gerade nicht erreichbar."),
    ("desktop.task:", "Die Desktop-Aufgabe ist nicht gelungen."),
)


def _inhalt(ergebnis: Any) -> Optional[Mapping[str, Any]]:
    try:
        from .desktop_schleife import WerkzeugFehler, werkzeug_inhalt
        try:
            return werkzeug_inhalt(ergebnis)
        except WerkzeugFehler:
            return None
    except Exception:  # noqa: BLE001
        return None


def _mehrzahl(anzahl: int, eins: str, viele: str) -> str:
    return f"{anzahl} {eins if anzahl == 1 else viele}"


def _zitat(text: Any, hoechstens: int) -> str:
    sauber = " ".join(str(text or "").split())
    return sauber if len(sauber) <= hoechstens else sauber[: hoechstens - 1].rstrip() + "…"


def _zahl(wert: Any) -> Optional[int]:
    return wert if isinstance(wert, int) and not isinstance(wert, bool) else None


def fehlersatz_desktop(fehler: Any) -> str:
    text = str(fehler or "")
    for teil, satz in _FEHLER:
        if teil in text:
            return satz
    return "Das hat am Desktop nicht geklappt."


def _status(inhalt: Mapping[str, Any]) -> str:
    if inhalt.get("dienst_erreichbar") is True:
        monitore = inhalt.get("monitore")
        anzahl = len(monitore) if isinstance(monitore, list) else 0
        teile = ["Desktop-Dienst läuft"]
        if anzahl:
            teile.append(_mehrzahl(anzahl, "Monitor", "Monitore"))
    else:
        teile = ["Desktop-Dienst ist nicht erreichbar"]
    teile.append("echte Klicks an" if inhalt.get("ausfuehren") is True else "Trockenlauf")
    satz = ", ".join(teile) + "."
    if inhalt.get("not_aus") is True:
        satz += " Der Not-Aus ist gesetzt."
    aufgabe = inhalt.get("aufgabe")
    if isinstance(aufgabe, Mapping) and aufgabe.get("zustand") in _ZUSTAND_KURZ:
        satz += f" Letzte Aufgabe: {_ZUSTAND_KURZ[aufgabe['zustand']]}."
    else:
        satz += " Noch keine Aufgabe."
    return satz


def _aufgabe(inhalt: Mapping[str, Any]) -> str:
    if inhalt.get("aufgabe") is None and "zustand" not in inhalt:
        return "Es gibt noch keine Desktop-Aufgabe."
    zustand = _ZUSTAND.get(str(inhalt.get("zustand")), "hat einen unbekannten Stand")
    schritte = inhalt.get("schritte")
    anzahl = len(schritte) if isinstance(schritte, list) else 0
    auftrag = _zitat(inhalt.get("aufgabe"), MAX_AUFTRAG_ZITAT)
    return f"Aufgabe „{auftrag}“ {zustand}, {_mehrzahl(anzahl, 'Schritt', 'Schritte')}."


def _bildschirm(inhalt: Mapping[str, Any]) -> str:
    text = str(inhalt.get("text") or "")
    zeilen = [" ".join(z.split()) for z in text.splitlines()]
    zeilen = [z for z in zeilen if z]
    if not zeilen:
        return "Auf dem Bildschirm ist kein Text zu lesen."
    zitat = _zitat(" · ".join(zeilen), MAX_BILDSCHIRM_ZITAT)
    mehr = "mehr als " if inhalt.get("gekuerzt") is True else ""
    return f"Auf dem Bildschirm steht: „{zitat}“ ({mehr}{len(text)} Zeichen gelesen)."


def _bildschirmfoto(inhalt: Mapping[str, Any]) -> str:
    breite, hoehe, monitor = _zahl(inhalt.get("breite")), _zahl(inhalt.get("hoehe")), _zahl(inhalt.get("monitor"))
    wo = f" von Monitor {monitor}" if monitor and monitor > 1 else ""
    groesse = f" ({breite}×{hoehe})" if breite and hoehe else ""
    return f"Bildschirmfoto{wo} gemacht{groesse}."


def _start(inhalt: Mapping[str, Any]) -> str:
    kennung = inhalt.get("task_id")
    auftrag = _zitat(inhalt.get("auftrag"), MAX_AUFTRAG_ZITAT)
    zustand = inhalt.get("zustand")
    if zustand == "laeuft":
        return f"Aufgabe „{auftrag}“ läuft (Kennung {kennung})."
    return f"Aufgabe „{auftrag}“ {_ZUSTAND.get(str(zustand), 'ist gestartet')} (Kennung {kennung})."


_SAETZE = {
    "desktop_status": _status,
    "desktop_task_status": _aufgabe,
    "desktop_screen_read": _bildschirm,
    "desktop_screenshot": _bildschirmfoto,
    "desktop_task_start": _start,
}


def antwort_fuer_desktop(capability: Any, ok: bool, ergebnis: Any, fehler: Any) -> Optional[str]:
    """Der Satz fuer den Chat nach einem Desktop-Schritt; ``None`` nur fuer fremde Faehigkeiten.

    Ein unlesbares Ergebnis gibt einen neutralen Satz, nie den Rohtext. Wirft nie.
    """
    if capability not in _SAETZE:
        return None
    try:
        if not ok:
            return fehlersatz_desktop(fehler)
        inhalt = _inhalt(ergebnis)
        if inhalt is None:
            return "Der Desktop-Dienst hat keine lesbare Antwort gegeben."
        return _SAETZE[capability](inhalt)
    except Exception:  # noqa: BLE001
        return "Der Desktop-Dienst hat keine lesbare Antwort gegeben."
