"""Fund 3: ein Eintrag wird abschnittsweise gestreckt.

Intro und Outro mit ihrem lokalen Tempo, der Koerper mit dem Gesamttempo.
MixEntry rechnet Quell- in Ausgabezeit um - an genau einer Stelle, damit
Designer, Critic und Renderer dieselbe Zeitleiste haben.
"""

import pytest
from pydantic import ValidationError

from djset.contracts import MixEntry, Streckabschnitt


def _eintrag(**extra) -> MixEntry:
    return MixEntry(
        track_id="t", play_from_s=10.0, play_to_s=100.0,
        timestretch_ratio=1.0, gain_db=0.0, **extra,
    )


def test_ohne_abschnitte_gilt_das_eine_verhaeltnis():
    eintrag = MixEntry(track_id="t", play_from_s=10.0, play_to_s=100.0,
                       timestretch_ratio=0.95, gain_db=0.0)

    assert eintrag.ausgabe_dauer_s == pytest.approx(90.0 / 0.95)
    assert eintrag.ausgabe_s(19.5) == pytest.approx(10.0)
    assert eintrag.quelle_s(10.0) == pytest.approx(19.5)


def test_abschnitte_rechnen_stueckweise():
    eintrag = _eintrag(abschnitte=[
        Streckabschnitt(bis_s=30.0, ratio=1.05),   # 20 s -> 19.0476 s
        Streckabschnitt(bis_s=80.0, ratio=1.0),    # 50 s -> 50 s
        Streckabschnitt(bis_s=100.0, ratio=0.95),  # 20 s -> 21.0526 s
    ])

    assert eintrag.ausgabe_s(30.0) == pytest.approx(20 / 1.05)
    assert eintrag.ausgabe_s(90.0) == pytest.approx(20 / 1.05 + 50 + 10 / 0.95)
    assert eintrag.ausgabe_dauer_s == pytest.approx(20 / 1.05 + 50 + 20 / 0.95)
    for quelle in (10.0, 25.0, 30.0, 55.5, 99.0):
        assert eintrag.quelle_s(eintrag.ausgabe_s(quelle)) == pytest.approx(quelle)
    assert eintrag.stuecke() == [(10.0, 30.0, 1.05), (30.0, 80.0, 1.0), (80.0, 100.0, 0.95)]


def test_der_letzte_abschnitt_muss_am_ende_des_eintrags_enden():
    with pytest.raises(ValidationError):
        _eintrag(abschnitte=[Streckabschnitt(bis_s=90.0, ratio=1.0)])


def test_abschnitte_muessen_aufsteigen_und_im_eintrag_liegen():
    with pytest.raises(ValidationError):
        _eintrag(abschnitte=[
            Streckabschnitt(bis_s=50.0, ratio=1.0),
            Streckabschnitt(bis_s=40.0, ratio=1.0),
            Streckabschnitt(bis_s=100.0, ratio=1.0),
        ])
    with pytest.raises(ValidationError):
        _eintrag(abschnitte=[
            Streckabschnitt(bis_s=5.0, ratio=1.0),
            Streckabschnitt(bis_s=100.0, ratio=1.0),
        ])


def test_ein_abschnitt_ausserhalb_der_stretch_grenze_ist_ungueltig():
    with pytest.raises(ValidationError):
        Streckabschnitt(bis_s=50.0, ratio=1.2)
