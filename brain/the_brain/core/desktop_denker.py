"""Wer in der Desktop-Schleife denkt: ``VIBEMIND_DESKTOP_AGENT=hermes|brain`` (Vorgabe ``brain``).

Eigenes Modul, damit ``desktop_schleife`` selbst keine Umgebung liest (siehe Test
``test_die_schleife_setzt_den_schalter_fuer_echte_klicks_nirgends``).

- ``brain``: OpenFang-Agent ``brain-desktop`` (wie bisher).
- ``hermes``: OpenFang-Agent ``hermes-desktop``, der auf den Hermes-API-Dienst zeigt (Profil
  ``desktop``, ohne Werkzeuge; Vorlage ``spaces/desktop/hermes/``).

Nur der Denkende wechselt: die festen Aufrufe laufen weiter ueber ``brain-desktop`` mit Freigabe
je Aufruf, und jeder Vorschlag geht durch dieselbe Pruefung (``desktop_schleife_contract``).
Unbekannte oder leere Werte: ``brain``.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

from .desktop_schleife_contract import AGENT

HERMES_AGENT = "hermes-desktop"


def denkender_agent(env: Optional[Mapping[str, str]] = None) -> str:
    u = os.environ if env is None else env
    return HERMES_AGENT if str(u.get("VIBEMIND_DESKTOP_AGENT") or "").strip().lower() == "hermes" else AGENT
