"""The Brain -> coding-engine wiring, checked against the engine's own source.

The previous version of this file mocked a server that does not exist: it
answered ``/api/health`` with ``{"healthy": true}`` and asserted calls to
``/api/start``. Those routes came from ``infra/control_server/server.py``
in the coding-engine repo - a branch the image never starts and never even
copies in. Every test passed and every real call would have failed.

So the route assertions here are derived from the engine's actual FastAPI
declarations (``src/api/main.py`` + ``src/api/routes/*.py``) rather than
restated by hand, and the mocked response bodies are pinned to literals
that must still be present in that source.
"""
from pathlib import Path
import re
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "brain" / "the_brain"))

from core.capability_router import CapabilityRouter
from core.capability_targets import CodingEngineExecutor

ENGINE_API = ROOT / "coding-engine" / "src" / "api"
CAPABILITIES = ROOT / "brain" / "the_brain" / "data" / "capabilities.yaml"
REGISTRY = ROOT / "config" / "space_agent_registry.yml"

_needs_engine = pytest.mark.skipif(
    not (ENGINE_API / "main.py").is_file(),
    reason="coding-engine submodule not checked out here",
)


def _engine_routes() -> set:
    """(METHOD, path) the running engine actually serves, from its source.

    Reads the router prefix out of main.py and the decorators out of the
    route module, so a route rename on either side breaks this test
    instead of silently breaking the wiring.
    """
    main = (ENGINE_API / "main.py").read_text(encoding="utf-8")
    routes = set()
    for module, prefix in re.findall(
        r"include_router\(\s*(\w+)(?:_routes)?\.router\s*,"
        r"(?:\s*prefix=\"([^\"]*)\")?",
        main,
    ):
        source = ENGINE_API / "routes" / f"{module}.py"
        if not source.is_file():
            continue
        for method, path in re.findall(
            r"@router\.(get|post|put|delete)\(\s*\"([^\"]*)\"",
            source.read_text(encoding="utf-8"),
        ):
            routes.add((method.upper(), (prefix + path) or "/"))
    return routes


def _coding_targets() -> dict:
    """capability name -> execution_target, enabled capabilities only."""
    caps = yaml.safe_load(CAPABILITIES.read_text(encoding="utf-8"))
    return {
        c["capability"]: c["execution_target"]
        for c in caps
        if isinstance(c, dict)
        and str(c.get("execution_target", "")).startswith("coding-engine:")
        and c.get("enabled") is not False
    }


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


# ---------------------------------------------------------------------
# The wiring itself: every target must name a route the engine serves.
# ---------------------------------------------------------------------

@_needs_engine
def test_every_enabled_coding_target_exists_in_the_engine_api():
    routes = _engine_routes()
    assert ("POST", "/api/v1/jobs") in routes, (
        "route extraction is broken, not the wiring"
    )

    missing = []
    for name, target in sorted(_coding_targets().items()):
        _, method, route = target.split(":", 2)
        # Path params are declared as {job_id} on our side and {job_id} in
        # FastAPI too, so they compare directly.
        if (method.upper(), route) not in routes:
            missing.append(f"{name} -> {method} {route}")
    assert not missing, (
        "these capabilities point at routes the engine does not serve: "
        + ", ".join(missing)
    )


@_needs_engine
def test_health_gate_uses_the_route_and_shape_the_engine_answers():
    """The gate used to ask /api/health for {"healthy": true}; the engine
    answers /health with {"status": "healthy"}, so every call failed closed
    before it began."""
    health_source = (ENGINE_API / "routes" / "health.py").read_text(
        encoding="utf-8"
    )
    assert '@router.get("/health")' in health_source
    assert '"status": "healthy"' in health_source


def test_preview_capabilities_are_disabled():
    """No /api/preview route exists anywhere in the engine, so these two
    must not be routable."""
    caps = yaml.safe_load(CAPABILITIES.read_text(encoding="utf-8"))
    by_name = {c["capability"]: c for c in caps if isinstance(c, dict)}
    for name in ("code_preview_start", "code_preview_stop"):
        assert by_name[name].get("enabled") is False, name

    router = CapabilityRouter(CAPABILITIES)
    assert router.route("code.preview.start") is None
    assert router.route("code.preview.stop") is None


def test_registry_and_capabilities_agree_on_every_coding_event():
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    events = registry["spaces"]["coding"]["events"]
    router = CapabilityRouter(CAPABILITIES)

    for event, config in events.items():
        match = router.route(event)
        if match is None:
            # Only the two disabled preview events may be unroutable.
            assert event.startswith("code.preview."), event
            continue
        assert match.execution_target == config["tool"], event


