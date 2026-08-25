from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from spaces.learning.deployment.cutover import (
    CutoverController,
    CutoverEvidence,
    RouteTarget,
    confirmation_digest,
    initialize_route_state,
    main,
)


TOKEN = "confirm-learning-cutover-1"


@pytest.fixture()
def route_fixture(tmp_path: Path):
    legacy = tmp_path / "Learning_plattform"
    legacy.mkdir()
    legacy_marker = legacy / "legacy.db.marker"
    legacy_marker.write_text("untouched", encoding="utf-8")
    profile = tmp_path / "learning-profile.yml"
    profile.write_text("profile: local-single-host\n", encoding="utf-8")
    state_path = tmp_path / "route-state.json"
    initialize_route_state(
        state_path,
        targets={
            "legacy": RouteTarget(
                kind="local_path",
                address=str(legacy),
                health_ref=str(legacy_marker),
                read_only=True,
            ),
            "learning_space": RouteTarget(
                kind="compose_profile",
                address=str(profile),
                health_ref="http://127.0.0.1:8010/health/ready",
                read_only=False,
            ),
        },
        active_target="legacy",
    )
    health = {"legacy": True, "learning_space": True}
    controller = CutoverController(
        state_path,
        confirmation_sha256=confirmation_digest(TOKEN),
        health_probe=lambda name, target: health[name],
    )
    evidence = CutoverEvidence(
        migration_batch_id="legacy-real-13845e47",
        migration_ready=True,
        reconciliation_ready=True,
        golden_path_correlation_id="golden-correlation-1",
        golden_path_ready=True,
        source_read_only=True,
        rollback_ready=True,
    )
    return controller, state_path, legacy_marker, health, evidence


def test_cutover_plan_is_non_mutating_and_lists_failed_preflights(route_fixture) -> None:
    controller, state_path, _, health, evidence = route_fixture
    before = state_path.read_bytes()
    health["learning_space"] = False

    plan = controller.plan(
        target="learning_space", expected_revision=1, evidence=evidence
    )

    assert plan.ready is False
    assert plan.blockers == ("target_health_unavailable",)
    assert state_path.read_bytes() == before
    with pytest.raises(PermissionError, match="confirmation"):
        controller.apply(
            target="learning_space",
            expected_revision=1,
            evidence=evidence,
            confirmation_token=None,
            confirmation_ref="approval-1",
        )


def test_cutover_is_atomic_idempotent_and_retains_previous_state(route_fixture) -> None:
    controller, state_path, legacy_marker, _, evidence = route_fixture

    switched = controller.apply(
        target="learning_space",
        expected_revision=1,
        evidence=evidence,
        confirmation_token=TOKEN,
        confirmation_ref="approval-cutover-1",
    )
    replay = controller.apply(
        target="learning_space",
        expected_revision=2,
        evidence=evidence,
        confirmation_token=TOKEN,
        confirmation_ref="approval-cutover-1",
    )

    assert switched == replay
    assert switched.active_target == "learning_space"
    assert switched.revision == 2
    assert len(switched.receipts) == 1
    receipt = switched.receipts[0]
    assert receipt.operation == "cutover"
    assert receipt.previous_target == "legacy"
    assert receipt.previous_revision == 1
    assert receipt.current_target == "learning_space"
    assert receipt.current_revision == 2
    assert receipt.migration_batch_id == evidence.migration_batch_id
    assert legacy_marker.read_text(encoding="utf-8") == "untouched"
    assert hashlib.sha256(state_path.read_bytes()).hexdigest()

    verified = controller.verify(
        target="learning_space",
        expected_revision=2,
        confirmation_token=TOKEN,
    )
    assert verified.active_target == "learning_space"
    assert verified.healthy is True


def test_cutover_rejects_stale_revision_and_target_health_failure(route_fixture) -> None:
    controller, _, _, health, evidence = route_fixture
    with pytest.raises(RuntimeError, match="revision"):
        controller.apply(
            target="learning_space",
            expected_revision=0,
            evidence=evidence,
            confirmation_token=TOKEN,
            confirmation_ref="approval-1",
        )
    health["learning_space"] = False
    with pytest.raises(RuntimeError, match="preflight"):
        controller.apply(
            target="learning_space",
            expected_revision=1,
            evidence=evidence,
            confirmation_token=TOKEN,
            confirmation_ref="approval-1",
        )


def test_rollback_restores_previous_route_without_deleting_data(route_fixture) -> None:
    controller, _, legacy_marker, _, evidence = route_fixture
    controller.apply(
        target="learning_space",
        expected_revision=1,
        evidence=evidence,
        confirmation_token=TOKEN,
        confirmation_ref="approval-cutover-1",
    )

    rolled_back = controller.rollback(
        expected_revision=2,
        confirmation_token=TOKEN,
        confirmation_ref="approval-rollback-1",
    )
    replay = controller.rollback(
        expected_revision=3,
        confirmation_token=TOKEN,
        confirmation_ref="approval-rollback-1",
    )

    assert rolled_back == replay
    assert rolled_back.active_target == "legacy"
    assert rolled_back.revision == 3
    assert [receipt.operation for receipt in rolled_back.receipts] == [
        "cutover",
        "rollback",
    ]
    assert legacy_marker.read_text(encoding="utf-8") == "untouched"


def test_route_state_matches_published_schema(route_fixture) -> None:
    _, state_path, _, _, _ = route_fixture
    schema_path = (
        Path(__file__).parents[2] / "deployment" / "route-state.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    state = json.loads(state_path.read_text(encoding="utf-8"))

    Draft202012Validator(schema).validate(state)


def test_cli_allows_plan_but_blocks_apply_without_action_token(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    profile = tmp_path / "profile.yml"
    profile.write_text("profile: local-single-host\n", encoding="utf-8")
    state_path = tmp_path / "route-state.json"
    initialize_route_state(
        state_path,
        targets={
            "legacy": RouteTarget(
                kind="local_path",
                address=str(legacy),
                health_ref=str(profile),
                read_only=True,
            ),
            "learning_space": RouteTarget(
                kind="compose_profile",
                address=str(profile),
                health_ref=str(profile),
                read_only=False,
            ),
        },
        active_target="legacy",
    )
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(
        CutoverEvidence(
            migration_batch_id="batch-cli-1",
            migration_ready=True,
            reconciliation_ready=True,
            golden_path_correlation_id="golden-cli-1",
            golden_path_ready=True,
            source_read_only=True,
            rollback_ready=True,
        ).model_dump_json(),
        encoding="utf-8",
    )
    common = [
        "--state-file", str(state_path),
        "--expected-revision", "1",
        "--target", "learning_space",
        "--evidence-file", str(evidence_path),
    ]

    assert main(["plan", *common]) == 0
    assert json.loads(capsys.readouterr().out)["ready"] is True
    monkeypatch.delenv("LEARNING_CUTOVER_CONFIRMATION_TOKEN", raising=False)
    monkeypatch.delenv("LEARNING_CUTOVER_CONFIRMATION_SHA256", raising=False)
    with pytest.raises(PermissionError, match="confirmation"):
        main(["apply", *common, "--confirmation-ref", "approval-cli-1"])
