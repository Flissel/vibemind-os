"""Check a vault against the schema. Errors break the hierarchy; warnings are worth a look."""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from .issues import Issue
from .model import Schema, TypeDef
from .notes import DATE, Note, link_value, links, parent_target, read_vault
from .placement import Placement, resolve
from .profile import Profile

FIELD = re.compile(r"^\*\*(Email|Domain):\*\*\s*(\S+)", re.M)
BASE_KEYS = ("id", "type", "domain", "level")


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == "error"]


def lint(schema: Schema, vault: Path, profile: Profile | None = None) -> list[Issue]:
    """Without a profile every note needs schema front matter (the EHL vault). With a profile the resolved
    view is checked: notes placed by a folder rule need no front matter, only a valid parent."""
    notes, files = read_vault(vault)
    if profile is None:
        issues: list[Issue] = []
        ids: dict[str, str] = {}
        for note in notes.values():
            issues.extend(_check_note(schema, note, notes, files, ids))
        issues.extend(_check_cycles(notes))
        issues.extend(_check_duplicates(notes))
        return sorted(issues, key=lambda i: (i.path, i.code, i.message))
    res = resolve(schema, profile, notes)
    placed = res.placements

    def type_of(path: str) -> str | None:
        return placed[path].type if path in placed else schema.type_of_path(path)

    issues = list(res.issues)
    ids = {}
    for path, pl in placed.items():
        note = notes[path]
        if pl.source == "front":
            issues.extend(_check_note(schema, note, notes, files, ids, pl.type, type_of))
        elif note.error:
            issues.append(Issue("error", path, "bad-yaml", note.error))
        else:
            issues.extend(_check_placed_parent(schema, path, pl, placed))
    issues.extend(_cycles({p: pl.parent for p, pl in placed.items() if pl.parent}))
    issues.extend(_check_duplicates({p: notes[p] for p in placed}))
    return sorted(issues, key=lambda i: (i.path, i.code, i.message))


def _check_placed_parent(schema: Schema, path: str, pl: Placement, placed: dict[str, Placement]) -> list[Issue]:
    parent = pl.parent
    if parent not in placed:
        return [Issue("error", path, "parent-missing", f"parent [[{parent}]] does not exist")]
    td, ptype = schema.types[pl.type], placed[parent].type
    if ptype not in td.parent:
        return [Issue("error", path, "parent-type", f"parent [[{parent}]] is a {ptype}; allowed: {', '.join(td.parent)}")]
    if placed[parent].level > pl.level:
        return [Issue("error", path, "parent-level", f"parent [[{parent}]] is on a lower level than the note")]
    return []


def _expected_domain(schema: Schema, tname: str, path: str) -> str | None:
    name = path.partition("/")[2]
    if tname == "Domains":
        return schema.domain_key(name)
    if tname == "Areas":
        return schema.areas.get(name)
    return schema.types[tname].domain


def _check_note(schema: Schema, note: Note, notes: dict, files: set, ids: dict,
                tname: str | None = None, type_of: Callable[[str], str | None] | None = None) -> list[Issue]:
    out: list[Issue] = []
    type_of = type_of or schema.type_of_path

    def add(severity: str, code: str, message: str) -> None:
        out.append(Issue(severity, note.path, code, message))

    tname = tname or schema.type_of_path(note.path)
    if tname is None:
        add("error", "unknown-type", "the folder of this note is not a note type in the schema")
        return out
    if note.error:
        add("error", "bad-yaml", note.error)
        return out
    if note.front is None:
        add("error", "no-front-matter", "the note has no front matter (run: ehl-schema migrate)")
        return out
    front = note.front
    for key, value in front.items():
        if isinstance(value, dict) or (isinstance(value, list) and not all(isinstance(v, str) for v in value)):
            add("error", "nested", f"{key!r} must be flat: a value or a list of strings")
    needed = BASE_KEYS + (() if tname == "Company" else ("parent",))
    for key in needed:
        if front.get(key) in (None, ""):
            add("error", "missing-key", f"{key!r} is missing")
    if out:
        return out
    td = schema.types[tname]
    if front["type"] != tname:
        add("error", "type-mismatch", f"type is {front['type']!r}, but the folder says {tname!r}")
    if tname != "Company":
        expected = _expected_domain(schema, tname, note.path)
        if expected is None:
            add("error", "not-in-schema", f"{note.path!r} is not a domain or area of the schema")
        elif front["domain"] != expected:
            add("error", "domain-mismatch", f"domain is {front['domain']!r}, expected {expected!r}")
    if front["level"] != td.level:
        add("error", "level-mismatch", f"level is {front['level']!r}, expected {td.level}")
    first = ids.setdefault(str(front["id"]), note.path)
    if first != note.path:
        add("error", "duplicate-id", f"id {front['id']!r} is also used by {first!r}")
    if tname != "Company":
        out.extend(_check_parent(schema, note, td, notes, type_of))
    out.extend(_check_relations(schema, note, td, notes, files, type_of))
    if td.statuses and "status" in front and front["status"] not in td.statuses:
        add("warning", "status-value", f"status {front['status']!r} is not one of {', '.join(td.statuses)}")
    for target in links(note.body):
        if target not in notes and target not in files:
            add("warning", "broken-link", f"[[{target}]] does not exist")
    return out


