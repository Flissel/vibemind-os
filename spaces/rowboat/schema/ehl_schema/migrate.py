"""Give existing notes the schema's front matter keys. Proposes changes; writes only through apply()."""
from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .model import STRUCTURE_TYPES, Schema, TypeDef
from .notes import Note, add_keys, links, parent_target, read_vault, slug

log = logging.getLogger("ehl_schema.migrate")


@dataclass
class Report:
    changes: dict[str, tuple[str, str]] = field(default_factory=dict)  # path -> (old text, new text)
    unmapped: list[str] = field(default_factory=list)   # notes in folders the schema does not know
    unresolved: list[str] = field(default_factory=list)  # no parent found and no default area


def _neighbours(notes: dict[str, Note]) -> dict[str, set[str]]:
    out = {path: set() for path in notes}
    for path, note in notes.items():
        for target in links(note.front_text + "\n" + note.body):
            if target in notes and target != path:
                out[path].add(target)
                out[target].add(path)
    return out


def _creates_cycle(child: str, candidate: str, parents: dict[str, str]) -> bool:
    node, steps = candidate, 0
    while node is not None and steps <= len(parents):
        if node == child:
            return True
        node = parents.get(node)
        steps += 1
    return False


def _choose_parent(schema: Schema, td: TypeDef, path: str, notes: dict, near: dict, parents: dict) -> str | None:
    best = None
    for candidate in near[path]:
        ctype = schema.type_of_path(candidate)
        if ctype not in td.parent or schema.types[ctype].level > td.level:
            continue
        if ctype == td.name and len(near[candidate]) <= len(near[path]):
            continue
        if _creates_cycle(path, candidate, parents):
            continue
        rank = (td.parent.index(ctype), -len(near[candidate]), candidate)
        if best is None or rank < best[0]:
            best = (rank, candidate)
    if best is not None:
        return best[1]
    return schema.area_note(td.default_parent) if td.default_parent else None


def _unique_id(base: str, used: set[str]) -> str:
    candidate, n = base, 1
    while candidate in used:
        n += 1
        candidate = f"{base}-{n}"
    used.add(candidate)
    return candidate


def plan(schema: Schema, vault: Path) -> Report:
    notes, _ = read_vault(vault)
    near = _neighbours(notes)
    parents = {p: t for p, n in notes.items() if (t := parent_target(n)) is not None}
    used_ids = {str(n.front["id"]) for n in notes.values() if n.front and n.front.get("id")}
    report = Report()
    todo = []
    for path, note in notes.items():
        tname = schema.type_of_path(path)
        if tname is None or (tname not in STRUCTURE_TYPES and schema.types[tname].domain == "*"):
            report.unmapped.append(path)
        elif tname not in STRUCTURE_TYPES and note.error is None:
            todo.append((schema.types[tname].level, -len(near[path]), path))
    for _, _, path in sorted(todo):
        note, td = notes[path], schema.types[schema.type_of_path(path)]
        front = note.front or {}
        fields: dict = {}
        if not front.get("parent"):
            target = _choose_parent(schema, td, path, notes, near, parents)
            if target is None:
                report.unresolved.append(path)
                continue
            parents[path] = target
        if not front.get("id"):
            fields["id"] = _unique_id(slug(f"{td.name}-{path.partition('/')[2]}"), used_ids)
        fields.update({"type": td.name, "domain": td.domain, "level": td.level})
        if path in parents and not front.get("parent"):
            fields["parent"] = f"[[{parents[path]}]]"
        fields.update({"visibility": "team", "env": "prod", "schema_version": schema.version})
        missing = {k: v for k, v in fields.items() if k not in front}
        if missing:
            report.changes[path] = (note.raw, add_keys(note, missing))
    return report


def apply(report: Report, vault: Path) -> int:
    for path, (_, new) in report.changes.items():
        (vault / f"{path}.md").write_bytes(new.encode("utf-8"))
    return len(report.changes)


def diff(report: Report) -> str:
    parts = []
    for path, (old, new) in report.changes.items():
        parts.extend(difflib.unified_diff(old.replace("\r\n", "\n").splitlines(keepends=True),
                                          new.replace("\r\n", "\n").splitlines(keepends=True),
                                          f"a/{path}.md", f"b/{path}.md"))
    return "".join(parts)
