"""Checks for ehl_schema.lint."""
import pytest
from helpers import valid_vault, vibemind_like_vault, write_note, write_raw

from ehl_schema.lint import errors, lint
from ehl_schema.model import load_schema


@pytest.fixture
def schema():
    return load_schema()


def codes(issues, path=None):
    return {i.code for i in issues if path is None or i.path == path}


def deal(**changes):
    front = {"id": "deal-x", "type": "Deals", "domain": "sales", "level": 3, "parent": "[[Areas/Pipeline]]",
             "customer": ["[[Organizations/Acme]]"], "owner": ["[[People/Felix Baumann]]"]}
    front.update(changes)
    return {k: v for k, v in front.items() if v is not None}


def test_valid_vault_has_no_issues(tmp_path, schema):
    assert lint(schema, valid_vault(tmp_path, schema)) == []


def test_unknown_folder_and_missing_front_matter(tmp_path, schema):
    vault = valid_vault(tmp_path, schema)
    write_note(vault, "Random/Thing", {"id": "x"})
    write_note(vault, "Topics/Loose", None, "# Loose\n")
    issues = lint(schema, vault)
    assert codes(issues, "Random/Thing") == {"unknown-type"}
    assert codes(issues, "Topics/Loose") == {"no-front-matter"}


def test_bad_yaml(tmp_path, schema):
    vault = valid_vault(tmp_path, schema)
    (vault / "Topics").mkdir()
    (vault / "Topics" / "Bad.md").write_text("---\nid: [x\n---\n", encoding="utf-8")
    assert codes(lint(schema, vault), "Topics/Bad") == {"bad-yaml"}


def test_wrong_type_domain_level(tmp_path, schema):
    vault = valid_vault(tmp_path, schema)
    write_note(vault, "Deals/X", deal(type="Leads", domain="legal", level=4))
    assert codes(lint(schema, vault), "Deals/X") >= {"type-mismatch", "domain-mismatch", "level-mismatch"}


def test_missing_key_and_nested_value(tmp_path, schema):
    vault = valid_vault(tmp_path, schema)
    write_note(vault, "Deals/X", deal(parent=None))
    (vault / "Deals" / "Y.md").write_text(
        '---\nid: deal-y\ntype: Deals\ndomain: sales\nlevel: 3\nparent: "[[Areas/Pipeline]]"\n'
        'owner:\n  name: Felix\n---\n', encoding="utf-8")
    issues = lint(schema, vault)
    assert "missing-key" in codes(issues, "Deals/X")
    assert "nested" in codes(issues, "Deals/Y")


def test_parent_rules(tmp_path, schema):
    vault = valid_vault(tmp_path, schema)
    write_note(vault, "Deals/A", deal(id="a", parent="[[People/Felix Baumann]]"))
    write_note(vault, "Deals/B", deal(id="b", parent="[[Areas/Nowhere]]"))
    write_note(vault, "Deals/C", deal(id="c", parent="Areas/Pipeline"))
    issues = lint(schema, vault)
    assert "parent-type" in codes(issues, "Deals/A")
    assert "parent-missing" in codes(issues, "Deals/B")
    assert "parent-format" in codes(issues, "Deals/C")


def test_cycle(tmp_path, schema):
    vault = valid_vault(tmp_path, schema)
    base = {"type": "Projects", "domain": "delivery", "level": 3}
    write_note(vault, "Projects/A", {**base, "id": "pa", "parent": "[[Projects/B]]"})
    write_note(vault, "Projects/B", {**base, "id": "pb", "parent": "[[Projects/A]]"})
    assert "cycle" in codes(lint(schema, vault))


def test_relations(tmp_path, schema):
    vault = valid_vault(tmp_path, schema)
    write_note(vault, "Deals/A", deal(id="a", customer=None))
    write_note(vault, "Deals/B", deal(id="b", customer=["[[People/Felix Baumann]]"]))
    write_note(vault, "Deals/C", deal(id="c", customer="[[Organizations/Acme]]"))
    write_note(vault, "Deals/D", deal(id="d", customer=["[[Organizations/Nobody]]"]))
    write_note(vault, "Deals/E", deal(id="e", when="next week"))
    issues = lint(schema, vault)
    assert "required-relation" in codes(issues, "Deals/A")
    assert "relation-target" in codes(issues, "Deals/B")
    assert "relation-format" in codes(issues, "Deals/C")
    assert "relation-missing" in codes(issues, "Deals/D")
    assert "relation-format" in codes(issues, "Deals/E")


