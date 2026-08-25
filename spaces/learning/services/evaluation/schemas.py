from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
