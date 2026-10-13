"""Checks for ehl_schema.placement."""
import pytest
from helpers import valid_vault, vibemind_like_vault

from ehl_schema.model import load_schema
from ehl_schema.notes import read_vault
from ehl_schema.placement import Placement, resolve
from ehl_schema.profile import default_profile, load_profile


@pytest.fixture
def schema():
    return load_schema()


@pytest.fixture
def resolution(tmp_path, schema):
    notes, _ = read_vault(vibemind_like_vault(tmp_path, schema))
    return resolve(schema, load_profile("vibemind", schema), notes)


EXPECTED = {
    "Agents/alpha-bot (abc123)": ("Agents", 3, "ai", "Areas/Agents", "default"),
    "Agents/Veraltet/old-bot (def456)": ("Agents", 3, "ai", "Areas/Agents", "default"),
    "Bubbles/Green Idea — Beta (fa8d41)": ("Ideas", 3, "product", "Areas/Roadmap", "rule"),
    "Hubs/Agents": ("Hubs", 3, "ai", "Areas/Agents", "rule"),
    "Hubs/Misc": ("Hubs", 3, "knowledge", "Areas/Topics", "rule"),
    "Projects/Demo Root": ("Projects", 3, "delivery", "Areas/Client projects", "rule"),
    "Projects/TestCo - Shop/_overview": ("Projects", 3, "delivery", "Areas/Client projects", "rule"),
    "Projects/TestCo - Shop/Payment Flow": ("Requirements", 3, "product", "Projects/TestCo - Shop/_overview", "rule"),
    "Projects/VibeMind - TODO Write tests (z. B. unit)/_overview": ("Tasks", 4, "delivery", "Areas/Milestones", "rule"),
    "Projects/VibeMind - TODO Write tests (z. B. unit)/Detail": ("Tasks", 4, "delivery", "Areas/Milestones", "rule"),
    "Projects/TestCo - Orphan/Lonely": ("Requirements", 3, "product", "Areas/Requirements", "rule"),
    "companys/Acme Brand/Marke": ("Brands", 3, "marketing", "Areas/Brand", "rule"),
    "companys/Acme Brand/Markenhandbuch": ("Content", 4, "marketing", "companys/Acme Brand/Marke", "rule"),
    "companys/Acme Brand/Marke-Verlauf/2026-01-01 v1": ("Content", 4, "marketing", "companys/Acme Brand/Marke", "rule"),
    "companys/Acme Brand/Agent-Notizen/2026-01-02 hint": ("Agent Notes", 4, "sources", "companys/Acme Brand/Marke", "rule"),
    "vibemind-discourse/2026-01-01/04-07": ("Discourse", 4, "sources", "Areas/Discourse", "rule"),
    "Videos/Demo Clip": ("Assets", 4, "marketing", "Areas/Content", "rule"),
    "System/PC (system)": ("Systems", 3, "operations", "Areas/Infrastructure", "rule"),
    "Notes/Traction Metrics 2026-Q1": ("KPIs", 3, "strategy", "Areas/KPIs", "rule"),
    "Notes/Random": ("Topics", 3, "knowledge", "Areas/Topics", "rule"),
    "Investor Programs/Grant X/Questions": ("Funding", 3, "finance", "Areas/Funding", "rule"),
    "Coding-Projects/tool-a": ("Projects", 3, "delivery", "Areas/Client projects", "rule"),
    "Workflows/flow-a": ("Workflows", 3, "ai", "Areas/Workflows", "default"),
    "Topics/AI": ("Topics", 3, "knowledge", "Areas/Topics", "default"),
    "People/Jane Doe": ("People", 3, "register", "Areas/Contacts", "default"),
    "People/Design Partners/Partner Co": ("People", 3, "register", "Areas/Contacts", "default"),
    "Strange Folder/thing": ("Topics", 3, "knowledge", "Areas/Topics", "fallback"),
}


def test_every_vibemind_note_is_placed(resolution):
    for path, (tname, level, domain, parent, source) in EXPECTED.items():
        assert resolution.placements[path] == Placement(tname, level, domain, parent, source), path


def test_private_notes_are_excluded(resolution):
    assert sorted(resolution.excluded) == ["Bewerbung/Firm/Letter", "Diary/Secret", "Welcome"]
    assert not any(p in resolution.placements for p in resolution.excluded)


def test_structure_notes_come_from_their_front_matter(resolution):
    assert resolution.placements["Company"] == Placement("Company", 0, "company", None, "front")
    assert resolution.placements["Areas/Agents"] == Placement("Areas", 2, "ai", "Domains/AI & agents", "front")


def test_warnings(resolution):
    found = {(i.severity, i.path, i.code) for i in resolution.issues}
    assert found == {
        ("warning", "Strange Folder/thing", "no-rule"),
        ("warning", "Projects/TestCo - Orphan/Lonely", "rule-parent-missing"),
    }


def test_ehl_vault_with_default_profile_uses_front_matter_only(tmp_path, schema):
    notes, _ = read_vault(valid_vault(tmp_path, schema))
    res = resolve(schema, default_profile("EHL"), notes)
    assert {p.source for p in res.placements.values()} == {"front"}
    assert res.placements["Deals/Acme deal"] == Placement("Deals", 3, "sales", "Areas/Pipeline", "front")
    assert res.issues == [] and res.excluded == []


def test_front_matter_without_parent_gets_the_default_area(tmp_path, schema):
    from helpers import write_raw
    vault = vibemind_like_vault(tmp_path, schema)
    write_raw(vault, "Topics/Only type", "---\ntype: Topics\n---\n# x\n")
    notes, _ = read_vault(vault)
    res = resolve(schema, load_profile("vibemind", schema), notes)
    assert res.placements["Topics/Only type"] == Placement("Topics", 3, "knowledge", "Areas/Topics", "front")
