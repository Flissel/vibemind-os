"""Checks for ehl_schema.model: the real schema file loads and is consistent; bad schemas are rejected."""
import pytest

from ehl_schema.model import DEFAULT_SCHEMA, STRUCTURE_TYPES, SchemaError, load_schema

MINI = """
schema_version: 1
domains: {sales: {title: Sales}}
areas: {Pipeline: sales}
relations: {owner: {targets: [People]}, about: {targets: any}, when: {kind: date}}
types:
  People: {domain: sales, level: 3, parent: [Areas], default_parent: Pipeline, builtin: true, optional: [owner]}
"""


def test_real_schema_loads_and_is_consistent():
    schema = load_schema()
    assert schema.version == 1
    assert len(schema.domains) == 15
    assert {"register", "sources", "knowledge"} == set(schema.cross_cutting)
    assert {"People", "Organizations", "Projects", "Topics", "Meetings"} <= set(schema.types)
    for name, td in schema.types.items():
        if name in STRUCTURE_TYPES:
            continue
        assert td.default_parent in schema.areas or td.domain == "*", name
        assert all(p in schema.types for p in td.parent), name


def test_every_domain_has_an_area():
    schema = load_schema()
    assert set(schema.areas.values()) == set(schema.domains)


def test_structure_types_are_added():
    schema = load_schema()
    assert schema.types["Company"].level == 0
    assert schema.types["Domains"].parent == ("Company",)
    assert schema.types["Areas"].parent == ("Domains",)


def test_type_of_path_and_note_names():
    schema = load_schema()
    assert schema.type_of_path("Company") == "Company"
    assert schema.type_of_path("Components/Hood") == "Components"
    assert schema.type_of_path("Unknown/Thing") is None
    assert schema.type_of_path("Loose") is None
    assert schema.domain_note("sales") == "Domains/Market & sales"
    assert schema.domain_key("Market & sales") == "sales"
    assert schema.area_note("Pipeline") == "Areas/Pipeline"


def test_relation_targets_any_and_date():
    schema = load_schema()
    assert schema.relations["about"].targets is None
    assert schema.relations["when"].kind == "date"
    assert schema.relations["customer"].targets == ("Organizations",)


def test_mini_schema_loads(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(MINI, encoding="utf-8")
    assert load_schema(path).types["People"].default_parent == "Pipeline"


@pytest.mark.parametrize("old, new, message", [
    ("domain: sales, level", "domain: nope, level", "unknown domain"),
    ("default_parent: Pipeline", "default_parent: Nowhere", "unknown default_parent"),
    ("optional: [owner]", "optional: [boss]", "unknown relation"),
    ("parent: [Areas]", "parent: [Domains]", "not an allowed parent"),
    ("level: 3", "level: 2", "level must be 3 or 4"),
])
def test_bad_schema_is_rejected(tmp_path, old, new, message):
    path = tmp_path / "s.yaml"
    path.write_text(MINI.replace(old, new), encoding="utf-8")
    with pytest.raises(SchemaError, match=message):
        load_schema(path)


def test_reserved_type_name_is_rejected(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text(MINI + "  Areas: {domain: sales, level: 3, parent: [Areas]}\n", encoding="utf-8")
    with pytest.raises(SchemaError, match="reserved"):
        load_schema(path)


def test_vibemind_types_and_area():
    schema = load_schema()
    assert schema.areas["Discourse"] == "sources"
    assert (schema.types["Ideas"].domain, schema.types["Ideas"].level, schema.types["Ideas"].default_parent) == ("product", 3, "Roadmap")
    assert (schema.types["Discourse"].domain, schema.types["Discourse"].level) == ("sources", 4)
    hubs = schema.types["Hubs"]
    assert (hubs.domain, hubs.level, hubs.parent, hubs.default_parent) == ("*", 3, ("Areas",), None)
    assert schema.types["Requirements"].parent == ("Models", "Products", "Projects", "Areas")
    assert schema.types["Content"].parent == ("Campaigns", "Brands", "Areas")
    assert schema.types["Agent Notes"].parent == ("Brands", "Projects", "Agents", "Areas")


def test_star_domain_needs_areas_as_only_parent(tmp_path):
    line = "Hubs:               {domain: \"*\", level: 3, parent: [Areas]}"
    original = DEFAULT_SCHEMA.read_text(encoding="utf-8")
    assert line in original
    path = tmp_path / "schema.yaml"
    path.write_text(original.replace(line, line.replace("[Areas]", "[Agents, Areas]")), encoding="utf-8", newline="\n")
    with pytest.raises(SchemaError, match="Hubs"):
        load_schema(path)
