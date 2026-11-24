"""Ein Hauptschalter fuer Desktop-Aktionen (Vorgabe des Betreibers, 24.11.2026).

Der Betreiber gibt seinen eigenen Rechner frei: "Sachen, die auf unserem PC sind, von Agents
gesteuert werden, ist okay." Deshalb gilt als Vorgabe FREI:
- Desktop-Ereignisse mit ``requires_confirmation`` (Registry) laufen ohne Bestaetigung,
- Desktop-Aufgaben laufen ohne Herkunftsnachweis, egal ob der Satz getippt wurde oder ein
  Agent des Gehirns den Schritt plant.

``VIBEMIND_DESKTOP_RUECKFRAGE=1`` schaltet beides zurueck auf den strengen Stand: Ereignisse
mit Bestaetigungspflicht nur mit ``confirmed_events``, Desktop-Aufgaben nur aus einem Satz,
den die App als getippt belegt (``desktop_tagesrest.herkunft_pruefen``).

Was bleibt, auch wenn frei: der Vertrag je Schritt (``desktop_schleife_contract``), die
Sperrliste beim Sehen (``spaces/desktop/blick.py``), die Grenzen und der Not-Aus des
Aufgabenbuchs und der Trockenlauf-Schalter des Werkzeug-Dienstes.

Der Wert wird bei jedem Aufruf frisch aus der Umgebung gelesen.
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

RUECKFRAGE = "VIBEMIND_DESKTOP_RUECKFRAGE"
GETIPPT = "getippt"
FREI = "frei"
# Herkuenfte, mit denen eine Desktop-Aufgabe weiterlaufen darf.
ZUGELASSEN = (GETIPPT, FREI)
# Antwortkanaele von aussen: Saetze daher loesen nie eine Desktop-Aufgabe aus.
AUSSEN = ("telegram",)


def _u(umgebung: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if umgebung is None else umgebung


def rueckfrage_an(umgebung: Optional[Mapping[str, str]] = None) -> bool:
    """Nur ``1`` schaltet Bestaetigungspflicht und Herkunftspruefung wieder ein; sonst frei."""
    return (_u(umgebung).get(RUECKFRAGE) or "").strip() == "1"


def von_aussen(kanal: Any) -> bool:
    """Kam der Satz ueber einen Kanal von aussen (heute: Telegram)?

    Das Gehirn kennt nur, was der Aufrufer als ``antwortkanal.art`` angibt (/api/multihop/execute
    laesst api/telegram/dashboard zu). Das ist eine Angabe, kein Nachweis: Ein Aufrufer, der
    "api" sagt, wird nicht erkannt. Postfach, WhatsApp, Sales-/Marketing-Kanaele und Webhooks
    kommen beim Gehirn heute nicht unterscheidbar an.
    """
    art = kanal.get("art") if isinstance(kanal, Mapping) else None
    return isinstance(art, str) and art.strip().lower() in AUSSEN


def desktop_herkunft(eingabe_herkunft: Any, umgebung: Optional[Mapping[str, str]] = None, *,
                     kanal: Any = None) -> Optional[str]:
    """Die Herkunft, mit der eine Desktop-Aufgabe weiterlaeuft.

    None, wenn der Satz erklaertermassen von aussen kommt (immer, auch ohne Hauptschalter);
    "getippt", wenn die App den Satz belegt hat; sonst "frei" (Vorgabe) oder None, wenn der
    Hauptschalter die Pruefung wieder eingeschaltet hat.
    """
    if von_aussen(kanal):
        return None
    if eingabe_herkunft == GETIPPT:
        return GETIPPT
    return None if rueckfrage_an(umgebung) else FREI


def bestaetigung_noetig(requires_confirmation: bool, bestaetigt: bool, eingabe_herkunft: Any,
                        umgebung: Optional[Mapping[str, str]] = None, *, kanal: Any = None) -> bool:
    """Muss dieser Hop auf eine Bestaetigung warten? Mit Hauptschalter, oder wenn der Satz
    erklaertermassen von aussen kommt.

    ``eingabe_herkunft`` bleibt Teil der Unterschrift: Mit Hauptschalter gilt die alte Pflicht
    auch fuer getippte Saetze (nur ``confirmed_events`` gibt frei).
    """
    del eingabe_herkunft
    if not requires_confirmation or bestaetigt:
        return False
    return von_aussen(kanal) or rueckfrage_an(umgebung)
