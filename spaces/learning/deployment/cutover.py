from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator


TargetName = Literal["legacy", "learning_space"]
HealthProbe = Callable[[TargetName, "RouteTarget"], bool]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RouteTarget(_StrictModel):
    kind: Literal["local_path", "compose_profile"]
    address: str = Field(min_length=1, max_length=2048)
    health_ref: str = Field(min_length=1, max_length=2048)
    read_only: bool


class CutoverEvidence(_StrictModel):
    version: Literal["learning-cutover-evidence-v1"] = "learning-cutover-evidence-v1"
    migration_batch_id: str = Field(min_length=1, max_length=128)
    migration_ready: bool
    reconciliation_ready: bool
    golden_path_correlation_id: str = Field(min_length=1, max_length=128)
    golden_path_ready: bool
    source_read_only: bool
    rollback_ready: bool


class RouteReceipt(_StrictModel):
    version: Literal["learning-route-receipt-v1"] = "learning-route-receipt-v1"
    operation: Literal["cutover", "rollback"]
    previous_target: TargetName
    previous_revision: int = Field(ge=1)
    current_target: TargetName
    current_revision: int = Field(ge=2)
    migration_batch_id: str = Field(min_length=1, max_length=128)
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    created_at: datetime


class RouteState(_StrictModel):
    version: Literal["learning-route-state-v1"] = "learning-route-state-v1"
    revision: int = Field(ge=1)
    active_target: TargetName
    targets: dict[TargetName, RouteTarget]
    receipts: tuple[RouteReceipt, ...] = ()

    @model_validator(mode="after")
    def validate_targets(self) -> "RouteState":
        if set(self.targets) != {"legacy", "learning_space"}:
            raise ValueError("both route targets are required")
        if not self.targets["legacy"].read_only:
            raise ValueError("legacy route target must remain read-only")
        return self


class CutoverPlan(_StrictModel):
    version: Literal["learning-cutover-plan-v1"] = "learning-cutover-plan-v1"
    current_target: TargetName
    target: TargetName
    expected_revision: int = Field(ge=1)
    ready: bool
    blockers: tuple[str, ...]
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    destructive_operations: Literal[0] = 0


class RouteVerification(_StrictModel):
    version: Literal["learning-route-verification-v1"] = "learning-route-verification-v1"
    active_target: TargetName
    revision: int = Field(ge=1)
    healthy: bool
    receipt_count: int = Field(ge=0)


def confirmation_digest(token: str) -> str:
    if len(token) < 16 or token.strip() != token:
        raise ValueError("cutover confirmation token is invalid")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def initialize_route_state(
    path: Path,
    *,
    targets: dict[TargetName, RouteTarget],
    active_target: TargetName,
) -> RouteState:
    if path.exists():
        raise FileExistsError("route state already exists")
    if set(targets) != {"legacy", "learning_space"}:
        raise ValueError("both route targets are required")
    if not targets["legacy"].read_only:
        raise ValueError("legacy route target must remain read-only")
    state = RouteState(
        revision=1,
        active_target=active_target,
        targets=targets,
    )
    _atomic_write(path, state)
    return state


