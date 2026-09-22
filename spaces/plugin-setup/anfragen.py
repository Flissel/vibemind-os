"""Die schwebende Anfrage: zwischen `eingabe_anfordern` und dem Absenden
des Formulars.

WARUM IM SPEICHER UND NICHT IN SUPABASE: eine schwebende Anfrage ist
Prozesszustand mit 15 Minuten Lebensdauer. Sie in die Datenbank zu legen
hiesse, einen Zustand zu persistieren, der einen Neustart nicht ueberleben
SOLL -- der Agent fordert dann einfach neu an.

WARUM DIESES MODUL KEINEN WERT KENNT: es gibt hier kein Feld, in dem ein
Geheimnis liegen koennte. Das ist eine strukturelle Zusicherung, kein
Vorsatz -- `test_anfrage_hat_strukturell_kein_wertfeld` haelt sie fest.
Der Wert existiert erst im POST des Formulars und verlaesst die
Verarbeitung nie wieder.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

GUELTIGKEIT_SEKUNDEN = 900  # 15 Minuten: lang genug fuer ein Anbieter-Portal,
                            # kurz genug, dass ein vergessener Link nicht
                            # wochenlang scharf bleibt.

_SPERRE = threading.Lock()
_OFFEN: dict[str, "Anfrage"] = {}


@dataclass(frozen=True)
class Anfrage:
    token: str
    projekt: str
    plugin: str
    referenz: str
    art: str
    ziel: str
    ablauf: float


def _abgelaufen(a: "Anfrage", jetzt: float) -> bool:
    return a.ablauf <= jetzt


def anlegen(projekt: str, plugin: str, referenz: str, art: str, ziel: str) -> Anfrage:
    a = Anfrage(
        token=secrets.token_urlsafe(32),
        projekt=projekt, plugin=plugin, referenz=referenz,
        art=art, ziel=ziel,
        ablauf=time.time() + GUELTIGKEIT_SEKUNDEN,
    )
    with _SPERRE:
        jetzt = time.time()
        for tot in [t for t, x in _OFFEN.items() if _abgelaufen(x, jetzt)]:
            del _OFFEN[tot]
        _OFFEN[a.token] = a
    return a


def holen(token: str) -> Anfrage | None:
    with _SPERRE:
        a = _OFFEN.get(token)
        if a is None:
            return None
        if _abgelaufen(a, time.time()):
            del _OFFEN[token]
            return None
        return a


def verbrauchen(token: str) -> Anfrage | None:
    with _SPERRE:
        a = _OFFEN.pop(token, None)
        if a is None or _abgelaufen(a, time.time()):
            return None
        return a


def offen_fuer(referenz: str) -> Anfrage | None:
    with _SPERRE:
        jetzt = time.time()
        for a in _OFFEN.values():
            if a.referenz == referenz and not _abgelaufen(a, jetzt):
                return a
        return None


def alle_offenen() -> list[Anfrage]:
    """Alle nicht abgelaufenen Anfragen, fruehste Ablaufzeit zuerst -- fuer
    die Listen-Seite (N7, s. fenster.listenseite): der Mensch oeffnet sie
    direkt, der Agent bekommt sie nie zu sehen. Raeumt wie `anlegen()`
    nebenbei abgelaufene Eintraege weg, statt sie nur zu ignorieren."""
    with _SPERRE:
        jetzt = time.time()
        for tot in [t for t, x in _OFFEN.items() if _abgelaufen(x, jetzt)]:
            del _OFFEN[tot]
        return sorted(_OFFEN.values(), key=lambda a: a.ablauf)
