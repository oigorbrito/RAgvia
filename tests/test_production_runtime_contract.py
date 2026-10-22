from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate_production_compose.py"
spec = importlib.util.spec_from_file_location("validate_production_compose", MODULE_PATH)
assert spec is not None and spec.loader is not None  # nosec B101
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def _runtime_service(*, cpus: float, mem_limit: str, pids_limit: int) -> dict:
    return {
        "read_only": True,
        "user": "10001:10001",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "tmpfs": ["/tmp:rw,noexec,nosuid,nodev,size=64m"],  # nosec B108
        "cpus": cpus,
        "mem_limit": mem_limit,
        "pids_limit": pids_limit,
    }


def _runtime_services() -> dict:
    return {
        "migrate": _runtime_service(cpus=1.0, mem_limit="512m", pids_limit=128),
        "api": _runtime_service(cpus=1.0, mem_limit="512m", pids_limit=128),
        "egress-proxy": _runtime_service(cpus=0.5, mem_limit="256m", pids_limit=128),
        "attachment-parser": _runtime_service(cpus=0.75, mem_limit="768m", pids_limit=64),
        "worker-1": _runtime_service(cpus=2.0, mem_limit="8g", pids_limit=256),
        "worker-2": _runtime_service(cpus=2.0, mem_limit="8g", pids_limit=256),
        "scheduler": _runtime_service(cpus=0.5, mem_limit="256m", pids_limit=64),
    }


def test_runtime_confinement_accepts_expected_budget() -> None:
    contract._validate_runtime_confinement(_runtime_services())


def test_datajud_activation_gate_is_shared_by_api_and_workers() -> None:
    services = {
        "api": {"environment": {"DATAJUD_ENABLED": "false", "DATAJUD_AUTHORIZED_USE": "false"}},
        "worker-1": {"environment": {"DATAJUD_ENABLED": "false", "DATAJUD_AUTHORIZED_USE": "false"}},
    }
    contract._validate_datajud_activation_contract(services)
    services["api"]["environment"]["DATAJUD_ENABLED"] = "true"
    with pytest.raises(SystemExit, match="api and workers must use the same DATAJUD_ENABLED"):
        contract._validate_datajud_activation_contract(services)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("read_only", False, "root filesystem must be read-only"),
        ("cap_drop", [], "must drop all Linux capabilities"),
        ("security_opt", [], "must enable no-new-privileges"),
        ("tmpfs", ["/tmp:rw"], "/tmp tmpfs must include noexec"),  # nosec B108
        ("pids_limit", 0, "pids_limit must be positive"),
    ],
)
def test_runtime_confinement_rejects_removed_controls(
    field: str, value: object, message: str
) -> None:
    services = _runtime_services()
    services["api"][field] = value
    with pytest.raises(SystemExit, match=message):
        contract._validate_runtime_confinement(services)


