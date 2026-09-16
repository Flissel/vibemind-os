from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

from core.capability_router import CapabilityRouter
from core.capability_targets import build_executor
from spaces.research import execution_target
from web.brain_server import create_app


ROOT = Path(__file__).resolve().parents[3]
CAPABILITIES = ROOT / "brain" / "the_brain" / "data" / "capabilities.yaml"
SPACE_REGISTRY = ROOT / "config" / "space_agent_registry.yml"


def test_research_events_have_brain_execution_targets():
    registry = yaml.safe_load(SPACE_REGISTRY.read_text(encoding="utf-8"))
    events = registry["spaces"]["research"]["events"]
    assert set(events) >= {
        "research.web",
        "research.scrape",
        "research.summarize",
        "research.to_idea",
    }

    router = CapabilityRouter(CAPABILITIES)
    expected = {
        "research_web": "research:web",
        "research_scrape": "research:scrape",
        "research_summarize": "research:summarize",
        "research_to_idea": "research:to_idea",
    }
    for capability, target in expected.items():
        detail = router.get_capability(capability)
        assert detail is not None
        assert detail["execution_target"] == target


@pytest.mark.parametrize("operation", ("web", "scrape", "summarize", "to_idea"))
def test_research_operations_use_the_canonical_openfang_researcher(operation, monkeypatch):
    monkeypatch.setenv("RESEARCH_AGENT", "hostile-agent")

    executor = build_executor(f"research:{operation}")

    assert executor._agent.target == "openfang:brain-researcher"


def test_research_executor_has_no_environment_or_openclaw_routing_branch():
    source = Path(execution_target.__file__).read_text(encoding="utf-8")

    assert "RESEARCH_AGENT" not in source
    assert "openclaw-visible" not in source
    assert "OpenClaw" not in source


def test_research_target_verifies_the_report_file_not_the_agent_claim(tmp_path, monkeypatch):
    executor = build_executor("research:web")
    report = tmp_path / "report.md"
    report.write_text("Ergebnis von https://example.test/report\n", encoding="utf-8")
    monkeypatch.setattr(execution_target, "_report_path", lambda run_id: report)

    agent_result = {"ok": True, "result": {"response": "fertig"}}
    with patch.object(executor, "_agent", Mock(call=Mock(return_value=agent_result))):
        result = executor.call(query="evidence based topic")

    assert result["ok"] is True
    assert result["result"]["sources"] == ["https://example.test/report"]
    assert result["result"]["evidence"]["citation_count"] == 1


def test_research_target_fails_closed_when_the_report_file_is_absent(tmp_path, monkeypatch):
    executor = build_executor("research:scrape")
    monkeypatch.setattr(
        execution_target, "_report_path", lambda run_id: tmp_path / "never-written.md"
    )

    agent_result = {"ok": True, "result": {"response": "plausibel, aber nichts geschrieben"}}
    with patch.object(executor, "_agent", Mock(call=Mock(return_value=agent_result))):
        result = executor.call(url="https://example.test")

    assert result["ok"] is False
    assert "report file" in result["error"]


def test_research_target_fails_closed_on_a_report_without_citations(tmp_path, monkeypatch):
    executor = build_executor("research:web")
    report = tmp_path / "report.md"
    report.write_text("Kein einziger Beleg.", encoding="utf-8")
    monkeypatch.setattr(execution_target, "_report_path", lambda run_id: report)

    agent_result = {"ok": True, "result": {"response": "fertig"}}
    with patch.object(executor, "_agent", Mock(call=Mock(return_value=agent_result))):
        result = executor.call(query="x")

    assert result["ok"] is False
    assert "citation" in result["error"]


def test_instruction_dictates_an_absolute_output_path(monkeypatch):
    executor = build_executor("research:web")
    instruction = executor._instruction({"query": "x"}, "job_v1_0000000000000000000000000A")
    assert "job_v1_0000000000000000000000000A" in instruction
    assert "file_write" in instruction


def test_research_health_is_false_when_external_infrastructure_is_down():
    executor = build_executor("research:web")
    failed = Mock()
    failed.raise_for_status.side_effect = RuntimeError("offline")
    with patch("spaces.research.execution_target.requests.get", return_value=failed):
        health = executor.health_check()

    assert health["ok"] is False
    assert health["components"]["openfang"]["ok"] is False
    assert health["components"]["qdrant"]["ok"] is False


def test_research_health_is_queryable_from_brain_api():
    reported = {"ok": True, "components": {"openfang": {"ok": True}, "qdrant": {"ok": True}}}
    with patch("spaces.research.execution_target.ResearchTarget.health_check", return_value=reported):
        response = TestClient(create_app(testing=True)).get("/api/research/health")

    assert response.status_code == 200
    assert response.json() == reported
