"""spaces.rowboat laedt trotz fehlender Altbausteine; die alten Namen melden weiter einen ImportError."""
from __future__ import annotations

import pytest

import spaces.rowboat as paket


def test_paket_laedt_und_alte_namen_bleiben_ein_importfehler():
    if paket._ALTLAST_FEHLER is None:
        pytest.skip("Altbausteine vorhanden")
    with pytest.raises(ImportError, match="get_roarboot_agent"):
        from spaces.rowboat import get_roarboot_agent  # noqa: F401
    with pytest.raises(AttributeError):
        paket.gibt_es_nicht  # noqa: B018
