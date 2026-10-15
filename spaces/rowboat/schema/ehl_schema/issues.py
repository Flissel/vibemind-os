"""One finding about a note: lint errors and warnings, and the resolver's warnings."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Issue:
    severity: str   # "error" | "warning"
    path: str
    code: str
    message: str
