import json
from datetime import date
from pathlib import Path

import pytest

from djset import cli
from djset.agents.base import Budget
from djset.bestand import Bestand, SongEintrag
from djset.cli import cmd_build, cmd_set
from djset.contracts import (
    AudioFormat,
    BpmRange,
    MixPlan,
    Profile,
    QaReport,
    TrackMeasurements,
)
from djset.run import Run
from djset.tempo import TempoWahl
from djset.watch import AudioFile
from tests.agents.test_profiler import DRAFT
from tests.fixtures.analyses import make_analysis
from tests.fixtures.fake_client import FakeClient, ZaehlenderClient
from tests.fixtures.make_audio import click_track, click_track_with_quiet_edges

HEUTE = date(2026, 11, 23)


def _budget() -> Budget:
    return Budget(max_tokens=400_000, max_seconds=300.0)


@pytest.fixture
def budget() -> Budget:
    return _budget()


@pytest.fixture
def zaehlender_client() -> ZaehlenderClient:
    return ZaehlenderClient([])


def _lege_song_ab(
    bestand: Bestand, song_id: str, bpm: float | None, dauer_s: float = 600.0
) -> SongEintrag:
    """Ein Song im Bestand, ohne den Weg ueber aufnehmen().

    Mit bpm wird eine erfundene Messung eingetragen (schnell, deterministisch),
    ohne bpm bleibt `messung` leer und die WAV ist ein echter Clicktrack, den
    cmd_set nachmessen kann.
    """
    wav = bestand.songs / f"{song_id}.wav"
    click_track(wav, bpm=bpm or 126.0, bars=16 if bpm is None else 1)
    messung = None
    if bpm is not None:
        messung = TrackMeasurements.model_validate(
            make_analysis(song_id, bpm=bpm, duration_s=dauer_s).model_dump(
                exclude={"verdict"}
            )
            | {"path": str(wav)}
        )
    eintrag = SongEintrag(
        id=song_id,
        titel=song_id,
        datei=f"{song_id}.wav",
        herkunft="suno-eigen",
        aufgenommen_am=HEUTE,
        pruefsumme=song_id.ljust(64, "0"),
        messung=messung,
    )
    (bestand.songs / f"{song_id}.json").write_text(
        eintrag.model_dump_json(indent=2), encoding="utf-8"
    )
    return eintrag


def _bestand(tmp_path: Path, tempi: dict[str, float], dauer_s: float = 600.0) -> Bestand:
    bestand = Bestand(tmp_path)
    for song_id, bpm in tempi.items():
        _lege_song_ab(bestand, song_id, bpm, dauer_s)
    return bestand


class _BuildRunAufzeichnung:
    def __init__(self) -> None:
        self.aufrufe: list[dict[str, object]] = []

    def __call__(
        self,
        run: Run,
        files: list[AudioFile],
        profile: Profile,
        client: object,
        budget: Budget,
    ) -> int:
        self.aufrufe.append(
            {"run": run, "files": files, "profile": profile, "client": client}
        )
        return 0


@pytest.fixture
def build_run_aufzeichnung(monkeypatch) -> _BuildRunAufzeichnung:
    aufzeichnung = _BuildRunAufzeichnung()
    monkeypatch.setattr(cli, "build_run", aufzeichnung)
    return aufzeichnung


def test_set_bricht_vor_dem_modell_ab_wenn_zu_wenige_passen(
    tmp_path, zaehlender_client, budget, capsys
):
    _bestand(tmp_path, {"a": 90.0, "b": 128.0, "c": 174.0})

    assert cmd_set(tmp_path, "Party", 20, None, zaehlender_client, budget, HEUTE) == 1

    assert zaehlender_client.aufrufe == 0
    err = capsys.readouterr().err
    assert "90" in err and "128" in err and "174" in err
    assert not (tmp_path / "runs").exists()


def test_set_bricht_ab_wenn_das_material_zu_kurz_ist(
    tmp_path, zaehlender_client, budget, capsys
):
    # Drei Songs zu je zwei Minuten: sechs Minuten Material, zwanzig gewuenscht.
    _bestand(tmp_path, {"a": 124.0, "b": 126.0, "c": 128.0}, dauer_s=120.0)

    assert cmd_set(tmp_path, "Party", 20, None, zaehlender_client, budget, HEUTE) == 1

    assert zaehlender_client.aufrufe == 0
    err = capsys.readouterr().err
    assert "6.0" in err
    assert "20" in err
    assert not (tmp_path / "runs").exists()


