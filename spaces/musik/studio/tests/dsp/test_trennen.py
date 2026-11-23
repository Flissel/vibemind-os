from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset.dsp.trennen import DemucsTrenner, trenner_fehlt


def test_trenner_fehlt_nennt_den_grund_ohne_demucs(monkeypatch) -> None:
    import djset.dsp.trennen as modul

    monkeypatch.setattr(modul, "find_spec", lambda name: None)
    grund = trenner_fehlt()
    assert grund is not None
    assert "demucs" in grund.lower()
    assert "pip install" in grund


@pytest.mark.skipif(trenner_fehlt() is not None, reason="Demucs nicht installiert")
def test_demucs_trennt_in_zwei_spuren_die_zusammen_den_song_ergeben(
    tmp_path: Path,
) -> None:
    """Der echte Trenner auf vier Sekunden Rauschen: zwei Dateien, gleiche
    Laenge und Rate wie die Quelle, und ihre Summe ist wieder die Quelle."""
    sr = 44_100
    rng = np.random.default_rng(7)
    quelle = tmp_path / "lied.wav"
    signal = (rng.standard_normal((4 * sr, 2)) * 0.1).astype(np.float32)
    sf.write(quelle, signal, sr, subtype="FLOAT")

    DemucsTrenner().trennen(quelle, tmp_path / "s.wav", tmp_path / "b.wav")

    stimme, sr_s = sf.read(tmp_path / "s.wav", dtype="float32", always_2d=True)
    begleitung, sr_b = sf.read(tmp_path / "b.wav", dtype="float32", always_2d=True)
    assert sr_s == sr_b == sr
    assert stimme.shape == begleitung.shape == signal.shape
    rest = signal - (stimme + begleitung)
    # Wie genau die Summe den Song trifft, ist Sache des Modells: auf diesem
    # Rauschen 17 dB darunter (Faktor 0,14), am echten Song 31 dB (0,03). Der
    # Test haelt fest, dass die Begleitung wirklich alles ausser der Stimme ist
    # - fehlte eine der drei Spuren, laege der Rest weit hoeher.
    assert np.sqrt(np.mean(rest**2)) < 0.25 * np.sqrt(np.mean(signal**2))
