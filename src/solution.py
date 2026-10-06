"""Assessment adapter.

`handle_request` is the single entry point the evaluation harness, the UI and
any other caller use. It picks an architecture and a reasoner, runs the request,
and returns a `ProcurementDecision`.
"""
from __future__ import annotations

from src.agents import single, staged
from src.contracts import Architecture, ProcurementDecision
from src.llm import Reasoner, get_reasoner

_ARCHITECTURES = {"single": single.run, "staged": staged.run}


def handle_request(
    request_id: str,
    architecture: Architecture = "single",
    reasoner: Reasoner | None = None,
) -> ProcurementDecision:
    """Assess one purchase request and recommend the next action.

    Args:
        request_id: the request to assess, e.g. ``REQ-1001``.
        architecture: ``single`` (one agent) or ``staged`` (analyst + reviewer).
        reasoner: injected for tests; by default chosen from the environment.
    """
    if architecture not in _ARCHITECTURES:
        raise ValueError(f"Unknown architecture {architecture!r}. Use 'single' or 'staged'.")
    return _ARCHITECTURES[architecture](request_id, reasoner or get_reasoner())