def test_duplicate_id_and_status(tmp_path, schema):
    vault = valid_vault(tmp_path, schema)
    write_note(vault, "Deals/Copy", deal(id="deal-acme", status="maybe"))
    issues = lint(schema, vault)
    assert "duplicate-id" in codes(issues, "Deals/Copy")
    assert any(i.code == "status-value" and i.severity == "warning" for i in issues)


def test_broken_body_link_is_a_warning(tmp_path, schema):
    vault = valid_vault(tmp_path, schema)
    write_note(vault, "Deals/X", deal(), "See [[Topics/Nope]].\n")
    issues = lint(schema, vault)
    assert codes(issues, "Deals/X") == {"broken-link"}
    assert errors(issues) == []


def test_alias_and_attachment_links_resolve(tmp_path, schema):
    vault = valid_vault(tmp_path, schema)
    (vault / "Data").mkdir()
    (vault / "Data" / "BMW Dataset.xlsx").write_bytes(b"x")
    write_note(vault, "Deals/X", deal(), "[[Organizations/Acme|Acme]] [[Data/BMW Dataset.xlsx|data]] [[Deals/Acme deal#Top]]\n")
    assert codes(lint(schema, vault), "Deals/X") == set()


def test_possible_duplicate_people(tmp_path, schema):
    vault = valid_vault(tmp_path, schema)
    write_note(vault, "People/Felix-Baumann", {"id": "p2", "type": "People", "domain": "register", "level": 3,
                                               "parent": "[[Areas/Contacts]]"})
    issues = lint(schema, vault)
    assert {i.path for i in issues if i.code == "possible-duplicate"} == {"People/Felix Baumann",
                                                                          "People/Felix-Baumann"}


def test_vibemind_vault_lints_clean_except_the_two_warnings(tmp_path, schema):
    from ehl_schema.profile import load_profile

    vault = vibemind_like_vault(tmp_path, schema)
    issues = lint(schema, vault, load_profile("vibemind", schema))
    assert errors(issues) == []
    assert {(i.path, i.code) for i in issues} == {
        ("Strange Folder/thing", "no-rule"),
        ("Projects/TestCo - Orphan/Lonely", "rule-parent-missing"),
    }


def test_profile_lint_still_reports_broken_yaml_and_cycles(tmp_path, schema):
    from ehl_schema.profile import load_profile

    vault = vibemind_like_vault(tmp_path, schema)
    write_raw(vault, "Agents/broken", "---\nfoo: [unclosed\n---\n# broken\n")
    write_raw(vault, "Topics/Loop A", '---\nid: a\ntype: Topics\ndomain: knowledge\nlevel: 3\nparent: "[[Topics/Loop B]]"\n---\n')
    write_raw(vault, "Topics/Loop B", '---\nid: b\ntype: Topics\ndomain: knowledge\nlevel: 3\nparent: "[[Topics/Loop A]]"\n---\n')
    found = {(i.path, i.code) for i in errors(lint(schema, vault, load_profile("vibemind", schema)))}
    assert ("Agents/broken", "bad-yaml") in found
    assert any(code == "cycle" for _, code in found)


def test_profile_lint_checks_parent_types_of_placed_notes(tmp_path, schema):
    from ehl_schema.profile import load_profile

    vault = vibemind_like_vault(tmp_path, schema)
    profile_file = tmp_path / "bad.yaml"
    profile_file.write_text("company: TestCo\nrules:\n  - {match: 'Videos/**', type: Assets, parent: 'Topics/AI'}\n",
                            encoding="utf-8", newline="\n")
    issues = lint(schema, vault, load_profile(str(profile_file), schema))
    assert ("Videos/Demo Clip", "parent-type") in {(i.path, i.code) for i in errors(issues)}


def test_ehl_lint_is_unchanged_without_profile(tmp_path, schema):
    assert lint(schema, valid_vault(tmp_path, schema), None) == []
