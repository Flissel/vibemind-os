"""Prompts und Parser des Auftrags-Arbeiters (M8 Stufe A): Drehbuch fuer das Reel und die Beitragstexte danach.
Ohne I/O - der Arbeiter fragt Claude, dieses Modul baut die Fragen und liest die Antworten.
Die Grenzen entsprechen api/auftrag.py (Beitrag) und api/video.py (Drehbuch)."""
from __future__ import annotations

import json
import re

TITEL_MAX, THEMA_MAX, SPRECHERTEXT_MAX, HOOK_MAX = 120, 300, 900, 120
BEITRAG_TITEL_MAX, TEXT_LANG_MAX, TEXT_KURZ_MAX = 90, 1300, 280
DAUER_MIN, DAUER_MAX, DAUER_VORGABE = 15, 60, 30
WOERTER_JE_SEKUNDE = 2.5
_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _json(text: str) -> dict | None:
    """Das erste JSON-Objekt der Antwort (auch in einem ```-Block oder mit Text drumherum), sonst None."""
    if not isinstance(text, str):
        return None
    kandidaten = [text.strip(), *(m.strip() for m in _BLOCK.findall(text))]
    anfang, ende = text.find("{"), text.rfind("}")
    if 0 <= anfang < ende:
        kandidaten.append(text[anfang:ende + 1])
    for k in kandidaten:
        try:
            wert = json.loads(k)
        except ValueError:
            continue
        if isinstance(wert, dict):
            return wert
    return None


def _feld(daten: dict, name: str, maximal: int, kuerzen: bool = False) -> str | None:
    wert = daten.get(name)
    if not isinstance(wert, str) or not wert.strip():
        return None
    wert = wert.strip()
    if len(wert) > maximal:
        if not kuerzen:
            return None
        wert = wert[:maximal].rstrip()
    return wert


def _marke_abschnitt(marke: str) -> str:
    return f"\n\nWissen über die Marke:\n{marke.strip()}" if marke and marke.strip() else ""


def drehbuch_prompt(thema: str, marke: str) -> tuple[str, str]:
    """(system, nachricht) fuer das Drehbuch eines Kurzvideos (Reel) zum Thema."""
    woerter = int(DAUER_VORGABE * WOERTER_JE_SEKUNDE)
    system = (
        "Du schreibst Drehbücher für kurze Hochkant-Videos (Reels) auf Deutsch, für die Marke des Auftraggebers. "
        "Der Sprechertext wird von einer Stimme gesprochen und von KI-Bildern begleitet. "
        "Antworte ausschließlich mit einem JSON-Objekt, ohne Text davor oder danach, mit den Feldern: "
        f'"titel" (höchstens {TITEL_MAX} Zeichen), "sprechertext" (reiner Sprechtext ohne Regieanweisungen, '
        f"höchstens {SPRECHERTEXT_MAX} Zeichen), "
        f'"hook" (der erste Satz, der zum Weiterschauen zwingt, höchstens {HOOK_MAX} Zeichen) und '
        f'"dauer_s" (ganze Sekunden zwischen {DAUER_MIN} und {DAUER_MAX}). '
        f"Rechne mit etwa {WOERTER_JE_SEKUNDE} gesprochenen Wörtern je Sekunde: {DAUER_VORGABE} Sekunden "
        f"sind rund {woerter} Wörter, der Sprechertext muss zur gewählten Dauer passen. "
        "Beginne den Sprechertext mit dem Hook, nenne nur Belegbares aus dem Markenwissen und erfinde keine Zahlen."
        + _marke_abschnitt(marke))
    return system, f"Thema des Reels: {thema.strip()}"


def drehbuch_lesen(text: str) -> dict | None:
    """{titel, thema, sprechertext, hook, dauer_s} aus der Antwort; None, wenn kein brauchbares JSON.
    dauer_s wird auf 15..60 begrenzt (Vorgabe 30). Das Thema setzt der Aufrufer ein (hier leer)."""
    daten = _json(text)
    if daten is None:
        return None
    titel = _feld(daten, "titel", TITEL_MAX, kuerzen=True)
    sprechertext = _feld(daten, "sprechertext", SPRECHERTEXT_MAX)
    hook = _feld(daten, "hook", HOOK_MAX, kuerzen=True)
    if not titel or not sprechertext or not hook:
        return None
    dauer = daten.get("dauer_s", DAUER_VORGABE)
    if isinstance(dauer, bool) or not isinstance(dauer, (int, float)):
        dauer = DAUER_VORGABE
    dauer = max(DAUER_MIN, min(DAUER_MAX, int(dauer)))
    return {"titel": titel, "thema": "", "sprechertext": sprechertext, "hook": hook, "dauer_s": dauer}


def beitrag_prompt(thema: str, sprechertext: str, marke: str) -> tuple[str, str]:
    """(system, nachricht) fuer die Beitragstexte zum fertigen Reel."""
    system = (
        "Du schreibst Beitragstexte für Social-Media-Kanäle auf Deutsch, passend zu einem fertigen Reel der Marke. "
        "Antworte ausschließlich mit einem JSON-Objekt, ohne Text davor oder danach, mit den Feldern: "
        f'"titel" (höchstens {BEITRAG_TITEL_MAX} Zeichen), '
        f'"text_lang" (Beitrag für Kanäle mit viel Platz, höchstens {TEXT_LANG_MAX} Zeichen) und '
        f'"text_kurz" (kurze Fassung für Kanäle mit wenig Platz, höchstens {TEXT_KURZ_MAX} Zeichen). '
        "Die Texte machen neugierig auf das Video, sagen nichts, was nicht im Sprechertext oder im "
        "Markenwissen steht, und enthalten keine Links." + _marke_abschnitt(marke))
    nachricht = f"Thema: {thema.strip()}"
    if sprechertext and sprechertext.strip():     # nach einem Neustart des Arbeiters fehlt er: dann nur das Thema
        nachricht += f"\n\nSprechertext des Reels:\n{sprechertext.strip()}"
    return system, nachricht


def beitrag_lesen(text: str) -> dict | None:
    """{titel, text_lang, text_kurz}; ein fehlendes oder zu langes Feld -> None."""
    daten = _json(text)
    if daten is None:
        return None
    titel = _feld(daten, "titel", BEITRAG_TITEL_MAX)
    lang = _feld(daten, "text_lang", TEXT_LANG_MAX)
    kurz = _feld(daten, "text_kurz", TEXT_KURZ_MAX)
    if not titel or not lang or not kurz:
        return None
    return {"titel": titel, "text_lang": lang, "text_kurz": kurz}
