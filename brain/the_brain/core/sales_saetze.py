"""Feste Lesesaetze des Sales-Space, ohne Planer und ohne Modell.

Vorlage: ``docs/operations/sales-space/SAETZE.md``. Ein Satz im Sales-Reiter wird zu genau einem
festen Aufruf an das Auftragsbuch (Werkzeug ``sales_<art>``, Ereignis ``sales.<art>``), zu einer
Rueckfrage oder zu ``None`` (dann entscheidet der bisherige Weg).

Nur lesende Arten. Was veraendern oder versenden wuerde (freigeben, schicken, loeschen, eintragen),
ist nie ein Treffer: freigegeben wird nur in der Sales-Oberflaeche, versendet nur von sales-claw.
Den Suchtext schreibt das Gehirn nie in Standzeile, Protokoll oder Antwortsatz.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

_FRAGE = r"\s*\??"
_HEUTE = r"heute(?:\s+noch)?"

# Reihenfolge ist Teil der Regel: die erste Art mit einem Treffer gilt.
SALES_SAETZE: Dict[str, tuple] = {
    "termine_heute": (
        r"(?:welche|was\s+f(?:ü|ue)r)\s+termine\s+(?:habe|hab|hätte|haette)\s+ich\s+" + _HEUTE + _FRAGE,
        r"wie\s+viele\s+termine\s+(?:habe|hab)\s+ich\s+" + _HEUTE + _FRAGE,
        r"(?:habe|hab)\s+ich\s+" + _HEUTE + r"\s+(?:einen\s+termin|termine)" + _FRAGE,
        r"was\s+steht\s+heute\s+(?:noch\s+)?(?:im|in\s+meinem)\s+kalender(?:\s+an)?" + _FRAGE,
        r"was\s+steht\s+heute\s+(?:noch\s+)?an" + _FRAGE,
        r"wie\s+voll\s+ist\s+(?:mein|der)\s+tag(?:\s+heute)?" + _FRAGE,
        r"(?:zeig(?:e)?\s+(?:mir\s+)?)?(?:meine\s+|die\s+)?termine\s+(?:f(?:ü|ue)r|von)\s+heute" + _FRAGE,
    ),
    "freigaben_zaehlen": (
        r"was\s+liegt\s+(?:in\s+den|bei\s+den)\s+freigaben" + _FRAGE,
        r"muss\s+ich\s+(?:noch\s+)?(?:was|etwas|irgendwas)\s+freigeben" + _FRAGE,
        r"wie\s+viele\s+entw(?:ü|ue)rfe\s+(?:warten(?:\s+auf\s+(?:meine\s+)?freigabe)?|sind\s+offen"
        r"|liegen\s+(?:in\s+den\s+freigaben|zur\s+freigabe))" + _FRAGE,
        r"(?:gibt\s+es|liegen)\s+(?:offene\s+|neue\s+)?freigaben" + _FRAGE,
        r"wartet\s+(?:was|etwas)\s+auf\s+(?:meine\s+)?freigabe" + _FRAGE,
    ),
    "post_offen": (
        r"wie\s+viele\s+(?:ungelesene|unbeantwortete|offene|neue)\s+(?:nachrichten|mails|chats)"
        r"(?:\s+(?:habe|hab)\s+ich)?" + _FRAGE,
        r"hat\s+mir\s+(?:heute\s+)?(?:jemand|wer)\s+geschrieben" + _FRAGE,
        r"ist\s+(?:noch\s+)?post\s+offen" + _FRAGE,
        r"(?:habe|hab)\s+ich\s+(?:neue\s+|ungelesene\s+|offene\s+)?(?:post|nachrichten|mails)" + _FRAGE,
        r"was\s+ist\s+(?:im\s+posteingang|in\s+der\s+post)\s+offen" + _FRAGE,
    ),
    "kontakt_finden": (
        r"zeig(?:e)?\s+(?:mir\s+)?den\s+kontakt\s+(?P<suche>.+?)",
        r"(?:such(?:e)?|finde?)\s+(?:mir\s+)?(?:den\s+)?kontakt\s+(?P<suche>.+?)",
        r"wen\s+haben\s+wir\s+(?:bei|von)\s+(?:der\s+|dem\s+|den\s+)?(?P<suche>.+?)" + _FRAGE,
        r"(?:gibt\s+es|haben\s+wir)\s+(?:einen\s+)?kontakt\s+(?:namens\s+|zu\s+|mit\s+dem\s+namen\s+)?"
        r"(?P<suche>.+?)" + _FRAGE,
    ),
}
_MUSTER = {art: tuple(re.compile(m, re.IGNORECASE) for m in muster) for art, muster in SALES_SAETZE.items()}

# Was veraendert, ist nie ein fester Lesesatz, auch wenn ein Musterwort vorkommt.
# Nur ganze Verben am Satzanfang: "Gibt es ..." bleibt ein Lesesatz, "Gib ... frei" nicht.
_VERAENDERND = re.compile(
    r"(?:gib|gebe|schick(?:e)?|send(?:e)?|versende?|schreib(?:e)?|l(?:ö|oe)sch(?:e)?|entfern(?:e)?|"
    r"trag(?:e)?|markier(?:e)?|erledig(?:e)?|antworte?|leg(?:e)?)\b", re.IGNORECASE)

SOFORT = {
    "termine_heute": "Ich schaue in den Kalender.",
    "freigaben_zaehlen": "Ich schaue in die Freigaben.",
    "post_offen": "Ich schaue in die Post.",
    "kontakt_finden": "Ich suche.",
}
SUCHE_RUECKFRAGE = "Nach welchem Namen soll ich suchen? Bitte mindestens zwei Buchstaben."
SUCHE_MAX = 100
_ANFUEHRUNG = "„“\"'‚‘’"


def ist_sales(kontext: Any) -> bool:
    """Gilt nur im Sales-Reiter: ``context.space == "sales"``."""
    if not isinstance(kontext, dict):
        return False
    space = kontext.get("space")
    return isinstance(space, str) and space.strip().lower() == "sales"


def _text(satz: str) -> str:
    return re.sub(r"\s+", " ", satz or "").strip().rstrip(".!")


def _suche(roh: str) -> Optional[str]:
    suche = roh.strip().strip(_ANFUEHRUNG).strip()
    if len(re.sub(r"\s", "", suche)) < 2 or len(suche) > SUCHE_MAX:
        return None
    return suche


def loese(satz: str, kontext: Any) -> Optional[Dict[str, Any]]:
    """``None``, ``{"rueckfrage": …}`` oder ``{"ereignis", "capability", "argumente", "antwort", "art"}``.

    ``argumente`` ist schon die Form des Werkzeugs: ``{"eingabe": {...}}``. Wirft nie.
    """
    try:
        if not ist_sales(kontext) or not isinstance(satz, str):
            return None
        text = _text(satz)
        if not text or _VERAENDERND.match(text):
            return None
        for art, muster in _MUSTER.items():
            for m in muster:
                treffer = m.fullmatch(text)
                if treffer is None:
                    continue
                eingabe: Dict[str, Any] = {}
                if "suche" in treffer.re.groupindex:
                    suche = _suche(treffer.group("suche"))
                    if suche is None:
                        return {"rueckfrage": SUCHE_RUECKFRAGE}
                    eingabe = {"suche": suche}
                ereignis = f"sales.{art}"
                return {"ereignis": ereignis, "capability": ereignis, "art": art,
                        "argumente": {"eingabe": eingabe}, "antwort": SOFORT[art]}
        return None
    except Exception:  # noqa: BLE001 - nie die Anfrage daran scheitern lassen
        return None


def ist_sales_faehigkeit(capability: Any) -> bool:
    """Eine Faehigkeit aus diesem Modul (``sales.<art>`` einer bekannten Art)."""
    return isinstance(capability, str) and capability.startswith("sales.") and capability[6:] in SALES_SAETZE
