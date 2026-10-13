"""Generate what the schema implies: Company/Domains/Areas notes, the rules note and Rowboat's notes.json."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .model import STRUCTURE_TYPES, Schema, TypeDef
from .notes import note_text, slug, split
from .profile import Profile

log = logging.getLogger("ehl_schema.build")

MANAGED = "schema"
RULES_NOTE = "Agent Notes/Vault Schema"
RULES_MARK = "\n\nSchema rules ("
GENERIC_BODY = ("# {Title}\n\n## Summary\n{2-3 sentences.}\n\n## Key facts\n{Substantive facts only. Leave empty if "
                "none.}\n\n## Open items\n{Next steps only. Leave empty if none.}\n")


def _front(schema: Schema, **fields) -> dict:
    # the same keys migrate adds to every note, so build and migrate agree and re-runs settle
    return {**fields, "visibility": "team", "env": "prod", "managed_by": MANAGED, "schema_version": schema.version}


def structure_notes(schema: Schema, company: str, profile: Profile | None = None) -> dict[str, str]:
    notes: dict[str, str] = {}
    domain_links = "\n".join(f"- [[{schema.domain_note(k)}]]" for k in schema.domains)
    notes["Company"] = note_text(
        _front(schema, id="company", type="Company", domain="company", level=0),
        f"# {company}\n\n## Info\n**Type:** Company (L0)\n\n## Domains\n{domain_links}\n")
    for key, title in schema.domains.items():
        areas = "\n".join(f"- [[{schema.area_note(a)}]]" for a, d in schema.areas.items() if d == key)
        notes[schema.domain_note(key)] = note_text(
            _front(schema, id=f"domain-{key}", type="Domains", domain=key, level=1, parent="[[Company]]"),
            f"# {title}\n\n## Info\n**Type:** Domain (L1)\n**Key:** {key}\n\n## Areas\n{areas}\n")
    for area, key in schema.areas.items():
        types = ", ".join(n for n, td in schema.types.items() if td.default_parent == area) or "none"
        notes[schema.area_note(area)] = note_text(
            _front(schema, id=f"area-{slug(area)}", type="Areas", domain=key, level=2,
                   parent=f"[[{schema.domain_note(key)}]]"),
            f"# {area}\n\n## Info\n**Type:** Area (L2)\n**Domain:** [[{schema.domain_note(key)}]]\n"
            f"**Note types placed here by default:** {types}\n")
    td = schema.types["Agent Notes"]
    notes[RULES_NOTE] = note_text(
        _front(schema, id="agent-notes-vault-schema", type="Agent Notes", domain=td.domain, level=td.level,
               parent=f"[[{schema.area_note(td.default_parent)}]]"),
        rules_body(schema, profile))
    return notes


def rules_body(schema: Schema, profile: Profile | None = None) -> str:
    lines = [f"# Vault schema (v{schema.version})", "",
             "Generated from schema/vault-schema.yaml by `ehl-schema build`. Do not edit; change the schema file.", "",
             "## Front matter", "",
             "Every note starts with flat YAML front matter. Required keys: id, type, domain, level, parent.",
             "- `id`: a stable slug; never change it. `type`: the folder name. `level`: from the table below.",
             '- `parent`: exactly one quoted link, for example `parent: "[[Areas/Pipeline]]"`.',
             '- Relations are lists of quoted links with the full path, for example `about: ["[[Areas/Pipeline]]"]`.',
             "- Never nest keys. Optional: status, visibility (team | restricted), env (prod | test), when (YYYY-MM-DD).",
             "", "## Domains and areas", "", "| Domain | Key | Areas |", "|---|---|---|"]
    for key, title in schema.domains.items():
        areas = ", ".join(a for a, d in schema.areas.items() if d == key)
        lines.append(f"| {title} | {key} | {areas} |")
    lines += ["", "## Note types", "",
              "| Type (folder) | Domain | Level | Parent (in order) | Default area | Required | Optional |",
              "|---|---|---|---|---|---|---|"]
    for name, td in schema.types.items():
        if name in STRUCTURE_TYPES:
            continue
        domain = "from its parent area" if td.domain == "*" else td.domain
        lines.append(f"| {name} | {domain} | {td.level} | {', '.join(td.parent)} | {td.default_parent or ''} | "
                     f"{', '.join(td.required) or '–'} | {', '.join(td.optional) or '–'} |")
    lines += ["", "## Relations", "", "| Key | Points to |", "|---|---|"]
    for name, rel in schema.relations.items():
        target = "a date" if rel.kind == "date" else ("any note" if rel.targets is None else ", ".join(rel.targets))
        lines.append(f"| {name} | {target} |")
    if profile is not None and profile.has_rules:
        lines += ["", "## Folders in this vault", "",
                  f"This vault ({profile.company}) keeps its own folders. A new note goes into the folder whose rule "
                  "gives the type you need; the first matching rule wins. `*` is any text inside one folder name, "
                  "`**` any sub-path, `{x}` one folder name.", "",
                  "| Path pattern | Type | Parent |", "|---|---|---|"]
        for rule in profile.rules:
            lines.append(f"| `{rule.match}` | {rule.type} | {rule.parent or 'default area of the type'} |")
        lines.append("| any other `<Type>/…` | the folder name | default area of the type |")
        if profile.exclude_raw:
            lines += ["", "Private, not part of the team graph: "
                      + ", ".join(f"`{p}`" for p in profile.exclude_raw) + "."]
    return "\n".join(lines) + "\n"


def write_structure(schema: Schema, vault: Path, company: str, profile: Profile | None = None) -> int:
    written = 0
    for path, text in structure_notes(schema, company, profile).items():
        file = vault / f"{path}.md"
        if file.exists():
            old = file.read_text(encoding="utf-8")
            if old == text:
                continue
            front, _, _ = split(old)
            if (front or {}).get("managed_by") != MANAGED:
                log.warning("%s exists and is not managed by the schema; left as it is", file)
                continue
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8", newline="\n")
        written += 1
    return written


def _parent_hint(td: TypeDef) -> str:
    if td.parent == ("Areas",):
        return f"Areas/{td.default_parent}"
    options = ", ".join(f"{p}/…" for p in td.parent if p != "Areas")
    return f"one of: {options}" + (f" (else Areas/{td.default_parent})" if td.default_parent else "")


def _skeleton(schema: Schema, td: TypeDef) -> str:
    lines = ["---", "id: {slug of the title}", f"type: {td.name}", f"domain: {td.domain}", f"level: {td.level}",
             f'parent: "[[{{{_parent_hint(td)}}}]]"']
    for name in td.required:
        rel = schema.relations[name]
        if rel.kind == "date":
            lines.append(f"{name}: {{YYYY-MM-DD}}")
        else:
            target = rel.targets[0] if rel.targets else "Folder"
            lines.append(f'{name}: ["[[{{{target}/…}}]]"]')
    lines += [f"schema_version: {schema.version}", "---", ""]
    return "\n".join(lines)


def _guide(schema: Schema, td: TypeDef) -> str:
    default = f" (else [[Areas/{td.default_parent}]])" if td.default_parent else ""
    return (f"v{schema.version}): flat YAML front matter. type: {td.name}; domain: {td.domain}; level: {td.level}; "
            f"id: a stable slug, never change it. parent: exactly one quoted link to a note of type "
            f"{', '.join(td.parent)}{default}. Required relations: {', '.join(td.required) or 'none'}. "
            f"Optional relations: {', '.join(td.optional) or 'none'}. Relation values are lists of quoted "
            f'full-path links like ["[[Organizations/Acme]]"]. Status: {", ".join(td.statuses) or "free text"}.')


def _strip_front(template: str) -> str:
    """The template body without a leading --- block. Text-based: template placeholders are not always YAML."""
    t = template.replace("\r\n", "\n")
    if t.startswith("---\n"):
        end = t.find("\n---\n", 3)
        if end != -1:
            return t[end + 5:]
    return t


def notes_json(schema: Schema, existing: list[dict]) -> list[dict]:
    by_type = {e["type"]: dict(e) for e in existing if isinstance(e, dict) and "type" in e}
    order = [e["type"] for e in existing if isinstance(e, dict) and "type" in e]
    for name, td in schema.types.items():
        if name in STRUCTURE_TYPES or td.domain == "*" or (td.builtin and name not in by_type):
            continue
        entry = by_type.get(name, {"type": name, "folder": name, "template": "", "extractionGuide": ""})
        if name not in by_type:
            order.append(name)
        template = entry.get("template") or ""
        if not (td.builtin and not template.strip()):
            body = _strip_front(template)
            body = body if body.strip() else (td.template or GENERIC_BODY)
            entry["template"] = _skeleton(schema, td) + body
        guide = (entry.get("extractionGuide") or "").split(RULES_MARK)[0]
        entry["extractionGuide"] = guide + RULES_MARK + _guide(schema, td)
        by_type[name] = entry
    return [by_type[name] for name in order]


def build(schema: Schema, workdir: Path, company: str, profile: Profile | None = None) -> dict:
    written = write_structure(schema, workdir / "knowledge", company, profile)
    config = workdir / "config" / "notes.json"
    if not config.exists():
        log.warning("%s does not exist yet: start Rowboat once, then run build again", config)
        return {"notes": written, "notes_json": "missing"}
    old = config.read_text(encoding="utf-8")
    new = json.dumps(notes_json(schema, json.loads(old)), indent=2, ensure_ascii=False) + "\n"
    if new == old:
        return {"notes": written, "notes_json": "unchanged"}
    config.write_text(new, encoding="utf-8", newline="\n")
    return {"notes": written, "notes_json": "updated"}
