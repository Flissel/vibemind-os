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
    "LEARNHOUSE_LEARNING_SERVICE_KEY",
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
        "${LEARNHOUSE_LOCAL_BOOTSTRAP_KEY:?set LEARNHOUSE_LOCAL_BOOTSTRAP_KEY}"
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


def test_learning_bootstrap_uses_internal_api_service_address() -> None:
    services = _load(COMPOSE_PATH)["services"]
    assert isinstance(services, dict)
    web = services["learnhouse-web"]
    assert isinstance(web, dict)
    environment = web["environment"]
    assert isinstance(environment, dict)

    assert environment["LEARNHOUSE_INTERNAL_API_URL"] == (
        "http://learnhouse-api:9000/api/v1/"
    )
    assert environment["NEXT_PUBLIC_LEARNHOUSE_BACKEND_URL"] == (
        "http://127.0.0.1:${LEARNHOUSE_API_PORT:-1338}"
    )
    assert "NEXT_PUBLIC_LEARNHOUSE_INTERNAL_API_URL" not in environment


def test_learning_service_credentials_are_limited_to_authorized_services() -> None:
    services = _load(COMPOSE_PATH)["services"]
    assert isinstance(services, dict)
    api = services["learnhouse-api"]
    mcp = services["learning-mcp"]
    worker = services["learning-worker"]
    assert isinstance(api, dict)
    assert isinstance(mcp, dict)
    assert isinstance(worker, dict)
    api_environment = api["environment"]
    mcp_environment = mcp["environment"]
    worker_environment = worker["environment"]
    assert isinstance(api_environment, dict)
    assert isinstance(mcp_environment, dict)
    assert isinstance(worker_environment, dict)

    key_reference = (
        "${LEARNHOUSE_LEARNING_SERVICE_KEY:?set LEARNHOUSE_LEARNING_SERVICE_KEY}"
    )
    assert api_environment["LEARNHOUSE_LEARNING_SERVICE_KEY"] == key_reference
    assert mcp_environment["LEARNHOUSE_LEARNING_SERVICE_KEY"] == key_reference
    assert worker_environment["LEARNHOUSE_LEARNING_SERVICE_KEY"] == key_reference
    assert mcp_environment["LEARNHOUSE_LEARNING_SERVICE_URL"] == (
        "http://learnhouse-api:9000/api/v1/learning"
    )
    assert worker_environment["LEARNHOUSE_LEARNING_SERVICE_URL"] == (
        "http://learnhouse-api:9000/api/v1/learning"
    )
    for service_name, service in services.items():
        assert isinstance(service, dict)
        environment = service.get("environment", {})
        assert isinstance(environment, dict)
        if service_name not in {"learnhouse-api", "learning-mcp", "learning-worker"}:
            assert "LEARNHOUSE_LEARNING_SERVICE_KEY" not in environment
            assert "LEARNHOUSE_LEARNING_SERVICE_URL" not in environment


def test_learning_runtime_uses_only_the_openfang_authorized_embedding_service() -> None:
    services = _load(COMPOSE_PATH)["services"]
    assert isinstance(services, dict)
    for service_name in ("learning-api", "learning-worker"):
        service = services[service_name]
        assert isinstance(service, dict)
        environment = service["environment"]
        assert isinstance(environment, dict)
        assert environment["LEARNING_EMBEDDING_URL"] == (
            "${LEARNING_EMBEDDING_URL:?set LEARNING_EMBEDDING_URL}"
        )
    for service_name, service in services.items():
        assert isinstance(service, dict)
        service_environment = service.get("environment", {})
        assert isinstance(service_environment, dict)
        if service_name not in {"learning-api", "learning-worker"}:
            assert "LEARNING_EMBEDDING_URL" not in service_environment
    for env_path in (ENV_EXAMPLE_PATH, ROOT_ENV_EXAMPLE_PATH):
        assert re.search(
            r"(?m)^LEARNING_EMBEDDING_URL=<[^>]+>$",
            env_path.read_text(encoding="utf-8"),
        )