def test_set_ruft_build_run_nur_mit_songs_im_bereich(
    tmp_path, budget, build_run_aufzeichnung
):
    bestand = _bestand(
        tmp_path, {"a": 124.0, "b": 126.0, "c": 128.0, "d": 174.0, "e": 90.0}
    )
    client = ZaehlenderClient([json.dumps(DRAFT)])

    assert cmd_set(tmp_path, "Sommerfest", 20, None, client, budget, HEUTE) == 0

    assert client.aufrufe == 1
    [aufruf] = build_run_aufzeichnung.aufrufe
    files = aufruf["files"]
    assert isinstance(files, list)
    assert [f.id for f in files] == ["a", "b", "c"]
    assert [f.path for f in files] == [bestand.pfad(i) for i in ("a", "b", "c")]

    run = aufruf["run"]
    assert isinstance(run, Run)
    assert run.directory.parent == tmp_path / "runs"
    assert run.run_id.startswith("2026-11-23-sommerfest")
    profile = run.read_model("profile.json", Profile)
    assert profile == aufruf["profile"]
    assert profile.bpm == BpmRange(target=126.0, min=124.0, max=128.0)
    assert profile.genre == "Sommerfest"
    assert profile.track_count.needed == 3
    wahl = run.read_model("tempo.json", TempoWahl)
    assert wahl.drin == ["a", "b", "c"]
    assert wahl.draussen == {"d": 174.0, "e": 90.0}


def test_set_mit_songs_nimmt_nur_die_genannten(tmp_path, budget, build_run_aufzeichnung):
    _bestand(tmp_path, {"a": 124.0, "b": 126.0, "c": 128.0, "d": 125.0, "e": 127.0})
    client = ZaehlenderClient([json.dumps(DRAFT)])

    assert cmd_set(tmp_path, "Party", 20, ["e", "a", "c"], client, budget, HEUTE) == 0

    [aufruf] = build_run_aufzeichnung.aufrufe
    files = aufruf["files"]
    assert isinstance(files, list)
    assert sorted(f.id for f in files) == ["a", "c", "e"]


def test_set_mit_unbekannter_song_id(tmp_path, zaehlender_client, budget, capsys):
    _bestand(tmp_path, {"a": 124.0, "b": 126.0, "c": 128.0})

    code = cmd_set(
        tmp_path, "Party", 20, ["a", "gibtsnicht"], zaehlender_client, budget, HEUTE
    )

    assert code == 1
    assert zaehlender_client.aufrufe == 0
    assert "gibtsnicht" in capsys.readouterr().err


def test_set_mit_leerem_bestand(tmp_path, zaehlender_client, budget, capsys):
    code = cmd_set(tmp_path, "Party", 20, None, zaehlender_client, budget, HEUTE)

    assert code == 1
    assert zaehlender_client.aufrufe == 0
    err = capsys.readouterr().err
    assert err.startswith("Fehler:")
    assert "leer" in err


def test_set_legt_unter_einem_vertippten_pfad_nichts_an(
    tmp_path, zaehlender_client, budget, capsys
):
    falsch = tmp_path / "gibtsnicht"

    assert cmd_set(falsch, "Party", 20, None, zaehlender_client, budget, HEUTE) == 1

    assert not falsch.exists()
    assert str(falsch) in capsys.readouterr().err


@pytest.mark.parametrize("wunsch, laenge", [("", 20.0), ("   ", 20.0), ("Party", 0.0)])
def test_set_prueft_wunsch_und_laenge(
    tmp_path, zaehlender_client, budget, capsys, wunsch, laenge
):
    _bestand(tmp_path, {"a": 124.0, "b": 126.0, "c": 128.0})

    assert cmd_set(tmp_path, wunsch, laenge, None, zaehlender_client, budget, HEUTE) == 1

    assert zaehlender_client.aufrufe == 0
    assert capsys.readouterr().err.startswith("Fehler:")


def test_set_misst_fehlende_messungen_nach(tmp_path, budget, build_run_aufzeichnung):
    bestand = _bestand(tmp_path, {"a": 124.0, "b": 128.0})
    _lege_song_ab(bestand, "c", None)
    client = ZaehlenderClient([json.dumps(DRAFT)])

    assert cmd_set(tmp_path, "Party", 20, None, client, budget, HEUTE) == 0

    messung = bestand.hole("c").messung
    assert messung is not None
    [aufruf] = build_run_aufzeichnung.aufrufe
    files = aufruf["files"]
    assert isinstance(files, list)
    assert [f.id for f in files] == ["a", "b", "c"]


def test_main_set_nimmt_daten_aus_der_umgebung(tmp_path, monkeypatch):
    gesehen: dict[str, object] = {}

    def fake_set(daten, wunsch, length_minutes, songs, client, budget, today) -> int:
        gesehen.update(
            daten=daten, wunsch=wunsch, length=length_minutes, songs=songs, today=today
        )
        return 0

    monkeypatch.setattr(cli, "cmd_set", fake_set)
    monkeypatch.setenv("MUSIK_DATEN", str(tmp_path))

    code = cli.main(["set", "--wunsch", "Party", "--length", "20", "--songs", "a, b,"])

    assert code == 0
    assert gesehen["daten"] == tmp_path
    assert gesehen["wunsch"] == "Party"
    assert gesehen["length"] == 20.0
    assert gesehen["songs"] == ["a", "b"]
    assert isinstance(gesehen["today"], date)


