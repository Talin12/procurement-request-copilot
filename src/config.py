"""Runtime configuration derived from the policy file rather than from constants.

Date-based checks must use the policy's data snapshot date, never the host
clock, so the snapshot date and the security-assessment validity window are
parsed out of `data/procurement_policy.md` at import time. If the policy file is
re-issued with a new snapshot, the engine follows it without a code change.
"""
from __future__ import annotations

import os
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
POLICY_PATH = DATA_DIR / "procurement_policy.md"

FALLBACK_REFERENCE_DATE = date(2026, 9, 30)
FALLBACK_SECURITY_VALIDITY_DAYS = 365


@lru_cache(maxsize=1)
def policy_text() -> str:
    return POLICY_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def policy_version() -> str:
    match = re.search(r"Policy version:\*{0,2}\s*([0-9.]+)", policy_text())
    return match.group(1) if match else "unknown"


@lru_cache(maxsize=1)
def reference_date() -> date:
    """The data snapshot date all staleness checks are measured against."""
    match = re.search(
        r"reference date:?\*{0,2}\s*(\d{4}-\d{2}-\d{2})", policy_text(), re.IGNORECASE
    )
    return date.fromisoformat(match.group(1)) if match else FALLBACK_REFERENCE_DATE


@lru_cache(maxsize=1)
def security_validity_days() -> int:
    """How long a vendor security assessment stays current (policy section 5)."""
    match = re.search(r"current for \*{0,2}(\d+)\s*days", policy_text(), re.IGNORECASE)
    return int(match.group(1)) if match else FALLBACK_SECURITY_VALIDITY_DAYS


def vendor_risk_base_url() -> str:
    return os.getenv("VENDOR_RISK_BASE_URL", "http://127.0.0.1:8001").rstrip("/")


def vendor_risk_timeout() -> float:
    return float(os.getenv("VENDOR_RISK_TIMEOUT_SECONDS", "3.0"))
