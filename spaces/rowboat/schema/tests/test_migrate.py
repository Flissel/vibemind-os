"""Checks for ehl_schema.migrate on a small vault shaped like the EHL vault."""
import pytest
from helpers import valid_vault, write_note

from ehl_schema.build import build
from ehl_schema.lint import errors, lint
from ehl_schema.migrate import apply, diff, plan
from ehl_schema.model import load_schema
from ehl_schema.notes import read_vault


@pytest.fixture
def schema():
    return load_schema()


@pytest.fixture
def vault(tmp_path, schema):
    v = tmp_path / "knowledge"
    build(schema, tmp_path, "EHL")
    write_note(v, "Brands/BMW", {"source": "dataset", "managed_by": "ehl-visu"}, "[[Models/5er G60]]\n")
    write_note(v, "Models/5er G60", {"source": "dataset"}, "[[Brands/BMW|BMW]] [[Components/Hood]] [[Feature Classes/Hood open close]]\n")
    write_note(v, "Components/Hood", {"part_id": "hood"}, "[[Models/5er G60]]\n")
    write_note(v, "Feature Classes/Hood open close", {"source": "dataset"}, "[[Models/5er G60]] [[Components/Hood]]\n")
    write_note(v, "Projects/G60", None, "[[Projects/G60 Roadmap]] [[Topics/Comfort]]\n")
    write_note(v, "Projects/G60 Roadmap", None, "[[Projects/G60]] [[Projects/G60]]\n")
    write_note(v, "Topics/Comfort", None, "[[Projects/G60]]\n")
    write_note(v, "Purchase Drivers/5 Series top 10", {"managed_by": "torque"},
               "[[Projects/G60]] [[Purchase Drivers/Quiet cabin]] [[Purchase Drivers/Range]]\n")
    write_note(v, "Purchase Drivers/Quiet cabin", {"managed_by": "torque"}, "[[Topics/Comfort]]\n")
    write_note(v, "Purchase Drivers/Range", {"managed_by": "torque"}, "")
    write_note(v, "Requirements/R-01 Fast charging", {"req_id": "R-01"}, "# R-01\n")
    return v


def parents(report):
    out = {}
    for path, (_, new) in report.changes.items():
        line = next(l for l in new.splitlines() if l.startswith("parent: "))
        out[path] = line.removeprefix('parent: "[[').removesuffix(']]"')
    return out


def test_plan_chooses_parents(schema, vault):
    got = parents(plan(schema, vault))
    assert got["Brands/BMW"] == "Areas/Brand"
    assert got["Models/5er G60"] == "Brands/BMW"
    assert got["Components/Hood"] == "Models/5er G60"
    assert got["Feature Classes/Hood open close"] == "Components/Hood"
    assert got["Projects/G60"] == "Areas/Client projects"
    assert got["Projects/G60 Roadmap"] == "Projects/G60"
    assert got["Topics/Comfort"] == "Areas/Topics"
    assert got["Purchase Drivers/5 Series top 10"] == "Projects/G60"
    assert got["Purchase Drivers/Quiet cabin"] == "Purchase Drivers/5 Series top 10"
    assert got["Purchase Drivers/Range"] == "Purchase Drivers/5 Series top 10"
    assert got["Requirements/R-01 Fast charging"] == "Areas/Requirements"


def test_existing_keys_stay_first_and_unchanged(schema, vault):
    _, new = plan(schema, vault).changes["Brands/BMW"]
    assert new.startswith("---\nsource: dataset\nmanaged_by: ehl-visu\nid: brands-bmw\ntype: Brands\n")


def test_dry_run_writes_nothing_and_shows_a_diff(schema, vault):
    before = (vault / "Brands" / "BMW.md").read_text(encoding="utf-8")
    report = plan(schema, vault)
    assert "+parent: \"[[Areas/Brand]]\"" in diff(report)
    assert (vault / "Brands" / "BMW.md").read_text(encoding="utf-8") == before


def test_apply_then_lint_has_no_errors(schema, vault):
    report = plan(schema, vault)
    assert apply(report, vault) == len(report.changes)
    assert errors(lint(schema, vault)) == []


def test_migrate_is_idempotent(schema, vault):
    apply(plan(schema, vault), vault)
    assert plan(schema, vault).changes == {}


def test_unknown_folders_are_reported_not_touched(schema, vault):
    write_note(vault, "Diary/2026", None, "private\n")
    report = plan(schema, vault)
    assert report.unmapped == ["Diary/2026"]
    assert "Diary/2026" not in report.changes


def test_ids_are_unique(schema, vault):
    write_note(vault, "Topics/Comfort!", None, "")
    ids = [l for _, (_, new) in plan(schema, vault).changes.items() for l in new.splitlines() if l.startswith("id: ")]
    assert len(ids) == len(set(ids))


def test_note_without_any_parent_option_is_unresolved(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("""
schema_version: 1
domains: {sales: {title: Sales}}
areas: {Pipeline: sales}
relations: {owner: {targets: [People]}}
types:
  People: {domain: sales, level: 3, parent: [People]}
""", encoding="utf-8")
    schema = load_schema(path)
    vault = tmp_path / "knowledge"
    write_note(vault, "People/Alone", None, "")
    assert plan(schema, vault).unresolved == ["People/Alone"]
    assert read_vault(vault)[0]["People/Alone"].front is None


def test_build_then_migrate_settles(schema, tmp_path):
    build(schema, tmp_path, "EHL")
    vault = tmp_path / "knowledge"
    assert plan(schema, vault).changes == {}
    assert build(schema, tmp_path, "EHL")["notes"] == 0


def test_rowboat_owned_agent_notes_are_left_alone(schema, vault):
    write_note(vault, "Agent Notes/inbox", None, "")
    write_note(vault, "Agent Notes/preferences", None, "- prefers short answers\n")
    report = plan(schema, vault)
    assert not any(p.startswith("Agent Notes/") and p != "Agent Notes/Vault Schema" for p in report.changes)
    apply(report, vault)
    assert errors(lint(schema, vault)) == []


def test_migrate_leaves_hub_notes_alone(tmp_path, schema):
    vault = valid_vault(tmp_path, schema)
    write_note(vault, "Hubs/Agents", None, "# Agents\n\n- [[alpha]]\n")
    report = plan(schema, vault)
    assert "Hubs/Agents" not in report.changes
    assert "Hubs/Agents" in report.unmapped
