from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "provision_lightsail_staging.py"
SPEC = importlib.util.spec_from_file_location("provision_lightsail_staging", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


def test_select_zone_uses_available_zone_in_requested_region() -> None:
    payload = {
        "regions": [
            {
                "name": "sa-east-1",
                "availabilityZones": [
                    {"zoneName": "sa-east-1b", "state": "available"},
                    {"zoneName": "sa-east-1a", "state": "available"},
                ],
            }
        ]
    }
    assert mod._select_zone(payload, "sa-east-1") == "sa-east-1a"


def test_select_blueprint_prefers_ubuntu_2404() -> None:
    payload = {
        "blueprints": [
            {
                "blueprintId": "ubuntu_22_04",
                "name": "Ubuntu 22.04 LTS",
                "platform": "LINUX_UNIX",
                "isActive": True,
            },
            {
                "blueprintId": "ubuntu_24_04",
                "name": "Ubuntu 24.04 LTS",
                "platform": "LINUX_UNIX",
                "isActive": True,
            },
        ]
    }
    assert mod._select_blueprint(payload) == "ubuntu_24_04"


def test_select_bundle_picks_cheapest_sufficient_linux_plan() -> None:
    payload = {
        "bundles": [
            {
                "bundleId": "too-small",
                "price": 20,
                "cpuCount": 2,
                "ramSizeInGb": 8,
                "isActive": True,
                "supportedPlatforms": ["LINUX_UNIX"],
            },
            {
                "bundleId": "candidate-a",
                "price": 90,
                "cpuCount": 4,
                "ramSizeInGb": 16,
                "isActive": True,
                "supportedPlatforms": ["LINUX_UNIX"],
            },
            {
                "bundleId": "candidate-b",
                "price": 120,
                "cpuCount": 8,
                "ramSizeInGb": 32,
                "isActive": True,
                "supportedPlatforms": ["LINUX_UNIX"],
            },
        ]
    }
    assert mod._select_bundle(payload, min_ram_gb=16, min_vcpus=4) == (
        "candidate-a",
        16.0,
        4,
        90.0,
    )


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "10.0.0.0/8", "2001:db8::/64"])
def test_ssh_cidr_rejects_broad_or_ipv6_networks(cidr: str) -> None:
    with pytest.raises(mod.ProvisionError):
        mod._validate_ssh_cidr(cidr)


def test_port_policy_contains_only_ssh_http_https() -> None:
    import json

    ports = json.loads(mod._port_infos("203.0.113.7/32"))
    assert [(item["fromPort"], item["toPort"]) for item in ports] == [
        (22, 22),
        (80, 80),
        (443, 443),
    ]
    assert ports[0]["cidrs"] == ["203.0.113.7/32"]
    assert ports[1]["cidrs"] == ["0.0.0.0/0"]
    assert ports[2]["cidrs"] == ["0.0.0.0/0"]
