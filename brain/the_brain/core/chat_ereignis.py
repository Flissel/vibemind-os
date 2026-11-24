"""Ereignis "Chat" statt starrer Absage (Entwurf chat-ereignis, freigegeben 24.11.2026).

Ein Satz, den das Gehirn keinem Weg zuordnen kann (``_ehrlich`` mit Weg "nicht-verstanden"
oder "mehrdeutig"), bekommt bei eingeschaltetem Schalter eine Antwort des Chat-Modells statt
der festen Absage. Das Modell bekommt nur den Satz, den Reiter und den Titel der offenen
Bubble; keine Werkzeuge, es schreibt nichts.

Fest bleiben: offene Rueckfragen (Loeschen, Befoerdern, White Paper ...), Hoeflichkeit,
Schreibwuensche (ein Modell bestaetigt nie eine Schreibhandlung, die nicht stattfand) und
Grenzsaetze (die kommen nie bis ``_ehrlich``). Fehler, Zeitueberschreitung oder eine
unbrauchbare Antwort: die heutige Absage.

Modell: Rolle ``brain_fast_reasoning`` (llm_config.yml, ``openfang:brain-fallback``), ein
bestehender Agent ohne Werkzeuge; keine neue Budgetzeile.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("brain.chat_ereignis")

SCHALTER = "CHAT_STATT_ABSAGE"
ZEITGRENZE_S = 20.0
WEGE = frozenset({"nicht-verstanden", "mehrdeutig"})
ROLLE = "brain_fast_reasoning"
MAX_TOKENS = 300
MAX_ZEICHEN = 1200

# Schreibwunsch: Verb am Satzanfang (auch nach "bitte"/"kannst du"). Solche Saetze behalten
# die feste Antwort, auch wenn kein Weg sie traf.
_SCHREIBWUNSCH = re.compile(
    r"^\s*(?:(?:bitte|kannst\s+du|könntest\s+du|koenntest\s+du|würdest\s+du|wuerdest\s+du)\s+)?"
    r"(?:lösch|loesch|entfern|leg(?:e)?\s|erstell|anleg|verschieb|benenn|umbenenn|verbind|trenn|"
    r"schreib|füg|fueg|befördr|befoerdr|beförder|befoerder|halt\w*\s+fest|merk\s+dir|änder|aender|"
    r"speicher|gruppier|bewert|setz|mach\s+(?:eine|einen|ein)\s)",
    re.IGNORECASE)

# Behauptet die Antwort eine Handlung, die nie stattfand? Dann gilt die feste Absage.
_HANDLUNG_BEHAUPTET = re.compile(
    r"\b(?:habe|hab|ist\s+jetzt|wurde|wurden)\b[^.!?]{0,60}\b(?:gelöscht|geloescht|angelegt|erstellt|"
    r"gespeichert|verschoben|geändert|geaendert|hinzugefügt|hinzugefuegt|befördert|befoerdert|"
    r"umbenannt|verbunden|getrennt|bewertet|gruppiert)\b",
    re.IGNORECASE)

_SYSTEM = (
    "Du bist der Chat des VibeMind-Gehirns. Der Satz des Nutzers passte zu keinem festen Weg. "
    "Antworte kurz auf Deutsch, in hoechstens drei Saetzen. Du kannst nichts ausfuehren und "
    "nichts aendern; behaupte nie, etwas getan zu haben. Wenn der Nutzer etwas aendern will, "
    "sag ihm, wie er es im Reiter formulieren kann. Keine Kennungen, kein Code."
)

_kontext: contextvars.ContextVar[Any] = contextvars.ContextVar("chat_ereignis_kontext", default=None)

Rufer = Callable[[List[Dict[str, str]]], Awaitable[str]]


def an() -> bool:
    """Schalter ``CHAT_STATT_ABSAGE``; Vorgabe aus."""
    return os.getenv(SCHALTER, "").strip().lower() in ("1", "true", "ja", "an", "on")


def kontext_setzen(kontext: Any) -> None:
    """``context`` des Satzes fuer diesen Aufruf merken (Titel der offenen Bubble). Wirft nie."""
    try:
        _kontext.set(kontext if isinstance(kontext, dict) else None)
    except Exception:  # noqa: BLE001
        pass


def bubble_titel(kontext: Any = None) -> Optional[str]:
    kontext = _kontext.get() if kontext is None else kontext
    bubble = kontext.get("bubble") if isinstance(kontext, dict) else None
    titel = bubble.get("title") if isinstance(bubble, dict) else None
    return titel.strip()[:120] if isinstance(titel, str) and titel.strip() else None


# Dasselbe mit dem Verb am Satzende ("Kannst du X loeschen?").
_SCHREIBWUNSCH_ENDE = re.compile(
    r"\b(?:löschen|loeschen|entfernen|anlegen|erstellen|verschieben|umbenennen|verbinden|trennen|"
    r"befördern|befoerdern|speichern|ändern|aendern|gruppieren|bewerten|hinzufügen|hinzufuegen|"
    r"festhalten|schreiben)\s*[?.!]*\s*$",
    re.IGNORECASE)


def schreibwunsch(satz: Any) -> bool:
    return isinstance(satz, str) and bool(_SCHREIBWUNSCH.match(satz) or _SCHREIBWUNSCH_ENDE.search(satz))


def fest_bleiben(satz: Any) -> bool:
    """Bleibt dieser Satz bei der festen Antwort? Im Zweifel ja. Wirft nie."""
    try:
        from . import hoeflichkeit

        if not isinstance(satz, str) or not satz.strip():
            return True
        if hoeflichkeit.art(satz) is not None or hoeflichkeit.etwas_offen():
            return True
        return schreibwunsch(satz)
    except Exception:  # noqa: BLE001
        return True


def nachrichten(satz: str, space: Optional[str], titel: Optional[str]) -> List[Dict[str, str]]:
    teile = [f"Reiter: {space or 'keiner'}"]
    if titel:
        teile.append(f"Offene Bubble: {titel}")
    teile.append(f"Satz: {satz}")
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": "\n".join(teile)}]


async def _openfang(nachr: List[Dict[str, str]]) -> str:
    """Ein Aufruf ueber die OpenFang-Rolle, ohne Werkzeuge."""
    from vibemind_shared import get_client, get_model

    modell = str(get_model(ROLLE))
    if not modell.startswith("openfang:"):
        raise RuntimeError("rolle_nicht_openfang")
    antwort = await get_client(ROLLE).chat.completions.create(
        model=modell, messages=nachr, max_tokens=MAX_TOKENS)
    wahl = getattr(antwort, "choices", None)
    return str(getattr(getattr(wahl[0], "message", None), "content", "") or "") if wahl else ""


def brauchbar(text: Any) -> Optional[str]:
    """Bereinigte Antwort oder ``None`` (leer, englisch, roh, behauptete Handlung)."""
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()[:MAX_ZEICHEN]
    try:
        from . import verstehen

        if verstehen.englisch(text) or verstehen.rohtext(text):
            return None
    except Exception:  # noqa: BLE001
        return None
    if _HANDLUNG_BEHAUPTET.search(text):
        return None
    return text


async def antwort(satz: str, weg: str, space: Optional[str], *, rufer: Optional[Rufer] = None,
                  zeitgrenze_s: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Chat-Antwort fuer einen nicht zuordenbaren Satz, oder ``None`` (dann die feste Absage).

    Wirft nie. ``rufer`` ist in Tests eine Attrappe; sonst OpenFang.
    """
    if not an() or weg not in WEGE or fest_bleiben(satz):
        return None
    try:
        nachr = nachrichten(satz, space, bubble_titel())
        text = await asyncio.wait_for((rufer or _openfang)(nachr), timeout=ZEITGRENZE_S if zeitgrenze_s is None else zeitgrenze_s)
    except asyncio.TimeoutError:
        logger.info("[chat-ereignis] Zeitgrenze, feste Absage")
        return None
    except Exception as e:  # noqa: BLE001
        logger.info(f"[chat-ereignis] Modell nicht erreichbar ({type(e).__name__}), feste Absage")
        return None
    text = brauchbar(text)
    if text is None:
        logger.info("[chat-ereignis] Antwort unbrauchbar, feste Absage")
        return None
    antwort_: Dict[str, Any] = {"ok": True, "weg": "chat", "ereignis": "chat", "executed": {},
                                "final_text": text}
    if space:
        antwort_["space"] = space
    return antwort_
