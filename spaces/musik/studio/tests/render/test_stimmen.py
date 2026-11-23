"""Plan 1c: der Renderer legt Stimme und Begleitung getrennt.

Alle Quellen sind konstant, die EQ-Kurven flach: der gerenderte Wert an jeder
Stelle ist dann die Summe der Spuren, die dort klingen, mal ihrer Huelle. So
laesst sich hinschreiben, wer wo singt.
"""

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset.contracts import MixEntry, MixPlan, Stimmwechsel, Transition
from djset.render.engine import STIMM_HUELLE_S, render
from tests.fixtures.render import flat_curve

SR = 44_100
SEKUNDEN = 10.0
UEBERGANG = 4.0
WECHSEL = Stimmwechsel(bei_s=2.0, blende_s=0.5)
# Je Track Stimme und Begleitung mit eigenen Zahlen, damit jede Summe
# eindeutig sagt, wer klingt.
STIMME = {"t0": 0.1, "t1": 0.01, "t2": 0.001}
BEGLEITUNG = {"t0": 0.2, "t1": 0.02, "t2": 0.002}


def _konstant(path: Path, value: float) -> Path:
    sf.write(path, np.full(int(SEKUNDEN * SR), value, dtype=np.float32), SR,
             subtype="FLOAT")
    return path


def _plan(stimme: bool = True, wechsel: Stimmwechsel | None = WECHSEL) -> MixPlan:
    entries = [
        MixEntry(track_id=t, play_from_s=0.0, play_to_s=SEKUNDEN,
                 timestretch_ratio=1.0, gain_db=0.0, stimme=stimme)
        for t in STIMME
    ]
    starts = [SEKUNDEN - UEBERGANG, 2 * (SEKUNDEN - UEBERGANG)]
    transitions = [
        Transition(from_track=f"t{i}", to_track=f"t{i + 1}", start_at_s=starts[i],
                   overlap_s=UEBERGANG, bars=2, technique="blend",
                   eq_out=flat_curve(), eq_in=flat_curve(), stimmwechsel=wechsel)
        for i in range(2)
    ]
    return MixPlan(target_length_s=starts[-1] + SEKUNDEN, target_bpm=120.0,
                   entries=entries, transitions=transitions)


def _spuren(tmp_path: Path) -> tuple[dict[str, Path], dict[str, tuple[Path, Path]]]:
    songs = {t: _konstant(tmp_path / f"{t}.wav", STIMME[t] + BEGLEITUNG[t]) for t in STIMME}
    stimmen = {
        t: (_konstant(tmp_path / f"{t}.stimme.wav", STIMME[t]),
            _konstant(tmp_path / f"{t}.begleitung.wav", BEGLEITUNG[t]))
        for t in STIMME
    }
    return songs, stimmen


def _wert(path: Path, sekunde: float) -> float:
    signal, _ = sf.read(path, dtype="float32", always_2d=True)
    return float(signal[int(round(sekunde * SR)), 0])


def test_im_uebergang_singt_vor_dem_wechsel_nur_der_alte_und_danach_nur_der_neue(
    tmp_path: Path,
) -> None:
    songs, stimmen = _spuren(tmp_path)
    ziel = tmp_path / "set.wav"

    render(_plan(), songs, ziel, sr=SR, stimmen=stimmen)

    start = SEKUNDEN - UEBERGANG
    # Vor dem Uebergang: nur t0, ganz.
    assert _wert(ziel, 3.0) == pytest.approx(0.1 + 0.2, abs=1e-5)
    # Im Uebergang vor der Ausblende: Stimme t0, beide Begleitungen.
    assert _wert(ziel, start + 1.0) == pytest.approx(0.1 + 0.2 + 0.02, abs=1e-5)
    # Mitten in der Ausblende von t0: halbe Stimme.
    assert _wert(ziel, start + 1.75) == pytest.approx(0.05 + 0.2 + 0.02, abs=1e-4)
    # Mitten in der Einblende von t1: halbe Stimme von t1, keine von t0.
    assert _wert(ziel, start + 2.25) == pytest.approx(0.2 + 0.005 + 0.02, abs=1e-4)
    # Nach der Einblende: Stimme t1, beide Begleitungen.
    assert _wert(ziel, start + 3.0) == pytest.approx(0.2 + 0.01 + 0.02, abs=1e-5)
    # Nach dem Uebergang: nur t1, ganz.
    assert _wert(ziel, SEKUNDEN + 1.0) == pytest.approx(0.01 + 0.02, abs=1e-5)


