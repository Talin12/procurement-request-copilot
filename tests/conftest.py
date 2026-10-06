from __future__ import annotations

import os

import pytest

# Every test calls the vendor-risk service in-process so the suite needs no
# listening port and gives the same result on a developer machine and in CI.
os.environ.setdefault("VENDOR_RISK_TRANSPORT", "asgi")
os.environ.setdefault("LLM_PROVIDER", "offline")


@pytest.fixture
def reasoner():
    from src.llm.offline import OfflineReasoner

    return OfflineReasoner()
