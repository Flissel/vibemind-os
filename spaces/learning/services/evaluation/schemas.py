from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DeterministicItemType = Literal[
    "single_choice",
    "multiple_choice",
    "matching",
    "ordering",
    "numeric",
    "fill_in",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChoiceOption(_StrictModel):
    id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=2_000)


class StudentItem(_StrictModel):
    item_id: str = Field(min_length=1, max_length=200)
    item_type: DeterministicItemType
    prompt: str = Field(min_length=1, max_length=20_000)
    options: list[ChoiceOption] = Field(max_length=500)


class DeterministicItem(StudentItem):
    expected_answer: dict[str, object]
    scoring_config: dict[str, object]

    def student_view(self) -> StudentItem:
        return StudentItem(
            item_id=self.item_id,
            item_type=self.item_type,
            prompt=self.prompt,
            options=self.options,
        )


class ScoringResult(_StrictModel):
    score: float = Field(ge=0, le=1)
    confidence: Literal[1.0] = 1.0
    rationale_codes: list[str] = Field(min_length=1, max_length=20)


class RubricCriterion(_StrictModel):
    criterion_id: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2_000)
    points: float = Field(gt=0, le=1_000)


class RubricEvaluationRequest(_StrictModel):
    evaluation_id: str = Field(min_length=36, max_length=36)
    response_id: str = Field(min_length=36, max_length=36)
    response: dict[str, object]
    expected_answer: str = Field(min_length=1, max_length=50_000)
    rubric: list[RubricCriterion] = Field(min_length=1, max_length=100)
    source_refs: list[str] = Field(min_length=1, max_length=256)
    model_version: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=200)
    source_version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_unique_contract(self) -> "RubricEvaluationRequest":
        criterion_ids = [item.criterion_id for item in self.rubric]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("rubric criterion IDs must be unique")
        if len(self.source_refs) != len(set(self.source_refs)):
            raise ValueError("rubric source references must be unique")
        return self


class CriterionEvaluation(_StrictModel):
    criterion_id: str = Field(min_length=1, max_length=200)
    awarded_points: float = Field(ge=0, le=1_000)
    feedback: str = Field(min_length=1, max_length=5_000)
    source_refs: list[str] = Field(min_length=1, max_length=100)


class RubricModelOutput(_StrictModel):
    schema_version: Literal["rubric-evaluation-v1"]
    criteria: list[CriterionEvaluation] = Field(min_length=1, max_length=100)
    confidence: float = Field(ge=0, le=1)
    misconception_tags: list[str] = Field(max_length=100)


class RubricDecision(_StrictModel):
    evaluation_id: str
    response_id: str
    score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    accepted: bool
    rationale_codes: list[str] = Field(min_length=1, max_length=20)
    criterion_results: list[CriterionEvaluation]
    misconception_tags: list[str]
    evaluator_versions: dict[str, str]
    source_refs: list[str]
    evidence_ref: str = Field(min_length=1, max_length=512)
