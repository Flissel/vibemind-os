"""Eingabe fuer ``rowboat.vorschlaege_anlegen``: aus Titeln werden die Punkte des Vertrags.

Nur Standardbibliothek, keine Kenntnis vom Gehirn. Gleiche Rueckgabe wie die festen
Ideen-Aufrufe (``feste_argumente``): Argumente oder eine Rueckfrage der Form
``clarification required: ...``; nie eine Ausnahme, nie ein erratenes Feld.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

PUNKTE_MAX = 50
TITEL_MAX = 200  # maxLength von `titel` im Vertrag rowboat/vorschlaege_anlegen

_NUMMER_ODER_STRICH = re.compile(r"^\s*(?:\d{1,3}\s*[.)]|[-*•])\s+")
# Ein Titel, der nur der Name der Liste ist ("To-do-Liste", "meine Liste."), ist nie ein Punkt: So sieht das
# letzte Wort des Satzes aus, das der Muster-Kurzweg als Argument weiterreicht.
_LISTENNAME = re.compile(r"^(?:(?:die|meine|unsere)\s+)?(?:to-?do|aufgaben|task)?[-\s]?(?:liste|list)$", re.I)
_SATZ = re.compile(
    r"^\s*(?:leg|lege|setz|setze|schreib|schreibe)\b(?P<inhalt>.+?)\s+(?:auf|in)\s+(?:die|meine|unsere)\s+"
    r"(?:(?:to-?do|aufgaben)[-\s]?)?liste\s*[.!?]*\s*$",
    re.I | re.S,
)
_FUELLWORT_ANFANG = re.compile(r"^\s*(?:mir|bitte)\b", re.I)
_RUECKFRAGE = "clarification required: "
_FORM = _RUECKFRAGE + "titles of the to-do items (1 to 50) as a JSON list, one per line, or separated by ;"


def _leerraum(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _kennung(titel: str) -> str:
    return hashlib.sha256(("vorschlag:" + _leerraum(titel).lower()).encode("utf-8")).hexdigest()[:16]


def aus_satz(satz: str) -> str | None:
    """Der Inhalt zwischen Ankerverb und "auf ... Liste" aus der Anfrage, sonst ``None``. Wirft nie.

    Ankerverben: leg(e), setz(e), schreib(e), mit "mir" und "bitte" dazwischen und Satzzeichen am Ende.
    """
    try:
        if not isinstance(satz, str):
            return None
        treffer = _SATZ.match(satz)
        if not treffer:
            return None
        inhalt = treffer.group("inhalt")
        while True:
            ohne = _FUELLWORT_ANFANG.sub("", inhalt, count=1)
            if ohne == inhalt:
                break
            inhalt = ohne
        inhalt = inhalt.strip()
        return inhalt or None
    except Exception:  # noqa: BLE001
        return None


def _ist_listenname(titel: str) -> bool:
    return _LISTENNAME.match(titel.rstrip(".!? ").strip()) is not None


def _titel_aus_text(text: str) -> list[str]:
    titel: list[str] = []
    for zeile in text.splitlines():
        zeile = _NUMMER_ODER_STRICH.sub("", zeile, count=1)
        titel.extend(teil for teil in zeile.split(";"))
    return titel


def _rohtitel(roh: Any) -> tuple[list[Any] | None, str | None]:
    """Liste der rohen Titel oder eine Rueckfrage."""
    if isinstance(roh, str):
        text = roh.strip()
        if text[:1] in ("[", "{"):
            try:
                roh = json.loads(text)
            except ValueError:
                return None, _FORM
        else:
            return _titel_aus_text(text), None
    if isinstance(roh, dict):
        if set(roh) != {"titel"} or not isinstance(roh["titel"], list):
            return None, _FORM
        roh = roh["titel"]
    if isinstance(roh, list):
        return roh, None
    return None, _FORM


def argumente(roh: str | list | dict, plan_id: str) -> tuple[dict | None, str | None]:
    """``({"eingabe": {"punkte": [...]}, "plan_id": plan_id}, None)`` oder ``(None, Rueckfrage)``."""
    try:
        titel_roh, frage = _rohtitel(roh)
        if titel_roh is None:
            return None, frage
        punkte: list[dict[str, Any]] = []
        gesehen: set[str] = set()
        for eintrag in titel_roh:
            if not isinstance(eintrag, str):
                return None, _FORM
            titel = _leerraum(eintrag)
            if not titel:
                continue
            if _ist_listenname(titel):
                return None, _RUECKFRAGE + "the item is only the name of the list; which items should go on it?"
            if len(titel) > TITEL_MAX:
                return None, _RUECKFRAGE + f"a title is longer than {TITEL_MAX} characters"
            kennung = _kennung(titel)
            if kennung in gesehen:
                continue
            gesehen.add(kennung)
            punkte.append({"punkt_id": kennung, "titel": titel, "frist": None, "link": ""})
        if not punkte:
            return None, _FORM
        if len(punkte) > PUNKTE_MAX:
            return None, _RUECKFRAGE + f"at most {PUNKTE_MAX} to-do items at once, got {len(punkte)}"
        return {"eingabe": {"punkte": punkte}, "plan_id": plan_id}, None
    except Exception:  # noqa: BLE001 - die Eingabe darf nie einen Plan abbrechen
        return None, _FORM
