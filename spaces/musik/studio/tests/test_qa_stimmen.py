"""Plan 1c: die QA misst, ob im Uebergang zwei Stimmen gleichzeitig singen."""

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset.contracts import MixPlan, Stimmwechsel
from djset.qa import STIMME_AKTIV_DB, STIMMEN_ERLAUBT_S, check_set, stimmen_messen
from djset.render.engine import STIMM_HUELLE_S, StimmSpur, render
from tests.fixtures.render import simple_plan, sources_for

LAUT = -20.0
STILL = -120.0


def _plan() -> MixPlan:
    """simple_plan mit zwei Takten Uebergang und Wechsel nach einem."""
    plan = simple_plan(bars=2)
    takt = plan.transitions[0].overlap_s / 2
    return MixPlan.model_validate(
        plan.model_dump()
        | {
            "transitions": [
                t.model_dump()
                | {"stimmwechsel": Stimmwechsel(bei_s=takt, blende_s=takt).model_dump()}
                for t in plan.transitions
            ]
        }
    )


def _spur(track_id: str, start_s: float, dauer_s: float, laut_von: float,
          laut_bis: float) -> StimmSpur:
    """Eine Stimme, die zwischen laut_von und laut_bis (Setzeit) singt."""
    zeiten = start_s + np.arange(int(dauer_s / STIMM_HUELLE_S)) * STIMM_HUELLE_S
    pegel = np.where((zeiten >= laut_von) & (zeiten < laut_bis), LAUT, STILL)
    return StimmSpur(track_id=track_id, start_s=start_s, schritt_s=STIMM_HUELLE_S,
                     pegel_db=pegel)


def _spuren(plan: MixPlan, ueberlappung_s: float) -> list[StimmSpur]:
    """Drei Stimmen; im ersten Uebergang singt t1 schon `ueberlappung_s`
    bevor t0 aufhoert, im zweiten ist sauber gewechselt."""
    t0, t1 = plan.transitions
    w0 = t0.start_at_s + t0.stimmwechsel.bei_s
    w1 = t1.start_at_s + t1.stimmwechsel.bei_s
    return [
        _spur("t0", 0.0, 10.0, 0.0, w0),
        _spur("t1", t0.start_at_s, 10.0, w0 - ueberlappung_s, w1),
        _spur("t2", t1.start_at_s, 10.0, w1, 99.0),
    ]


def test_sauber_gewechselte_stimmen_ergeben_keinen_befund() -> None:
    plan = _plan()

    naehte, befunde = stimmen_messen(_spuren(plan, 0.0), plan, [])

    # Jede Huelle hat ihr eigenes 50-ms-Raster: an der Grenze kann ein
    # Schritt beider Stimmen laut sein.
    assert all(n.gleichzeitig_s <= STIMM_HUELLE_S + 1e-9 for n in naehte)
    assert naehte[0].wechsel_s == pytest.approx(
        plan.transitions[0].start_at_s + plan.transitions[0].stimmwechsel.bei_s
    )
    assert befunde == []


def test_zwei_stimmen_laenger_als_erlaubt_sind_ein_blockierender_befund() -> None:
    plan = _plan()

    naehte, befunde = stimmen_messen(_spuren(plan, 1.0), plan, [])

    assert naehte[0].gleichzeitig_s == pytest.approx(1.0, abs=2 * STIMM_HUELLE_S)
    assert naehte[1].gleichzeitig_s <= STIMM_HUELLE_S + 1e-9
    assert [b.kind for b in befunde] == ["stimmen"]
    assert "t0" in befunde[0].text and "t1" in befunde[0].text
    assert f"{naehte[0].gleichzeitig_s:.1f} s gleichzeitig" in befunde[0].text


def test_eine_kurze_ueberlagerung_bleibt_unter_der_grenze() -> None:
    plan = _plan()

    _, befunde = stimmen_messen(_spuren(plan, STIMMEN_ERLAUBT_S / 2), plan, [])

    assert befunde == []


def test_ungetrennte_songs_sind_ein_hinweis_und_ihre_naht_bleibt_ungemessen() -> None:
    plan = _plan()
    spuren = [s for s in _spuren(plan, 0.0) if s.track_id != "t1"]

    naehte, befunde = stimmen_messen(spuren, plan, ["t1"])

    assert [n.gleichzeitig_s for n in naehte] == [None, None]
    assert [b.kind for b in befunde] == ["stimmen_ungetrennt"]
    assert "t1" in befunde[0].text


def test_die_schwelle_trennt_stimme_von_uebersprechen() -> None:
    """Demucs laesst in der Stimmspur etwas Begleitung stehen. Das darf nicht
    als Gesang zaehlen."""
    plan = _plan()
    spuren = _spuren(plan, 2.0)
    leise = STIMME_AKTIV_DB - 5.0
    spuren[1] = StimmSpur(
        track_id="t1", start_s=spuren[1].start_s, schritt_s=STIMM_HUELLE_S,
        pegel_db=np.where(spuren[1].pegel_db == LAUT, leise, STILL),
    )

    naehte, befunde = stimmen_messen(spuren, plan, [])

    assert naehte[0].gleichzeitig_s == 0.0
    assert befunde == []


def test_check_set_traegt_die_stimmen_in_den_bericht(tmp_path: Path) -> None:
    plan = _plan()
    ziel = tmp_path / "set.wav"
    render(plan, sources_for(tmp_path), ziel)

    qa = check_set(ziel, plan, stimmspuren=_spuren(plan, 1.0), ungetrennt=[])

    assert len(qa.stimmen) == 2
    assert [f.kind for f in qa.blocking] == ["stimmen"]