# ---------------------------------------------------------------------
# Executor behaviour
# ---------------------------------------------------------------------

def _healthy(url):
    return _Response({"status": "healthy"})


def test_start_submits_a_job_with_project_id_and_a_string_payload(monkeypatch):
    """JobSubmit wants project_id: int and requirements_json: str. The old
    executor sent a bare dict under requirements_json and no project at
    all, which the engine rejects with 422."""
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/health"):
            return _healthy(url)
        if url.endswith("/api/v1/projects") and method == "GET":
            return _Response({"projects": [{"id": 7, "name": "vibemind-brain"}]})
        return _Response({"id": 42, "status": "pending"})

    monkeypatch.setattr("core.capability_targets.requests.request", request)
    monkeypatch.delenv("CODING_ENGINE_PROJECT_ID", raising=False)
    executor = CodingEngineExecutor("coding-engine:POST:/api/v1/jobs")

    result = executor.call(description="write hello.py")

    assert result["ok"] is True
    submitted = calls[-1]
    assert submitted[1] == "http://127.0.0.1:8000/api/v1/jobs"
    body = submitted[2]["json"]
    assert body["project_id"] == 7
    assert isinstance(body["requirements_json"], str)
    assert "write hello.py" in body["requirements_json"]


def test_pinned_project_id_skips_the_lookup(monkeypatch):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/health"):
            return _healthy(url)
        return _Response({"id": 1, "status": "pending"})

    monkeypatch.setattr("core.capability_targets.requests.request", request)
    monkeypatch.setenv("CODING_ENGINE_PROJECT_ID", "3")

    CodingEngineExecutor("coding-engine:POST:/api/v1/jobs").call(
        description="x"
    )

    assert not any("/projects" in url for _, url, _ in calls)
    assert calls[-1][2]["json"]["project_id"] == 3


def test_status_resolves_the_most_recent_job(monkeypatch):
    """code.status carries no job id - it means the run that is going on.
    list_jobs orders by created_at desc, so the first row is that run."""
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/health"):
            return _healthy(url)
        if url.endswith("/api/v1/jobs") and method == "GET":
            return _Response({"jobs": [{"id": 99}], "total": 1})
        return _Response({"status": "running", "progress_percent": 40.0})

    monkeypatch.setattr("core.capability_targets.requests.request", request)
    monkeypatch.setenv("CODING_ENGINE_PROJECT_ID", "3")

    result = CodingEngineExecutor(
        "coding-engine:GET:/api/v1/jobs/{job_id}/status"
    ).call()

    assert result["ok"] is True
    assert calls[-1][1] == "http://127.0.0.1:8000/api/v1/jobs/99/status"


def test_status_fails_closed_when_no_job_exists(monkeypatch):
    def request(method, url, **kwargs):
        if url.endswith("/health"):
            return _healthy(url)
        if url.endswith("/api/v1/jobs") and method == "GET":
            return _Response({"jobs": [], "total": 0})
        raise AssertionError("must not call a job route without a job")

    monkeypatch.setattr("core.capability_targets.requests.request", request)
    monkeypatch.setenv("CODING_ENGINE_PROJECT_ID", "3")

    result = CodingEngineExecutor(
        "coding-engine:GET:/api/v1/jobs/{job_id}/status"
    ).call()

    assert result["ok"] is False
    assert "no coding-engine job" in result["error"]


def test_explicit_job_id_wins_over_the_lookup(monkeypatch):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/health"):
            return _healthy(url)
        return _Response({"status": "done"})

    monkeypatch.setattr("core.capability_targets.requests.request", request)

    CodingEngineExecutor(
        "coding-engine:GET:/api/v1/jobs/{job_id}/results"
    ).call(job_id=5)

    assert calls[-1][1] == "http://127.0.0.1:8000/api/v1/jobs/5/results"
    assert not any("limit" in (kw.get("params") or {}) for _, _, kw in calls)


def test_unhealthy_engine_fails_closed_before_any_call(monkeypatch):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return _Response({"status": "degraded"})

    monkeypatch.setattr("core.capability_targets.requests.request", request)

    result = CodingEngineExecutor("coding-engine:GET:/api/v1/jobs").call()

    assert result["ok"] is False
    assert "unhealthy" in result["error"].lower()
    assert len(calls) == 1


def test_unrecognised_health_body_is_not_healthy(monkeypatch):
    """A body the gate does not understand must never read as healthy."""
    def request(method, url, **kwargs):
        return _Response({"uptime": 12})

    monkeypatch.setattr("core.capability_targets.requests.request", request)

    result = CodingEngineExecutor("coding-engine:GET:/api/v1/jobs").call()

    assert result["ok"] is False
    assert "unhealthy" in result["error"].lower()
