"""
VibeMind Ideas Space Module

Rachel's domain - Bubble and idea management.
Includes backend agents, tools, and user agent.
"""

# Import from local agents module (migrated code)
from .agents import (
    IdeasAgent,
    get_ideas_agent,
    BubblesAgent,
    get_bubbles_agent,
)

# RachelAgent depends on swarm.user_agents which may not exist (removed dead code)
try:
    from .agents import RachelAgent, create_rachel_agent, RACHEL_VOICE_PROMPT
except ImportError:
    RachelAgent = None
    create_rachel_agent = None
    RACHEL_VOICE_PROMPT = ""

__all__ = [
    "IdeasAgent",
    "get_ideas_agent",
    "BubblesAgent",
    "get_bubbles_agent",
    "RachelAgent",
    "create_rachel_agent",
    "RACHEL_VOICE_PROMPT",
]
