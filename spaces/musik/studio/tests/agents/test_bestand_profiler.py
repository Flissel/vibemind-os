import json
from datetime import date

import pytest
from pydantic import ValidationError

from djset.agents.base import Budget
from djset.agents.bestand_profiler import (
    benoetigte_songs,
    build_profile_from_bestand,
    spielbare_laenge_s,
)
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


def test_needed_rechnet_der_code_nicht_das_modell(songs, wahl, budget):
    """Fix-Welle B2 (frueher test_ein_kleinerer_entwurf_bleibt_stehen: dort
    blieb der needed-Wert des Entwurfs stehen). Jetzt rechnet der Code:
    spielbare Laenge = Songlaenge minus ein mittlerer Uebergang (DRAFT: 16..32
    Takte, also 24 Takte bei 126 BPM = 45,7 s). Die kuerzesten passenden Songs
    (200, 240, 240, 260 s) ergeben 154 + 194 + 194 = 543 s nach drei und
    757 s nach vier Songs - fuer zehn Minuten braucht es vier. request ist
    die Zahl aller passenden Songs, damit der Kurator aus allen waehlen darf."""
    entwurf = json.loads(json.dumps(DRAFT))
    entwurf["track_count"] = {"needed": 3, "request": 5}
    many = songs + [_song("d", 125.0), _song("e", 127.0)]
    breite_wahl = waehle_tempo({s.id: s.messung.bpm.value for s in many if s.messung})
    assert len(breite_wahl.drin) == 5
    assert breite_wahl.bereich.target == 126.0

    profile = build_profile_from_bestand(
        FakeClient([json.dumps(entwurf)]), "Party", 10, many, breite_wahl, budget
    )

    assert profile.track_count.needed == 4
    assert profile.track_count.request == 5


def test_needed_hoechstens_alle_passenden_songs(songs, wahl, budget):
    """Reicht selbst alles Material nicht (die Vorpruefung in cmd_set rechnet
    mit 16 statt der 24 Takte des Entwurfs), wird es nicht mehr als alles."""
    profile = build_profile_from_bestand(
        FakeClient([json.dumps(DRAFT)]), "Party", 13, songs, wahl, budget
    )
    assert profile.track_count.needed == len(wahl.drin) == 3


def test_benoetigte_songs_zaehlt_die_kuerzesten():
    # 100 s Uebergang abgezogen: 100, 200, 300 spielbar.
    dauern = [400.0, 200.0, 300.0, 1000.0]
    assert benoetigte_songs(dauern, 250.0, 50, 120.0) == 3  # MIN_TRACKS
    assert benoetigte_songs(dauern, 700.0, 50, 120.0) == 4
    assert benoetigte_songs(dauern, 10_000.0, 50, 120.0) == 4
    assert spielbare_laenge_s(400.0, 50, 120.0) == 300.0


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
