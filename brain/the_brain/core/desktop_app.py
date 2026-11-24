"""Fester Satz des Desktop-Space: "Öffne <Programm>" und "Hol <Programm> nach vorn".

Ohne Modell. Aus dem Satz wird nur einer von drei festen Schluesseln (editor, chrome, explorer)
und einer von zwei Modi; nichts sonst aus dem Satz erreicht den Werkzeug-Dienst. Ausgefuehrt
wird ueber den festen Aufruf ``desktop_app`` (Ereignis ``desktop.app``) mit eigenem
Laufzeit-Kontext (Spur in OpenFang), gebucht im Aufgabenbuch des Desktop-Space (Protokoll, das
die App liest). Wirkklasse ``external``: es geschieht etwas am Bildschirm.

Fenstertitel gibt es hier nicht: das Werkzeug meldet nur Programm, Modus und Ausgang.
"""
from __future__ import annotations

import itertools
import logging
import re
import time
from typing import Any, Mapping, Optional, Tuple

from .desktop_schleife import FreigabeAusstehend, WerkzeugFehler, fester_aufruf
from .desktop_schleife_contract import AGENT, ZIEL_SERVER

logger = logging.getLogger(__name__)

WERKZEUG = "desktop_app"
EREIGNIS = "desktop.app"
CAPABILITY = "desktop_app"
_ZAEHLER = itertools.count(1)

# Woerter fuer die drei erlaubten Programme. Mehr gibt es nicht: was hier fehlt, hat keinen festen Weg.
NAMEN = {
    "editor": ("editor", "texteditor", "text-editor", "notepad", "windows-editor"),
    "chrome": ("chrome", "google chrome", "chrome-browser", "google-chrome"),
    "explorer": ("explorer", "datei-explorer", "dateiexplorer", "windows-explorer", "file explorer"),
}
ANZEIGE = {"editor": "den Editor", "chrome": "Chrome", "explorer": "den Explorer"}  # Akkusativ
NOMINATIV = {"editor": "der Editor", "chrome": "Chrome", "explorer": "der Explorer"}

_PROGRAMM = "(?P<programm>" + "|".join(
    re.escape(n).replace(r"\ ", r"\s+") for n in sorted({n for ns in NAMEN.values() for n in ns}, key=len, reverse=True)
) + ")"
_ARTIKEL = r"(?:(?:den|das|die|dem)\s+)?"
_FENSTER = r"(?:-?fenster)?"
_ORT = r"(?:\s+(?:auf|am)\s+(?:dem\s+|meinem\s+)?desktop)?"
_BITTE = r"(?:bitte\s+)?"

_ENDE = r"(?:\s+bitte)?$"
# (Muster, Modus); "vorholen" zuerst. Jedes Muster hat genau eine Gruppe "programm".
_MUSTER = tuple((re.compile(m, re.IGNORECASE), modus) for m, modus in (
    (rf"^{_BITTE}(?:hol|hole|bring|bringe)\s+(?:mir\s+)?{_ARTIKEL}{_PROGRAMM}{_FENSTER}{_ORT}\s+"
     rf"(?:nach\s+vorn(?:e)?|in\s+den\s+vordergrund){_ENDE}", "vorholen"),
    (rf"^{_BITTE}(?:wechsle|wechsel|geh|gehe)\s+(?:zu|zum|in\s+den|ins)\s+{_ARTIKEL}{_PROGRAMM}{_FENSTER}{_ENDE}",
     "vorholen"),
    (rf"^{_BITTE}(?:öffne|oeffne|starte|start)\s+{_BITTE}(?:mir\s+)?{_ARTIKEL}{_PROGRAMM}{_ORT}{_ENDE}", "oeffnen"),
    (rf"^{_BITTE}(?:mach|mache)\s+(?:mir\s+)?{_ARTIKEL}{_PROGRAMM}\s+auf{_ORT}{_ENDE}", "oeffnen"),
))


def schluessel_aus_wort(wort: str) -> Optional[str]:
    wort = " ".join(str(wort or "").lower().split())
    for schluessel, namen in NAMEN.items():
        if wort in namen:
            return schluessel
    return None


def auftrag_aus_satz(satz: Any) -> Optional[Tuple[str, str]]:
    """("editor", "oeffnen") aus "Öffne den Editor"; ("chrome", "vorholen") aus "Hol Chrome nach vorn"."""
    if not isinstance(satz, str):
        return None
    text = " ".join(satz.split()).rstrip("?.!").strip()
    if not text or len(text) > 120:
        return None
    for muster, modus in _MUSTER:
        m = muster.match(text)
        if m:
            schluessel = schluessel_aus_wort(m.group("programm"))
            if schluessel:
                return schluessel, modus
    return None


