from __future__ import annotations

import configparser
import subprocess
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[4]
LOCK_PATH = REPO_ROOT / "spaces" / "learning" / "deployment" / "upstream-lock.yml"
README_PATH = REPO_ROOT / "spaces" / "learning" / "README.md"

EXPECTED = {
    "learnhouse": {
        "path": "spaces/learning/learnhouse",
        "url": "https://github.com/Flissel/learnhouse.git",
        "upstream": "https://github.com/learnhouse/learnhouse.git",
        "commit": "5a1728787a56483b1af7c1de85e2804a41e43953",
        "license": "AGPL-3.0-only",
    },
    "penecho": {
        "path": "spaces/learning/penecho",
        "url": "https://github.com/Flissel/penecho.git",
        "upstream": "https://github.com/penecho/penecho.git",
        "commit": "d5801103406f3ddad2f261439bce1d9ab48f9def",
        "license": "AGPL-3.0-only",
    },
}


def _gitlinks() -> dict[str, tuple[str, str]]:
    result = subprocess.run(
        ["git", "ls-files", "--stage", "--", "spaces/learning"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    links: dict[str, tuple[str, str]] = {}
    for line in result.stdout.splitlines():
        metadata, path = line.split("\t", 1)
        mode, object_id, _stage = metadata.split()
        if mode == "160000":
            links[path] = (mode, object_id)
    return links


def test_learning_upstreams_are_exact_controlled_gitlinks() -> None:
    modules = configparser.ConfigParser()
    modules.read(REPO_ROOT / ".gitmodules", encoding="utf-8")
    links = _gitlinks()

    for expected in EXPECTED.values():
        section = f'submodule "{expected["path"]}"'
        assert modules[section]["path"] == expected["path"]
        assert modules[section]["url"] == expected["url"]
        assert links[expected["path"]] == ("160000", expected["commit"])


def test_upstream_lock_records_provenance_and_license() -> None:
    lock = yaml.safe_load(LOCK_PATH.read_text(encoding="utf-8"))
    assert lock["version"] == 1
    assert lock["sources"] == EXPECTED


def test_learning_readme_documents_update_and_baseline_procedure() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    for required_text in (
        "AGPL-3.0-only",
        "corresponding source",
        "git fetch upstream",
        "baseline",
        "LearnHouse",
        "PenEcho",
    ):
        assert required_text in readme
