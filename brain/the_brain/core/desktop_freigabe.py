"""Zwei Schalter fuer Desktop-Aufgaben, getrennt voneinander (Vorgabe des Betreibers, 24.11.2026).

1. Rueckfrage: Desktop-Ereignisse mit ``requires_confirmation`` (Registry) laufen aus der
   Eingabe des Betreibers OHNE Bestaetigung. ``VIBEMIND_DESKTOP_RUECKFRAGE=1`` holt die alte
   Pflicht zurueck (dann nur mit ``confirmed_events``).
2. Herkunft: Nur ein Satz, den der Betreiber selbst in der App getippt hat (Zeichen
   ``X-Vibemind-Eingabe``, siehe ``desktop_tagesrest.herkunft_pruefen``), darf den Desktop
   steuern. Das ist keine Rueckfrage; der Betreiber merkt davon nichts. Sie bleibt an, bis
   ``VIBEMIND_DESKTOP_HERKUNFT=aus`` gesetzt ist. Dann zaehlt jede Herkunft, auch Agenten,
   Postfaecher, Telegram und Skripte.

Beide Werte werden bei jedem Aufruf frisch aus der Umgebung gelesen.
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

RUECKFRAGE = "VIBEMIND_DESKTOP_RUECKFRAGE"
HERKUNFT = "VIBEMIND_DESKTOP_HERKUNFT"
GETIPPT = "getippt"


def _u(umgebung: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if umgebung is None else umgebung


def rueckfrage_an(umgebung: Optional[Mapping[str, str]] = None) -> bool:
    """Nur ``1`` schaltet die Bestaetigungspflicht wieder ein; alles andere heisst: keine Rueckfrage."""
    return (_u(umgebung).get(RUECKFRAGE) or "").strip() == "1"


def herkunft_pruefung_an(umgebung: Optional[Mapping[str, str]] = None) -> bool:
    """Nur genau ``aus`` schaltet die Herkunftspruefung ab. Fehlt der Wert oder ist er anders: an."""
    return (_u(umgebung).get(HERKUNFT) or "").strip().lower() != "aus"


def desktop_herkunft(eingabe_herkunft: Any, umgebung: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Die Herkunft, mit der eine Desktop-Aufgabe weiterlaeuft: "getippt" oder None."""
    if eingabe_herkunft == GETIPPT or not herkunft_pruefung_an(umgebung):
        return GETIPPT
    return None


def bestaetigung_noetig(requires_confirmation: bool, bestaetigt: bool, eingabe_herkunft: Any,
                        umgebung: Optional[Mapping[str, str]] = None) -> bool:
    """Muss dieser Hop auf eine Bestaetigung warten?

    Ohne Rueckfrage (Standard) laeuft ein Ereignis mit Bestaetigungspflicht, sobald die
    Herkunft passt. Eine fremde Herkunft bekommt dadurch nichts frei: Sie wird hier weiter
    angehalten, solange die Herkunftspruefung an ist.
    """
    if not requires_confirmation or bestaetigt:
        return False
    if rueckfrage_an(umgebung):
        return True
    return desktop_herkunft(eingabe_herkunft, umgebung) != GETIPPT
