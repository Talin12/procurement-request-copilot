"""Architecture A - single-agent baseline.

One agent owns the whole job. It is given every tool, including the policy
engine, and it decides what to call and when. It then reads the raw request
text, interprets it, and writes the recommendation itself.

The orchestrator keeps three guarantees around it that the agent cannot opt out
of: the untrusted-text scan always runs, the deterministic verdict is always
computed and used as the floor for the final answer, and the output filter always
runs. Without that floor, an agent that forgot to call the policy engine would
silently return an unchecked decision.

`expose_policy_tool=True` restores the variant drawn in the brief, where the
policy engine is one more tool the agent may choose to call. The evaluation runs
both; the shipped default is False because making a deterministic check
model-optional cost a model turn per request and changed no outcome.
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
from src.tools import ToolRunner

ROLE = "sole procurement analyst"
GOAL = (
    "Assess a software purchase request end to end. Gather the budget position, any "
    "overlapping approved software, the internal vendor registry entry and the external "
    "vendor-risk assessment, then apply the procurement policy."
)

AGENT_TOOLS = EVIDENCE_TOOLS + ["evaluate_procurement_policy"]

#: The shipped toolset. The policy engine is deliberately absent: the orchestrator
#: runs it unconditionally, so there is nothing for the agent to decide about it.
#: Evaluation showed exposing it as a tool costs one model turn and one redundant
#: evaluation per request and changes no outcome - see evals/results/comparison.md.
AGENT_TOOLS_WITHOUT_POLICY = list(EVIDENCE_TOOLS)


def run(
    request_id: str, reasoner: Reasoner, *, expose_policy_tool: bool = False
) -> ProcurementDecision:
    runner = ToolRunner()
    pack = seed_pack(runner, request_id)

    # The agent chooses its own evidence-gathering path.
    run_tool_loop(
        reasoner=reasoner,
        runner=runner,
        pack=pack,
        available_tools=AGENT_TOOLS if expose_policy_tool else AGENT_TOOLS_WITHOUT_POLICY,
        role=ROLE,
        goal=f"{GOAL}\nRequest under review: {request_id}.",
    )

    enforce_guardrails(runner, pack)

    # The same agent reads the raw request text and interprets it.
    interpretation = reasoner.interpret_request(pack.request, pack)

    # Recomputed in code whether or not the agent called the policy tool.
    verdict = run_policy_engine(runner, pack, interpretation)

    narration = reasoner.narrate(
        role=ROLE, pack=pack, verdict=verdict, interpretation=interpretation
    )

    return finalize(
        request_id=request_id,
        architecture="single",
        pack=pack,
        verdict=verdict,
        interpretation=interpretation,
        narration=narration,
        reasoner=reasoner,
        runner=runner,
    )
