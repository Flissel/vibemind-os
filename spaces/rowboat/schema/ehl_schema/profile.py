"""Vault profiles: company name, excluded paths and folder rules (path pattern -> note type).

Pattern syntax (paths are vault-relative, without ".md"): `*` matches inside one segment, `**` matches one or
more whole segments, `{name}` captures one whole segment for use in a rule's `parent`. Case-sensitive.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .model import DEFAULT_SCHEMA, STRUCTURE_TYPES, Schema, SchemaError

PROFILES_DIR = DEFAULT_SCHEMA.parent / "profiles"
CAPTURE = re.compile(r"\{(\w+)\}")


def compile_pattern(pattern: str) -> re.Pattern[str]:
    parts = []
    for part in pattern.split("/"):
        whole = CAPTURE.fullmatch(part)
        if part == "**":
            parts.append(r"[^/]+(?:/[^/]+)*")
        elif whole:
            parts.append(f"(?P<{whole.group(1)}>[^/]+)")
        elif "{" in part or "}" in part or "**" in part or not part:
            raise SchemaError(f"bad path pattern {pattern!r}: {part!r}")
        else:
            parts.append("[^/]*".join(re.escape(s) for s in part.split("*")))
    try:
        return re.compile("/".join(parts))
    except re.error as err:  # e.g. the same capture twice
        raise SchemaError(f"bad path pattern {pattern!r}: {err}") from err


@dataclass(frozen=True)
class Rule:
    match: str
    type: str
    parent: str | None
    regex: re.Pattern[str]


@dataclass(frozen=True)
class Profile:
    company: str
    rules: tuple[Rule, ...] = ()
    exclude: tuple[re.Pattern[str], ...] = ()
    name: str = "default"
    exclude_raw: tuple[str, ...] = ()   # the exclude patterns as written, for the rules note

    @property
    def has_rules(self) -> bool:
        return bool(self.rules or self.exclude)

    def excludes(self, path: str) -> bool:
        return any(p.fullmatch(path) for p in self.exclude)

    def match(self, path: str) -> tuple[Rule, dict[str, str]] | None:
        for rule in self.rules:
            m = rule.regex.fullmatch(path)
            if m:
                return rule, m.groupdict()
        return None


def default_profile(company: str) -> Profile:
    """Every note lives in the folder of its type (the EHL vault)."""
    return Profile(company)


def _rule(raw: object, schema: Schema) -> Rule:
    if not isinstance(raw, dict) or not isinstance(raw.get("match"), str) or not isinstance(raw.get("type"), str):
        raise SchemaError(f"rule {raw!r}: needs 'match' and 'type'")
    tname, parent = raw["type"], raw.get("parent")
    if tname not in schema.types or tname in STRUCTURE_TYPES:
        raise SchemaError(f"rule {raw['match']!r}: {tname!r} is not a note type of the schema")
    regex = compile_pattern(raw["match"])
    if parent is not None:
        if not isinstance(parent, str):
            raise SchemaError(f"rule {raw['match']!r}: parent must be a path")
        unknown = set(CAPTURE.findall(parent)) - set(regex.groupindex)
        if unknown:
            raise SchemaError(f"rule {raw['match']!r}: parent uses {sorted(unknown)} not captured in match")
    return Rule(raw["match"], tname, parent, regex)


def load_profile(ref: str, schema: Schema) -> Profile:
    path = Path(ref) if ref.endswith((".yaml", ".yml")) else PROFILES_DIR / f"{ref}.yaml"
    if not path.is_file():
        raise SchemaError(f"no profile {ref!r} (looked for {path})")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("company"), str) or not raw["company"].strip():
        raise SchemaError(f"{path}: a profile needs 'company'")
    rules = tuple(_rule(r, schema) for r in raw.get("rules") or [])
    exclude_raw = tuple(str(p) for p in raw.get("exclude") or [])
    exclude = tuple(compile_pattern(p) for p in exclude_raw)
    return Profile(raw["company"].strip(), rules, exclude, path.stem, exclude_raw)
