from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from spaces.learning.bridge.dispatcher import (
    ApplicationGateway,
    ApplicationOutcomeV1,
)
from spaces.learning.contracts.events import LearningToolName
from spaces.learning.contracts.mcp_models import ToolRequestV1
from spaces.learning.contracts.outcomes import TruthReadbackV1
from spaces.learning.contracts.ui_intents import (
    OpenChapterIntentV1,
    OpenCourseIntentV1,
    ShowProgressIntentV1,
    UiIntent,
)


NAVIGATION_TOOL_NAMES = frozenset(
    {
        LearningToolName.COURSE_OPEN,
        LearningToolName.CHAPTER_OPEN,
        LearningToolName.PROGRESS_SHOW,
    }
)


class NavigationGateway:
    """Adds revisioned navigation intents around the LearnHouse gateway."""

    def __init__(self, learnhouse: ApplicationGateway) -> None:
        self._learnhouse = learnhouse

    def execute(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        outcome = self._learnhouse.execute(request)
        if outcome.state != "completed" or outcome.aggregate is None:
            return outcome
        intent = self._intent_for(request, outcome)
        if intent is None:
            return outcome
        return outcome.model_copy(update={"ui_intent": intent})

    def readback(
        self, request: ToolRequestV1, outcome: ApplicationOutcomeV1
    ) -> TruthReadbackV1 | None:
        return self._learnhouse.readback(request, outcome)

    @staticmethod
    def _intent_for(
        request: ToolRequestV1, outcome: ApplicationOutcomeV1
    ) -> UiIntent | None:
        aggregate = outcome.aggregate
        assert aggregate is not None
        if request.tool is LearningToolName.COURSE_OPEN:
            return OpenCourseIntentV1(
                aggregate_id=aggregate.aggregate_id,
                aggregate_revision=aggregate.revision,
                course_id=UUID(aggregate.aggregate_id),
            )
        course_id = request.event.course_id
        if course_id is None:
            return None
        if request.tool is LearningToolName.CHAPTER_OPEN:
            return OpenChapterIntentV1(
                aggregate_id=aggregate.aggregate_id,
                aggregate_revision=aggregate.revision,
                course_id=course_id,
                chapter_id=UUID(aggregate.aggregate_id),
            )
        if request.tool is LearningToolName.PROGRESS_SHOW:
            return ShowProgressIntentV1(
                aggregate_id=aggregate.aggregate_id,
                aggregate_revision=aggregate.revision,
                course_id=course_id,
            )
        return None


def build_navigation_gateways(
    learnhouse: ApplicationGateway,
) -> Mapping[LearningToolName, ApplicationGateway]:
    gateway = NavigationGateway(learnhouse)
    return {tool: gateway for tool in NAVIGATION_TOOL_NAMES}