class CutoverController:
    def __init__(
        self,
        state_path: Path,
        *,
        confirmation_sha256: str,
        health_probe: HealthProbe | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if confirmation_sha256 and len(confirmation_sha256) != 64:
            raise ValueError("cutover confirmation digest is invalid")
        self._state_path = state_path
        self._confirmation_sha256 = confirmation_sha256
        self._health_probe = health_probe or _default_health_probe
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def plan(
        self,
        *,
        target: TargetName,
        expected_revision: int,
        evidence: CutoverEvidence,
    ) -> CutoverPlan:
        state = self._load()
        if state.revision != expected_revision:
            raise RuntimeError("route revision conflict")
        blockers: list[str] = []
        checks = {
            "migration_not_ready": evidence.migration_ready,
            "reconciliation_not_ready": evidence.reconciliation_ready,
            "golden_path_not_ready": evidence.golden_path_ready,
            "legacy_source_not_read_only": evidence.source_read_only,
            "rollback_not_ready": evidence.rollback_ready,
        }
        for code, passed in checks.items():
            if not passed:
                blockers.append(code)
        if target == state.active_target:
            blockers.append("target_already_active")
        if not self._safe_health(target, state.targets[target]):
            blockers.append("target_health_unavailable")
        evidence_sha256 = _hash_json(evidence.model_dump(mode="json"))
        return CutoverPlan(
            current_target=state.active_target,
            target=target,
            expected_revision=expected_revision,
            ready=not blockers,
            blockers=tuple(blockers),
            evidence_sha256=evidence_sha256,
        )

    def apply(
        self,
        *,
        target: TargetName,
        expected_revision: int,
        evidence: CutoverEvidence,
        confirmation_token: str | None,
        confirmation_ref: str,
    ) -> RouteState:
        self._authorize(confirmation_token)
        state = self._load()
        if state.revision != expected_revision:
            raise RuntimeError("route revision conflict")
        if state.active_target == target:
            if (
                state.receipts
                and state.receipts[-1].operation == "cutover"
                and state.receipts[-1].confirmation_ref == confirmation_ref
            ):
                return state
            raise RuntimeError("route target is already active")
        plan = self.plan(
            target=target,
            expected_revision=expected_revision,
            evidence=evidence,
        )
        if not plan.ready:
            raise RuntimeError(f"cutover preflight failed: {','.join(plan.blockers)}")
        receipt = RouteReceipt(
            operation="cutover",
            previous_target=state.active_target,
            previous_revision=state.revision,
            current_target=target,
            current_revision=state.revision + 1,
            migration_batch_id=evidence.migration_batch_id,
            evidence_sha256=plan.evidence_sha256,
            confirmation_ref=confirmation_ref,
            created_at=self._clock(),
        )
        updated = state.model_copy(update={
            "revision": state.revision + 1,
            "active_target": target,
            "receipts": (*state.receipts, receipt),
        })
        _atomic_write(self._state_path, updated)
        return updated

    def verify(
        self,
        *,
        target: TargetName,
        expected_revision: int,
        confirmation_token: str | None,
    ) -> RouteVerification:
        self._authorize(confirmation_token)
        state = self._load()
        if state.revision != expected_revision:
            raise RuntimeError("route revision conflict")
        if state.active_target != target:
            raise RuntimeError("route target does not match readback")
        healthy = self._safe_health(target, state.targets[target])
        if not healthy:
            raise RuntimeError("active route health unavailable")
        return RouteVerification(
            active_target=state.active_target,
            revision=state.revision,
            healthy=True,
            receipt_count=len(state.receipts),
        )

    def rollback(
        self,
        *,
        expected_revision: int,
        confirmation_token: str | None,
        confirmation_ref: str,
    ) -> RouteState:
        self._authorize(confirmation_token)
        state = self._load()
        if state.revision != expected_revision:
            raise RuntimeError("route revision conflict")
        if state.receipts and state.receipts[-1].operation == "rollback":
            if state.receipts[-1].confirmation_ref == confirmation_ref:
                return state
            raise RuntimeError("route has already been rolled back")
        if not state.receipts:
            raise RuntimeError("rollback receipt is unavailable")
        previous = state.receipts[-1]
        target = previous.previous_target
        if not self._safe_health(target, state.targets[target]):
            raise RuntimeError("rollback target health unavailable")
        receipt = RouteReceipt(
            operation="rollback",
            previous_target=state.active_target,
            previous_revision=state.revision,
            current_target=target,
            current_revision=state.revision + 1,
            migration_batch_id=previous.migration_batch_id,
            evidence_sha256=previous.evidence_sha256,
            confirmation_ref=confirmation_ref,
            created_at=self._clock(),
        )
        updated = state.model_copy(update={
            "revision": state.revision + 1,
            "active_target": target,
            "receipts": (*state.receipts, receipt),
        })
        _atomic_write(self._state_path, updated)
        return updated

    def _load(self) -> RouteState:
        return RouteState.model_validate_json(self._state_path.read_bytes())

    def _authorize(self, token: str | None) -> None:
        if token is None or not self._confirmation_sha256:
            raise PermissionError("cutover action requires confirmation")
        try:
            observed = confirmation_digest(token)
        except ValueError as error:
            raise PermissionError("cutover action requires confirmation") from error
        if not hmac.compare_digest(observed, self._confirmation_sha256):
            raise PermissionError("cutover action requires confirmation")

    def _safe_health(self, name: TargetName, target: RouteTarget) -> bool:
        try:
            return self._health_probe(name, target) is True
        except Exception:
            return False


def _default_health_probe(_: TargetName, target: RouteTarget) -> bool:
    if target.health_ref.startswith(("http://127.0.0.1", "http://localhost")):
        response = httpx.get(target.health_ref, timeout=5.0, follow_redirects=False)
        return response.status_code == 200
    return Path(target.health_ref).is_file()


def _hash_json(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _atomic_write(path: Path, state: RouteState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = state.model_dump_json(indent=2).encode("utf-8") + b"\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or execute a guarded route cutover")
    parser.add_argument("command", choices=("plan", "apply", "verify", "rollback"))
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--expected-revision", type=int, required=True)
    parser.add_argument("--target", choices=("legacy", "learning_space"))
    parser.add_argument("--evidence-file", type=Path)
    parser.add_argument("--confirmation-ref")
    args = parser.parse_args(argv)

    digest = os.environ.get("LEARNING_CUTOVER_CONFIRMATION_SHA256", "")
    token = os.environ.get("LEARNING_CUTOVER_CONFIRMATION_TOKEN")
    controller = CutoverController(args.state_file, confirmation_sha256=digest)
    if args.command in {"plan", "apply"}:
        if args.target is None or args.evidence_file is None:
            parser.error("plan/apply require --target and --evidence-file")
        evidence = CutoverEvidence.model_validate_json(args.evidence_file.read_bytes())
        if args.command == "plan":
            result: BaseModel = controller.plan(
                target=args.target,
                expected_revision=args.expected_revision,
                evidence=evidence,
            )
        else:
            if args.confirmation_ref is None:
                parser.error("apply requires --confirmation-ref")
            result = controller.apply(
                target=args.target,
                expected_revision=args.expected_revision,
                evidence=evidence,
                confirmation_token=token,
                confirmation_ref=args.confirmation_ref,
            )
    elif args.command == "verify":
        if args.target is None:
            parser.error("verify requires --target")
        result = controller.verify(
            target=args.target,
            expected_revision=args.expected_revision,
            confirmation_token=token,
        )
    else:
        if args.confirmation_ref is None:
            parser.error("rollback requires --confirmation-ref")
        result = controller.rollback(
            expected_revision=args.expected_revision,
            confirmation_token=token,
            confirmation_ref=args.confirmation_ref,
        )
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
