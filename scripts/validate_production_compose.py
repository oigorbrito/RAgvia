from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

EXPECTED_SERVICES = {
    "postgres",
    "migrate",
    "api",
    "egress-proxy",
    "attachment-parser",
    "worker-1",
    "worker-2",
    "scheduler",
}
APPLICATION_SERVICES = (
    "migrate",
    "api",
    "egress-proxy",
    "attachment-parser",
    "worker-1",
    "worker-2",
    "scheduler",
)
IMMUTABLE_IMAGE_RE = re.compile(r"^.+@sha256:[0-9a-fA-F]{64}$")
DURATION_RE = re.compile(r"^(?P<value>[0-9]+(?:\.[0-9]+)?)(?P<unit>ms|s|m|h)$")
API_REQUIRED_ENV = {
    "DATABASE_URL",
    "JUDIT_WEBHOOK_TOKEN",
    "JUDIT_WEBHOOK_MAX_BODY_BYTES",
    "RPY_BEARER_TOKENS",
    "RPY_OPS_TOKEN",
    "RPY_BROWSER_ORIGINS",
    "DATAJUD_ENABLED",
    "DATAJUD_AUTHORIZED_USE",
    "OPS_BACKUP_MAX_AGE_SECONDS",
    "OPS_QUEUE_WARN_SECONDS",
    "OPS_QUEUE_CRITICAL_SECONDS",
    "OPS_STALE_PROCESSING_SECONDS",
    "OPS_DEAD_JOBS_WARN_24H",
    "OPS_DEAD_JOBS_CRITICAL_24H",
    "OPS_GENERATION_P95_WARN_MS",
    "OPS_GENERATION_P95_CRITICAL_MS",
}
WORKER_REQUIRED_ENV = {
    "DATABASE_URL",
    "JUDIT_API_KEY",
    "JUDIT_CALLBACK_URL",
    "JUDIT_TIMEOUT_SECONDS",
    "DATAJUD_ENABLED",
    "DATAJUD_AUTHORIZED_USE",
    "DATAJUD_API_KEY",
    "DATAJUD_BASE_URL",
    "DATAJUD_TIMEOUT_SECONDS",
    "LANGFUSE_ENABLED",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "LANGFUSE_TRACING_ENVIRONMENT",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "EMBEDDING_MODEL",
    "EMBEDDING_SPACE_RUNTIME_ENABLED",
    "EMBEDDING_PROVIDER",
    "BGE_EMBEDDING_MODEL",
    "BGE_EMBEDDING_PATH",
    "BGE_EMBEDDING_DEVICE",
    "BGE_EMBEDDING_USE_FP16",
    "ALLOW_EXTERNAL_EMBEDDINGS",
    "COHERE_API_KEY",
    "COHERE_EMBEDDING_MODEL",
    "RERANKER_ENABLED",
    "RERANKER_PROVIDER",
    "RERANKER_MODEL",
    "BGE_RERANKER_PATH",
    "RERANKER_USE_FP16",
    "ALLOW_EXTERNAL_RERANKER",
    "COHERE_RERANKER_MODEL",
    "RERANKER_TIMEOUT_SECONDS",
    "ANTHROPIC_TIMEOUT_SECONDS",
    "EMBEDDING_TIMEOUT_SECONDS",
    "PROVIDER_MAX_ATTEMPTS",
    "PROVIDER_RETRY_BACKOFF_SECONDS",
    "PROVIDER_PROMPT_MAX_CHARS",
    "PROVIDER_STEP_TEXT_MAX_CHARS",
    "PROVIDER_STEPS_TEXT_MAX_CHARS",
    "WORKER_TASK_TIMEOUT_SECONDS",
    "WORKER_SHUTDOWN_GRACE_SECONDS",
    "HTTPS_PROXY",
    "https_proxy",
    "NO_PROXY",
    "no_proxy",
    "HF_HOME",
    "XDG_CACHE_HOME",
    "ATTACHMENT_PARSER_SOCKET",
    "ATTACHMENT_PARSER_TIMEOUT_SECONDS",
}
PARSER_REQUIRED_ENV = {
    "ATTACHMENT_PARSER_SOCKET",
    "ATTACHMENT_PARSER_REQUEST_TIMEOUT_SECONDS",
    "ATTACHMENT_MAX_BYTES",
    "ATTACHMENT_CHUNK_CHARS",
    "ATTACHMENT_OCR_ENABLED",
    "ATTACHMENT_OCR_BINARY",
    "ATTACHMENT_OCR_LANGUAGE",
    "ATTACHMENT_OCR_TIMEOUT_SECONDS",
    "ATTACHMENT_PDF_OCR_SCALE",
    "ATTACHMENT_PDF_OCR_MAX_PAGES",
}
SCHEDULER_REQUIRED_ENV = {
    "DATABASE_URL",
    "RETENTION_DAYS",
    "JOB_RETENTION_DAYS",
    "EXPUNGE_INTERVAL_SECONDS",
    "TRACKING_STALE_HOURS",
    "TRACKING_RECONCILE_BATCH",
}
MIGRATE_REQUIRED_ENV = {
    "MIGRATION_DATABASE_URL",
    "API_DATABASE_URL",
    "WORKER_DATABASE_URL",
    "SCHEDULER_DATABASE_URL",
    "BACKUP_DATABASE_URL",
}
HTTP_SECRETS = {"JUDIT_WEBHOOK_TOKEN", "RPY_BEARER_TOKENS", "RPY_OPS_TOKEN"}
PROVIDER_SECRETS = {
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "COHERE_API_KEY",
    "JUDIT_API_KEY",
    "JUDIT_CALLBACK_URL",
    "DATAJUD_API_KEY",
    "LANGFUSE_SECRET_KEY",
}
DB_ROLE_USERS = {
    "api": "rpy_api",
    "worker-1": "rpy_worker",
    "worker-2": "rpy_worker",
    "scheduler": "rpy_scheduler",
}


