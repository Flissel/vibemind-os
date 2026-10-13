"""Load the vault schema (schema/vault-schema.yaml) and check that it is consistent."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger("ehl_schema.model")

DEFAULT_SCHEMA = Path(__file__).resolve().parents[1] / "vault-schema.yaml"
STRUCTURE_TYPES = ("Company", "Domains", "Areas")


class SchemaError(ValueError):
    """The schema file is malformed or contradicts itself."""


@dataclass(frozen=True)
class TypeDef:
    name: str
    domain: str                      # domain key; "*" for the generated structure types
    level: int                       # 0 Company, 1 Domains, 2 Areas, 3 entity, 4 detail
    parent: tuple[str, ...]          # allowed parent types, in order of preference
    default_parent: str | None = None  # area name used when no linked note fits
    builtin: bool = False            # a Rowboat type: keep its template body
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    template: str | None = None


@dataclass(frozen=True)
class RelationDef:
    name: str
    targets: tuple[str, ...] | None  # None = any note type
    kind: str = "link"               # "link" or "date"


@dataclass(frozen=True)
class Schema:
    version: int
    domains: dict[str, str]          # key -> title
    cross_cutting: frozenset[str]
    areas: dict[str, str]            # area name -> domain key
    relations: dict[str, RelationDef]
    types: dict[str, TypeDef]

    def domain_note(self, key: str) -> str:
        return f"Domains/{self.domains[key]}"

    def area_note(self, name: str) -> str:
        return f"Areas/{name}"

    def domain_key(self, title: str) -> str | None:
        return next((k for k, t in self.domains.items() if t == title), None)

    def type_of_path(self, path: str) -> str | None:
        """Schema type of a vault-relative note path without '.md', or None if the schema does not know it."""
        if path == "Company":
            return "Company"
        folder, sep, _ = path.partition("/")
        return folder if sep and folder in self.types else None


def _structure_types() -> dict[str, TypeDef]:
    return {
        "Company": TypeDef("Company", "*", 0, ()),
        "Domains": TypeDef("Domains", "*", 1, ("Company",)),
        "Areas": TypeDef("Areas", "*", 2, ("Domains",)),
    }


def load_schema(path: Path = DEFAULT_SCHEMA) -> Schema:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SchemaError(f"{path}: the schema must be a mapping")
    try:
        domains = {str(k): str(v["title"]) for k, v in raw["domains"].items()}
        cross = frozenset(str(k) for k, v in raw["domains"].items() if v.get("cross_cutting"))
        areas = {str(k): str(v) for k, v in raw["areas"].items()}
        relations = {}
        for name, spec in raw["relations"].items():
            spec = spec or {}
            targets = spec.get("targets")
            relations[str(name)] = RelationDef(
                str(name), None if targets in (None, "any") else tuple(targets), spec.get("kind", "link"))
        types = _structure_types()
        for name, spec in raw["types"].items():
            if name in types:
                raise SchemaError(f"type {name!r} is reserved for the generated structure notes")
            types[str(name)] = TypeDef(
                name=str(name), domain=str(spec["domain"]), level=int(spec["level"]),
                parent=tuple(spec["parent"]), default_parent=spec.get("default_parent"),
                builtin=bool(spec.get("builtin", False)), required=tuple(spec.get("required", ())),
                optional=tuple(spec.get("optional", ())), statuses=tuple(spec.get("statuses", ())),
                template=spec.get("template"))
        schema = Schema(int(raw["schema_version"]), domains, cross, areas, relations, types)
    except (KeyError, TypeError, AttributeError) as err:
        raise SchemaError(f"{path}: malformed schema ({err!r})") from err
    _check(schema)
    return schema


def _check(schema: Schema) -> None:
    errors = []
    for area, domain in schema.areas.items():
        if domain not in schema.domains:
            errors.append(f"area {area!r}: unknown domain {domain!r}")
    for key in schema.domains:
        if key not in schema.areas.values():
            errors.append(f"domain {key!r} has no area")
    for rel in schema.relations.values():
        if rel.kind not in ("link", "date"):
            errors.append(f"relation {rel.name!r}: kind must be link or date")
        for target in rel.targets or ():
            if target not in schema.types:
                errors.append(f"relation {rel.name!r}: unknown target type {target!r}")
    for name, td in schema.types.items():
        if name in STRUCTURE_TYPES:
            continue
        if td.domain == "*":
            if td.parent != ("Areas",):
                errors.append(f"type {name!r}: an index type (domain '*') must have parent [Areas] only")
        elif td.domain not in schema.domains:
            errors.append(f"type {name!r}: unknown domain {td.domain!r}")
        if td.level not in (3, 4):
            errors.append(f"type {name!r}: level must be 3 or 4")
        if not td.parent:
            errors.append(f"type {name!r}: parent must list at least one type")
        for parent in td.parent:
            if parent not in schema.types or parent in ("Company", "Domains"):
                errors.append(f"type {name!r}: {parent!r} is not an allowed parent")
        if td.default_parent is not None and td.default_parent not in schema.areas:
            errors.append(f"type {name!r}: unknown default_parent {td.default_parent!r}")
        for rel in td.required + td.optional:
            if rel not in schema.relations:
                errors.append(f"type {name!r}: unknown relation {rel!r}")
    if errors:
        raise SchemaError("\n".join(errors))
