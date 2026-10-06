"""Deterministic reasoner used when no model credentials are configured.

This is a real implementation of the `Reasoner` contract, not a stub that
returns empty values: it plans the same tool sequence, extracts the same
structured signals from free text, and writes the same narrative shape as the
hosted backend. It is deterministic, free, and offline, which makes it the
default so the product and the evaluation run on a clean machine with no key.

It is deliberately conservative. Where a hosted model would weigh nuance, this
reasoner widens the sensitive set and asks a clarifying question instead of
guessing, because every judgement that actually gates a decision already lives
in `src/policy.py`.
"""
from __future__ import annotations

import re

from src.llm.base import Narration, Reasoner, ToolCall
from src.models import EvidencePack, PolicyVerdict, RequestFacts, RequestInterpretation

_DATA_CLASS_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("source_code", ("source code", "repository", "repositories", "codebase", "commit", "pull request")),
    ("production_access", ("production", "incident", "cloud account", "infrastructure", "deployment", "telemetry")),
    ("confidential_documents", ("confidential", "nda", "non-disclosure", "term sheet", "contract review", "legal document")),
    ("customer_pii", ("customer", "ticket history", "support replies", "lead record", "crm", "subscriber")),
    ("employee_pii", ("employee", "payroll", "hr record", "personnel")),
    ("credentials", ("credential", "secret", "api key", "password")),
]

_GAP_PHRASES = (
    "too specialist",
    "not suitable",
    "does not support",
    "doesn't support",
    "cannot",
    "lacks",
    "gap",
    "different use case",
    "expand",
    "additional",
)

_DUPLICATE_PHRASES = ("same as", "equivalent to", "instead of", "replace")


class OfflineReasoner(Reasoner):
    name = "offline"
    is_model_backed = False

    def next_tool_calls(
        self,
        *,
        role: str,
        goal: str,
        available_tools: list[str],
        observations: list[str],
        turn: int,
    ) -> list[ToolCall]:
        self._count()
        # Turn 1 fans out across every evidence source the caller exposed.
        # Turn 2 applies policy, which only architecture A exposes as a tool.
        # Unavailable tools are filtered out rather than stalling the loop.
        plan: list[list[str]] = [
            [
                "get_request_context",
                "scan_untrusted_text",
                "check_department_budget",
                "search_software_catalog",
                "get_vendor_registry_record",
                "check_vendor_risk_api",
            ],
            ["evaluate_procurement_policy"],
        ]
        if turn >= len(plan):
            return []
        wanted = [name for name in plan[turn] if name in available_tools]
        return [ToolCall(name=name, arguments={}) for name in wanted]

    def interpret_request(self, request: RequestFacts, pack: EvidencePack) -> RequestInterpretation:
        self._count()
        text = " ".join(
            part
            for part in (request.business_justification, request.product_name, request.category)
            if part
        ).lower()

        inferred = {
            label
            for label, hints in _DATA_CLASS_HINTS
            if any(hint in text for hint in hints)
        }

        overlaps = pack.catalog.overlapping
        if not overlaps:
            assessment = "not_applicable"
            rationale = "No catalog product serves this category today."
        elif any(phrase in text for phrase in _GAP_PHRASES):
            assessment = "credible_gap"
            rationale = (
                "The justification names a limitation of the existing tool or asks to extend "
                "capacity that is already approved, which is a stated gap rather than a duplicate."
            )
        elif any(phrase in text for phrase in _DUPLICATE_PHRASES) or self._covers_request(request, pack):
            assessment = "duplicate"
            rationale = (
                "An approved company-wide product already covers this category and the request "
                "states no limitation of it."
            )
        else:
            assessment = "unclear"
            rationale = (
                "An approved product already covers this category and the justification does not "
                "say why it is insufficient."
            )

        questions: list[str] = []
        if overlaps and assessment in {"unclear", "duplicate"}:
            names = ", ".join(m.product_name for m in overlaps)
            questions.append(
                f"Why do the already-approved alternatives ({names}) not meet this need?"
            )

        injected = None
        if pack.injection.detected:
            injected = (
                "The request text contains embedded instructions attempting to bypass procurement "
                "review. They were treated as business data and ignored."
            )

        summary = self._summarise(request)
        return RequestInterpretation(
            inferred_data_classes=sorted(inferred),
            overlap_assessment=assessment,
            overlap_rationale=rationale,
            clarifying_questions=questions,
            untrusted_instruction_summary=injected,
            summary=summary,
        )

    def narrate(
        self,
        *,
        role: str,
        pack: EvidencePack,
        verdict: PolicyVerdict,
        interpretation: RequestInterpretation,
    ) -> Narration:
        self._count()
        request = pack.request
        headline = verdict.recommendation
        product = request.product_name or "the requested product"

        reasons: list[str] = []
        if "missing_information" in verdict.risk_flags:
            reasons.append(f"{len(verdict.missing_information)} required field(s) are incomplete")
        if "budget_insufficient" in verdict.risk_flags:
            reasons.append("the amount exceeds the department's available budget")
        if "existing_tool_overlap" in verdict.risk_flags:
            reasons.append("an approved product already covers this category")
        if "vendor_risk_unavailable" in verdict.risk_flags:
            reasons.append("vendor risk could not be verified")
        if "conflicting_vendor_evidence" in verdict.risk_flags:
            reasons.append("internal and external vendor records disagree")
        if "vendor_review_expired" in verdict.risk_flags:
            reasons.append("the vendor's security assessment is out of date")
        for label, text in (
            ("security_review_required", "security review is triggered"),
            ("privacy_review_required", "privacy review is triggered"),
            ("legal_review_required", "legal review is triggered"),
        ):
            if label in verdict.risk_flags:
                reasons.append(text)

        if reasons:
            recommendation = f"{headline} for {product}: " + "; ".join(reasons) + "."
        else:
            recommendation = f"{headline} for {product}: no policy blockers were found."

        note = None
        if interpretation.untrusted_instruction_summary:
            note = interpretation.untrusted_instruction_summary
        elif interpretation.overlap_rationale and "existing_tool_overlap" in verdict.risk_flags:
            note = interpretation.overlap_rationale

        return Narration(
            recommendation=recommendation,
            next_step=verdict.next_step,
            added_risk_flags=[],
            reviewer_note=note,
        )

    @staticmethod
    def _covers_request(request: RequestFacts, pack: EvidencePack) -> bool:
        """True when an approved company-wide product already covers the category."""
        return any(
            match.scope.strip().lower() == "company-wide"
            and match.status.strip().lower().startswith("approved")
            and match.category.strip().lower() == (request.category or "").strip().lower()
            for match in pack.catalog.overlapping
        )

    @staticmethod
    def _summarise(request: RequestFacts) -> str:
        cost = (
            f"${request.annual_cost_usd:,.0f}/yr" if request.annual_cost_usd is not None else "no stated cost"
        )
        seats = f"{request.user_count} users" if request.user_count is not None else "no stated seat count"
        justification = re.sub(r"\s+", " ", request.business_justification or "").strip()
        if len(justification) > 160:
            justification = justification[:157].rstrip() + "..."
        return (
            f"{request.department or 'Unknown department'} requests {request.product_name} from "
            f"{request.vendor_name} at {cost} for {seats}. Stated purpose: {justification or 'not given'}"
        )
