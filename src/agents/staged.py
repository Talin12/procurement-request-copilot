"""Architecture B - staged two-agent variant.

Stage 1, the Procurement Analyst, reads the raw request and gathers evidence. It
produces a structured evidence pack and a structured interpretation. It never
sees a verdict and never writes the recommendation.

Code then runs the deterministic policy engine on that pack. The engine is not
exposed to either agent as a tool here - orchestration is the orchestrator's job.

Stage 2, the Policy / Risk Reviewer, reads only the structured evidence pack and
the verdict. The requester's free text does not reach it. That containment is
the point of the split: the context that writes the final recommendation never
reads attacker-controlled prose, so an injection that survives stage 1 still has
no path into the output.
"""
from __future__ import annotations

from src.agents.common import (
    EVIDENCE_TOOLS,
    enforce_guardrails,
    finalize,
    run_policy_engine,
    run_tool_loop,
    seed_pack,
)
from src.contracts import ProcurementDecision
from src.llm.base import Reasoner
from src.models import EvidencePack, RequestFacts
from src.tools import ToolRunner

ANALYST_ROLE = "procurement analyst (evidence only)"
ANALYST_GOAL = (
    "Gather the complete evidence package for a software purchase request: the department "
    "budget position, overlapping approved software, the internal vendor registry entry and "
    "the external vendor-risk assessment. Do not judge the request."
)
REVIEWER_ROLE = "policy and risk reviewer"

#: Free-text fields that stay inside stage 1. Stage 2 sees structured facts only.
REDACTED_FIELDS = ("business_justification",)


def redact_for_reviewer(pack: EvidencePack) -> EvidencePack:
    """Strip requester-authored prose before the reviewer context sees the pack.

    The reviewer needs the facts, not the narrative the requester wrote. Removing
    the prose removes the only channel an injected instruction had into stage 2.
    """
    reduced = pack.model_copy(deep=True)
    fields = reduced.request.model_dump()
    for name in REDACTED_FIELDS:
        if fields.get(name):
            fields[name] = "[redacted: requester free text is not shown to the reviewer stage]"
    reduced.request = RequestFacts.model_validate(fields)
    reduced.vendor_registry.notes = None
    reduced.vendor_risk.notes = None
    return reduced


def run(request_id: str, reasoner: Reasoner) -> ProcurementDecision:
    runner = ToolRunner()
    pack = seed_pack(runner, request_id)

    # --- Stage 1: analyst ---------------------------------------------------
    run_tool_loop(
        reasoner=reasoner,
        runner=runner,
        pack=pack,
        available_tools=EVIDENCE_TOOLS,
        role=ANALYST_ROLE,
        goal=f"{ANALYST_GOAL}\nRequest under review: {request_id}.",
    )
    enforce_guardrails(runner, pack)
    interpretation = reasoner.interpret_request(pack.request, pack)

    # --- Deterministic policy engine, in code between the two agents --------
    verdict = run_policy_engine(runner, pack, interpretation)

    # --- Stage 2: reviewer, on redacted evidence only -----------------------
    narration = reasoner.narrate(
        role=REVIEWER_ROLE,
        pack=redact_for_reviewer(pack),
        verdict=verdict,
        interpretation=interpretation,
    )

    return finalize(
        request_id=request_id,
        architecture="staged",
        pack=pack,
        verdict=verdict,
        interpretation=interpretation,
        narration=narration,
        reasoner=reasoner,
        runner=runner,
    )
