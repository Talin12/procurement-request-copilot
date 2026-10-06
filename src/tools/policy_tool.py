"""The policy engine exposed as a tool.

Architecture A lets the agent call this like any other tool. Architecture B runs
it in code between the two agents. Either way the engine itself is the same pure
function, so the two architectures are compared on orchestration, not on two
different rule sets.
"""
from __future__ import annotations

from src import policy
from src.models import EvidencePack, PolicyVerdict, RequestInterpretation
from src.tools.registry import register


@register(
    "evaluate_procurement_policy",
    "Apply the written procurement policy to a gathered evidence pack and return "
    "the required approvals, risk flags, missing information and next step. "
    "Fully deterministic: thresholds, budget comparison, staleness and review "
    "triggers are computed in code, not judged by a model.",
    {
        "type": "object",
        "properties": {
            "evidence_pack": {
                "type": "object",
                "description": "The EvidencePack gathered so far.",
            }
        },
        "required": ["evidence_pack"],
    },
    deterministic=True,
)
def evaluate_procurement_policy(
    evidence_pack: EvidencePack | dict,
    interpretation: RequestInterpretation | dict | None = None,
) -> PolicyVerdict:
    pack = evidence_pack if isinstance(evidence_pack, EvidencePack) else EvidencePack.model_validate(evidence_pack)
    if interpretation is None or isinstance(interpretation, RequestInterpretation):
        parsed = interpretation
    else:
        parsed = RequestInterpretation.model_validate(interpretation)
    return policy.evaluate(pack, parsed)
