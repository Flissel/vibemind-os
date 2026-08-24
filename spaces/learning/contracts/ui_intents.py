from __future__ import annotations

from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import ConfigDict, Field, TypeAdapter

from .mcp_models import ContractModel, SafeToken


class UiIntentV1(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["1"] = "1"
    kind: str
    aggregate_id: SafeToken
    aggregate_revision: Annotated[int, Field(ge=0)]


class NavigateIntentV1(UiIntentV1):
    kind: Literal["navigate"] = "navigate"
    route: Annotated[str, Field(pattern=r"^/learning(?:/.*)?$", max_length=512)]


class OpenCourseIntentV1(UiIntentV1):
    kind: Literal["open_course"] = "open_course"
    course_id: UUID


class OpenChapterIntentV1(UiIntentV1):
    kind: Literal["open_chapter"] = "open_chapter"
    course_id: UUID
    chapter_id: UUID


class OpenTaskIntentV1(UiIntentV1):
    kind: Literal["open_task"] = "open_task"
    session_id: UUID
    task_id: UUID


class FocusAnswerIntentV1(UiIntentV1):
    kind: Literal["focus_answer"] = "focus_answer"
    field_id: SafeToken = "answer"


class OpenCanvasIntentV1(UiIntentV1):
    kind: Literal["open_canvas"] = "open_canvas"
    activity_id: UUID
    canvas_revision: Annotated[int, Field(ge=0)]


class ShowResultIntentV1(UiIntentV1):
    kind: Literal["show_result"] = "show_result"
    result_id: UUID


class ShowProgressIntentV1(UiIntentV1):
    kind: Literal["show_progress"] = "show_progress"
    course_id: UUID


class ShowErrorIntentV1(UiIntentV1):
    kind: Literal["show_error"] = "show_error"
    error_code: SafeToken


UiIntent = Annotated[
    Union[
        NavigateIntentV1,
        OpenCourseIntentV1,
        OpenChapterIntentV1,
        OpenTaskIntentV1,
        FocusAnswerIntentV1,
        OpenCanvasIntentV1,
        ShowResultIntentV1,
        ShowProgressIntentV1,
        ShowErrorIntentV1,
    ],
    Field(discriminator="kind"),
]

_UI_INTENT_ADAPTER = TypeAdapter(UiIntent)


def validate_ui_intent(value: object) -> UiIntent:
    return _UI_INTENT_ADAPTER.validate_python(value)
