"""Orchestration primitives shared by both architectures.

Both architectures use the same tools, the same policy engine and the same
finalisation step. The only thing that differs is who decides what happens next
and what each model context is allowed to see - which is exactly the variable
the evaluation is meant to isolate.
"""
from __future__ import annotations

import re

from src.contracts import EvidenceItem, ProcurementDecision, RunTelemetry
from src.llm.base import Narration, Reasoner, ToolCall
from src.models import EvidencePack, PolicyVerdict, RequestInterpretation
from src.sanitize import scrub, scrub_all
from src.tools import ToolError, ToolRunner
from src.tools.policy_tool import evaluate_procurement_policy

EVIDENCE_TOOLS = [
    "check_department_budget",
    "search_software_catalog",
    "get_vendor_registry_record",
    "check_vendor_risk_api",
]

MAX_TOOL_TURNS = 4


def seed_pack(runner: ToolRunner, request_id: str) -> EvidencePack:
    """Load the request itself. This is the input, not something to discover."""
    facts = runner.call("get_request_context", request_id=request_id)
    return EvidencePack(request=facts, tools_run=["get_request_context"])


def resolve_arguments(name: str, pack: EvidencePack, supplied: dict) -> dict:
    """Fill in arguments the caller did not supply, from the evidence pack.

    A model may pass arguments explicitly; the offline reasoner passes none. The
    orchestrator derives them from the request so a tool is never called with an
    argument the model invented.
    """
    request = pack.request
    derived: dict = {
        "get_request_context": {"request_id": request.request_id},
        "check_department_budget": {"department": request.department},
        "search_software_catalog": {
            "product_name": request.product_name,
            "category": request.category,
            "vendor_name": request.vendor_name,
        },
        "get_vendor_registry_record": {"vendor_name": request.vendor_name},
        "check_vendor_risk_api": {"vendor_name": request.vendor_name},
        "scan_untrusted_text": {"fields": untrusted_fields(pack)},
        "evaluate_procurement_policy": {"evidence_pack": pack},
    }.get(name, {})
    # Derived values win for identifiers so a model cannot redirect a lookup to a
    # different vendor or department than the one on the request.
    return {**supplied, **{k: v for k, v in derived.items() if v is not None}}


def untrusted_fields(pack: EvidencePack) -> dict[str, str | None]:
    """Every string in the pack that originated outside our control."""
    request = pack.request
    return {
        "business_justification": request.business_justification,
        "product_name": request.product_name,
        "vendor_name": request.vendor_name,
        "category": request.category,
        "requested_integrations": ", ".join(request.requested_integrations or []) or None,
        "vendor_registry_notes": pack.vendor_registry.notes,
        "vendor_risk_notes": pack.vendor_risk.notes,
    }


def apply_result(pack: EvidencePack, name: str, result: object) -> None:
    """Fold a tool result into the evidence pack."""
    mapping = {
        "check_department_budget": "budget",
        "search_software_catalog": "catalog",
        "get_vendor_registry_record": "vendor_registry",
        "check_vendor_risk_api": "vendor_risk",
        "scan_untrusted_text": "injection",
    }
    if name in mapping:
        setattr(pack, mapping[name], result)
    if name not in pack.tools_run:
        pack.tools_run.append(name)


def describe_result(name: str, result: object) -> str:
    text = result.model_dump_json() if hasattr(result, "model_dump_json") else str(result)
    return f"{name} -> {text[:900]}"


def run_tool_loop(
    *,
    reasoner: Reasoner,
    runner: ToolRunner,
    pack: EvidencePack,
    available_tools: list[str],
    role: str,
    goal: str,
) -> list[str]:
    """Let the reasoner drive tool selection until it stops asking for more."""
    observations: list[str] = []
    for turn in range(MAX_TOOL_TURNS):
        try:
            calls = reasoner.next_tool_calls(
                role=role, goal=goal, available_tools=available_tools, observations=observations, turn=turn
            )
        except Exception as exc:
            pack.notes.append(f"Tool planning failed on turn {turn}: {type(exc).__name__}: {exc}")
            break
        if not calls:
            break
        for call in calls:
            _execute(runner, pack, call, observations)
    return observations