def _check_parent(schema: Schema, note: Note, td: TypeDef, notes: dict,
                  type_of: Callable[[str], str | None]) -> list[Issue]:
    target = parent_target(note)
    if target is None:
        return [Issue("error", note.path, "parent-format", 'parent must be one quoted link like "[[Areas/Pipeline]]"')]
    if target not in notes:
        return [Issue("error", note.path, "parent-missing", f"parent [[{target}]] does not exist")]
    ptype = type_of(target)
    if ptype not in td.parent:
        return [Issue("error", note.path, "parent-type",
                      f"parent [[{target}]] is a {ptype}; allowed: {', '.join(td.parent)}")]
    if schema.types[ptype].level > td.level:
        return [Issue("error", note.path, "parent-level", f"parent [[{target}]] is on a lower level than the note")]
    return []


def _check_relations(schema: Schema, note: Note, td: TypeDef, notes: dict, files: set,
                     type_of: Callable[[str], str | None]) -> list[Issue]:
    out: list[Issue] = []
    front = note.front or {}
    for name, rel in schema.relations.items():
        if name not in front:
            if name in td.required:
                out.append(Issue("error", note.path, "required-relation", f"{name!r} is required"))
            continue
        value = front[name]
        if rel.kind == "date":
            if not isinstance(value, str) or not DATE.fullmatch(value):
                out.append(Issue("error", note.path, "relation-format", f"{name!r} must be a date YYYY-MM-DD"))
            continue
        targets = [link_value(v) for v in value] if isinstance(value, list) else [None]
        if None in targets:
            out.append(Issue("error", note.path, "relation-format",
                             f'{name!r} must be a list of quoted links like ["[[Folder/Name]]"]'))
            continue
        if not targets and name in td.required:
            out.append(Issue("error", note.path, "required-relation", f"{name!r} must not be empty"))
        for target in targets:
            if target not in notes and target not in files:
                out.append(Issue("error", note.path, "relation-missing", f"{name}: [[{target}]] does not exist"))
            elif rel.targets is not None and type_of(target) not in rel.targets:
                out.append(Issue("error", note.path, "relation-target",
                                 f"{name}: [[{target}]] must be a {' or '.join(rel.targets)}"))
    return out


def _check_cycles(notes: dict[str, Note]) -> list[Issue]:
    return _cycles({p: t for p, n in notes.items() if (t := parent_target(n)) is not None})


def _cycles(parent: dict[str, str]) -> list[Issue]:
    out, reported = [], set()
    for start in parent:
        seen, node = [], start
        while node in parent and node not in seen:
            seen.append(node)
            node = parent[node]
        if node in seen:
            cycle = seen[seen.index(node):]
            key = frozenset(cycle)
            if key not in reported:
                reported.add(key)
                out.append(Issue("error", min(cycle), "cycle", "parent chain loops: " + " -> ".join(cycle + [node])))
    return out


def _check_duplicates(notes: dict[str, Note]) -> list[Issue]:
    groups: dict[tuple[str, str], list[str]] = {}
    for path, note in notes.items():
        folder, _, name = path.partition("/")
        if folder not in ("People", "Organizations") or not name:
            continue
        keys = {("name", folder + ":" + re.sub(r"[^a-z0-9]", "", name.lower()))}
        for kind, value in FIELD.findall(note.body):
            value = value.strip().lower()
            if value and not value.startswith("{"):
                keys.add((kind.lower(), value))
        for key in keys:
            groups.setdefault(key, []).append(path)
    out = []
    for (kind, _), paths in groups.items():
        if len(paths) > 1:
            for path in paths:
                others = ", ".join(p for p in paths if p != path)
                out.append(Issue("warning", path, "possible-duplicate", f"same {kind} as {others}"))
    return out
