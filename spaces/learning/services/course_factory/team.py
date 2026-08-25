from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from autogen_agentchat.teams import RoundRobinGroupChat

from spaces.learning.services.course_factory.model_gateway import (
    ModelGateway,
    ModelGatewayError,
    ROLE_CONTRACTS,
)
from spaces.learning.services.course_factory.repository import (
    CourseFactoryRepository,
    FactoryJobRecord,
    StageArtifactInput,
)
from spaces.learning.services.course_factory.roles import build_role_agent
from spaces.learning.services.course_factory.state_machine import FactoryState
from spaces.learning.services.db.repository import PersistenceConflict


@dataclass(frozen=True)
class CourseTeamInput:
    job_id: str
    expected_revision: int
    correlation_id: str
    course_id: str
    audience: str
    target_outcome: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        UUID(self.job_id)
        UUID(self.correlation_id)
        UUID(self.course_id)
        if self.expected_revision < 1:
            raise ValueError("course team revision must be positive")
        if not self.audience.strip() or len(self.audience) > 1_000:
            raise ValueError("course team audience is invalid")
        if not self.target_outcome.strip() or len(self.target_outcome) > 2_000:
            raise ValueError("course team target outcome is invalid")
        if (
            not self.source_refs
            or len(self.source_refs) > 256
            or any(not ref or len(ref) > 512 for ref in self.source_refs)
        ):
            raise ValueError("course team source references are invalid")


_STAGE_GROUPS = (
    (
        FactoryState.STRUCTURING,
        ("architect", "concept_mapper"),
        FactoryState.AUTHORING,
    ),
    (FactoryState.AUTHORING, ("lesson_author",), FactoryState.ASSESSING),
    (
        FactoryState.ASSESSING,
        ("assessment_designer",),
        FactoryState.VERIFYING,
    ),
    (
        FactoryState.VERIFYING,
        ("source_verifier",),
        FactoryState.QUALITY_GATE,
    ),
    (FactoryState.QUALITY_GATE, ("quality_reviewer",), None),
)


class CourseAgentTeam:
    def __init__(
        self, repository: CourseFactoryRepository, gateway: ModelGateway
    ) -> None:
        self._repository = repository
        self._gateway = gateway

    async def run(self, request: CourseTeamInput) -> FactoryJobRecord:
        job = self._repository.get(request.job_id)
        if job.revision != request.expected_revision:
            raise PersistenceConflict("course factory revision conflict")
        if job.course_id != request.course_id:
            raise PersistenceConflict("course factory course identity conflict")
        if job.state is not FactoryState.STRUCTURING:
            raise ValueError("course factory attempt must be in structuring state")

        context: dict[str, object] = {
            "course_id": request.course_id,
            "audience": request.audience,
            "target_outcome": request.target_outcome,
            "source_refs": list(request.source_refs),
            "role_outputs": {},
        }
        try:
            for stage, roles, next_state in _STAGE_GROUPS:
                if job.state is not stage:
                    raise PersistenceConflict("course factory stage changed concurrently")
                agents = [
                    build_role_agent(
                        role,
                        correlation_id=request.correlation_id,
                        gateway=self._gateway,
                    )
                    for role in roles
                ]
                stage_input = {
                    "context": context,
                    "role_contracts": [
                        {
                            "role": role,
                            "stage": ROLE_CONTRACTS[role][0],
                            "prompt_version": ROLE_CONTRACTS[role][1],
                            "output_schema_version": ROLE_CONTRACTS[role][2],
                        }
                        for role in roles
                    ],
                }
                input_hash = _hash_json(stage_input)
                autogen_team = RoundRobinGroupChat(agents, max_turns=len(agents))
                try:
                    await autogen_team.run(task=_canonical_json(stage_input))
                except RuntimeError as error:
                    gateway_error = next(
                        (agent.last_error for agent in agents if agent.last_error),
                        None,
                    )
                    if gateway_error is not None:
                        raise gateway_error from error
                    raise
                role_outputs: dict[str, object] = {}
                evidence_refs: list[str] = []
                for agent in agents:
                    if agent.last_result is None:
                        raise RuntimeError("AutoGen role completed without durable output")
                    role_outputs[agent.name] = agent.last_result.output.model_dump(
                        mode="json"
                    )
                    evidence_refs.append(agent.last_result.evidence_ref)
                context["role_outputs"] = {
                    **dict(context["role_outputs"]),
                    **role_outputs,
                }
                self._repository.record_stage_artifact(
                    job.id,
                    expected_revision=job.revision,
                    artifact=StageArtifactInput(
                        stage=stage,
                        input_hash=input_hash,
                        output_hash=_hash_json(role_outputs),
                        evidence_refs=tuple(evidence_refs),
                    ),
                )
                if next_state is not None:
                    job = self._repository.advance(
                        job.id,
                        expected_revision=job.revision,
                        target=next_state,
                    )
            return self._repository.get(job.id)
        except ModelGatewayError:
            self._mark_failed(job.id, "model_gateway_failed")
            raise
        except RuntimeError:
            self._mark_failed(job.id, "agent_team_failed")
            raise

    def _mark_failed(self, job_id: str, reason_code: str) -> None:
        current = self._repository.get(job_id)
        if current.state not in {
            FactoryState.FAILED,
            FactoryState.CANCELLED,
            FactoryState.REJECTED,
            FactoryState.PUBLISHED,
        }:
            self._repository.advance(
                current.id,
                expected_revision=current.revision,
                target=FactoryState.FAILED,
                reason_code=reason_code,
            )


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()
