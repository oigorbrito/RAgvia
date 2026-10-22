from __future__ import annotations

import argparse
import ipaddress
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Sequence


class ProvisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    zone: str
    blueprint_id: str
    bundle_id: str
    bundle_ram_gb: float
    bundle_vcpus: int
    bundle_price_usd: float


def _aws_json(args: Sequence[str], *, region: str) -> dict[str, Any]:
    if shutil.which("aws") is None:
        raise ProvisionError("AWS CLI v2 is required")
    completed = subprocess.run(
        ["aws", *args, "--region", region, "--output", "json", "--no-cli-pager"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "AWS CLI command failed"
        raise ProvisionError(message)
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        raise ProvisionError("AWS CLI returned invalid JSON") from None
    if not isinstance(payload, dict):
        raise ProvisionError("AWS CLI returned an unexpected JSON shape")
    return payload


def _validate_name(value: str, label: str) -> str:
    value = value.strip()
    if len(value) < 2 or any(not (ch.isalnum() or ch in "-_") for ch in value):
        raise ProvisionError(f"{label} must contain only letters, numbers, hyphens or underscores")
    if not value[0].isalnum() or not value[-1].isalnum():
        raise ProvisionError(f"{label} must start and end with a letter or number")
    return value


def _validate_ssh_cidr(value: str) -> str:
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError:
        raise ProvisionError("SSH CIDR must be a valid network in CIDR notation") from None
    if network.version != 4:
        raise ProvisionError("SSH CIDR must be IPv4 for this staging bootstrap")
    if network.prefixlen < 24:
        raise ProvisionError("SSH CIDR is too broad; use /24 or narrower")
    return str(network)


def _select_zone(regions: dict[str, Any], region: str) -> str:
    for item in regions.get("regions", []):
        if item.get("name") != region:
            continue
        zones = [
            zone.get("zoneName")
            for zone in item.get("availabilityZones", [])
            if zone.get("state") in {None, "available"} and zone.get("zoneName")
        ]
        if zones:
            return sorted(zones)[0]
    raise ProvisionError(f"no Lightsail availability zone found for {region}")


def _select_blueprint(payload: dict[str, Any]) -> str:
    candidates: list[tuple[str, str]] = []
    for item in payload.get("blueprints", []):
        if item.get("isActive") is False:
            continue
        if item.get("platform") not in {None, "LINUX_UNIX"}:
            continue
        text = " ".join(
            str(item.get(key) or "") for key in ("name", "group", "description", "version")
        ).casefold()
        blueprint_id = str(item.get("blueprintId") or "")
        if blueprint_id and "ubuntu" in text:
            priority = "0" if "24.04" in text or "24_04" in blueprint_id else "1"
            candidates.append((priority + text, blueprint_id))
    if not candidates:
        raise ProvisionError("no active Ubuntu Lightsail blueprint found")
    return sorted(candidates)[0][1]


def _select_bundle(
    payload: dict[str, Any],
    *,
    min_ram_gb: float,
    min_vcpus: int,
) -> tuple[str, float, int, float]:
    candidates: list[tuple[float, float, int, str]] = []
    for item in payload.get("bundles", []):
        if item.get("isActive") is False:
            continue
        platforms = item.get("supportedPlatforms") or []
        if platforms and "LINUX_UNIX" not in platforms:
            continue
        ram = float(item.get("ramSizeInGb") or 0)
        cpus = int(item.get("cpuCount") or 0)
        price = float(item.get("price") or 0)
        bundle_id = str(item.get("bundleId") or "")
        if bundle_id and ram >= min_ram_gb and cpus >= min_vcpus:
            candidates.append((price, ram, cpus, bundle_id))
    if not candidates:
        raise ProvisionError(
            f"no active Linux Lightsail bundle meets {min_ram_gb:g} GB RAM / {min_vcpus} vCPU"
        )
    price, ram, cpus, bundle_id = sorted(candidates)[0]
    return bundle_id, ram, cpus, price


def _candidate(
    *,
    region: str,
    min_ram_gb: float,
    min_vcpus: int,
) -> Candidate:
    regions = _aws_json(
        ["lightsail", "get-regions", "--include-availability-zones"],
        region=region,
    )
    blueprints = _aws_json(["lightsail", "get-blueprints"], region=region)
    bundles = _aws_json(["lightsail", "get-bundles"], region=region)
    bundle_id, ram, cpus, price = _select_bundle(
        bundles,
        min_ram_gb=min_ram_gb,
        min_vcpus=min_vcpus,
    )
    return Candidate(
        zone=_select_zone(regions, region),
        blueprint_id=_select_blueprint(blueprints),
        bundle_id=bundle_id,
        bundle_ram_gb=ram,
        bundle_vcpus=cpus,
        bundle_price_usd=price,
    )


def _resource_exists(args: Sequence[str], *, region: str) -> bool:
    if shutil.which("aws") is None:
        raise ProvisionError("AWS CLI v2 is required")
    completed = subprocess.run(
        ["aws", *args, "--region", region, "--output", "json", "--no-cli-pager"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0:
        return True
    stderr = completed.stderr.casefold()
    if "notfoundexception" in stderr or "does not exist" in stderr:
        return False
    raise ProvisionError(completed.stderr.strip() or "AWS CLI lookup failed")


def _run_aws(args: Sequence[str], *, region: str) -> None:
    _aws_json(list(args), region=region)


def _wait_running(instance_name: str, *, region: str, timeout: int = 300) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = _aws_json(
            ["lightsail", "get-instance-state", "--instance-name", instance_name],
            region=region,
        )
        if str((state.get("state") or {}).get("name") or "").casefold() == "running":
            return
        time.sleep(5)
    raise ProvisionError("Lightsail instance did not reach running state in time")


def _port_infos(ssh_cidr: str) -> str:
    return json.dumps(
        [
            {
                "fromPort": 22,
                "toPort": 22,
                "protocol": "tcp",
                "cidrs": [ssh_cidr],
            },
            {
                "fromPort": 80,
                "toPort": 80,
                "protocol": "tcp",
                "cidrs": ["0.0.0.0/0"],
            },
            {
                "fromPort": 443,
                "toPort": 443,
                "protocol": "tcp",
                "cidrs": ["0.0.0.0/0"],
            },
        ],
        separators=(",", ":"),
    )


def provision(
    *,
    region: str,
    instance_name: str,
    static_ip_name: str,
    ssh_cidr: str,
    min_ram_gb: float,
    min_vcpus: int,
    apply: bool,
) -> Candidate:
    instance_name = _validate_name(instance_name, "instance name")
    static_ip_name = _validate_name(static_ip_name, "static IP name")
    ssh_cidr = _validate_ssh_cidr(ssh_cidr)
    candidate = _candidate(
        region=region,
        min_ram_gb=min_ram_gb,
        min_vcpus=min_vcpus,
    )

    print(
        json.dumps(
            {
                "region": region,
                "availability_zone": candidate.zone,
                "instance_name": instance_name,
                "static_ip_name": static_ip_name,
                "blueprint_id": candidate.blueprint_id,
                "bundle_id": candidate.bundle_id,
                "bundle_ram_gb": candidate.bundle_ram_gb,
                "bundle_vcpus": candidate.bundle_vcpus,
                "bundle_price_usd_month": candidate.bundle_price_usd,
                "ssh_cidr": ssh_cidr,
                "apply": apply,
            },
            indent=2,
            sort_keys=True,
        )
    )
    if not apply:
        return candidate

    if not _resource_exists(
        ["lightsail", "get-instance", "--instance-name", instance_name],
        region=region,
    ):
        _run_aws(
            [
                "lightsail",
                "create-instances",
                "--instance-names",
                instance_name,
                "--availability-zone",
                candidate.zone,
                "--blueprint-id",
                candidate.blueprint_id,
                "--bundle-id",
                candidate.bundle_id,
                "--ip-address-type",
                "ipv4",
                "--tags",
                "key=Environment,value=staging",
                "key=Project,value=rpy",
            ],
            region=region,
        )
    _wait_running(instance_name, region=region)

    if not _resource_exists(
        ["lightsail", "get-static-ip", "--static-ip-name", static_ip_name],
        region=region,
    ):
        _run_aws(
            ["lightsail", "allocate-static-ip", "--static-ip-name", static_ip_name],
            region=region,
        )
    _run_aws(
        [
            "lightsail",
            "attach-static-ip",
            "--static-ip-name",
            static_ip_name,
            "--instance-name",
            instance_name,
        ],
        region=region,
    )
    _run_aws(
        [
            "lightsail",
            "put-instance-public-ports",
            "--instance-name",
            instance_name,
            "--port-infos",
            _port_infos(ssh_cidr),
        ],
        region=region,
    )

    ip = _aws_json(
        ["lightsail", "get-static-ip", "--static-ip-name", static_ip_name],
        region=region,
    ).get("staticIp", {}).get("ipAddress")
    print(json.dumps({"public_ipv4": ip, "status": "provisioned"}, sort_keys=True))
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan or provision the persistent Rpy Lightsail staging host."
    )
    parser.add_argument("--region", default="sa-east-1")
    parser.add_argument("--instance-name", default="rpy-staging")
    parser.add_argument("--static-ip-name", default="rpy-staging-ip")
    parser.add_argument("--ssh-cidr", required=True, help="IPv4 /24-or-narrower admin network")
    parser.add_argument("--min-ram-gb", type=float, default=16.0)
    parser.add_argument("--min-vcpus", type=int, default=4)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create/update Lightsail resources. Without this flag, only prints the resolved plan.",
    )
    args = parser.parse_args()

    if args.min_ram_gb <= 0 or args.min_vcpus <= 0:
        print("provision: minimum RAM/vCPU values must be positive", file=sys.stderr)
        return 1

    try:
        provision(
            region=args.region,
            instance_name=args.instance_name,
            static_ip_name=args.static_ip_name,
            ssh_cidr=args.ssh_cidr,
            min_ram_gb=args.min_ram_gb,
            min_vcpus=args.min_vcpus,
            apply=args.apply,
        )
    except ProvisionError as exc:
        print(f"provision: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
