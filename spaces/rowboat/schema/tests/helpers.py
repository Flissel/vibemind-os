"""Helpers for the schema tests: write notes and small valid vaults."""
from pathlib import Path

from ehl_schema.notes import note_text


def write_note(vault: Path, path: str, front: dict | None, body: str = "") -> Path:
    file = vault / f"{path}.md"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(note_text(front, body) if front is not None else body, encoding="utf-8", newline="\n")
    return file


def structure(schema, key: str, area: str) -> dict:
    """front matter of the domain note and the area note a test needs."""
    return {
        schema.domain_note(key): {"id": f"domain-{key}", "type": "Domains", "domain": key, "level": 1,
                                  "parent": "[[Company]]"},
        schema.area_note(area): {"id": f"area-{area.lower()}", "type": "Areas", "domain": key, "level": 2,
                                 "parent": f"[[{schema.domain_note(key)}]]"},
    }


def valid_vault(tmp_path: Path, schema) -> Path:
    vault = tmp_path / "knowledge"
    write_note(vault, "Company", {"id": "company", "type": "Company", "domain": "company", "level": 0})
    notes = {**structure(schema, "sales", "Pipeline"), **structure(schema, "register", "Companies")}
    notes[schema.area_note("Contacts")] = {"id": "area-contacts", "type": "Areas", "domain": "register",
                                           "level": 2, "parent": f"[[{schema.domain_note('register')}]]"}
    for path, front in notes.items():
        write_note(vault, path, front)
    write_note(vault, "Organizations/Acme", {"id": "org-acme", "type": "Organizations", "domain": "register",
                                             "level": 3, "parent": "[[Areas/Companies]]"})
    write_note(vault, "People/Felix Baumann", {"id": "person-felix", "type": "People", "domain": "register",
                                               "level": 3, "parent": "[[Areas/Contacts]]",
                                               "works_at": ["[[Organizations/Acme]]"]})
    write_note(vault, "Deals/Acme deal", {"id": "deal-acme", "type": "Deals", "domain": "sales", "level": 3,
                                          "parent": "[[Areas/Pipeline]]", "status": "proposal",
                                          "customer": ["[[Organizations/Acme]]"],
                                          "owner": ["[[People/Felix Baumann]]"], "when": "2026-10-20"},
               "# Acme deal\n\nWith [[Organizations/Acme|Acme]].\n")
    return vault


CURATOR_FRONT = ("---\ntyp: agent\nid: 0000\ntitel: {name}\nfakten:\n- schluessel: zustand\n  wert: Running\n"
                 "  beleg: 1\n---\n# {name}\n")


def write_raw(vault: Path, path: str, text: str) -> None:
    file = vault / f"{path}.md"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8", newline="\n")


def vibemind_like_vault(tmp_path: Path, schema) -> Path:
    """A small vault laid out like the VibeMind vault. All names and texts are made up."""
    from ehl_schema.build import write_structure

    vault = tmp_path / "knowledge"
    write_structure(schema, vault, "TestCo")
    write_raw(vault, "Agents/alpha-bot (abc123)", CURATOR_FRONT.format(name="alpha-bot"))
    write_raw(vault, "Agents/Veraltet/old-bot (def456)", CURATOR_FRONT.format(name="old-bot"))
    write_raw(vault, "Bubbles/Green Idea — Beta (fa8d41)", CURATOR_FRONT.format(name="Green Idea"))
    write_raw(vault, "Hubs/Agents", "# Agents\n\n- [[alpha-bot (abc123)]]\n")
    write_raw(vault, "Hubs/Misc", "# Misc\n")
    write_raw(vault, "Projects/Demo Root", "# Demo Root\n")
    write_raw(vault, "Projects/TestCo - Shop/_overview", "---\nidea_id: x1\ntitle: null\ntags: []\n---\n# Shop\n")
    write_raw(vault, "Projects/TestCo - Shop/Payment Flow", "# Payment Flow\n")
    write_raw(vault, "Projects/VibeMind - TODO Write tests (z. B. unit)/_overview", "# TODO\n")
    write_raw(vault, "Projects/VibeMind - TODO Write tests (z. B. unit)/Detail", "# detail\n")
    write_raw(vault, "Projects/TestCo - Orphan/Lonely", "# Lonely\n")   # project folder without _overview
    write_raw(vault, "companys/Acme Brand/Marke", "# Marke\n")
    write_raw(vault, "companys/Acme Brand/Markenhandbuch", "# Handbuch\n")
    write_raw(vault, "companys/Acme Brand/Marke-Verlauf/2026-01-01 v1", "# v1\n")
    write_raw(vault, "companys/Acme Brand/Agent-Notizen/2026-01-02 hint", "# hint\n")
    write_raw(vault, "vibemind-discourse/2026-01-01/04-07", "# Discourse\n")
    write_raw(vault, "Videos/Demo Clip", "# Clip\n")
    write_raw(vault, "System/PC (system)", CURATOR_FRONT.format(name="PC"))
    write_raw(vault, "Notes/Traction Metrics 2026-Q1", "# Traction\n")
    write_raw(vault, "Notes/Random", "# Random\n")
    write_raw(vault, "Investor Programs/Grant X/Questions", "# Questions\n")
    write_raw(vault, "Coding-Projects/tool-a", "---\ntype: coding-project\n---\n# tool-a\n")
    write_raw(vault, "Workflows/flow-a", "# flow\n")
    write_raw(vault, "Topics/AI", "# AI\n")
    write_raw(vault, "People/Jane Doe", "# Jane Doe\n")
    write_raw(vault, "People/Design Partners/Partner Co", "# Partner Co\n")
    write_raw(vault, "Strange Folder/thing", "# thing\n")
    write_raw(vault, "Diary/Secret", "# private\n")
    write_raw(vault, "Bewerbung/Firm/Letter", "# private\n")
    write_raw(vault, "Welcome", "# Welcome\n")
    return vault
