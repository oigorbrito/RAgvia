from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_staging_ingress.py"
_SPEC = importlib.util.spec_from_file_location("validate_staging_ingress", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(module)


def test_repository_staging_ingress_config_is_dynamic() -> None:
    config = (
        Path(__file__).resolve().parents[1]
        / "ops"
        / "staging"
        / "nginx.conf"
    ).read_text(encoding="utf-8")
    assert module.validate_staging_ingress(config) == []


def test_static_container_ip_is_rejected() -> None:
    config = """
    events {}
    http {
      resolver 127.0.0.11 ipv6=off valid=10s;
      server {
        set $rpy_api "api:8000";
        location / {
          proxy_pass http://172.20.0.5:8000;
        }
      }
    }
    """
    errors = module.validate_staging_ingress(config)
    assert "static private/container IP upstreams are prohibited" in errors
    assert any("proxy_pass must use" in error for error in errors)


def test_missing_docker_dns_is_rejected() -> None:
    config = """
    events {}
    http {
      server {
        set $rpy_api "api:8000";
        location / {
          proxy_pass http://$rpy_api$request_uri;
        }
      }
    }
    """
    assert "Docker DNS resolver 127.0.0.11 is required" in module.validate_staging_ingress(config)