@pytest.mark.parametrize("field", ["cpus", "pids_limit"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_runtime_confinement_rejects_non_finite_numeric_budgets(
    field: str,
    value: float,
) -> None:
    services = _runtime_services()
    services["api"][field] = value
    with pytest.raises(SystemExit, match=f"api {field} must be finite"):
        contract._validate_runtime_confinement(services)


def test_runtime_confinement_rejects_boolean_numeric_budget() -> None:
    services = _runtime_services()
    services["api"]["cpus"] = True
    with pytest.raises(SystemExit, match="api cpus must be numeric"):
        contract._validate_runtime_confinement(services)


def test_runtime_confinement_rejects_seccomp_unconfined_and_added_caps() -> None:
    services = _runtime_services()
    services["worker-1"]["security_opt"].append("seccomp=unconfined")
    with pytest.raises(SystemExit, match="preserve Docker seccomp confinement"):
        contract._validate_runtime_confinement(services)

    services = _runtime_services()
    services["worker-1"]["cap_add"] = ["NET_ADMIN"]
    with pytest.raises(SystemExit, match="must not add Linux capabilities"):
        contract._validate_runtime_confinement(services)


def _networked_services() -> dict:
    worker_env = {
        "HTTPS_PROXY": "http://egress-proxy:3128",
        "https_proxy": "http://egress-proxy:3128",
        "NO_PROXY": "postgres,localhost,127.0.0.1",
        "no_proxy": "postgres,localhost,127.0.0.1",
    }
    return {
        "postgres": {"networks": {"backend": None}, "environment": {}},
        "migrate": {"networks": {"backend": None}, "environment": {}},
        "api": {"networks": {"backend": None}, "environment": {}},
        "worker-1": {
            "networks": {"backend": None, "provider-gateway": None},
            "environment": dict(worker_env),
        },
        "worker-2": {
            "networks": {"backend": None, "provider-gateway": None},
            "environment": dict(worker_env),
        },
        "scheduler": {"networks": {"backend": None}, "environment": {}},
        "egress-proxy": {
            "networks": {"provider-gateway": None, "egress": None},
            "environment": {
                "EGRESS_PROXY_BIND_HOST": "0.0.0.0",  # nosec B104
                "EGRESS_PROXY_ALLOWED_HOSTS": "api.anthropic.com",
                "EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS": "10",
            },
        },
    }


def test_egress_topology_has_single_external_gateway() -> None:
    contract._validate_egress_topology(
        _networked_services(),
        {
            "backend": {"internal": True},
            "provider-gateway": {"internal": True},
            "egress": {},
        },
    )


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_egress_topology_rejects_non_finite_proxy_timeout(value: str) -> None:
    services = _networked_services()
    services["egress-proxy"]["environment"][
        "EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS"
    ] = value
    with pytest.raises(
        SystemExit,
        match="EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS must be finite",
    ):
        contract._validate_egress_topology(
            services,
            {
                "backend": {"internal": True},
                "provider-gateway": {"internal": True},
                "egress": {},
            },
        )


def test_egress_topology_rejects_proxy_timeout_over_runtime_bound() -> None:
    services = _networked_services()
    services["egress-proxy"]["environment"][
        "EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS"
    ] = "61"
    with pytest.raises(SystemExit, match="must not exceed 60"):
        contract._validate_egress_topology(
            services,
            {
                "backend": {"internal": True},
                "provider-gateway": {"internal": True},
                "egress": {},
            },
        )


def test_worker_direct_egress_route_is_rejected() -> None:
    services = _networked_services()
    services["worker-1"]["networks"]["egress"] = None
    with pytest.raises(SystemExit, match="worker-1 network set"):
        contract._validate_egress_topology(
            services,
            {
                "backend": {"internal": True},
                "provider-gateway": {"internal": True},
                "egress": {},
            },
        )


def test_proxy_cannot_receive_provider_secrets_by_contract() -> None:
    services = _networked_services()
    services["egress-proxy"]["environment"]["ANTHROPIC_API_KEY"] = "secret"
    with pytest.raises(SystemExit, match="must receive only its allowlist"):
        contract._validate_egress_topology(
            services,
            {
                "backend": {"internal": True},
                "provider-gateway": {"internal": True},
                "egress": {},
            },
        )



def _parser_contract_services() -> dict:
    parser_env = {
        "ATTACHMENT_PARSER_SOCKET": "/run/rpy-parser/parser.sock",
        "ATTACHMENT_PARSER_REQUEST_TIMEOUT_SECONDS": "45",
        "ATTACHMENT_MAX_BYTES": "10485760",
        "ATTACHMENT_CHUNK_CHARS": "4000",
        "ATTACHMENT_OCR_ENABLED": "false",
        "ATTACHMENT_OCR_BINARY": "tesseract",
        "ATTACHMENT_OCR_LANGUAGE": "por",
        "ATTACHMENT_OCR_TIMEOUT_SECONDS": "30",
        "ATTACHMENT_PDF_OCR_SCALE": "2.0",
        "ATTACHMENT_PDF_OCR_MAX_PAGES": "100",
    }
    worker_env = {
        "ATTACHMENT_PARSER_SOCKET": "/run/rpy-parser/parser.sock",
        "ATTACHMENT_PARSER_TIMEOUT_SECONDS": "45",
    }
    return {
        "attachment-parser": {
            "network_mode": "none",
            "command": ["python", "-m", "app.attachment_sandbox"],
            "environment": parser_env,
            "volumes": [
                {"type": "volume", "source": "parser_socket", "target": "/run/rpy-parser"}
            ],
        },
        "worker-1": {
            "environment": dict(worker_env),
            "volumes": [
                {"type": "volume", "source": "parser_socket", "target": "/run/rpy-parser"}
            ],
            "depends_on": {"attachment-parser": {"condition": "service_healthy"}},
        },
        "worker-2": {
            "environment": dict(worker_env),
            "volumes": [
                {"type": "volume", "source": "parser_socket", "target": "/run/rpy-parser"}
            ],
            "depends_on": {"attachment-parser": {"condition": "service_healthy"}},
        },
    }


def _parser_volume() -> dict:
    return {
        "parser_socket": {
            "driver": "local",
            "driver_opts": {
                "type": "tmpfs",
                "device": "tmpfs",
                "o": "uid=10001,gid=10001,mode=0770,size=1m",
            },
        }
    }


def test_attachment_parser_contract_accepts_no_network_tmpfs_socket() -> None:
    contract._validate_attachment_parser_contract(
        _parser_contract_services(), _parser_volume()
    )


def test_attachment_parser_contract_rejects_network_access() -> None:
    services = _parser_contract_services()
    services["attachment-parser"]["network_mode"] = "bridge"
    with pytest.raises(SystemExit, match="network_mode none"):
        contract._validate_attachment_parser_contract(services, _parser_volume())


def test_attachment_parser_contract_rejects_provider_secret() -> None:
    services = _parser_contract_services()
    services["attachment-parser"]["environment"]["ANTHROPIC_API_KEY"] = "secret"
    with pytest.raises(SystemExit, match="only parser settings"):
        contract._validate_attachment_parser_contract(services, _parser_volume())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("cpus", 1.0, "cpus must equal 0.75"),
        ("mem_limit", "1g", "mem_limit does not match"),
        ("pids_limit", 128, "pids_limit does not match"),
    ],
)
def test_attachment_parser_resource_budgets_are_mechanical(
    field: str, value: object, message: str
) -> None:
    services = _runtime_services()
    services["attachment-parser"][field] = value
    with pytest.raises(SystemExit, match=message):
        contract._validate_runtime_confinement(services)