def kontext() -> Any:
    """Eigener Laufzeit-Kontext fuer EINEN Aufruf, gebunden ueber ``desktop.app``."""
    from .capability_targets import find_registered_mcp_authority
    from .openfang_runtime_authority import RuntimeInvocationContext, runtime_invocation_id

    autoritaet = find_registered_mcp_authority(AGENT, ZIEL_SERVER, WERKZEUG)
    if autoritaet is None or autoritaet.event_id != EREIGNIS or not autoritaet.space_id:
        raise WerkzeugFehler(f"nicht_gebunden: {WERKZEUG} ist im Verzeichnis nicht ueber {EREIGNIS} gebunden")
    nummer = next(_ZAEHLER)
    plan_id = f"app-{int(time.time() * 1000)}-{nummer:04d}"
    schritt = f"s1/app/{nummer:04d}/{WERKZEUG}"
    return RuntimeInvocationContext(
        correlation_id=plan_id, plan_id=plan_id, plan_revision=1, step_id=schritt,
        space_id=autoritaet.space_id, agent_name=autoritaet.agent,
        invocation_id=runtime_invocation_id(plan_id, 1, schritt),
    )


def auftrag(value: Any, intent: Any = None) -> Optional[Tuple[str, str]]:
    """Programm und Modus: ausdrueckliche Felder (nur feste Werte), sonst der Satz, sonst ``_intent``."""
    daten = value if isinstance(value, Mapping) else {"value": value}
    programm, modus = daten.get("programm"), daten.get("modus")
    if isinstance(programm, str) and isinstance(modus, str):
        schluessel = schluessel_aus_wort(programm)
        if schluessel and modus in ("oeffnen", "vorholen"):
            return schluessel, modus
    for quelle in (daten.get("value"), daten.get("_intent"), intent):
        gefunden = auftrag_aus_satz(quelle)
        if gefunden:
            return gefunden
    return None


def app(value: Any = None, _intent: Any = None, **_: Any) -> dict:
    """Ziel ``direct:core.desktop_app:app``."""
    gefunden = auftrag(value, _intent)
    if gefunden is None:
        return {"success": False, "error": "auftrag_unklar"}
    schluessel, modus = gefunden
    try:
        kontext_ = kontext()
        antwort = fester_aufruf(WERKZEUG, {"programm": schluessel, "modus": modus}, kontext_)
    except FreigabeAusstehend:
        return {"success": False, "error": "freigabe_ausstehend", "programm": schluessel, "modus": modus}
    except WerkzeugFehler as fehler:
        art = fehlerart(str(fehler))
        logger.info("desktop_app %s/%s abgelehnt: %s", schluessel, modus, art)
        return {"success": False, "error": art, "programm": schluessel, "modus": modus}
    if not isinstance(antwort, Mapping) or "getan" not in antwort:
        return {"success": False, "error": "unlesbar", "programm": schluessel, "modus": modus}
    ergebnis = {
        "success": bool(antwort.get("ok")), "programm": schluessel, "modus": modus,
        "getan": str(antwort.get("getan") or ""), "trocken": bool(antwort.get("trocken")),
        "fenster_da": antwort.get("fenster_da") if isinstance(antwort.get("fenster_da"), bool) else None,
        "task_id": str(antwort.get("task_id") or "")[:40], "plan_id": kontext_.plan_id,
    }
    if not ergebnis["success"]:
        ergebnis["error"] = fehlerart(str(antwort.get("meldung") or ergebnis["getan"]))
    logger.info("desktop_app %s/%s: %s (Aufgabe %s, Spur %s)", schluessel, modus, ergebnis["getan"],
                ergebnis["task_id"], ergebnis["plan_id"])
    return ergebnis


_FEHLERARTEN = (
    ("nicht_freigegeben", ("not permitted", "nicht freigegeben", "not allowed")),
    ("freigabe_ausstehend", ("approval was not approved", "pending", "ausstehend")),
    ("nicht_gebunden", ("nicht_gebunden",)),
    ("not_aus", ("not_aus",)),
    ("aufgabe_laeuft", ("aufgabe_laeuft",)),
    ("programm_fehlt", ("programm_fehlt",)),
    ("nicht_offen", ("nicht_offen",)),
    ("nicht_vorn", ("nicht_vorn",)),
    ("fenster_unlesbar", ("fenster_unlesbar",)),
    ("start_fehlgeschlagen", ("start_fehlgeschlagen",)),
    ("dienst_aus", ("connection", "refused", "verweigert", "unreachable", "not reachable", "timed out",
                    "timeout", "openfang_url", "no mcp server", "not connected", "unknown server")),
)


