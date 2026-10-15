"""Checks for ehl_schema.vaultmap and the profile-aware rules note."""
import json

import pytest
from helpers import vibemind_like_vault, write_raw

from ehl_schema.build import RULES_NOTE, structure_notes
from ehl_schema.model import load_schema
from ehl_schema.profile import default_profile, load_profile
from ehl_schema.vaultmap import MAP_FILE, run_map


@pytest.fixture
def schema():
    return load_schema()


@pytest.fixture
def workdir(tmp_path, schema):
    vibemind_like_vault(tmp_path, schema)
    return tmp_path


def test_map_lists_every_placed_note(workdir, schema):
    result = run_map(schema, load_profile("vibemind", schema), workdir)
    doc = json.loads((workdir / MAP_FILE).read_text(encoding="utf-8"))
    assert (doc["version"], doc["company"]) == (1, "VibeMind")
    assert doc["notes"]["Hubs/Agents"] == {"type": "Hubs", "level": 3, "domain": "ai", "parent": "Areas/Agents"}
    assert doc["notes"]["Company"] == {"type": "Company", "level": 0, "domain": "company", "parent": None}
    assert "Diary/Secret" not in doc["notes"]
    assert result.excluded == 3 and result.written
    assert result.notes == len(doc["notes"])


def test_existing_notes_are_never_touched(workdir, schema):
    vault = workdir / "knowledge"
    managed = {"Company.md", f"{RULES_NOTE}.md"}
    before = {p: p.read_bytes() for p in vault.rglob("*.md")
              if p.relative_to(vault).parts[0] not in ("Areas", "Domains")
              and p.relative_to(vault).as_posix() not in managed}
    run_map(schema, load_profile("vibemind", schema), workdir)
    assert {p: p.read_bytes() for p in before} == before


def test_unchanged_map_is_not_rewritten(workdir, schema):
    profile = load_profile("vibemind", schema)
    assert run_map(schema, profile, workdir).written
    stamp = (workdir / MAP_FILE).stat().st_mtime_ns
    second = run_map(schema, profile, workdir)
    assert not second.written and second.structure == 0
    assert (workdir / MAP_FILE).stat().st_mtime_ns == stamp
    write_raw(workdir / "knowledge", "Agents/new-bot (999999)", "# new\n")
    assert run_map(schema, profile, workdir).written


def test_company_comes_from_the_profile(workdir, schema):
    run_map(schema, load_profile("vibemind", schema), workdir)
    assert "# VibeMind" in (workdir / "knowledge" / "Company.md").read_text(encoding="utf-8")


def test_rules_note_lists_the_folders_only_for_profiles_with_rules(schema):
    with_rules = structure_notes(schema, "VibeMind", load_profile("vibemind", schema))[RULES_NOTE]
    assert "## Folders in this vault" in with_rules
    assert "| `companys/{c}/Marke` | Brands |" in with_rules
    assert "Diary/**" in with_rules
    plain = structure_notes(schema, "EHL", default_profile("EHL"))[RULES_NOTE]
    assert plain == structure_notes(schema, "EHL")[RULES_NOTE]
    assert "Folders in this vault" not in plain
