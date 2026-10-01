"""Tests für erzeugte Tech-Grafiken (Signal-Karte und Lichtschein)."""
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image

from spaces.marketing.claw import vorlagen_grafik as vg


def test_signal_und_glow_entstehen_einmal(tmp_path):
    """Signal und Glow werden erzeugt, Duplikate überspringen."""
    a = vg.signal("#B5F750", str(tmp_path))
    b = vg.signal("#b5f750", str(tmp_path))
    g = vg.glow("#b5f750", str(tmp_path))
    assert a == b == "medien:tech-signal-b5f750.png"
    assert g == "medien:tech-glow-b5f750.jpg"

    with Image.open(tmp_path / "tech-signal-b5f750.png") as bild:
        assert bild.size == (1072, 760)
        rgb_bild = bild.convert("RGB")
        # Kern liegt in der Mitte bei (536, 380); Funkel-Zeichen können darum liegen
        # Pixel 80 px oberhalb (536, 300) ist sicher im Kern, weg von allen Funkeln
        mitte = rgb_bild.getpixel((536, 300))
        # Akzent #b5f750 = RGB(181, 247, 80)
        assert mitte == (0xb5, 0xf7, 0x50) or sum(abs(x - y) for x, y in zip(mitte, (0xb5, 0xf7, 0x50))) < 30

    with Image.open(tmp_path / "tech-glow-b5f750.jpg") as bild:
        assert bild.size == (1200, 900)

    assert len(list(tmp_path.iterdir())) == 2


def test_ungueltig(tmp_path):
    """Ungültige Eingaben und fehlender Ordner geben None."""
    assert vg.signal("gruen", str(tmp_path)) is None
    assert vg.glow("#b5f750", "") is None


def test_speichern_fehler_bei_replace(tmp_path):
    """os.replace Fehler führt zu None und keine .tmp-Datei bleibt."""
    ordner = str(tmp_path)
    akzent = "#C2410C"
    name = f"tech-signal-{akzent[1:].lower()}.png"
    pfad = os.path.join(ordner, name)

    # Patch os.replace in vorlagen_grafik um OSError zu werfen
    with mock.patch("spaces.marketing.claw.vorlagen_grafik.os.replace", side_effect=OSError("Simulated disk error")):
        result = vg.signal(akzent, ordner)
        assert result is None

    # Keine .tmp-Datei sollte übrig sein
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0

    # Zieldatei sollte nicht existieren
    assert not os.path.exists(pfad)
