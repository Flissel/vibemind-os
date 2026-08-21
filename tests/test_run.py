from datetime import date
from pathlib import Path

import pytest

from djset.contracts import BpmRange
from djset.run import Run, RunNotFound, slugify

DAY = date(2026, 8, 5)


def test_slugify_makes_filesystem_safe_names():
    assert slugify("Liquid DnB") == "liquid-dnb"
    assert slugify("Hard  Techno!") == "hard-techno"


def test_create_numbers_runs_of_the_same_day(tmp_path: Path):
    first = Run.create(tmp_path, "liquid dnb", today=DAY)
    second = Run.create(tmp_path, "liquid dnb", today=DAY)
    assert first.run_id == "2026-08-05-liquid-dnb-01"
    assert second.run_id == "2026-08-05-liquid-dnb-02"


def test_create_makes_the_directory(tmp_path: Path):
    run = Run.create(tmp_path, "techno", today=DAY)
    assert run.path("").is_dir()


def test_write_and_read_model_round_trip(tmp_path: Path):
    run = Run.create(tmp_path, "techno", today=DAY)
    run.write_model("bpm.json", BpmRange(target=130.0, min=128.0, max=134.0))
    restored = run.read_model("bpm.json", BpmRange)
    assert restored.target == 130.0


def test_has_reports_missing_artifacts(tmp_path: Path):
    run = Run.create(tmp_path, "techno", today=DAY)
    assert run.has("profile.json") is False
    run.write_text("profile.json", "{}")
    assert run.has("profile.json") is True


def test_latest_unfinished_skips_runs_with_a_finished_set(tmp_path: Path):
    done = Run.create(tmp_path, "techno", today=DAY)
    done.write_text("set.wav", "nicht wirklich audio")
    pending = Run.create(tmp_path, "techno", today=DAY)
    assert Run.latest_unfinished(tmp_path).run_id == pending.run_id


def test_latest_unfinished_returns_none_when_all_are_done(tmp_path: Path):
    done = Run.create(tmp_path, "techno", today=DAY)
    done.write_text("set.wav", "nicht wirklich audio")
    assert Run.latest_unfinished(tmp_path) is None


def test_load_raises_for_unknown_run(tmp_path: Path):
    with pytest.raises(RunNotFound):
        Run.load(tmp_path, "2026-08-05-gibt-es-nicht-01")
