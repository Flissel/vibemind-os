from __future__ import annotations

from spaces.learning.contracts.events import LearningToolName
from spaces.learning.mcp.server import SERVER_NAME, TOOLS


def test_learning_mcp_exposes_only_the_registry_contract_tools() -> None:
    assert SERVER_NAME == "spaces-learning"
    assert {tool["name"] for tool in TOOLS} == {tool.value for tool in LearningToolName}
