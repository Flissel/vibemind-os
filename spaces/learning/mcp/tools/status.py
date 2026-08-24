from __future__ import annotations

import os

from spaces.learning.bridge.dispatcher import (
    ApplicationOutcomeV1,
    InMemoryReceiptStore,
    LearningDispatcher,
)
from spaces.learning.contracts.events import LearningToolName
from spaces.learning.contracts.mcp_models import ToolRequestV1
from spaces.learning.contracts.outcomes import (
    AggregateRefV1,
    EvidenceRefV1,
    TruthReadbackV1,
)
from spaces.learning.services.db.repository import SqlReceiptStore
from spaces.learning.services.db.session import build_session_factory, create_learning_engine


class StructuralStatusGateway:
    _aggregate = AggregateRefV1(
        aggregate_type="learning_space", aggregate_id="learning", revision=0
    )

    def execute(self, request: ToolRequestV1) -> ApplicationOutcomeV1:
        return ApplicationOutcomeV1(
            state="completed",
            aggregate=self._aggregate,
            result={"status": "configured", "live_claim": False},
        )

    def readback(
        self, request: ToolRequestV1, outcome: ApplicationOutcomeV1
    ) -> TruthReadbackV1:
        return TruthReadbackV1(
            invocation_id=request.event.invocation_id,
            correlation_id=request.event.correlation_id,
            owner="learning-registry",
            terminal_state="completed",
            aggregate=self._aggregate,
            evidence=EvidenceRefV1(
                owner="learning-registry",
                evidence_id="learning-registry-v1",
                evidence_type="structural_status",
            ),
        )


def build_default_dispatcher() -> LearningDispatcher:
    database_url = os.environ.get("LEARNING_DATABASE_URL", "").strip()
    receipts = InMemoryReceiptStore()
    if database_url:
        engine = create_learning_engine(database_url)
        receipts = SqlReceiptStore(build_session_factory(engine))
    return LearningDispatcher(
        gateways={LearningToolName.STATUS: StructuralStatusGateway()},
        receipts=receipts,
    )
