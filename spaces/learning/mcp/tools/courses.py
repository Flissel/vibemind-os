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
    NavigateIntentV1,
    OpenCourseIntentV1,
    UiIntent,
)


COURSE_TOOL_NAMES = frozenset(
    {
        LearningToolName.COURSE_LIST,
        LearningToolName.COURSE_CREATE,
        LearningToolName.MATERIAL_IMPORT,
        LearningToolName.COURSE_REVIEW,
        LearningToolName.COURSE_PUBLISH,
    }
)


class CourseGateway:
    """Adds semantic course intents around the admitted LearnHouse gateway."""

    def __init__(self, learnhouse: ApplicationGateway) -> None:
        self._learnhouse = learnhouse

    def execute(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        outcome = self._learnhouse.execute(request)
        if outcome.state != "completed" or outcome.aggregate is None:
            return outcome
        return outcome.model_copy(
            update={"ui_intent": self._intent_for(request, outcome)}
        )

    def readback(
        self, request: ToolRequestV1, outcome: ApplicationOutcomeV1
    ) -> TruthReadbackV1 | None:
        return self._learnhouse.readback(request, outcome)

    @staticmethod
    def _intent_for(request: ToolRequestV1, outcome: ApplicationOutcomeV1) -> UiIntent:
        aggregate = outcome.aggregate
        assert aggregate is not None
        if request.tool is LearningToolName.COURSE_CREATE:
            return OpenCourseIntentV1(
                aggregate_id=aggregate.aggregate_id,
                aggregate_revision=aggregate.revision,
                course_id=UUID(aggregate.aggregate_id),
            )
        return NavigateIntentV1(
            aggregate_id=aggregate.aggregate_id,
            aggregate_revision=aggregate.revision,
            route="/learning",
        )


def build_course_gateways(
    learnhouse: ApplicationGateway,
) -> Mapping[LearningToolName, ApplicationGateway]:
    gateway = CourseGateway(learnhouse)
    return {tool: gateway for tool in COURSE_TOOL_NAMES}
