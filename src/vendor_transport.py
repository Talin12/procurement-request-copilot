"""How the vendor-risk tool reaches the external service.

The shipped product talks HTTP to a real endpoint. The evaluation harness needs
the same call to be hermetic and reproducible on any machine, with no ports to
bind, no service to start first, and no chance of a stale process changing the
result - so the same FastAPI application can also be called in-process over
ASGI.

Both transports return identical status codes and payloads, including the 503
outage and the 404 for an unknown vendor, so a result produced under one is
valid under the other. Selected with VENDOR_RISK_TRANSPORT (`http` by default).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import requests

from src.config import vendor_risk_base_url, vendor_risk_timeout


@dataclass
class VendorRiskResponse:
    """A normalised response, so callers never branch on the transport."""

    ok: bool
    status_code: int | None = None
    payload: dict | None = None
    error: str | None = None


def _detail(payload: object, fallback: str) -> str:
    if isinstance(payload, dict) and payload.get("detail"):
        return str(payload["detail"])
    return fallback


def _interpret(status_code: int, payload: object) -> VendorRiskResponse:
    if status_code == 200 and isinstance(payload, dict):
        return VendorRiskResponse(ok=True, status_code=200, payload=payload)
    if status_code == 404:
        return VendorRiskResponse(
            ok=False, status_code=404, error="no assessment on file for this vendor (HTTP 404)"
        )
    return VendorRiskResponse(
        ok=False,
        status_code=status_code,
        error=f"HTTP {status_code}: {_detail(payload, 'service error')}",
    )


def _fetch_http(vendor_name: str) -> VendorRiskResponse:
    url = f"{vendor_risk_base_url()}/vendor-risk/{requests.utils.quote(vendor_name, safe='')}"
    try:
        response = requests.get(url, timeout=vendor_risk_timeout())
    except requests.RequestException as exc:
        return VendorRiskResponse(ok=False, error=f"service unreachable: {type(exc).__name__}")
    try:
        payload = response.json()
    except ValueError:
        payload = None
        if response.status_code == 200:
            return VendorRiskResponse(ok=False, status_code=200, error="service returned a malformed response")
    return _interpret(response.status_code, payload)


@lru_cache(maxsize=1)
def _asgi_client():
    from fastapi.testclient import TestClient

    from mock_api.app import app

    return TestClient(app, raise_server_exceptions=False)


def _fetch_asgi(vendor_name: str) -> VendorRiskResponse:
    try:
        response = _asgi_client().get(f"/vendor-risk/{requests.utils.quote(vendor_name, safe='')}")
    except Exception as exc:
        return VendorRiskResponse(ok=False, error=f"service unreachable: {type(exc).__name__}")
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return _interpret(response.status_code, payload)


def active_transport() -> str:
    return (os.getenv("VENDOR_RISK_TRANSPORT") or "http").strip().lower()


def fetch_vendor_risk(vendor_name: str) -> VendorRiskResponse:
    transport = active_transport()
    if transport == "asgi":
        return _fetch_asgi(vendor_name)
    if transport == "http":
        return _fetch_http(vendor_name)
    return VendorRiskResponse(ok=False, error=f"unknown VENDOR_RISK_TRANSPORT: {transport!r}")
