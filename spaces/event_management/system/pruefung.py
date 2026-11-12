"""Zentrale Eingabepruefung der Fachschicht: Text, Zahlen, Metadaten-Typen (R2).

Die API hat mit `_sauber` eine eigene Wache; diese hier gilt fuer jeden Aufrufer der
Fachschicht (auch den spaeteren Werkzeug-Server). NUL-Zeichen und einzelne Surrogate
wuerden in psycopg zu einem Datenbankfehler (API: 500), NaN/Infinity zu ungueltigem
JSON. Meldungen nennen nur den Feldnamen, nie den Wert.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any


def zeichen_ok(wert: str) -> bool:
    """False bei NUL oder einzelnen Surrogaten (nicht als UTF-8 kodierbar)."""
    if "\x00" in wert:
        return False
    try:
        wert.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def text(feld: str, wert: Any) -> str:
    """Pflicht-Text: ein str ohne NUL und ohne einzelne Surrogate."""
    if not isinstance(wert, str):
        raise ValueError(f"Feld '{feld}' muss Text sein")
    if not zeichen_ok(wert):
        raise ValueError(f"Feld '{feld}' enthält ungültige Zeichen")
    return wert


def text_oder_none(feld: str, wert: Any) -> str | None:
    return None if wert is None else text(feld, wert)


def zahl(feld: str, wert: Any) -> int | float:
    """int oder endlicher float (kein bool, kein NaN, kein +-Infinity)."""
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        raise ValueError(f"Feld '{feld}' muss eine Zahl sein")
    if isinstance(wert, float) and not math.isfinite(wert):
        raise ValueError(f"Feld '{feld}' muss eine endliche Zahl sein")
    return wert


def metadatum_ok(typ: str, optionen: Sequence[str], wert: Any) -> bool:
    """Passt `wert` zum Metadaten-Feldtyp (R2)? Texte zusaetzlich ohne NUL/Surrogate,
    Zahlen endlich."""
    if isinstance(wert, str) and not zeichen_ok(wert):
        return False
    if typ in ("text", "langtext"):
        return isinstance(wert, str)
    if typ == "url":
        return isinstance(wert, str) and wert.startswith(("http://", "https://"))
    if typ == "zahl":
        return (isinstance(wert, (int, float)) and not isinstance(wert, bool)
                and (not isinstance(wert, float) or math.isfinite(wert)))
    if typ == "ja_nein":
        return isinstance(wert, bool)
    if typ in ("datum", "datumzeit"):
        if not isinstance(wert, str):
            return False
        try:
            (date if typ == "datum" else datetime).fromisoformat(wert)
        except ValueError:
            return False
        return True
    if typ == "auswahl":
        return isinstance(wert, str) and wert in optionen
    return False
