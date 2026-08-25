from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from spaces.learning.services.adaptive_engine.models import (
    AdaptiveConcept,
    AdaptiveItem,
    LearningAttempt,
    LearningSession,
)
from spaces.learning.services.course_factory.repository import SessionFactory
from spaces.learning.services.db.models import utc_now
from spaces.learning.services.db.repository import PersistenceConflict


_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class SessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    course_id: str
    course_revision: int = Field(ge=1)
    actor_id: str = Field(min_length=1, max_length=128)
    mode: Literal["training", "exam"]
    blueprint: dict[str, object]

    @field_validator("course_id")
    @classmethod
    def validate_course_id(cls, value: str) -> str:
        return str(UUID(value))

    @field_validator("actor_id")
    @classmethod
    def validate_actor_id(cls, value: str) -> str:
        if not _ACTOR.fullmatch(value):
            raise ValueError("adaptive actor ID is invalid")
        return value


class AttemptInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str
    selection_reason: dict[str, object]

    @field_validator("item_id")
    @classmethod
    def validate_item_id(cls, value: str) -> str:
        return str(UUID(value))


@dataclass(frozen=True)
class SessionRecord:
    id: str
    course_id: str
    course_revision: int
    actor_id: str
    mode: str
    state: str
    revision: int


@dataclass(frozen=True)
class AttemptRecord:
    id: str
    session_id: str
    item_id: str
    ordinal: int
    session_revision: int


class AdaptiveRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def start_session(self, value: SessionInput) -> SessionRecord:
        _bounded_json(value.blueprint, maximum=100_000)
        now = utc_now()
        try:
            with self._session_factory() as session, session.begin():
                catalog_exists = session.scalar(
                    select(AdaptiveConcept.id)
                    .where(
                        AdaptiveConcept.course_id == value.course_id,
                        AdaptiveConcept.course_revision == value.course_revision,
                    )
                    .limit(1)
                )
                if catalog_exists is None:
                    raise LookupError("adaptive course revision has no concepts")
                row = LearningSession(
                    id=str(uuid4()),
                    course_id=value.course_id,
                    course_revision=value.course_revision,
                    actor_id=value.actor_id,
                    mode=value.mode,
                    state="active",
                    revision=1,
                    blueprint=value.blueprint,
                    created_at=now,
                    updated_at=now,
                    completed_at=None,
                )
                session.add(row)
                session.flush()
                return _session_record(row)
        except IntegrityError as error:
            raise PersistenceConflict("adaptive session conflict") from error

    def append_attempt(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        value: AttemptInput,
    ) -> AttemptRecord:
        session_id = str(UUID(session_id))
        _bounded_json(value.selection_reason, maximum=50_000)
        try:
            with self._session_factory() as session, session.begin():
                learning_session = session.get(
                    LearningSession, session_id, with_for_update=True
                )
                if learning_session is None:
                    raise LookupError("adaptive session not found")
                if learning_session.revision != expected_session_revision:
                    raise PersistenceConflict("adaptive session revision conflict")
                if learning_session.state != "active":
                    raise PersistenceConflict("adaptive session is not active")
                item = session.get(AdaptiveItem, value.item_id)
                if (
                    item is None
                    or item.course_id != learning_session.course_id
                    or item.course_revision != learning_session.course_revision
                ):
                    raise ValueError("adaptive item is outside the session revision")
                ordinal = (
                    session.scalar(
                        select(func.max(LearningAttempt.ordinal)).where(
                            LearningAttempt.session_id == learning_session.id
                        )
                    )
                    or 0
                ) + 1
                row = LearningAttempt(
                    id=str(uuid4()),
                    session_id=learning_session.id,
                    item_id=item.id,
                    course_id=learning_session.course_id,
                    course_revision=learning_session.course_revision,
                    ordinal=ordinal,
                    selection_reason=value.selection_reason,
                    selected_at=utc_now(),
                )
                session.add(row)
                learning_session.revision += 1
                learning_session.updated_at = utc_now()
                session.flush()
                return AttemptRecord(
                    id=row.id,
                    session_id=row.session_id,
                    item_id=row.item_id,
                    ordinal=row.ordinal,
                    session_revision=learning_session.revision,
                )
        except IntegrityError as error:
            raise PersistenceConflict("adaptive attempt conflict") from error


def _bounded_json(value: object, *, maximum: int) -> None:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("adaptive metadata must be JSON serializable") from error
    if len(encoded.encode()) > maximum:
        raise ValueError("adaptive metadata exceeds its size limit")


def _session_record(row: LearningSession) -> SessionRecord:
    return SessionRecord(
        id=row.id,
        course_id=row.course_id,
        course_revision=row.course_revision,
        actor_id=row.actor_id,
        mode=row.mode,
        state=row.state,
        revision=row.revision,
    )