def _fail(message: str) -> None:
    raise SystemExit(f"production compose contract failed: {message}")


def _environment(services: dict[str, Any], service_name: str) -> dict[str, Any]:
    environment = services[service_name].get("environment") or {}
    if not isinstance(environment, dict):
        _fail(f"{service_name} environment must be an object")
    return environment


def _require_env(
    services: dict[str, Any], service_name: str, required: set[str]
) -> dict[str, Any]:
    environment = _environment(services, service_name)
    missing = sorted(required - set(environment))
    if missing:
        _fail(f"{service_name} environment is missing required settings: {missing}")
    return environment


def _forbid_env(
    services: dict[str, Any], service_name: str, forbidden: set[str]
) -> None:
    environment = _environment(services, service_name)
    leaked = sorted(forbidden & set(environment))
    if leaked:
        _fail(f"{service_name} must not receive unrelated secrets: {leaked}")


def _validate_application_image(services: dict[str, Any]) -> None:
    images: dict[str, str] = {}
    for service_name in APPLICATION_SERVICES:
        service = services[service_name]
        if "build" in service:
            _fail(f"{service_name} must not build source on the production host")
        image = str(service.get("image") or "")
        if not IMMUTABLE_IMAGE_RE.fullmatch(image):
            _fail(
                f"{service_name} image must be pinned by sha256 digest, got {image!r}"
            )
        images[service_name] = image

    unique_images = set(images.values())
    if len(unique_images) != 1:
        rendered = ", ".join(f"{name}={image}" for name, image in images.items())
        _fail(f"all application services must use the same image digest: {rendered}")


def _database_user(url: str) -> str:
    return urlparse(url).username or ""


def _validate_database_isolation(services: dict[str, Any]) -> None:
    runtime_urls: dict[str, str] = {}
    for service_name, expected_user in DB_ROLE_USERS.items():
        environment = _environment(services, service_name)
        database_url = str(environment.get("DATABASE_URL") or "")
        actual_user = _database_user(database_url)
        if actual_user != expected_user:
            _fail(
                f"{service_name} DATABASE_URL must use role {expected_user!r}, got {actual_user!r}"
            )
        runtime_urls[service_name] = database_url

    if runtime_urls["api"] == runtime_urls["worker-1"]:
        _fail("api and worker database credentials must be distinct")
    if runtime_urls["api"] == runtime_urls["scheduler"]:
        _fail("api and scheduler database credentials must be distinct")
    if runtime_urls["worker-1"] == runtime_urls["scheduler"]:
        _fail("worker and scheduler database credentials must be distinct")
    if runtime_urls["worker-1"] != runtime_urls["worker-2"]:
        _fail("both workers must use the same worker database credential")

    migrate_env = _environment(services, "migrate")
    if set(migrate_env) != MIGRATE_REQUIRED_ENV:
        _fail("migrate must receive only migration/runtime database provisioning URLs")
    migration_url = str(migrate_env["MIGRATION_DATABASE_URL"])
    migration_user = _database_user(migration_url)
    if migration_user in set(DB_ROLE_USERS.values()) | {"rpy_backup"}:
        _fail("migration credential must be distinct from every runtime database role")

    expected_provisioning_users = {
        "API_DATABASE_URL": "rpy_api",
        "WORKER_DATABASE_URL": "rpy_worker",
        "SCHEDULER_DATABASE_URL": "rpy_scheduler",
        "BACKUP_DATABASE_URL": "rpy_backup",
    }
    for env_name, expected_user in expected_provisioning_users.items():
        actual_user = _database_user(str(migrate_env[env_name]))
        if actual_user != expected_user:
            _fail(f"migrate {env_name} must use role {expected_user!r}")



