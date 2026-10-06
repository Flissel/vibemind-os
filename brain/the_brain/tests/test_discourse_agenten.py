from unittest.mock import MagicMock, patch

from core.discourse_engine import DiscourseEngine


def _antwort(agenten):
    r = MagicMock()
    r.json.return_value = agenten
    r.raise_for_status.return_value = None
    return r


def test_ohne_phi3_spricht_engine_niemanden_an():
    eng = DiscourseEngine(kg=MagicMock())
    with patch("core.discourse_engine.requests.get",
               return_value=_antwort([{"name": "brain-planner", "state": "Running"}])):
        assert eng._ensure_agents() is False
    assert eng._agents == []
    assert eng.stats["last_error"] == "keine Discourse-Agenten (-phi3)"


def test_mit_phi3_nur_diese():
    eng = DiscourseEngine(kg=MagicMock())
    with patch("core.discourse_engine.requests.get", return_value=_antwort(
            [{"name": "a-phi3", "state": "Running"}, {"name": "brain-planner", "state": "Running"}])):
        assert eng._ensure_agents() is True
    assert [a["name"] for a in eng._agents] == ["a-phi3"]
