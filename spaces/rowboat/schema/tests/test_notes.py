"""Checks for ehl_schema.notes: front matter, links, values and reading a vault."""
import pytest
import yaml

from ehl_schema.notes import (FrontMatterError, add_keys, format_value, link_value, links, parent_target,
                              read_vault, slug, split)


def test_split_accepts_bom_and_crlf():
    front, raw, body = split("﻿---\r\nid: a\r\nwhen: 2026-10-12\r\n---\r\n# T\r\n")
    assert front == {"id": "a", "when": "2026-10-12"}
    assert raw == "id: a\nwhen: 2026-10-12"
    assert body == "# T\n"


def test_split_without_front_matter():
    assert split("# Just text\n") == (None, "", "# Just text\n")


def test_split_empty_front_matter():
    assert split("---\n---\nBody\n") == ({}, "", "Body\n")


def test_split_rejects_bad_yaml():
    with pytest.raises(FrontMatterError):
        split("---\nid: [unclosed\n---\nBody\n")


def test_links_strip_alias_and_heading():
    text = "[[Brands/BMW|BMW]] and [[Models/5er G60#Sales]] and [[Data/BMW Dataset.xlsx]]"
    assert links(text) == ["Brands/BMW", "Models/5er G60", "Data/BMW Dataset.xlsx"]


@pytest.mark.parametrize("value, rendered", [
    (3, "3"),
    ("prod", "prod"),
    ("2026-10-12", "2026-10-12"),
    ("yes", '"yes"'),
    ("[[Areas/Pipeline]]", '"[[Areas/Pipeline]]"'),
    ("Badges & logos", '"Badges & logos"'),
    (["[[Organizations/Acme]]"], '["[[Organizations/Acme]]"]'),
])
def test_format_value(value, rendered):
    assert format_value(value) == rendered


@pytest.mark.parametrize("value", ["[[Areas/IP & licences]]", "Ümlaut, comma: (x)", ["[[A/b]]", "[[C/d]]"], "yes", 4])
def test_format_value_round_trips(value):
    assert yaml.safe_load(f"k: {format_value(value)}")["k"] == value


def test_link_value_and_parent_target(tmp_path):
    assert link_value(' "[[Areas/Pipeline]]" '.strip().strip('"')) == "Areas/Pipeline"
    assert link_value("Areas/Pipeline") is None
    (tmp_path / "Deals").mkdir()
    (tmp_path / "Deals" / "D.md").write_text('---\nparent: "[[Areas/Pipeline|P]]"\n---\n', encoding="utf-8")
    notes, _ = read_vault(tmp_path)
    assert parent_target(notes["Deals/D"]) == "Areas/Pipeline"


def test_read_vault(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "x.md").write_text("ignored", encoding="utf-8")
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "BMW Dataset.xlsx").write_bytes(b"x")
    (tmp_path / "Components").mkdir()
    (tmp_path / "Components" / "Badges & logos.md").write_bytes(b"---\r\nid: c\r\n---\r\nBody\r\n")
    (tmp_path / "Broken.md").write_text("---\nid: [x\n---\n", encoding="utf-8")
    notes, files = read_vault(tmp_path)
    assert set(notes) == {"Components/Badges & logos", "Broken"}
    assert "Data/BMW Dataset.xlsx" in files and ".git/x.md" not in files
    note = notes["Components/Badges & logos"]
    assert note.front == {"id": "c"} and note.newline == "\r\n" and note.error is None
    assert notes["Broken"].error


def test_add_keys_keeps_crlf_and_existing_lines(tmp_path):
    (tmp_path / "Brands").mkdir()
    (tmp_path / "Brands" / "BMW.md").write_bytes(b"---\r\nsource: dataset\r\nmanaged_by: torque\r\n---\r\nBody\r\n")
    notes, _ = read_vault(tmp_path)
    text = add_keys(notes["Brands/BMW"], {"id": "brands-bmw", "source": "other", "parent": "[[Areas/Brand]]"})
    assert text == ('---\r\nsource: dataset\r\nmanaged_by: torque\r\nid: brands-bmw\r\n'
                    'parent: "[[Areas/Brand]]"\r\n---\r\nBody\r\n')
    assert text.index("managed_by: torque") < 300


def test_add_keys_to_note_without_front_matter(tmp_path):
    (tmp_path / "Topics").mkdir()
    (tmp_path / "Topics" / "T.md").write_text("# T\n", encoding="utf-8", newline="\n")
    notes, _ = read_vault(tmp_path)
    assert add_keys(notes["Topics/T"], {"id": "topics-t"}) == "---\nid: topics-t\n---\n# T\n"


def test_slug():
    assert slug("Feature Classes-Hood, open / close") == "feature-classes-hood-open-close"
    assert slug("Ümlaut Ärger") == "umlaut-arger"


def test_invalid_date_is_a_front_matter_error():
    with pytest.raises(FrontMatterError):
        split("---\nwhen: 2026-02-30\n---\nBody\n")


def test_read_vault_survives_bad_notes(tmp_path):
    (tmp_path / "Topics").mkdir()
    (tmp_path / "Topics" / "Latin1.md").write_bytes(b"---\nid: x\n---\n\xe4\xf6\xfc\n")
    (tmp_path / "Topics" / "Date.md").write_text("---\nwhen: 2026-02-30\n---\n", encoding="utf-8")
    notes, _ = read_vault(tmp_path)
    assert notes["Topics/Latin1"].error and notes["Topics/Date"].error


def test_read_vault_skips_symlinks_and_rowboat_owned_files(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    vault = tmp_path / "knowledge"
    (vault / "Topics").mkdir(parents=True)
    (vault / "Agent Notes").mkdir()
    for name in ("inbox", "user", "preferences"):
        (vault / "Agent Notes" / f"{name}.md").write_text("- a memory line\n", encoding="utf-8")
    try:
        (vault / "Topics" / "Link.md").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not available")
    notes, files = read_vault(vault)
    assert notes == {} and files == set()