_RUNTIME_LIMITS = {
    "migrate": {"cpus": 1.0, "mem_limit": 512 * 1024 * 1024, "pids_limit": 128},
    "api": {"cpus": 1.0, "mem_limit": 512 * 1024 * 1024, "pids_limit": 128},
    "egress-proxy": {"cpus": 0.5, "mem_limit": 256 * 1024 * 1024, "pids_limit": 128},
    "attachment-parser": {"cpus": 0.75, "mem_limit": 768 * 1024 * 1024, "pids_limit": 64},
    "worker-1": {"cpus": 2.0, "mem_limit": 8 * 1024 * 1024 * 1024, "pids_limit": 256},
    "worker-2": {"cpus": 2.0, "mem_limit": 8 * 1024 * 1024 * 1024, "pids_limit": 256},
    "scheduler": {"cpus": 0.5, "mem_limit": 256 * 1024 * 1024, "pids_limit": 64},
}


def _finite_positive_float(value: Any, *, label: str) -> float:
    if isinstance(value, bool):
        _fail(f"{label} must be numeric")
    try:
        rendered = float(value)
    except (TypeError, ValueError):
        _fail(f"{label} must be numeric")
    if not math.isfinite(rendered):
        _fail(f"{label} must be finite")
    if rendered <= 0:
        _fail(f"{label} must be positive")
    return rendered


def _numeric(value: Any, *, field: str, service_name: str) -> float:
    return _finite_positive_float(value, label=f"{service_name} {field}")


