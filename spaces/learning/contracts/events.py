from __future__ import annotations

from enum import StrEnum


class LearningEventType(StrEnum):
    STATUS = "learning.status"
    COURSE_LIST = "learning.course.list"
    COURSE_CREATE = "learning.course.create"
    COURSE_OPEN = "learning.course.open"
    MATERIAL_IMPORT = "learning.material.import"
    COURSE_GENERATE = "learning.course.generate"
    GENERATION_STATUS = "learning.generation.status"
    COURSE_REVIEW = "learning.course.review"
    COURSE_PUBLISH = "learning.course.publish"
    CHAPTER_OPEN = "learning.chapter.open"
    SESSION_START = "learning.session.start"
    TASK_NEXT = "learning.task.next"
    TASK_ANSWER = "learning.task.answer"
    HINT_REQUEST = "learning.hint.request"
    TUTOR_ASK = "learning.tutor.ask"
    PROGRESS_SHOW = "learning.progress.show"
    CANVAS_OPEN = "learning.canvas.open"
    CANVAS_SAVE = "learning.canvas.save"
    CANVAS_HINT = "learning.canvas.hint"
    CANVAS_SUBMIT = "learning.canvas.submit"
    CANVAS_REVIEW = "learning.canvas.review"


class LearningToolName(StrEnum):
    STATUS = "learning_status"
    COURSE_LIST = "learning_course_list"
    COURSE_CREATE = "learning_course_create"
    COURSE_OPEN = "learning_course_open"
    MATERIAL_IMPORT = "learning_material_import"
    COURSE_GENERATE = "learning_course_generate"
    GENERATION_STATUS = "learning_generation_status"
    COURSE_REVIEW = "learning_course_review"
    COURSE_PUBLISH = "learning_course_publish"
    CHAPTER_OPEN = "learning_chapter_open"
    SESSION_START = "learning_session_start"
    TASK_NEXT = "learning_task_next"
    TASK_ANSWER = "learning_task_answer"
    HINT_REQUEST = "learning_hint_request"
    TUTOR_ASK = "learning_tutor_ask"
    PROGRESS_SHOW = "learning_progress_show"
    CANVAS_OPEN = "learning_canvas_open"
    CANVAS_SAVE = "learning_canvas_save"
    CANVAS_HINT = "learning_canvas_hint"
    CANVAS_SUBMIT = "learning_canvas_submit"
    CANVAS_REVIEW = "learning_canvas_review"


EVENT_TOOL_MAP: dict[LearningEventType, LearningToolName] = {
    LearningEventType.STATUS: LearningToolName.STATUS,
    LearningEventType.COURSE_LIST: LearningToolName.COURSE_LIST,
    LearningEventType.COURSE_CREATE: LearningToolName.COURSE_CREATE,
    LearningEventType.COURSE_OPEN: LearningToolName.COURSE_OPEN,
    LearningEventType.MATERIAL_IMPORT: LearningToolName.MATERIAL_IMPORT,
    LearningEventType.COURSE_GENERATE: LearningToolName.COURSE_GENERATE,
    LearningEventType.GENERATION_STATUS: LearningToolName.GENERATION_STATUS,
    LearningEventType.COURSE_REVIEW: LearningToolName.COURSE_REVIEW,
    LearningEventType.COURSE_PUBLISH: LearningToolName.COURSE_PUBLISH,
    LearningEventType.CHAPTER_OPEN: LearningToolName.CHAPTER_OPEN,
    LearningEventType.SESSION_START: LearningToolName.SESSION_START,
    LearningEventType.TASK_NEXT: LearningToolName.TASK_NEXT,
    LearningEventType.TASK_ANSWER: LearningToolName.TASK_ANSWER,
    LearningEventType.HINT_REQUEST: LearningToolName.HINT_REQUEST,
    LearningEventType.TUTOR_ASK: LearningToolName.TUTOR_ASK,
    LearningEventType.PROGRESS_SHOW: LearningToolName.PROGRESS_SHOW,
    LearningEventType.CANVAS_OPEN: LearningToolName.CANVAS_OPEN,
    LearningEventType.CANVAS_SAVE: LearningToolName.CANVAS_SAVE,
    LearningEventType.CANVAS_HINT: LearningToolName.CANVAS_HINT,
    LearningEventType.CANVAS_SUBMIT: LearningToolName.CANVAS_SUBMIT,
    LearningEventType.CANVAS_REVIEW: LearningToolName.CANVAS_REVIEW,
}


WRITE_EVENTS = frozenset(
    {
        LearningEventType.COURSE_CREATE,
        LearningEventType.MATERIAL_IMPORT,
        LearningEventType.COURSE_GENERATE,
        LearningEventType.COURSE_REVIEW,
        LearningEventType.COURSE_PUBLISH,
        LearningEventType.SESSION_START,
        LearningEventType.TASK_ANSWER,
        LearningEventType.CANVAS_SAVE,
        LearningEventType.CANVAS_SUBMIT,
        LearningEventType.CANVAS_REVIEW,
    }
)
