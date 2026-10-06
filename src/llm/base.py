"""The reasoner interface.

A *reasoner* is the probabilistic half of the system. It does three things and
nothing else:

1. decides which tools to call next (`next_tool_calls`),
2. interprets free text into structured signals (`interpret_request`),
3. writes the human-facing narrative (`narrate`).

It never sets approvals, thresholds, or risk flags - those come from
`src/policy.py`. Both architectures talk to this interface, so swapping a hosted
model for the offline reasoner changes no orchestration code, and the two
architectures are always measured against the same abstraction.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from src.models import EvidencePack, PolicyVerdict, RequestFacts, RequestInterpretation


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class Narration:
    recommendation: str
    next_step: str
    added_risk_flags: list[str] = field(default_factory=list)
    reviewer_note: str | None = None


class Reasoner(ABC):
    """Base class for every reasoning backend."""

    name: str = "base"
    #: True when outputs come from a hosted model rather than local computation.
    is_model_backed: bool = False

    def __init__(self) -> None:
        self.calls = 0

    def _count(self) -> None:
        self.calls += 1

    @abstractmethod
    def next_tool_calls(
        self,
        *,
        role: str,
        goal: str,
        available_tools: list[str],
        observations: list[str],
        turn: int,
    ) -> list[ToolCall]:
        """Choose the next batch of tools. An empty list ends the loop."""

    @abstractmethod
    def interpret_request(
        self, request: RequestFacts, pack: EvidencePack
    ) -> RequestInterpretation:
        """Read intent out of the request's free text, as structured signals."""

    @abstractmethod
    def narrate(
        self,
        *,
        role: str,
        pack: EvidencePack,
        verdict: PolicyVerdict,
        interpretation: RequestInterpretation,
    ) -> Narration:
        """Write the recommendation and next step a human will read."""
