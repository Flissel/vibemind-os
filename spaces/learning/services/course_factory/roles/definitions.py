from __future__ import annotations

from pydantic import BaseModel

from spaces.learning.services.course_factory.model_gateway import (
    ModelGateway,
    ROLE_CONTRACTS,
)
from spaces.learning.services.course_factory.roles.base import GatewayRoleAgent
from spaces.learning.services.course_factory.roles.schemas import (
    ArchitectOutput,
    AssessmentOutput,
    ConceptMapOutput,
    LessonOutput,
    QualityReviewOutput,
    SourceVerificationOutput,
)


ROLE_OUTPUTS: dict[str, type[BaseModel]] = {
    "architect": ArchitectOutput,
    "concept_mapper": ConceptMapOutput,
    "lesson_author": LessonOutput,
    "assessment_designer": AssessmentOutput,
    "source_verifier": SourceVerificationOutput,
    "quality_reviewer": QualityReviewOutput,
}
ROLE_DESCRIPTIONS = {
    "architect": "Defines curriculum outcomes and chapter sequence.",
    "concept_mapper": "Maps concepts, prerequisites, and coverage.",
    "lesson_author": "Drafts source-grounded lessons.",
    "assessment_designer": "Creates authentic assessment activities.",
    "source_verifier": "Maps claims to source evidence and flags unsupported claims.",
    "quality_reviewer": "Reviews quality without lifecycle or publish authority.",
}


def build_role_agent(
    role: str, *, correlation_id: str, gateway: ModelGateway
) -> GatewayRoleAgent:
    contract = ROLE_CONTRACTS.get(role)
    output_model = ROLE_OUTPUTS.get(role)
    if contract is None or output_model is None:
        raise ValueError("course factory role is not authorized")
    stage, prompt_version, schema_version = contract
    return GatewayRoleAgent(
        name=role,
        description=ROLE_DESCRIPTIONS[role],
        stage=stage,
        prompt_version=prompt_version,
        output_schema_version=schema_version,
        correlation_id=correlation_id,
        gateway=gateway,
        output_model=output_model,
    )
