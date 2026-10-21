"""Die Stimmen fuer Reels und Podcast (Reels Stufe 1, E2, 21.10.2026).

Eine Liste in der Env-Variable MARKETING_STIMMEN (Vorgabe: die drei Stimmproben in E:\\marketing-stimmen).
Ein eigenes kleines Modul, damit api/, claw/ und workers/ dieselbe Liste lesen, ohne api/ zu importieren.
"standard" steht nicht in der Liste und ist immer erlaubt.
"""
from __future__ import annotations

import os
import re

STANDARD = "standard"
VORGABE = "eigene-a,eigene-b,eigene-c"
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def stimmen_liste() -> list[str]:
    """Gueltige Namen aus MARKETING_STIMMEN, in Reihenfolge, ohne Doppelte.

    strip() je Eintrag: eine Env-Datei mit Windows-Zeilenenden haengt sonst ein '\\r' an den letzten Namen.
    Ungueltige Namen fallen still weg (der Name wird spaeter Dateiname einer Stimmprobe)."""
    roh = os.environ.get("MARKETING_STIMMEN")
    if roh is None:
        roh = VORGABE
    namen = (n.strip() for n in roh.split(","))
    return list(dict.fromkeys(n for n in namen if n and NAME.fullmatch(n)))
