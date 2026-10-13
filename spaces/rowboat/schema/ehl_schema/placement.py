"""Where every note sits in the hierarchy: from its own front matter, a profile rule, the default rule
(first folder = type) or the fallback (Topics). Reads notes only; never writes."""
from __future__ import annotations

from dataclasses import dataclass, field

from .issues import Issue
from .model import STRUCTURE_TYPES, Schema
from .notes import Note, parent_target
from .profile import Profile

FALLBACK_TYPE = "Topics"


@dataclass(frozen=True)
class Placement:
    type: str
    level: int
    domain: str
    parent: str | None   # vault-relative path without ".md"; None only for the Company note
    source: str          # "front" | "rule" | "default" | "fallback"


@dataclass
class Resolution:
    placements: dict[str, Placement] = field(default_factory=dict)
    excluded: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def _from_front(schema: Schema, note: Note) -> Placement | None:
    front = note.front or {}
    tname = front.get("type")
    if not isinstance(tname, str) or tname not in schema.types:
        return None
    td = schema.types[tname]
    level = front.get("level") if isinstance(front.get("level"), int) else td.level
    domain = str(front.get("domain") or td.domain)
    parent = parent_target(note)
    if parent is None and tname != "Company":  # front matter without parent: the type's default area
        parent = schema.area_note(td.default_parent or FALLBACK_TYPE)
    return Placement(tname, level, domain, parent, "front")


def _area_domain(schema: Schema, parent: str) -> str:
    folder, _, name = parent.partition("/")
    return schema.areas.get(name, "knowledge") if folder == "Areas" else "knowledge"


def resolve(schema: Schema, profile: Profile, notes: dict[str, Note]) -> Resolution:
    res = Resolution()
    pending: dict[str, tuple[str, str | None, str]] = {}
    for path in sorted(notes):
        if profile.excludes(path):
            res.excluded.append(path)
            continue
        placed = _from_front(schema, notes[path])
        if placed is not None:
            res.placements[path] = placed
            continue
        hit = profile.match(path)
        folder, sep, _ = path.partition("/")
        if hit is not None:
            rule, captures = hit
            parent = rule.parent.format_map(captures) if rule.parent else None
            pending[path] = (rule.type, parent, "rule")
        elif sep and folder in schema.types and folder not in STRUCTURE_TYPES:
            pending[path] = (folder, None, "default")
        else:
            pending[path] = (FALLBACK_TYPE, None, "fallback")
            res.issues.append(Issue("warning", path, "no-rule",
                                    f"no folder rule for this path; placed under Areas/{FALLBACK_TYPE}"))
    for path, (tname, parent, source) in pending.items():
        td = schema.types[tname]
        if parent is not None and (parent not in notes or profile.excludes(parent)):
            res.issues.append(Issue("warning", path, "rule-parent-missing",
                                    f"rule parent [[{parent}]] does not exist; placed under its default area"))
            parent = None
        if parent is None:
            parent = schema.area_note(td.default_parent or FALLBACK_TYPE)
        domain = _area_domain(schema, parent) if td.domain == "*" else td.domain
        res.placements[path] = Placement(tname, td.level, domain, parent, source)
    return res
