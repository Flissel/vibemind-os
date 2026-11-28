"""Fuer alle Tests des Coding-Space: nie eine echte Vorschau-Sandbox starten.

Am Rechner kann die Schalterdatei ``vorschau.an`` liegen. Ohne diese Sperre startete
ein Test mit gruener Abnahme die echte Sandbox auf seinen Temp-Ordner (Pruefung 28.11.:
die Live-Vorschau zeigte danach eine App, die zu keinem Lauf gehoerte).
Tests, die den Schalter pruefen, setzen ihn selbst.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _keine_echte_vorschau(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODING_VORSCHAU_AUTOSTART", "0")
    monkeypatch.setenv("CODING_VORSCHAU_BEIM_BAU", "0")