def test_ohne_gesang_klingt_nur_die_begleitung(tmp_path: Path) -> None:
    songs, stimmen = _spuren(tmp_path)
    ziel = tmp_path / "set.wav"

    render(_plan(stimme=False), songs, ziel, sr=SR, stimmen=stimmen)

    assert _wert(ziel, 3.0) == pytest.approx(0.2, abs=1e-6)
    assert _wert(ziel, SEKUNDEN - UEBERGANG + 1.0) == pytest.approx(0.22, abs=1e-6)
    assert _wert(ziel, SEKUNDEN + 1.0) == pytest.approx(0.02, abs=1e-6)


def test_ohne_stimmwechsel_klingen_beide_stimmen_wie_im_song(tmp_path: Path) -> None:
    """Ein Plan von vor Plan 1c: getrennt gelegt, aber nichts geblendet - das
    Ergebnis ist dasselbe wie mit dem ganzen Song."""
    songs, stimmen = _spuren(tmp_path)
    getrennt, ganz = tmp_path / "getrennt.wav", tmp_path / "ganz.wav"

    render(_plan(wechsel=None), songs, getrennt, sr=SR, stimmen=stimmen)
    render(_plan(wechsel=None), songs, ganz, sr=SR)

    a, _ = sf.read(getrennt, dtype="float32")
    b, _ = sf.read(ganz, dtype="float32")
    assert np.abs(a - b).max() < 1e-6


def test_ein_song_ohne_getrennte_stimme_klingt_wie_bisher(tmp_path: Path) -> None:
    songs, stimmen = _spuren(tmp_path)
    del stimmen["t1"]
    ziel = tmp_path / "set.wav"

    spuren = render(_plan(), songs, ziel, sr=SR, stimmen=stimmen)

    # t1 ganz, auch vor dem Wechsel; t0 blendet seine Stimme trotzdem aus.
    start = SEKUNDEN - UEBERGANG
    assert _wert(ziel, start + 1.0) == pytest.approx(0.1 + 0.2 + 0.03, abs=1e-5)
    assert _wert(ziel, start + 3.0) == pytest.approx(0.2 + 0.03, abs=1e-5)
    assert [s.track_id for s in spuren] == ["t0", "t2"]


def test_der_renderer_gibt_die_huelle_jeder_gelegten_stimme_zurueck(
    tmp_path: Path,
) -> None:
    songs, stimmen = _spuren(tmp_path)

    spuren = render(_plan(), songs, tmp_path / "set.wav", sr=SR, stimmen=stimmen)

    t0, t1 = spuren[0], spuren[1]
    assert (t0.start_s, t1.start_s) == (0.0, SEKUNDEN - UEBERGANG)
    assert t0.schritt_s == STIMM_HUELLE_S

    def pegel(spur, sekunde: float) -> float:
        return float(spur.pegel_db[int((sekunde - spur.start_s) / spur.schritt_s)])

    start = SEKUNDEN - UEBERGANG
    assert pegel(t0, 3.0) == pytest.approx(20 * np.log10(0.1), abs=0.1)
    assert pegel(t0, start + 3.0) < -100
    assert pegel(t1, start + 1.0) < -100
    assert pegel(t1, start + 3.0) == pytest.approx(20 * np.log10(0.01), abs=0.1)
