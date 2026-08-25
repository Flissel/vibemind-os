from __future__ import annotations

import re
from pathlib import Path

import yaml


DEPLOYMENT_DIR = Path(__file__).resolve().parent
COMPOSE_PATH = DEPLOYMENT_DIR / "compose.yml"
PROFILE_PATH = DEPLOYMENT_DIR / "profile.yml"
ENV_EXAMPLE_PATH = DEPLOYMENT_DIR / ".env.example"
ROOT_ENV_EXAMPLE_PATH = DEPLOYMENT_DIR.parents[2] / ".env.example"

REQUIRED_SERVICES = {
    "postgres",
    "redis",
    "qdrant",
    "learnhouse-api",
    "learnhouse-web",
    "learnhouse-collab",
    "learning-api",
    "learning-worker",
    "learning-mcp",
    "penecho",
}
STATEFUL_VOLUMES = {
    "postgres-data",
    "redis-data",
    "qdrant-data",
    "learnhouse-content",
    "learning-artifacts",
    "penecho-state",
}
SECRET_VARIABLES = {
    "LEARNING_POSTGRES_PASSWORD",
    "LEARNHOUSE_AUTH_JWT_SECRET_KEY",
    "LEARNHOUSE_COLLAB_INTERNAL_KEY",
    "LEARNHOUSE_INITIAL_ADMIN_PASSWORD",
    "LEARNHOUSE_LOCAL_BOOTSTRAP_KEY",
}


def _load(path: Path) -> dict[str, object]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_compose_declares_complete_local_runtime() -> None:
    compose = _load(COMPOSE_PATH)
    services = compose["services"]

    assert isinstance(services, dict)
    assert set(services) == REQUIRED_SERVICES
    assert set(compose["volumes"]) == STATEFUL_VOLUMES
    assert compose["name"] == "vibemind-learning"


def test_published_ports_are_loopback_only() -> None:
    services = _load(COMPOSE_PATH)["services"]
    assert isinstance(services, dict)

    for service_name, raw_service in services.items():
        assert isinstance(raw_service, dict)
        for port in raw_service.get("ports", []):
            rendered = str(port)
            assert rendered.startswith("127.0.0.1:"), (
                f"{service_name} publishes a non-loopback port: {rendered}"
            )


def test_services_restart_and_wait_for_healthy_dependencies() -> None:
    services = _load(COMPOSE_PATH)["services"]
    assert isinstance(services, dict)

    for service_name, raw_service in services.items():
        assert isinstance(raw_service, dict)
        assert raw_service.get("restart") == "unless-stopped", service_name
        assert "healthcheck" in raw_service, service_name

        depends_on = raw_service.get("depends_on", {})
        assert isinstance(depends_on, dict), service_name
        for dependency, relationship in depends_on.items():
            assert isinstance(relationship, dict), (service_name, dependency)
            assert relationship.get("condition") in {
                "service_healthy",
                "service_completed_successfully",
            }, (service_name, dependency)


def test_stateful_mounts_use_named_volumes() -> None:
    compose = _load(COMPOSE_PATH)
    services = compose["services"]
    declared = set(compose["volumes"])

    assert isinstance(services, dict)
    mounted = {
        str(mount).split(":", 1)[0]
        for service in services.values()
        if isinstance(service, dict)
        for mount in service.get("volumes", [])
    }
    assert STATEFUL_VOLUMES <= mounted
    assert mounted <= declared


def test_credentials_are_references_not_embedded_values() -> None:
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")
    env_text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")

    assert not re.search(r"sk-[A-Za-z0-9_-]{12,}", compose_text + env_text)
    assert "password: learnhouse" not in compose_text.lower()
    for variable in SECRET_VARIABLES:
        assert f"${{{variable}:?" in compose_text
        assert re.search(rf"(?m)^{variable}=<[^>]+>$", env_text)


