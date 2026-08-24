from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class GoldenPathEvidence:
    correlation_id: str
    terminal_readback: bool
    current: bool


class HealthService:
    def __init__(
        self,
        *,
        dependency_probes: dict[str, Callable[[], bool]],
        migration_probe: Callable[[], bool],
        structural_probe: Callable[[], list[str]],
        golden_path_probe: Callable[[], GoldenPathEvidence | None],
    ) -> None:
        self._dependency_probes = dependency_probes
        self._migration_probe = migration_probe
        self._structural_probe = structural_probe
        self._golden_path_probe = golden_path_probe

    def liveness(self) -> dict:
        return {
            "status": "ok",
            "evidence_level": "process",
            "live_claim": False,
            "components": {"process": "responding"},
        }

    def readiness(self) -> dict:
        components = {
            "migrations": "ready" if self._safe(self._migration_probe) else "unavailable"
        }
        for name in sorted(self._dependency_probes):
            components[name] = (
                "ready" if self._safe(self._dependency_probes[name]) else "unavailable"
            )
        ready = all(value == "ready" for value in components.values())
        return {
            "status": "ok" if ready else "degraded",
            "evidence_level": "reachable" if ready else "configured",
            "live_claim": False,
            "components": components,
        }

    def structural(self) -> dict:
        try:
            issues = list(self._structural_probe())
        except Exception:
            issues = ["structural probe failed"]
        return {
            "status": "ok" if not issues else "degraded",
            "evidence_level": "configured",
            "live_claim": False,
            "issues": issues,
        }

    def golden_path(self) -> dict:
        try:
            evidence = self._golden_path_probe()
        except Exception:
            evidence = None
        verified = bool(
            evidence is not None
            and evidence.current
            and evidence.terminal_readback
            and evidence.correlation_id
        )
        report = {
            "status": "ok" if verified else "unverified",
            "evidence_level": "verified_live" if verified else "configured",
            "live_claim": verified,
        }
        if verified and evidence is not None:
            report["correlation_id"] = evidence.correlation_id
        return report

    @staticmethod
    def _safe(probe: Callable[[], bool]) -> bool:
        try:
            return probe() is True
        except Exception:
            return False
