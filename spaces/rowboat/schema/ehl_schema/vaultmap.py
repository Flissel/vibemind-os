"""config/vault-map.json: the resolved hierarchy of every note, read by Rowboat's brain graph as a fallback for
notes without hierarchy front matter. Spec: docs/superpowers/specs/2026-10-13-vault-folder-rules-design.md §6."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .build import write_structure
from .issues import Issue
from .model import Schema
from .notes import read_vault
from .placement import Resolution, resolve
from .profile import Profile

log = logging.getLogger("ehl_schema.vaultmap")

MAP_FILE = Path("config") / "vault-map.json"
MAP_VERSION = 1


@dataclass
class MapResult:
    structure: int
    notes: int
    excluded: int
    issues: list[Issue]
    written: bool


def map_document(company: str, res: Resolution) -> dict:
    notes = {path: {"type": p.type, "level": p.level, "domain": p.domain, "parent": p.parent}
             for path, p in sorted(res.placements.items())}
    return {"version": MAP_VERSION, "company": company,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "notes": notes}


def _same(old_text: str, doc: dict) -> bool:
    try:
        old = json.loads(old_text)
    except json.JSONDecodeError:
        return False
    if not isinstance(old, dict):
        return False
    return {k: v for k, v in old.items() if k != "generated_at"} == {k: v for k, v in doc.items() if k != "generated_at"}


def write_map(workdir: Path, doc: dict) -> bool:
    file = workdir / MAP_FILE
    if file.exists() and _same(file.read_text(encoding="utf-8"), doc):
        return False
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return True


def run_map(schema: Schema, profile: Profile, workdir: Path) -> MapResult:
    vault = workdir / "knowledge"
    structure = write_structure(schema, vault, profile.company, profile)
    notes, _ = read_vault(vault)
    res = resolve(schema, profile, notes)
    written = write_map(workdir, map_document(profile.company, res))
    return MapResult(structure, len(res.placements), len(res.excluded), res.issues, written)
