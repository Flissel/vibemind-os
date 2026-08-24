from __future__ import annotations

from spaces.learning.deployment.health import GoldenPathEvidence, HealthService


def test_liveness_never_claims_dependency_or_live_execution() -> None:
    health = HealthService(
        dependency_probes={},
        migration_probe=lambda: False,
        structural_probe=lambda: [],
        golden_path_probe=lambda: None,
    ).liveness()

    assert health == {
        "status": "ok",
        "evidence_level": "process",
        "live_claim": False,
        "components": {"process": "responding"},
    }


def test_readiness_reports_qdrant_or_redis_degradation_without_terminal_state() -> None:
    service = HealthService(
        dependency_probes={
            "postgres": lambda: True,
            "redis": lambda: False,
            "qdrant": lambda: False,
        },
        migration_probe=lambda: True,
        structural_probe=lambda: [],
        golden_path_probe=lambda: None,
    )
    readiness = service.readiness()
    golden = service.golden_path()

    assert readiness["status"] == "degraded"
    assert readiness["components"] == {
        "migrations": "ready",
        "postgres": "ready",
        "qdrant": "unavailable",
        "redis": "unavailable",
    }
    assert readiness["live_claim"] is False
    assert golden["status"] == "unverified"
    assert golden["live_claim"] is False


def test_structural_health_is_configured_evidence_only() -> None:
    service = HealthService(
        dependency_probes={},
        migration_probe=lambda: True,
        structural_probe=lambda: ["learning tool scope drift"],
        golden_path_probe=lambda: None,
    )

    report = service.structural()
    assert report["status"] == "degraded"
    assert report["evidence_level"] == "configured"
    assert report["issues"] == ["learning tool scope drift"]
    assert report["live_claim"] is False


def test_golden_path_requires_current_correlated_terminal_readback() -> None:
    current = GoldenPathEvidence(
        correlation_id="correlation-1",
        terminal_readback=True,
        current=True,
    )
    service = HealthService(
        dependency_probes={},
        migration_probe=lambda: True,
        structural_probe=lambda: [],
        golden_path_probe=lambda: current,
    )

    report = service.golden_path()
    assert report["status"] == "ok"
    assert report["evidence_level"] == "verified_live"
    assert report["live_claim"] is True
    assert report["correlation_id"] == "correlation-1"
