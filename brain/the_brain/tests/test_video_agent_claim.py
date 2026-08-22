"""brain-video beansprucht video.status — genau einmal.

AgentYamlRegistry-Invariante: pro Event genau EIN Agent. Ein doppelter
Claim ist ein Routing-Bug, der sich sonst erst zur Laufzeit zeigt.
"""
from core.agent_yaml_registry import AgentYamlRegistry


def test_video_status_resolves_to_brain_video():
    reg = AgentYamlRegistry()
    assert reg.get_event_agent("video.status") == "brain-video"


def test_registry_has_no_conflicts():
    reg = AgentYamlRegistry()
    assert reg.validate() == []