def test_main_set_daten_schlaegt_die_umgebung(tmp_path, monkeypatch):
    gesehen: dict[str, object] = {}

    def fake_set(daten, wunsch, length_minutes, songs, client, budget, today) -> int:
        gesehen.update(daten=daten, songs=songs)
        return 0

    monkeypatch.setattr(cli, "cmd_set", fake_set)
    monkeypatch.setenv("MUSIK_DATEN", str(tmp_path / "anderswo"))

    assert cli.main(["set", "--daten", str(tmp_path), "--wunsch", "x", "--length", "5"]) == 0
    assert gesehen == {"daten": tmp_path, "songs": None}


def test_main_set_ohne_daten_ist_ein_fehler(monkeypatch, capsys):
    monkeypatch.delenv("MUSIK_DATEN", raising=False)
    monkeypatch.setattr(cli, "cmd_set", lambda *a, **k: pytest.fail("cmd_set gerufen"))

    assert cli.main(["set", "--wunsch", "Party", "--length", "20"]) == 1
    err = capsys.readouterr().err
    assert "--daten" in err and "MUSIK_DATEN" in err


def test_cmd_build_verhaelt_sich_wie_vorher(
    tmp_path, profile, build_run_aufzeichnung
):
    """cmd_build ist nur noch die Huelle: Lauf, Profil, watch-Ordner, Vorab-Check.
    Alles danach macht build_run - mit genau dem, was cmd_build gefunden hat.
    Das restliche Verhalten pinnt tests/test_cli_build.py, unveraendert."""
    run = Run.create(tmp_path, "liquid dnb", date(2026, 8, 21))
    small = Profile.model_validate(
        profile.model_dump() | {"track_count": {"needed": 3, "request": 3}}
    )
    run.write_model("profile.json", small)
    watch = tmp_path / "watch"
    watch.mkdir()
    for index in range(3):
        click_track(watch / f"t{index}.wav", bpm=174.0, bars=1)
    client = FakeClient([])

    assert cmd_build(tmp_path, run.run_id, client, _budget()) == 0

    [aufruf] = build_run_aufzeichnung.aufrufe
    assert aufruf["run"] == run
    assert aufruf["profile"] == small
    assert aufruf["client"] is client
    files = aufruf["files"]
    assert isinstance(files, list)
    assert [f.id for f in files] == ["t0", "t1", "t2"]
    assert client.calls == 0


def test_build_run_nennt_songs_ausserhalb_des_bereichs_im_bericht(
    tmp_path, profile, monkeypatch
):
    """Liegt tempo.json im Lauf, steht jeder ausgelassene Song mit seinem Tempo
    im QA-Bericht. Material und Antworten wie in
    test_cli_build::test_a_length_complaint_alone_does_not_buy_another_round;
    render und check_set sind ersetzt, geprueft wird nur die Verdrahtung."""
    run = Run.create(tmp_path, "party", HEUTE)
    flach = Profile.model_validate(
        profile.model_dump()
        | {
            "track_count": {"needed": 4, "request": 4},
            "energy_arc": [
                {"position": 0.0, "energy": 0.5},
                {"position": 1.0, "energy": 0.5},
            ],
        }
    )
    run.write_model("profile.json", flach)
    run.write_model(
        "tempo.json",
        TempoWahl(
            bereich=BpmRange(target=174.0, min=174.0, max=174.0),
            drin=["t0", "t1", "t2", "t3"],
            draussen={"langsam": 90.0, "zu-schnell": 200.5},
        ),
    )
    files = []
    for index in range(4):
        pfad = click_track_with_quiet_edges(
            tmp_path / f"t{index}.wav", bpm=174.0, bars=24, edge_bars=4
        )
        files.append(AudioFile(id=f"t{index}", path=pfad))

    def analyst(energy: float) -> str:
        return json.dumps(
            {"usable": True, "notes": "Passt.", "energy_level": energy,
             "remeasure_with_bpm": None}
        )

    client = FakeClient(
        [analyst(e) for e in (0.2, 0.4, 0.6, 0.8)]
        + [
            json.dumps({"accepted": ["t0", "t1", "t2", "t3"], "rejected": [], "note": "Gut."}),
            json.dumps({"order": ["t0", "t1", "t2", "t3"], "note": "Aufbau."}),
            json.dumps({"choices": [{"technique": "blend", "bars": 16} for _ in range(3)]}),
        ]
    )

    def fake_render(plan: MixPlan, sources, target: Path, master_gain_db=0.0, sr=None):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")

    def fake_check_set(path: Path, plan: MixPlan) -> QaReport:
        return QaReport(
            length_s=110.0,
            target_length_s=110.0,
            audio_format=AudioFormat(channels=2, sample_rate=44_100, subtype="FLOAT"),
            lufs_integrated=-14.0,
            true_peak=-2.0,
            findings=[],
        )

    monkeypatch.setattr(cli, "render", fake_render)
    monkeypatch.setattr(cli, "check_set", fake_check_set)

    assert cli.build_run(run, files, flach, client, _budget()) == 0

    bericht = run.path("qa-report.md").read_text(encoding="utf-8")
    abschnitt = bericht.split("## Nicht im Tempo-Bereich", 1)[1].split("\n## ", 1)[0]
    assert "`langsam`: 90.0 BPM" in abschnitt
    assert "`zu-schnell`: 200.5 BPM" in abschnitt