def test_attachment_parser_contract_rejects_nonpositive_request_timeout() -> None:
    services = _parser_contract_services()
    services["attachment-parser"]["environment"][
        "ATTACHMENT_PARSER_REQUEST_TIMEOUT_SECONDS"
    ] = "0"
    with pytest.raises(SystemExit, match="request timeout must be positive"):
        contract._validate_attachment_parser_contract(services, _parser_volume())


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_attachment_parser_contract_rejects_non_finite_request_timeout(
    value: str,
) -> None:
    services = _parser_contract_services()
    services["attachment-parser"]["environment"][
        "ATTACHMENT_PARSER_REQUEST_TIMEOUT_SECONDS"
    ] = value
    with pytest.raises(SystemExit, match="request timeout must be finite"):
        contract._validate_attachment_parser_contract(services, _parser_volume())


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_attachment_parser_contract_rejects_non_finite_worker_timeout(
    value: str,
) -> None:
    services = _parser_contract_services()
    services["worker-1"]["environment"]["ATTACHMENT_PARSER_TIMEOUT_SECONDS"] = value
    with pytest.raises(
        SystemExit,
        match="worker-1 ATTACHMENT_PARSER_TIMEOUT_SECONDS must be finite",
    ):
        contract._validate_attachment_parser_contract(services, _parser_volume())