def _memory_bytes(value: Any, *, service_name: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        if value <= 0:
            _fail(f"{service_name} mem_limit must be positive")
        return value
    rendered = str(value or "").strip().casefold()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([kmgt]?)(?:i?b)?", rendered)
    if match is None:
        _fail(f"{service_name} mem_limit has unsupported format")
    amount = float(match.group(1))
    unit = match.group(2)
    multiplier = {
        "": 1,
        "k": 1024,
        "m": 1024**2,
        "g": 1024**3,
        "t": 1024**4,
    }[unit]
    result = int(amount * multiplier)
    if result <= 0:
        _fail(f"{service_name} mem_limit must be positive")
    return result


def _validate_runtime_confinement(services: dict[str, Any]) -> None:
    for service_name, expected in _RUNTIME_LIMITS.items():
        service = services[service_name]
        if service.get("read_only") is not True:
            _fail(f"{service_name} root filesystem must be read-only")
        if str(service.get("user") or "") != "10001:10001":
            _fail(f"{service_name} must run as uid/gid 10001")
        if service.get("privileged") is True:
            _fail(f"{service_name} must not run privileged")
        if str(service.get("network_mode") or "") == "host":
            _fail(f"{service_name} must not use host networking")

        cap_drop = {str(value).upper() for value in (service.get("cap_drop") or [])}
        if cap_drop != {"ALL"}:
            _fail(f"{service_name} must drop all Linux capabilities")
        if service.get("cap_add"):
            _fail(f"{service_name} must not add Linux capabilities")

        security_opt = {str(value).casefold() for value in (service.get("security_opt") or [])}
        if "no-new-privileges:true" not in security_opt:
            _fail(f"{service_name} must enable no-new-privileges")
        if any("seccomp=unconfined" in value for value in security_opt):
            _fail(f"{service_name} must preserve Docker seccomp confinement")

        tmpfs = [str(value) for value in (service.get("tmpfs") or [])]
        tmp_entry = next((value for value in tmpfs if value.startswith("/tmp")), "")  # nosec B108
        if not tmp_entry:
            _fail(f"{service_name} must provide /tmp as tmpfs")
        for required_option in ("noexec", "nosuid", "nodev"):
            if required_option not in tmp_entry:
                _fail(f"{service_name} /tmp tmpfs must include {required_option}")

        cpus = _numeric(service.get("cpus"), field="cpus", service_name=service_name)
        if abs(cpus - expected["cpus"]) > 1e-9:
            _fail(f"{service_name} cpus must equal {expected['cpus']}")
        memory = _memory_bytes(service.get("mem_limit"), service_name=service_name)
        if memory != expected["mem_limit"]:
            _fail(f"{service_name} mem_limit does not match the production budget")
        pids = int(_numeric(service.get("pids_limit"), field="pids_limit", service_name=service_name))
        if pids != expected["pids_limit"]:
            _fail(f"{service_name} pids_limit does not match the production budget")


def _validate_egress_topology(services: dict[str, Any], networks: dict[str, Any]) -> None:
    backend = networks.get("backend") or {}
    provider_gateway = networks.get("provider-gateway") or {}
    if backend.get("internal") is not True:
        _fail("backend network must be internal")
    if provider_gateway.get("internal") is not True:
        _fail("provider-gateway network must be internal")
    if (networks.get("egress") or {}).get("internal") is True:
        _fail("egress network must provide external connectivity for the gateway only")

    expected_networks = {
        "postgres": {"backend"},
        "migrate": {"backend"},
        "api": {"backend"},
        "worker-1": {"backend", "provider-gateway"},
        "worker-2": {"backend", "provider-gateway"},
        "scheduler": {"backend"},
        "egress-proxy": {"provider-gateway", "egress"},
    }
    for service_name, expected in expected_networks.items():
        actual = set((services[service_name].get("networks") or {}).keys())
        if actual != expected:
            _fail(f"{service_name} network set must be {sorted(expected)}, got {sorted(actual)}")

    proxy_env = _environment(services, "egress-proxy")
    if set(proxy_env) != {
        "EGRESS_PROXY_BIND_HOST",
        "EGRESS_PROXY_ALLOWED_HOSTS",
        "EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS",
    }:
        _fail("egress-proxy must receive only its allowlist and timeout settings")
    if proxy_env.get("EGRESS_PROXY_BIND_HOST") != "0.0.0.0":  # nosec B104
        _fail("egress-proxy bind host must remain 0.0.0.0 inside the container network")
    if not str(proxy_env.get("EGRESS_PROXY_ALLOWED_HOSTS") or "").strip():
        _fail("egress-proxy allowlist must not be empty")
    proxy_timeout = _finite_positive_float(
        proxy_env.get("EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS"),
        label="egress-proxy EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS",
    )
    if proxy_timeout > 60:
        _fail("egress-proxy EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS must not exceed 60")

    for service_name in ("worker-1", "worker-2"):
        env = _environment(services, service_name)
        if env.get("HTTPS_PROXY") != "http://egress-proxy:3128":
            _fail(f"{service_name} HTTPS_PROXY must target the egress proxy")
        if env.get("https_proxy") != "http://egress-proxy:3128":
            _fail(f"{service_name} https_proxy must target the egress proxy")
        if env.get("NO_PROXY") != "postgres,localhost,127.0.0.1":
            _fail(f"{service_name} NO_PROXY must remain limited to local/backend names")
        if env.get("no_proxy") != "postgres,localhost,127.0.0.1":
            _fail(f"{service_name} no_proxy must remain limited to local/backend names")


def _volume_mount(service: dict[str, Any], *, source: str, target: str) -> bool:
    for item in service.get("volumes") or []:
        if isinstance(item, dict):
            if item.get("source") == source and item.get("target") == target:
                return True
        elif str(item).split(":")[:2] == [source, target]:
            return True
    return False


def _validate_attachment_parser_contract(
    services: dict[str, Any], volumes: dict[str, Any]
) -> None:
    parser = services["attachment-parser"]
    if str(parser.get("network_mode") or "") != "none":
        _fail("attachment-parser must use network_mode none")
    if parser.get("networks"):
        _fail("attachment-parser must not join any Docker network")
    if parser.get("command") != ["python", "-m", "app.attachment_sandbox"]:
        _fail("attachment-parser command must run the sandbox server")

    parser_env = _require_env(services, "attachment-parser", PARSER_REQUIRED_ENV)
    if set(parser_env) != PARSER_REQUIRED_ENV:
        _fail("attachment-parser must receive only parser settings")
    if parser_env.get("ATTACHMENT_PARSER_SOCKET") != "/run/rpy-parser/parser.sock":
        _fail("attachment-parser socket path must remain fixed inside the sandbox")
    if parser_env.get("ATTACHMENT_OCR_BINARY") != "tesseract":
        _fail("attachment-parser OCR binary must remain the packaged tesseract binary")
    _finite_positive_float(
        parser_env.get("ATTACHMENT_PARSER_REQUEST_TIMEOUT_SECONDS"),
        label="attachment-parser request timeout",
    )
    _forbid_env(
        services,
        "attachment-parser",
        HTTP_SECRETS | PROVIDER_SECRETS | MIGRATE_REQUIRED_ENV | {"DATABASE_URL"},
    )

    if not _volume_mount(parser, source="parser_socket", target="/run/rpy-parser"):
        _fail("attachment-parser must mount the private parser_socket volume")

    socket_volume = volumes.get("parser_socket") or {}
    driver_opts = socket_volume.get("driver_opts") or {}
    if socket_volume.get("driver") != "local":
        _fail("parser_socket must use the local volume driver")
    if driver_opts.get("type") != "tmpfs" or driver_opts.get("device") != "tmpfs":
        _fail("parser_socket must be backed by tmpfs")
    options = str(driver_opts.get("o") or "")
    for required in ("uid=10001", "gid=10001", "mode=0770", "size=1m"):
        if required not in options:
            _fail(f"parser_socket tmpfs must include {required}")

    for service_name in ("worker-1", "worker-2"):
        worker = services[service_name]
        env = _environment(services, service_name)
        if env.get("ATTACHMENT_PARSER_SOCKET") != "/run/rpy-parser/parser.sock":
            _fail(f"{service_name} must use the private parser Unix socket")
        _finite_positive_float(
            env.get("ATTACHMENT_PARSER_TIMEOUT_SECONDS"),
            label=f"{service_name} ATTACHMENT_PARSER_TIMEOUT_SECONDS",
        )
        if not _volume_mount(worker, source="parser_socket", target="/run/rpy-parser"):
            _fail(f"{service_name} must mount the private parser_socket volume")
        dependency = (worker.get("depends_on") or {}).get("attachment-parser") or {}
        if dependency.get("condition") != "service_healthy":
            _fail(f"{service_name} must wait for attachment-parser health")


def _duration_seconds(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _finite_positive_float(value, label="duration")

    rendered = str(value or "").strip()
    match = DURATION_RE.fullmatch(rendered)
    if match is None:
        _fail(f"unsupported duration value: {rendered!r}")
    amount = float(match.group("value"))
    unit = match.group("unit")
    multiplier = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
    seconds = amount * multiplier
    if seconds <= 0:
        _fail("duration must be greater than zero")
    return seconds


def _validate_worker_shutdown(services: dict[str, Any]) -> None:
    rendered_pairs: list[tuple[float, float]] = []
    for service_name in ("worker-1", "worker-2"):
        environment = _environment(services, service_name)
        if "WORKER_SHUTDOWN_GRACE_SECONDS" not in environment:
            _fail(f"{service_name} WORKER_SHUTDOWN_GRACE_SECONDS must be numeric")
        app_grace = _finite_positive_float(
            environment["WORKER_SHUTDOWN_GRACE_SECONDS"],
            label=f"{service_name} WORKER_SHUTDOWN_GRACE_SECONDS",
        )
        container_grace = _duration_seconds(
            services[service_name].get("stop_grace_period")
        )
        if container_grace <= app_grace:
            _fail(
                f"{service_name} stop_grace_period must exceed "
                "WORKER_SHUTDOWN_GRACE_SECONDS"
            )
        rendered_pairs.append((app_grace, container_grace))

    if len(set(rendered_pairs)) != 1:
        _fail("both workers must use the same shutdown grace contract")


def _validate_datajud_activation_contract(services: dict[str, Any]) -> None:
    api = _environment(services, "api")
    worker = _environment(services, "worker-1")
    for field in ("DATAJUD_ENABLED", "DATAJUD_AUTHORIZED_USE"):
        if api.get(field) != worker.get(field):
            _fail(f"api and workers must use the same {field} activation gate")


def _validate_worker_embedding_contract(services: dict[str, Any]) -> None:
    fields = (
        "JUDIT_CALLBACK_URL",
        "EMBEDDING_SPACE_RUNTIME_ENABLED",
        "EMBEDDING_PROVIDER",
        "BGE_EMBEDDING_MODEL",
        "BGE_EMBEDDING_PATH",
        "BGE_EMBEDDING_DEVICE",
        "BGE_EMBEDDING_USE_FP16",
        "ALLOW_EXTERNAL_EMBEDDINGS",
        "COHERE_EMBEDDING_MODEL",
        "EMBEDDING_MODEL",
        "RERANKER_ENABLED",
        "RERANKER_PROVIDER",
        "RERANKER_MODEL",
        "BGE_RERANKER_PATH",
        "RERANKER_USE_FP16",
        "ALLOW_EXTERNAL_RERANKER",
        "COHERE_RERANKER_MODEL",
        "RERANKER_TIMEOUT_SECONDS",
        "DATAJUD_ENABLED",
        "DATAJUD_AUTHORIZED_USE",
        "DATAJUD_API_KEY",
        "DATAJUD_BASE_URL",
        "DATAJUD_TIMEOUT_SECONDS",
        "LANGFUSE_ENABLED",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_BASE_URL",
        "LANGFUSE_TRACING_ENVIRONMENT",
    )
    left = _environment(services, "worker-1")
    right = _environment(services, "worker-2")
    for field in fields:
        if left.get(field) != right.get(field):
            _fail(f"both workers must use the same {field}")


def validate(config: dict[str, Any]) -> None:
    services = config.get("services")
    if not isinstance(services, dict):
        _fail("services must be an object")

    if set(services) != EXPECTED_SERVICES:
        _fail(f"unexpected services: {sorted(services)}")

    _validate_application_image(services)
    _validate_database_isolation(services)

    postgres_image = str(services["postgres"].get("image") or "")
    if not IMMUTABLE_IMAGE_RE.fullmatch(postgres_image):
        _fail("postgres image must be pinned by sha256 digest")

    if "ports" in services["postgres"]:
        _fail("postgres must not publish host ports")

    api_ports = services["api"].get("ports") or []
    if len(api_ports) != 1:
        _fail("api must publish exactly one host port")
    host_ip = str(api_ports[0].get("host_ip") or "")
    if host_ip != "127.0.0.1":
        _fail("api must bind to 127.0.0.1 by default")

    api_command = services["api"].get("command") or []
    if "--workers" not in api_command or api_command[api_command.index("--workers") + 1] != "1":
        _fail("api uvicorn worker count must be explicitly 1")

    worker_services = sorted(name for name in services if name.startswith("worker-"))
    if worker_services != ["worker-1", "worker-2"]:
        _fail("exactly two worker services are required")

    scheduler_services = [name for name in services if name == "scheduler"]
    if len(scheduler_services) != 1:
        _fail("exactly one scheduler service is required")

    networks = config.get("networks") or {}
    if not isinstance(networks, dict):
        _fail("networks must be an object")
    volumes = config.get("volumes") or {}
    if not isinstance(volumes, dict):
        _fail("volumes must be an object")
    _validate_egress_topology(services, networks)
    _validate_runtime_confinement(services)
    _validate_attachment_parser_contract(services, volumes)

    _require_env(services, "api", API_REQUIRED_ENV)
    for service_name in ("worker-1", "worker-2"):
        _require_env(services, service_name, WORKER_REQUIRED_ENV)
    _require_env(services, "scheduler", SCHEDULER_REQUIRED_ENV)
    _require_env(services, "attachment-parser", PARSER_REQUIRED_ENV)
    _validate_worker_shutdown(services)
    _validate_worker_embedding_contract(services)
    _validate_datajud_activation_contract(services)

    _forbid_env(services, "api", PROVIDER_SECRETS | MIGRATE_REQUIRED_ENV)
    for service_name in ("worker-1", "worker-2"):
        _forbid_env(services, service_name, HTTP_SECRETS | MIGRATE_REQUIRED_ENV)
    _forbid_env(
        services,
        "scheduler",
        HTTP_SECRETS | PROVIDER_SECRETS | MIGRATE_REQUIRED_ENV,
    )
    _forbid_env(services, "migrate", HTTP_SECRETS | PROVIDER_SECRETS)
    _forbid_env(
        services,
        "egress-proxy",
        HTTP_SECRETS | PROVIDER_SECRETS | MIGRATE_REQUIRED_ENV | {"DATABASE_URL"},
    )


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_production_compose.py <compose-config.json>")
    path = Path(sys.argv[1])
    validate(json.loads(path.read_text(encoding="utf-8")))
    print("production compose contract: ok")


if __name__ == "__main__":
    main()
