"""tempo_abschnitte: das Tempo je Abschnitt, damit ein Song auffaellt, dessen
Beat-Erkennung zwischen zwei Ebenen springt.

Anlass (erster echter Lauf, 2026-11-23): "No Chicks, No Eggs (Variante 2)"
mass als ganzer Song 119.6 BPM, in 30-Sekunden-Abschnitten aber
[119, 88, 89, 119, 119, 119, 88]. Der Gesamtwert brachte ihn in einen
120er-Bereich, die QA fand an seiner Naht 114.7 statt 120 BPM.
"""

from pathlib import Path

import numpy as np
import soundfile as sf

from djset.dsp.analyze import ABSCHNITT_S, tempo_abschnitte
from tests.fixtures.make_audio import SR, click_track


def _zwei_tempi(tmp_path: Path, erst: float, dann: float, je_s: float) -> Path:
    """Erst je_s Sekunden Klicks in einem Tempo, dann je_s in einem anderen."""
    teile = []
    for nummer, bpm in enumerate((erst, dann)):
        bars = int(round(je_s / (4 * 60.0 / bpm)))
        teil = click_track(tmp_path / f"teil{nummer}.wav", bpm=bpm, bars=bars)
        signal, _sr = sf.read(teil, dtype="float32")
        teile.append(signal)
    ziel = tmp_path / "wechsel.wav"
    sf.write(ziel, np.concatenate(teile), SR)
    return ziel


def test_ein_gleichmaessiger_song_hat_in_jedem_abschnitt_dasselbe_tempo(tmp_path):
    wav = click_track(tmp_path / "gleich.wav", bpm=120.0, bars=50)  # 100 s

    abschnitte = tempo_abschnitte(wav)

    assert len(abschnitte) == 3
    assert all(abs(t - 120.0) < 1.5 for t in abschnitte)


def test_ein_tempowechsel_zeigt_sich_in_den_abschnitten(tmp_path):
    wav = _zwei_tempi(tmp_path, erst=88.0, dann=120.0, je_s=2 * ABSCHNITT_S)

    abschnitte = tempo_abschnitte(wav)

    assert any(abs(t - 88.0) < 2.0 for t in abschnitte)
    assert any(abs(t - 120.0) < 2.0 for t in abschnitte)


def test_ein_rest_unter_einem_halben_abschnitt_zaehlt_nicht(tmp_path):
    # 40 s: ein voller Abschnitt, der Rest von 10 s ist zu kurz fuer ein Tempo.
    wav = click_track(tmp_path / "kurz.wav", bpm=120.0, bars=20)

    assert len(tempo_abschnitte(wav)) == 1


def test_ein_song_kuerzer_als_ein_halber_abschnitt_hat_keine_abschnitte(tmp_path):
    wav = click_track(tmp_path / "jingle.wav", bpm=120.0, bars=4)  # 8 s

    assert tempo_abschnitte(wav) == []
