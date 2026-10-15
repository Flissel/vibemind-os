"""Checks for ehl_schema.build."""
import json

import pytest
from helpers import write_note

from ehl_schema.build import RULES_MARK, RULES_NOTE, build, notes_json, structure_notes
from ehl_schema.lint import lint
from ehl_schema.model import load_schema

ROWBOAT_NOTES_JSON = [
    {"type": "People", "folder": "People", "template": "# {Full Name}\n\n## Info\n", "extractionGuide": "Look for: name"},
    {"type": "Meetings", "folder": "Meetings", "template": "", "extractionGuide": "Look for: meeting title"},
    {"type": "Custom", "folder": "Custom", "template": "x", "extractionGuide": ""},
]


@pytest.fixture
def schema():
    return load_schema()


@pytest.fixture
def workdir(tmp_path):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "notes.json").write_text(json.dumps(ROWBOAT_NOTES_JSON), encoding="utf-8")
    return tmp_path


def test_structure_notes_cover_company_domains_areas_and_rules(schema):
    notes = structure_notes(schema, "EHL")
    assert len(notes) == 1 + len(schema.domains) + len(schema.areas) + 1
    assert notes["Company"].startswith("---\nid: company\n")
    assert "# EHL" in notes["Company"]
    assert 'parent: "[[Domains/Market & sales]]"' in notes["Areas/Pipeline"]
    assert RULES_NOTE in notes


def test_build_on_empty_vault_lints_clean(schema, workdir):
    result = build(schema, workdir, "EHL")
    assert result == {"notes": len(structure_notes(schema, "EHL")), "notes_json": "updated"}
    assert lint(schema, workdir / "knowledge") == []


def test_build_is_idempotent(schema, workdir):
    build(schema, workdir, "EHL")
    before = (workdir / "config" / "notes.json").read_text(encoding="utf-8")
    assert build(schema, workdir, "EHL") == {"notes": 0, "notes_json": "unchanged"}
    after = (workdir / "config" / "notes.json").read_text(encoding="utf-8")
    assert before == after
    people = next(e for e in json.loads(after) if e["type"] == "People")
    assert people["extractionGuide"].count(RULES_MARK) == 1


def test_notes_json_keeps_rowboat_bodies_and_foreign_types(schema):
    entries = {e["type"]: e for e in notes_json(schema, ROWBOAT_NOTES_JSON)}
    people = entries["People"]
    assert people["template"].startswith("---\nid: {")
    assert "# {Full Name}" in people["template"]
    assert people["extractionGuide"].startswith("Look for: name")
    assert entries["Meetings"]["template"] == ""
    assert entries["Custom"] == ROWBOAT_NOTES_JSON[2]
    assert entries["Deals"]["folder"] == "Deals"
    assert 'customer: ["[[{Organizations/…}]]"]' in entries["Deals"]["template"]
    assert "Voice Memos" not in entries and "Areas" not in entries


def test_build_does_not_overwrite_a_human_note(schema, workdir):
    path = write_note(workdir / "knowledge", "Areas/Pipeline", {"id": "mine"}, "# My pipeline\n")
    before = path.read_text(encoding="utf-8")
    build(schema, workdir, "EHL")
    assert path.read_text(encoding="utf-8") == before


def test_build_without_notes_json(schema, tmp_path):
    (tmp_path / "knowledge").mkdir()
    assert build(schema, tmp_path, "EHL")["notes_json"] == "missing"


def test_notes_json_skips_index_types(schema):
    entries = notes_json(schema, [])
    assert "Hubs" not in {e["type"] for e in entries}
    assert "Ideas" in {e["type"] for e in entries}