def test_local_bootstrap_mode_and_key_are_shared_server_only_references() -> None:
    services = _load(COMPOSE_PATH)["services"]
    assert isinstance(services, dict)
    api = services["learnhouse-api"]
    web = services["learnhouse-web"]
    assert isinstance(api, dict)
    assert isinstance(web, dict)
    api_environment = api["environment"]
    web_environment = web["environment"]
    assert isinstance(api_environment, dict)
    assert isinstance(web_environment, dict)

    mode_reference = "${LEARNHOUSE_LOCAL_LEARNING_MODE:-false}"
    key_reference = (
        "${LEARNHOUSE_LOCAL_BOOTSTRAP_KEY:?set "
        "LEARNHOUSE_LOCAL_BOOTSTRAP_KEY}"
    )
    assert api_environment["LEARNHOUSE_LOCAL_LEARNING_MODE"] == mode_reference
    assert web_environment["LEARNHOUSE_LOCAL_LEARNING_MODE"] == mode_reference
    assert api_environment["LEARNHOUSE_LOCAL_BOOTSTRAP_KEY"] == key_reference
    assert web_environment["LEARNHOUSE_LOCAL_BOOTSTRAP_KEY"] == key_reference
    assert "NEXT_PUBLIC_LEARNHOUSE_LOCAL_BOOTSTRAP_KEY" not in web_environment

    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "LEARNHOUSE_ALLOW_REMOTE_LEARNING_BOOTSTRAP" not in compose_text
    for env_path in (ENV_EXAMPLE_PATH, ROOT_ENV_EXAMPLE_PATH):
        env_text = env_path.read_text(encoding="utf-8")
        assert re.search(
            r"(?m)^LEARNHOUSE_LOCAL_LEARNING_MODE=true$",
            env_text,
        )
        assert re.search(
            r"(?m)^LEARNHOUSE_LOCAL_BOOTSTRAP_KEY=<[^>]+>$",
            env_text,
        )


def test_profile_is_explicitly_local_and_has_no_ha_claim() -> None:
    profile = _load(PROFILE_PATH)

    assert profile["profile"] == "local-single-host"
    assert profile["availability"] == "restart-and-persistence"
    assert profile["multi_host_ha"] is False
    assert profile["compose_file"] == "compose.yml"
    assert profile["authoritative_status_tool"] == "learning_status"


def test_learnhouse_api_bypasses_checkout_entrypoint_line_endings() -> None:
    services = _load(COMPOSE_PATH)["services"]
    assert isinstance(services, dict)
    api = services["learnhouse-api"]
    assert isinstance(api, dict)

    assert api["entrypoint"] == ["/bin/bash", "-c"]
    assert api["command"] == [
        "exec /app/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 9000 "
        "--timeout-keep-alive 600"
    ]
    environment = api["environment"]
    assert isinstance(environment, dict)
    assert "${LEARNHOUSE_INITIAL_ADMIN_PASSWORD:?" in environment[
        "LEARNHOUSE_INITIAL_ADMIN_PASSWORD"
    ]


def test_learnhouse_collab_receives_upstream_environment_contract() -> None:
    services = _load(COMPOSE_PATH)["services"]
    assert isinstance(services, dict)
    collab = services["learnhouse-collab"]
    assert isinstance(collab, dict)
    environment = collab["environment"]
    assert isinstance(environment, dict)

    assert "${LEARNHOUSE_AUTH_JWT_SECRET_KEY:?" in environment[
        "LEARNHOUSE_AUTH_JWT_SECRET_KEY"
    ]
    assert environment["LEARNHOUSE_REDIS_URL"] == "redis://redis:6379/0"
    assert environment["LEARNHOUSE_API_URL"] == "http://learnhouse-api:9000"
    assert "DATABASE_URL" not in environment
    assert "REDIS_URL" not in environment


def test_postgres_image_provides_learnhouse_pgvector_contract() -> None:
    services = _load(COMPOSE_PATH)["services"]
    assert isinstance(services, dict)
    postgres = services["postgres"]
    assert isinstance(postgres, dict)

    assert postgres["image"] == "pgvector/pgvector:pg16"


def test_learnhouse_web_uses_the_reserved_learning_space_default_port() -> None:
    services = _load(COMPOSE_PATH)["services"]
    assert isinstance(services, dict)
    web = services["learnhouse-web"]
    assert isinstance(web, dict)

    assert web["ports"] == ["127.0.0.1:${LEARNHOUSE_WEB_PORT:-13000}:3000"]
    assert web["entrypoint"] == ["/bin/sh", "-c"]
    assert web["command"] == ["exec env bun server-wrapper.js"]
    api = services["learnhouse-api"]
    assert isinstance(api, dict)
    assert "${LEARNHOUSE_WEB_PORT:-13000}" in api["environment"][
        "LEARNHOUSE_ALLOWED_ORIGINS"
    ]
