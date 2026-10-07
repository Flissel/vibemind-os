"""Static drift guard for the approved Marketing cockpit contract.

Moved here from the outer repo (``tests/architecture/test_marketing_cockpit_contract.py``)
on 2026-09-23, together with ``docs/COCKPIT_CONTRACT.md``, when the unmaintained
outer copy of ``spaces/marketing`` was removed. The guard now measures the copy
that actually runs.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


MARKETING_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = MARKETING_ROOT / "docs" / "COCKPIT_CONTRACT.md"
ENTRY_DOCUMENT_SAFETY_ANCHORS = {
    "AGENTS.md": (
        "**Never write `consent_given_at`",
        "## Gate graph",
        "MARKETING_PROPOSAL_API_KEY",
        "Never embed an OpenAI/Anthropic/Mailcow API key",
    ),
    "README.md": (
        "This space lives at `vibemind-os/spaces/marketing/`",
        "## Architectural decisions",
        "## Helper scripts",
    ),
    "STATUS.md": (
        "## Components live",
        "MARKETING_PROPOSAL_API_KEY",
        "## HTTP routes",
        "## Migrations",
    ),
}


def _test_definition_count() -> int:
    total = 0
    for path in MARKETING_ROOT.rglob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        total += sum(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
            for node in ast.walk(tree)
        )
    return total


def _bool_returning_render_tests() -> int:
    path = MARKETING_ROOT / "sync" / "tests" / "test_render_md.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sum(
        any(isinstance(child, ast.Return) and child.value is not None for child in ast.walk(node))
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    )


def test_marketing_cockpit_contract_is_evidence_classified_and_inventory_backed() -> None:
    assert CONTRACT_PATH.is_file(), "Marketing cockpit contract is missing"
    contract = CONTRACT_PATH.read_text(encoding="utf-8")

    migration_numbers = {
        path.name.split("_", 1)[0]
        for path in (MARKETING_ROOT / "db").glob("*.sql")
        if re.fullmatch(r"\d{3}", path.name.split("_", 1)[0])
    }
    assert migration_numbers == {f"{number:03d}" for number in range(1, 63)} - {"039"}
    assert _test_definition_count() == 1285
    assert _bool_returning_render_tests() == 0

    agent_source = (MARKETING_ROOT / "agents" / "marketing_agent.py").read_text(encoding="utf-8")
    event_to_tool = re.search(r"EVENT_TO_TOOL: Dict\[str, str\] = \{(?P<body>.*?)^    \}", agent_source, re.MULTILINE | re.DOTALL)
    assert event_to_tool is not None
    assert len(re.findall(r'^        "marketing\.', event_to_tool.group("body"), re.MULTILINE)) == 13

    required_contract_terms = (
        "62 migration files: `001`–`062`; `039` is absent",
        "13 Marketing event-to-tool mappings",
        "1285 static pytest test definitions",
        "zero bool-returning pytest tests",
        "`API :5510` | historical",
        "`n8n` | configured/static",
        "OpenFang execution is required by the MVP but not implemented by this cockpit contract",
        "Proxmox repoint is not an integrated or live claim",
        "never `verified_live` without fresh evidence",
    )
    for term in required_contract_terms:
        assert term in contract

    for document_name, safety_anchors in ENTRY_DOCUMENT_SAFETY_ANCHORS.items():
        document = (MARKETING_ROOT / document_name).read_text(encoding="utf-8")
        assert "COCKPIT_CONTRACT.md" in document
        for anchor in safety_anchors:
            assert anchor in document
