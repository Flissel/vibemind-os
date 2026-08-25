from __future__ import annotations

import re
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from pydantic import ValidationError

from spaces.learning.contracts.events import LearningEventType
from spaces.learning.contracts.mcp_models import ActorV1, EventEnvelopeV1


ROOT = Path(__file__).resolve().parents[4]
COMPOSE = ROOT / "spaces" / "learning" / "deployment" / "compose.yml"
SECRET_VARIABLES = {
    "LEARNING_POSTGRES_PASSWORD",
    "LEARNHOUSE_AUTH_JWT_SECRET_KEY",
    "LEARNHOUSE_INITIAL_ADMIN_PASSWORD",
    "LEARNHOUSE_LOCAL_BOOTSTRAP_KEY",
    "LEARNHOUSE_LEARNING_SERVICE_KEY",
    "LEARNING_RETRIEVAL_SERVICE_KEY",
    "LEARNHOUSE_COLLAB_INTERNAL_KEY",
    "LEARNING_OPENFANG_API_KEY",
    "LEARNING_EVALUATION_SERVICE_KEY",
    "PENECHO_LEARNING_TOKEN",
}
SECRET_PATTERNS = (
    re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"ghp_[A-Za-z0-9]{20,}"),
)


@pytest.mark.parametrize("field", ["api_key", "provider_key", "access_token", "password"])
def test_public_mcp_payload_rejects_secret_fields(field: str) -> None:
    with pytest.raises(ValidationError, match="forbidden public payload field"):
        EventEnvelopeV1(
            event_type=LearningEventType.STATUS,
            invocation_id=uuid4(),
            correlation_id=uuid4(),
            actor=ActorV1(actor_id="secret-audit", actor_type="system"),
            payload={field: "audit-secret-shaped-value"},
        )


def test_browser_facing_compose_environment_contains_no_secret_variable() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    browser_environment = compose["services"]["learnhouse-web"]["environment"]
    public_values = {
        key: str(value)
        for key, value in browser_environment.items()
        if key.startswith("NEXT_PUBLIC_")
    }

    assert public_values
    assert not any(
        secret in value
        for secret in SECRET_VARIABLES
        for value in public_values.values()
    )


def test_tracked_runtime_and_browser_sources_have_no_embedded_provider_keys() -> None:
    paths = subprocess.run(
        ["git", "ls-files", "spaces/learning"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    scanned = 0
    findings: list[str] = []
    for relative in paths:
        normalized = relative.replace("\\", "/")
        if any(part in normalized for part in ("/tests/", "/docs/", "package-lock")):
            continue
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        content = path.read_bytes()
        scanned += 1
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            findings.append(normalized)

    assert scanned > 50
    assert findings == []