def _execute(runner: ToolRunner, pack: EvidencePack, call: ToolCall, observations: list[str]) -> None:
    arguments = resolve_arguments(call.name, pack, call.arguments or {})
    try:
        result = runner.call(call.name, **arguments)
    except ToolError as exc:
        # A failed tool is recorded as a gap in evidence, never skipped silently.
        pack.notes.append(f"{call.name} failed: {exc}")
        observations.append(f"{call.name} -> FAILED: {exc}")
        return
    apply_result(pack, call.name, result)
    observations.append(describe_result(call.name, result))


def enforce_guardrails(runner: ToolRunner, pack: EvidencePack) -> None:
    """Checks that are never left to model discretion.

    The injection scan runs in code, over the finished pack, every time. A model
    cannot decline to run it, and it sees vendor notes as well as request text.
    """
    result = runner.call("scan_untrusted_text", fields=untrusted_fields(pack))
    apply_result(pack, "scan_untrusted_text", result)


def run_policy_engine(
    runner: ToolRunner, pack: EvidencePack, interpretation: RequestInterpretation | None
) -> PolicyVerdict:
    runner.calls += 1
    runner.names.append("evaluate_procurement_policy")
    return evaluate_procurement_policy(pack, interpretation)


_FLAG_SAFE = re.compile(r"[^a-z0-9_]+")


def _normalise_flag(value: str) -> str:
    return _FLAG_SAFE.sub("_", str(value).strip().lower()).strip("_")[:48]


def finalize(
    *,
    request_id: str,
    architecture: str,
    pack: EvidencePack,
    verdict: PolicyVerdict,
    interpretation: RequestInterpretation,
    narration: Narration,
    reasoner: Reasoner,
    runner: ToolRunner,
) -> ProcurementDecision:
    """Assemble the final decision with the deterministic verdict as the floor.

    The model supplies wording and may append a risk it noticed. Everything that
    gates a human decision - approvals, flags, missing information, and the fact
    that a human must review - comes from the engine and cannot be overridden.
    """
    risk_flags = list(verdict.risk_flags)
    for extra in narration.added_risk_flags[:3]:
        flag = _normalise_flag(extra)
        if flag and flag not in risk_flags:
            risk_flags.append(flag)

    evidence = [
        EvidenceItem(source=item["source"], finding=item["finding"], reference=item.get("reference"))
        for item in verdict.evidence
    ]
    if interpretation.summary:
        evidence.insert(
            0,
            EvidenceItem(
                source=f"reasoner:{reasoner.name}",
                finding=interpretation.summary,
                reference="request interpretation",
            ),
        )
    for question in interpretation.clarifying_questions[:3]:
        evidence.append(
            EvidenceItem(
                source=f"reasoner:{reasoner.name}",
                finding=f"Open question for the requester: {question}",
                reference="not a policy-required field",
            )
        )
    if narration.reviewer_note:
        evidence.append(
            EvidenceItem(
                source=f"reasoner:{reasoner.name}",
                finding=narration.reviewer_note,
                reference="reviewer note",
            )
        )

    # Last line of defence: nothing instruction-shaped leaves the system, whichever
    # reasoner produced the wording.
    for item in evidence:
        item.finding = scrub(item.finding) or item.finding

    return ProcurementDecision(
        request_id=request_id,
        recommendation=scrub(narration.recommendation or verdict.recommendation),
        evidence=evidence,
        required_approvals=verdict.required_approvals,
        missing_information=scrub_all(verdict.missing_information),
        risk_flags=risk_flags,
        next_step=scrub(narration.next_step or verdict.next_step),
        # The copilot recommends; a human always decides. Not model-settable.
        human_review_required=True,
        telemetry=RunTelemetry(
            llm_calls=reasoner.calls,
            tool_calls=runner.calls,
            tool_names=runner.names,
        ),
    )
