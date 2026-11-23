import json
from datetime import date

import pytest
from pydantic import ValidationError

from djset.agents.base import Budget
from djset.agents.bestand_profiler import build_profile_from_bestand
from djset.bestand import SongEintrag
from djset.contracts import TrackMeasurements
from djset.tempo import TempoWahl, waehle_tempo
from tests.agents.test_profiler import DRAFT
from tests.fixtures.analyses import make_analysis
from tests.fixtures.fake_client import FakeClient


def _song(song_id: str, bpm: float, camelot: str = "8A", dauer_s: float = 240.0) -> SongEintrag:
    messung = TrackMeasurements.model_validate(
        make_analysis(song_id, bpm=bpm, camelot=camelot, duration_s=dauer_s).model_dump(
            exclude={"verdict"}
        )
    )
    return SongEintrag(
        id=song_id,
        titel=song_id.upper(),
        datei=f"{song_id}.wav",
        herkunft="suno-eigen",
        aufgenommen_am=date(2026, 11, 23),
        pruefsumme="0" * 64,
        messung=messung,
    )


@pytest.fixture
def songs() -> list[SongEintrag]:
    return [
        _song("a", 124.0, "8A", 200.0),
        _song("b", 126.0, "9A", 260.0),
        _song("c", 128.0, "10B", 305.0),
        _song("schnell", 174.0, "1A"),
    ]


@pytest.fixture
def wahl(songs: list[SongEintrag]) -> TempoWahl:
    assert songs[0].messung is not None
    return waehle_tempo({s.id: s.messung.bpm.value for s in songs if s.messung})


@pytest.fixture
def budget() -> Budget:
    return Budget(max_tokens=100_000, max_seconds=60.0)


@pytest.fixture
def fake_client() -> FakeClient:
    # DRAFT verlangt 12 Tracks und 174 BPM - beides muss der Code ueberschreiben.
    return FakeClient([json.dumps(DRAFT)])


def test_profil_traegt_den_gemessenen_bereich(fake_client, songs, wahl, budget):
    profile = build_profile_from_bestand(
        fake_client, "Sommerfest, erst ruhig", 20, songs, wahl, budget
    )
    assert profile.genre == "Sommerfest, erst ruhig"
    assert profile.bpm == wahl.bereich
    assert profile.track_count.needed <= len(wahl.drin)
    assert profile.track_count.request == profile.track_count.needed


def test_die_laenge_kommt_vom_aufrufer(fake_client, songs, wahl, budget):
    profile = build_profile_from_bestand(fake_client, "Party", 20, songs, wahl, budget)
    assert profile.length_minutes == 20


def test_ein_kleinerer_entwurf_bleibt_stehen(songs, wahl, budget):
    entwurf = json.loads(json.dumps(DRAFT))
    entwurf["track_count"] = {"needed": 3, "request": 5}
    many = songs + [_song("d", 125.0), _song("e", 127.0)]
    breite_wahl = waehle_tempo({s.id: s.messung.bpm.value for s in many if s.messung})
    assert len(breite_wahl.drin) == 5

    profile = build_profile_from_bestand(
        FakeClient([json.dumps(entwurf)]), "Party", 20, many, breite_wahl, budget
    )

    assert profile.track_count.needed == 3
    assert profile.track_count.request == 3


def test_der_prompt_traegt_wunsch_und_songtabelle(fake_client, songs, wahl, budget):
    build_profile_from_bestand(fake_client, "Sommerfest, erst ruhig", 20, songs, wahl, budget)
    prompt = fake_client.prompts[0]
    assert "Sommerfest, erst ruhig" in prompt
    for song_id, tempo, tonart, laenge in (
        ("a", "124.0", "8A", "3:20"),
        ("b", "126.0", "9A", "4:20"),
        ("c", "128.0", "10B", "5:05"),
    ):
        zeile = next(z for z in prompt.splitlines() if z.startswith(f"| {song_id} |"))
        assert tempo in zeile
        assert tonart in zeile
        assert laenge in zeile
    # Mittlere Energie der Fixture-Kurve [0.80, 0.84, 0.86, 0.85, 0.82].
    assert "0.83" in prompt
    # Was ausserhalb des Bereichs liegt, kommt nicht in die Tabelle.
    assert not any(z.startswith("| schnell |") for z in prompt.splitlines())


def test_zu_wenige_songs_im_bereich_ergeben_kein_profil(budget):
    zwei = [_song("a", 124.0), _song("b", 126.0)]
    wahl = waehle_tempo({"a": 124.0, "b": 126.0})
    with pytest.raises(ValidationError):
        build_profile_from_bestand(
            FakeClient([json.dumps(DRAFT)]), "Party", 20, zwei, wahl, budget
        )
