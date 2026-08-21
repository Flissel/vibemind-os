import json
from datetime import date
from pathlib import Path

from djset.agents.base import Budget
from djset.analysis import analyse_all, artifact_name
from djset.contracts import Profile, TrackAnalysis
from djset.run import Run
from djset.watch import AudioFile
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


def _budget() -> Budget:
    return Budget(max_tokens=200_000, max_seconds=120.0)
