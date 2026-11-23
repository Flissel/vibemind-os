import json
from datetime import date
from pathlib import Path

from djset.agents import analyst
from djset.agents.base import Budget
from djset.analysis import analyse_all, artifact_name
from djset.contracts import Profile, TrackAnalysis, TrackMeasurements
from djset.run import Run
from djset.watch import AudioFile
from tests.fixtures.analyses import make_analysis
from tests.fixtures.fake_client import FakeClient
from tests.fixtures.make_audio import click_track

ANSWER = json.dumps(
    {
        "usable": True,
        "notes": "Passt.",
        "energy_level": 0.5,
        "remeasure_with_bpm": None,
    }
)


def _run(tmp_path: Path) -> Run:
    return Run.create(tmp_path, "liquid dnb", date(2026, 8, 21))


def _files(tmp_path: Path, count: int) -> list[AudioFile]:
    return [
        AudioFile(
            id=f"t{i}",
            path=click_track(tmp_path / f"t{i}.wav", bpm=174.0, bars=14),
        )
        for i in range(count)
    ]


def test_every_file_gets_analysed_and_written(tmp_path: Path, profile: Profile) -> None:
    run = _run(tmp_path)
    files = _files(tmp_path, 3)
    client = FakeClient([ANSWER] * 3)

    analyses, unreadable = analyse_all(client, files, profile, run, _budget())

    assert sorted(a.id for a in analyses) == ["t0", "t1", "t2"]
    assert unreadable == []
    for track_id in ("t0", "t1", "t2"):
        assert run.has(artifact_name(track_id))


def test_existing_artifacts_are_reused_not_reanalysed(
    tmp_path: Path, profile: Profile
) -> None:
    """Die Analyse ist der teuerste Schritt - resume darf sie nicht wiederholen."""
    run = _run(tmp_path)
    files = _files(tmp_path, 3)

    analyse_all(FakeClient([ANSWER] * 3), files, profile, run, _budget())
    second = FakeClient([])

    analyses, unreadable = analyse_all(second, files, profile, run, _budget())

    assert second.calls == 0
    assert sorted(a.id for a in analyses) == ["t0", "t1", "t2"]
    assert unreadable == []


def test_a_partial_run_only_analyses_what_is_missing(
    tmp_path: Path, profile: Profile
) -> None:
    run = _run(tmp_path)
    files = _files(tmp_path, 3)
    analyse_all(FakeClient([ANSWER]), files[:1], profile, run, _budget())

    second = FakeClient([ANSWER] * 2)
    analyses, _ = analyse_all(second, files, profile, run, _budget())

    assert second.calls == 2
    assert len(analyses) == 3


def test_a_broken_file_is_recorded_and_the_rest_continues(
    tmp_path: Path, profile: Profile
) -> None:
    run = _run(tmp_path)
    files = _files(tmp_path, 2)
    broken = tmp_path / "kaputt.wav"
    broken.write_bytes(b"kein wav")
    files.append(AudioFile(id="kaputt", path=broken))

    analyses, unreadable = analyse_all(
        FakeClient([ANSWER] * 2), files, profile, run, _budget()
    )

    assert len(analyses) == 2
    assert [u.id for u in unreadable] == ["kaputt"]
    assert run.has(artifact_name("kaputt"))


def test_unreadable_artifacts_are_reused_too(tmp_path: Path, profile: Profile) -> None:
    run = _run(tmp_path)
    broken = tmp_path / "kaputt.wav"
    broken.write_bytes(b"kein wav")
    files = [AudioFile(id="kaputt", path=broken)]

    analyse_all(FakeClient([]), files, profile, run, _budget())
    analyses, unreadable = analyse_all(FakeClient([]), files, profile, run, _budget())

    assert analyses == []
    assert [u.id for u in unreadable] == ["kaputt"]


