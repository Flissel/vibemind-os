import json
import math
from datetime import date
from pathlib import Path

import pytest

from djset import cli
from djset.agents import analyst
from djset.agents.base import Budget
from djset.bestand import Bestand, SongEintrag
from djset.cli import cmd_build, cmd_set
from djset.contracts import (
    AudioFormat,
    BpmRange,
    MixPlan,
    Profile,
    QaFinding,
    QaReport,
    TrackMeasurements,
)
from djset.render.stretch import RenderFailed, RenderToolMissing
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
        messungen: dict[str, TrackMeasurements] | None = None,
    ) -> int:
        self.aufrufe.append(
            {
                "run": run,
                "files": files,
                "profile": profile,
                "client": client,
                "messungen": messungen,
            }
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

    # Fix-Welle B2: die Vorpruefung zieht je Song 16 Takte Uebergang ab - aus
    # 2 x 600 s plus dem kurzen Clicktrack werden rund 19 spielbare Minuten,
    # deshalb 15 statt 20 gewuenschte Minuten.
    assert cmd_set(tmp_path, "Party", 15, None, client, budget, HEUTE) == 0

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


def _lauf_mit_ersetztem_renderer(tmp_path, profile, monkeypatch):
    """Material und Antworten wie in
    test_cli_build::test_a_length_complaint_alone_does_not_buy_another_round;
    render und check_set sind ersetzt, to_mp3 bleibt Sache des Aufrufers.
    Gibt (run, files, profil, client) zurueck."""
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

    def fake_check_set(
        path: Path, plan: MixPlan, wunsch_laenge_s: float | None = None
    ) -> QaReport:
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
    return run, files, flach, client


def test_build_run_nennt_songs_ausserhalb_des_bereichs_im_bericht(
    tmp_path, profile, monkeypatch
):
    """Liegt tempo.json im Lauf, steht jeder ausgelassene Song mit seinem Tempo
    im QA-Bericht."""
    run, files, flach, client = _lauf_mit_ersetztem_renderer(
        tmp_path, profile, monkeypatch
    )
    monkeypatch.setattr(cli, "to_mp3", lambda quelle, ziel: ziel.write_bytes(b""))

    assert cli.build_run(run, files, flach, client, _budget()) == 0

    bericht = run.path("qa-report.md").read_text(encoding="utf-8")
    abschnitt = bericht.split("## Nicht im Tempo-Bereich", 1)[1].split("\n## ", 1)[0]
    assert "`langsam`: 90.0 BPM" in abschnitt
    assert "`zu-schnell`: 200.5 BPM" in abschnitt


def test_build_run_schreibt_set_mp3_und_nennt_sie(
    tmp_path, profile, monkeypatch, capsys
):
    run, files, flach, client = _lauf_mit_ersetztem_renderer(
        tmp_path, profile, monkeypatch
    )
    aufrufe: list[tuple[Path, Path]] = []

    def fake_to_mp3(quelle: Path, ziel: Path) -> None:
        # Der MP3-Schritt laeuft erst, wenn set.wav schon die Fertig-Marke ist.
        assert quelle == run.path("set.wav") and quelle.exists()
        aufrufe.append((quelle, ziel))
        ziel.write_bytes(b"mp3")

    monkeypatch.setattr(cli, "to_mp3", fake_to_mp3)

    assert cli.build_run(run, files, flach, client, _budget()) == 0

    assert aufrufe == [(run.path("set.wav"), run.path("set.mp3"))]
    assert f"  MP3: {run.path('set.mp3')}" in capsys.readouterr().out


@pytest.mark.parametrize(
    "fehler", [RenderFailed("ffmpeg kaputt"), RenderToolMissing("kein ffmpeg")]
)
def test_build_run_bleibt_fertig_wenn_die_mp3_scheitert(
    tmp_path, profile, monkeypatch, capsys, fehler
):
    run, files, flach, client = _lauf_mit_ersetztem_renderer(
        tmp_path, profile, monkeypatch
    )

    def kaputt(quelle: Path, ziel: Path) -> None:
        raise fehler

    monkeypatch.setattr(cli, "to_mp3", kaputt)

    assert cli.build_run(run, files, flach, client, _budget()) == 0

    assert run.has("set.wav")
    assert run.has("qa-report.md")
    assert not run.has("set.mp3")
    ausgabe = capsys.readouterr().out
    assert f"  MP3: nicht geschrieben - {fehler}" in ausgabe
    assert f"  MP3: {run.path('set.mp3')}" not in ausgabe


def test_build_run_schreibt_den_abschnitt_tonart_brueche(
    tmp_path, profile, monkeypatch
):
    """A1: der Bericht hat einen eigenen Abschnitt fuer Tonart-Brueche - die
    vier Clicktracks haben dieselbe Tonart, also steht dort "Keine"."""
    run, files, flach, client = _lauf_mit_ersetztem_renderer(
        tmp_path, profile, monkeypatch
    )
    monkeypatch.setattr(cli, "to_mp3", lambda quelle, ziel: ziel.write_bytes(b""))

    cli.build_run(run, files, flach, client, _budget())

    bericht = run.path("qa-report.md").read_text(encoding="utf-8")
    abschnitt = bericht.split("## Tonart-Brueche", 1)[1].split("\n## ", 1)[0]
    assert "Keine" in abschnitt


def test_build_run_nennt_zu_aehnliche_nachbarn_im_bericht(
    tmp_path, profile, monkeypatch
):
    """Nachzug A1: vier Clicktracks gleicher Tonart und gleichen Tempos, alle
    mit Energie 0.5 - jede Reihenfolge hat drei zu aehnliche Nachbarn. Der
    Lauf geht durch, und der Bericht nennt jedes Paar."""
    run, files, flach, _client = _lauf_mit_ersetztem_renderer(
        tmp_path, profile, monkeypatch
    )
    analyst = json.dumps(
        {"usable": True, "notes": "Passt.", "energy_level": 0.5,
         "remeasure_with_bpm": None}
    )
    client = FakeClient(
        [analyst] * 4
        + [
            json.dumps({"accepted": ["t0", "t1", "t2", "t3"], "rejected": [], "note": "Gut."}),
            json.dumps({"order": ["t0", "t1", "t2", "t3"], "note": "Aufbau."}),
            json.dumps({"choices": [{"technique": "blend", "bars": 16} for _ in range(3)]}),
        ]
    )
    monkeypatch.setattr(cli, "to_mp3", lambda quelle, ziel: ziel.write_bytes(b""))

    assert cli.build_run(run, files, flach, client, _budget()) == 0

    bericht = run.path("qa-report.md").read_text(encoding="utf-8")
    abschnitt = bericht.split("## Zu aehnliche Nachbarn", 1)[1].split("\n## ", 1)[0]
    for von, nach in (("t0", "t1"), ("t1", "t2"), ("t2", "t3")):
        assert f"`{von}` nach `{nach}`" in abschnitt


def _qa_mit(befunde: list[tuple[str, str]]):
    def fake_check_set(path: Path, plan: MixPlan, *args, **kwargs) -> QaReport:
        return QaReport(
            length_s=110.0,
            target_length_s=110.0,
            audio_format=AudioFormat(channels=2, sample_rate=44_100, subtype="FLOAT"),
            lufs_integrated=-14.0,
            true_peak=-2.0,
            findings=[QaFinding(kind=kind, text=text) for kind, text in befunde],
        )

    return fake_check_set


def test_ein_roter_qa_bericht_endet_mit_2_und_nennt_die_befunde(
    tmp_path, profile, monkeypatch, capsys
):
    """A3: vorher sah ein Lauf mit blockierendem Befund auf der Konsole fertig
    aus (exit 0, kein Wort zur QA)."""
    run, files, flach, client = _lauf_mit_ersetztem_renderer(
        tmp_path, profile, monkeypatch
    )
    monkeypatch.setattr(
        cli,
        "check_set",
        _qa_mit([("gap", "Bei 1:23 steht Stille."), ("length", "Etwas kurz.")]),
    )
    monkeypatch.setattr(cli, "to_mp3", lambda quelle, ziel: ziel.write_bytes(b""))

    assert cli.build_run(run, files, flach, client, _budget()) == 2

    ausgabe = capsys.readouterr().out
    assert "  QA: Befunde vorhanden" in ausgabe
    assert "Bei 1:23 steht Stille." in ausgabe
    zeilen = ausgabe.splitlines()
    befund = next(z for z in zeilen if "Bei 1:23" in z)
    hinweis = next(z for z in zeilen if "Etwas kurz." in z)
    assert befund != hinweis
    assert "gap" in befund and "Hinweis" in hinweis


def test_nur_hinweise_bleiben_exit_0_und_stehen_in_der_ausgabe(
    tmp_path, profile, monkeypatch, capsys
):
    run, files, flach, client = _lauf_mit_ersetztem_renderer(
        tmp_path, profile, monkeypatch
    )
    monkeypatch.setattr(cli, "check_set", _qa_mit([("length", "Etwas kurz.")]))
    monkeypatch.setattr(cli, "to_mp3", lambda quelle, ziel: ziel.write_bytes(b""))

    assert cli.build_run(run, files, flach, client, _budget()) == 0

    ausgabe = capsys.readouterr().out
    assert "  QA: Ausgeliefert, mit Hinweisen" in ausgabe
    assert "Etwas kurz." in ausgabe


def test_main_reicht_den_rueckgabewert_durch(monkeypatch):
    monkeypatch.setattr(cli, "cmd_build", lambda *a, **k: 2)
    assert cli.main(["build", "--run", "x"]) == 2


def test_set_reicht_die_gespeicherten_messungen_an_build_run(
    tmp_path, budget, build_run_aufzeichnung
):
    """B1: der Lauf misst nicht neu - das Tempo kann nicht von tempo.json
    abweichen."""
    bestand = _bestand(tmp_path, {"a": 124.0, "b": 126.0, "c": 128.0, "d": 174.0})
    client = ZaehlenderClient([json.dumps(DRAFT)])

    assert cmd_set(tmp_path, "Party", 20, None, client, budget, HEUTE) == 0

    [aufruf] = build_run_aufzeichnung.aufrufe
    messungen = aufruf["messungen"]
    assert isinstance(messungen, dict)
    assert sorted(messungen) == ["a", "b", "c"]
    for song_id, messung in messungen.items():
        assert messung == bestand.hole(song_id).messung


def test_build_run_misst_mit_mitgegebenen_messungen_nicht_neu(
    tmp_path, profile, monkeypatch
):
    run, files, flach, client = _lauf_mit_ersetztem_renderer(
        tmp_path, profile, monkeypatch
    )
    monkeypatch.setattr(cli, "to_mp3", lambda quelle, ziel: ziel.write_bytes(b""))
    monkeypatch.setattr(
        analyst, "measure_track", lambda *a, **k: pytest.fail("neu gemessen")
    )
    messungen = {
        f.id: TrackMeasurements.model_validate(
            make_analysis(f.id, bpm=174.0).model_dump(exclude={"verdict"})
            | {"path": str(f.path)}
        )
        for f in files
    }

    assert cli.build_run(run, files, flach, client, _budget(), messungen=messungen) == 0

    for f in files:
        artefakt = json.loads(run.path(f"tracks/{f.id}.json").read_text(encoding="utf-8"))
        assert artefakt["bpm"] == messungen[f.id].bpm.model_dump()


def test_set_rechnet_die_laengen_vorpruefung_mit_spielbarer_laenge(
    tmp_path, zaehlender_client, budget, capsys
):
    """B2: drei Songs zu je sieben Minuten sind roh 21 Minuten - nach Abzug
    von 16 Takten je Naht (bei 126 BPM 30,5 s) bleiben 19,5 Minuten, zu wenig
    fuer 20."""
    _bestand(tmp_path, {"a": 124.0, "b": 126.0, "c": 128.0}, dauer_s=420.0)

    assert cmd_set(tmp_path, "Party", 20, None, zaehlender_client, budget, HEUTE) == 1

    assert zaehlender_client.aufrufe == 0
    err = capsys.readouterr().err
    assert "19.5" in err
    assert not (tmp_path / "runs").exists()


def test_zu_wenige_brauchbare_songs_nennen_den_bestand_nicht_suno(
    tmp_path, profile, monkeypatch, capsys
):
    run, files, flach, _client = _lauf_mit_ersetztem_renderer(
        tmp_path, profile, monkeypatch
    )

    def analyst(energy: float) -> str:
        return json.dumps(
            {"usable": True, "notes": "Passt.", "energy_level": energy,
             "remeasure_with_bpm": None}
        )

    client = FakeClient(
        [analyst(e) for e in (0.2, 0.4, 0.6, 0.8)]
        + [
            json.dumps(
                {
                    "accepted": ["t0", "t1"],
                    "rejected": [
                        {"track_id": "t2", "reason": "Zu ruhig."},
                        {"track_id": "t3", "reason": "Zu laut."},
                    ],
                    "note": "Nur zwei.",
                }
            )
        ]
    )

    assert cli.build_run(run, files, flach, client, _budget()) == 1

    err = capsys.readouterr().err
    assert "Bestand" in err
    assert "Suno" not in err and "watch" not in err


def test_build_run_liest_tempo_json_vor_jedem_modellaufruf(
    tmp_path, profile, monkeypatch
):
    """B8: ein kaputtes tempo.json fiel bisher erst nach dem Rendern auf -
    nachdem jedes Token bezahlt war."""
    run, files, flach, client = _lauf_mit_ersetztem_renderer(
        tmp_path, profile, monkeypatch
    )
    run.write_text("tempo.json", "{kaputt")

    with pytest.raises(ValueError):
        cli.build_run(run, files, flach, client, _budget())

    assert client.calls == 0


def test_build_run_gibt_der_qa_die_gewuenschte_laenge(tmp_path, profile, monkeypatch):
    """B3: die QA vergleicht mit profile.length_minutes, nicht mit dem Plan."""
    run, files, flach, client = _lauf_mit_ersetztem_renderer(
        tmp_path, profile, monkeypatch
    )
    gesehen: list[float | None] = []
    fake = _qa_mit([])

    def merkt_sich(path: Path, plan: MixPlan, wunsch_laenge_s=None) -> QaReport:
        gesehen.append(wunsch_laenge_s)
        return fake(path, plan)

    monkeypatch.setattr(cli, "check_set", merkt_sich)
    monkeypatch.setattr(cli, "to_mp3", lambda quelle, ziel: ziel.write_bytes(b""))

    cli.build_run(run, files, flach, client, _budget())

    assert gesehen == [flach.length_minutes * 60.0]


@pytest.mark.parametrize("laenge", [math.nan, math.inf])
def test_set_lehnt_nan_und_inf_ab(tmp_path, zaehlender_client, budget, capsys, laenge):
    """B9."""
    _bestand(tmp_path, {"a": 124.0, "b": 126.0, "c": 128.0})

    assert cmd_set(tmp_path, "Party", laenge, None, zaehlender_client, budget, HEUTE) == 1

    assert zaehlender_client.aufrufe == 0
    assert capsys.readouterr().err.startswith("Fehler:")
    assert not (tmp_path / "runs").exists()


def test_set_kappt_den_ordnernamen_auf_40_zeichen(tmp_path, budget, build_run_aufzeichnung):
    """B6: der ganze Wunsch im Ordnernamen sprengt unter Windows schnell die
    Pfadlaenge."""
    _bestand(tmp_path, {"a": 124.0, "b": 126.0, "c": 128.0})
    wunsch = "Sommerfest am See, erst ruhig dann immer wilder bis tief in die Nacht"
    client = ZaehlenderClient([json.dumps(DRAFT)])

    assert cmd_set(tmp_path, wunsch, 20, None, client, budget, HEUTE) == 0

    [aufruf] = build_run_aufzeichnung.aufrufe
    run = aufruf["run"]
    assert isinstance(run, Run)
    slug = run.run_id.removeprefix("2026-11-23-").removesuffix("-01")
    assert slug == "sommerfest-am-see-erst-ruhig-dann-immer"
    assert len(slug) <= 40
    # Der Wunsch selbst bleibt ungekuerzt im Profil.
    assert aufruf["profile"].genre == wunsch


def test_eine_kaputte_messung_wird_zur_fehlermeldung_mit_song_id(
    tmp_path, zaehlender_client, budget, capsys, monkeypatch
):
    """B7: nicht nur BestandFehler - jede Ausnahme beim Messen."""
    bestand = _bestand(tmp_path, {"a": 124.0, "b": 126.0})
    _lege_song_ab(bestand, "c", None)

    def kaputt(self, song_id):
        raise RuntimeError("librosa ist umgefallen")

    monkeypatch.setattr(Bestand, "messen", kaputt)

    assert cmd_set(tmp_path, "Party", 5, None, zaehlender_client, budget, HEUTE) == 1

    assert zaehlender_client.aufrufe == 0
    assert capsys.readouterr().err == "Fehler: c: librosa ist umgefallen\n"