def fehlerart(meldung: str) -> str:
    text = str(meldung or "").lower()
    for art, teile in _FEHLERARTEN:
        if any(teil in text for teil in teile):
            return art
    return "werkzeug_fehler"


def _ergebnis(ergebnis: Any) -> Any:
    if isinstance(ergebnis, Mapping) and isinstance(ergebnis.get("result"), Mapping) and "getan" not in ergebnis \
            and "error" not in ergebnis:
        return ergebnis["result"]
    return ergebnis


def antwort(ergebnis: Any) -> Optional[str]:
    """Satz fuer die App bei Erfolg. ``None``: nicht lesbar."""
    try:
        ergebnis = _ergebnis(ergebnis)
        if not isinstance(ergebnis, Mapping) or ergebnis.get("success") is not True:
            return None
        name = ANZEIGE.get(str(ergebnis.get("programm")))
        if name is None:
            return None
        getan = ergebnis.get("getan")
        if getan == "trocken":
            return (f"Gebucht, aber nicht ausgeführt: {name} habe ich nicht angefasst, weil die Desktop-Steuerung "
                    "im Trockenlauf ist.")
        if getan == "vorgeholt":
            return f"Ich habe {name} nach vorn geholt."
        if getan == "gestartet":
            if ergebnis.get("fenster_da") is False:
                return f"Ich habe {name} gestartet, aber noch kein Fenster gesehen."
            return f"Ich habe {name} geöffnet."
        return None
    except Exception:  # noqa: BLE001
        return None


FEHLERSAETZE = {
    "nicht_offen": "{nom} ist nicht offen, darum habe ich nichts nach vorn geholt. „Öffne …“ startet es.",
    "nicht_vorn": "Windows hat {name} nicht nach vorn gelassen. Geändert habe ich sonst nichts.",
    "not_aus": "Die Desktop-Steuerung ist angehalten (Not-Aus). Ich habe {name} nicht angefasst.",
    "aufgabe_laeuft": "Am Desktop läuft gerade eine andere Aufgabe. Ich habe {name} nicht angefasst.",
    "programm_fehlt": "{name} finde ich auf diesem Rechner nicht.",
    "start_fehlgeschlagen": "Windows hat {name} nicht gestartet.",
    "fenster_unlesbar": "Die Fensterliste ist auf diesem Rechner nicht lesbar; ich habe {name} nicht angefasst.",
    "nicht_freigegeben": ("Das kann ich noch nicht: OpenFang hat dem Desktop-Agenten das Werkzeug "
                          "desktop_app nicht freigegeben."),
    "freigabe_ausstehend": "Das kann ich gerade nicht: die Freigabe für den Aufruf fehlt.",
    "nicht_gebunden": "Das kann ich noch nicht: das Werkzeug desktop_app ist im Verzeichnis nicht gebunden.",
    "dienst_aus": "Das kann ich gerade nicht: der Werkzeug-Dienst des Desktop antwortet nicht.",
    "auftrag_unklar": "Welches Programm? Ich kann den Editor, Chrome und den Explorer öffnen oder nach vorn holen.",
}
ALLGEMEIN = "Das hat nicht geklappt. Am Desktop habe ich nichts geändert."


def fehlersatz(ergebnis: Any, fehler: Any = None) -> str:
    ergebnis = _ergebnis(ergebnis)
    art = ergebnis.get("error") if isinstance(ergebnis, Mapping) else None
    if art not in FEHLERSAETZE and fehler:
        art = fehlerart(str(fehler))
    satz = FEHLERSAETZE.get(str(art))
    if satz is None:
        return ALLGEMEIN
    programm = str(ergebnis.get("programm")) if isinstance(ergebnis, Mapping) else ""
    satz = satz.format(name=ANZEIGE.get(programm, "das Programm"), nom=NOMINATIV.get(programm, "das Programm"))
    return satz[0].upper() + satz[1:]


def protokoll(ergebnis: Any) -> Optional[str]:
    """Satz fuer Plan-Verlauf und Protokolle: Programm, Modus, Ausgang, Kennung im Aufgabenbuch."""
    try:
        ergebnis = _ergebnis(ergebnis)
        if not isinstance(ergebnis, Mapping) or not ergebnis.get("programm"):
            return None
        ausgang = ergebnis.get("getan") or ergebnis.get("error") or "unbekannt"
        return (f"desktop_app {ergebnis.get('programm')}/{ergebnis.get('modus')}: {ausgang}"
                f" (Aufgabe {ergebnis.get('task_id') or '-'}).")
    except Exception:  # noqa: BLE001
        return None