def test_a_truncated_artifact_is_re_analysed_not_fatal(
    tmp_path: Path, profile: Profile
) -> None:
    """Ein Artefakt, das mitten in einem Mehrbyte-Zeichen abgeschnitten wurde,
    darf den ganzen Lauf nicht reissen - genau dieser Zustand ist der
    Normalfall, den resume ueberleben muss (Absturz mitten im Schreiben).
    """
    run = _run(tmp_path)
    files = _files(tmp_path, 1)
    target = run.path(artifact_name("t0"))
    target.parent.mkdir(parents=True, exist_ok=True)
    # "Ba" mit ensure_ascii=False roh in UTF-8 kodiert, dann das letzte Byte
    # gekappt: 0xC3 bleibt als Fuehrbyte ohne Fortsetzungsbyte stehen - genau
    # der Fall, den ein Absturz mitten im Schreiben erzeugt.
    raw = '{"verdict": {"notes": "B\u00e4'.encode("utf-8")
    target.write_bytes(raw[:-1])

    analyses, unreadable = analyse_all(
        FakeClient([ANSWER]), files, profile, run, _budget()
    )

    assert [a.id for a in analyses] == ["t0"]
    assert unreadable == []


def test_a_garbage_artifact_is_re_analysed_not_fatal(
    tmp_path: Path, profile: Profile
) -> None:
    """Weder gueltiges JSON noch eines der beiden Schemata - muss trotzdem
    einfach neu analysiert werden statt den Lauf zu reissen."""
    run = _run(tmp_path)
    files = _files(tmp_path, 1)
    target = run.path(artifact_name("t0"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"nicht mal json")

    analyses, unreadable = analyse_all(
        FakeClient([ANSWER]), files, profile, run, _budget()
    )

    assert [a.id for a in analyses] == ["t0"]
    assert unreadable == []


def _budget() -> Budget:
    return Budget(max_tokens=200_000, max_seconds=120.0)


def test_responses_can_be_keyed_by_prompt(tmp_path: Path, profile: Profile) -> None:
    """Bei vier Threads steht die Aufrufreihenfolge nicht fest - der Prompt schon."""
    run = _run(tmp_path)
    files = _files(tmp_path, 3)
    client = FakeClient(
        {
            "t0": json.dumps({"usable": True, "notes": "A.", "energy_level": 0.2,
                              "remeasure_with_bpm": None}),
            "t1": json.dumps({"usable": True, "notes": "B.", "energy_level": 0.5,
                              "remeasure_with_bpm": None}),
            "t2": json.dumps({"usable": True, "notes": "C.", "energy_level": 0.8,
                              "remeasure_with_bpm": None}),
        }
    )

    analyses, _ = analyse_all(client, files, profile, run, _budget())

    energies = {a.id: a.verdict.energy_level for a in analyses}
    assert energies == {"t0": 0.2, "t1": 0.5, "t2": 0.8}


def test_mitgegebene_messungen_ersetzen_die_messung(
    tmp_path: Path, profile: Profile, monkeypatch
) -> None:
    """B1: Laeufe aus dem Bestand reichen ihre gespeicherten Messungen durch."""
    def nie(*args: object, **kwargs: object) -> None:
        raise AssertionError("neu gemessen")

    monkeypatch.setattr(analyst, "measure_track", nie)
    run = _run(tmp_path)
    files = [AudioFile(id=f"t{i}", path=tmp_path / f"t{i}.wav") for i in range(2)]
    messungen = {
        f"t{i}": TrackMeasurements.model_validate(
            make_analysis(f"t{i}", bpm=126.0 + i).model_dump(exclude={"verdict"})
        )
        for i in range(2)
    }

    analyses, unreadable = analyse_all(
        FakeClient([ANSWER] * 2), files, profile, run, _budget(), messungen=messungen
    )

    assert unreadable == []
    assert {a.id: a.bpm.value for a in analyses} == {"t0": 126.0, "t1": 127.0}