def test_learning_retrieval_credentials_are_scoped_to_both_api_peers() -> None:
    services = _load(COMPOSE_PATH)["services"]
    expected_key = "${LEARNING_RETRIEVAL_SERVICE_KEY:?set LEARNING_RETRIEVAL_SERVICE_KEY}"

    assert services["learnhouse-api"]["environment"]["LEARNING_RETRIEVAL_SERVICE_URL"] == (
        "http://learning-api:8090"
    )
    for service_name in ("learnhouse-api", "learning-api"):
        assert (
            services[service_name]["environment"]["LEARNING_RETRIEVAL_SERVICE_KEY"]
            == expected_key
        )
    for service_name, service in services.items():
        if service_name not in {"learnhouse-api", "learning-api"}:
            assert "LEARNING_RETRIEVAL_SERVICE_KEY" not in service.get("environment", {})


def test_course_factory_model_authority_is_scoped_to_learning_worker() -> None:
    services = _load(COMPOSE_PATH)["services"]
    worker_environment = services["learning-worker"]["environment"]

    assert worker_environment["LEARNING_OPENFANG_URL"] == (
        "${LEARNING_OPENFANG_URL:?set LEARNING_OPENFANG_URL}"
    )
    assert worker_environment["LEARNING_OPENFANG_API_KEY"] == (
        "${LEARNING_OPENFANG_API_KEY:?set LEARNING_OPENFANG_API_KEY}"
    )
    assert worker_environment["LEARNING_OPENFANG_MODEL"] == (
        "${LEARNING_OPENFANG_MODEL:-learning-course-factory}"
    )
    for service_name, service in services.items():
        environment = service.get("environment", {})
        if service_name != "learning-worker":
            assert "LEARNING_OPENFANG_API_KEY" not in environment
            assert "LEARNING_OPENFANG_URL" not in environment
        for provider_key in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "GOOGLE_API_KEY",
        ):
            assert provider_key not in environment
    for env_path in (ENV_EXAMPLE_PATH, ROOT_ENV_EXAMPLE_PATH):
        env_text = env_path.read_text(encoding="utf-8")
        assert re.search(r"(?m)^LEARNING_OPENFANG_URL=<[^>]+>$", env_text)
        assert re.search(r"(?m)^LEARNING_OPENFANG_API_KEY=<[^>]+>$", env_text)


def test_profile_is_explicitly_local_and_has_no_ha_claim() -> None:
    profile = _load(PROFILE_PATH)

    assert profile["profile"] == "local-single-host"
    assert profile["availability"] == "restart-and-persistence"
    assert profile["multi_host_ha"] is False
    assert profile["compose_file"] == "compose.yml"
    assert profile["authoritative_status_tool"] == "learning_status"


def test_learnhouse_api_migrates_before_bypassing_checkout_entrypoint() -> None:
    services = _load(COMPOSE_PATH)["services"]
    assert isinstance(services, dict)
    api = services["learnhouse-api"]
    assert isinstance(api, dict)

    assert api["entrypoint"] == ["/bin/bash", "-c"]
    assert api["command"] == [
        "/app/.venv/bin/python -m scripts.runtime_schema && "
        "exec /app/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 9000 "
        "--timeout-keep-alive 600"
    ]
    environment = api["environment"]
    assert isinstance(environment, dict)
    assert (
        "${LEARNHOUSE_INITIAL_ADMIN_PASSWORD:?"
        in environment["LEARNHOUSE_INITIAL_ADMIN_PASSWORD"]
    )


def test_learnhouse_collab_receives_upstream_environment_contract() -> None:
    services = _load(COMPOSE_PATH)["services"]
    assert isinstance(services, dict)
    collab = services["learnhouse-collab"]
    assert isinstance(collab, dict)
    environment = collab["environment"]
    assert isinstance(environment, dict)

    assert (
        "${LEARNHOUSE_AUTH_JWT_SECRET_KEY:?"
        in environment["LEARNHOUSE_AUTH_JWT_SECRET_KEY"]
    )
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
    assert (
        "${LEARNHOUSE_WEB_PORT:-13000}"
        in api["environment"]["LEARNHOUSE_ALLOWED_ORIGINS"]
    )
