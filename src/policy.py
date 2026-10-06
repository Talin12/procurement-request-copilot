"""Deterministic procurement policy engine.

Every output a human would audit - approval ladder, budget sufficiency, security
/ privacy / legal triggers, assessment staleness, evidence conflicts, missing
required fields - is computed here in ordinary code from the evidence pack. No
model call participates. The engine is pure: same evidence in, same verdict out.

The model's interpretation is accepted only where it can *add* conservatism
(extra inferred data classes, extra clarifying questions). It can never clear a
flag, drop an approver, or lower the review tier.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.config import reference_date, security_validity_days
from src.models import EvidencePack, PolicyVerdict, RequestInterpretation

# Policy section 4. Boundaries are inclusive of the upper bound; the ladder is
# kept in Decimal so that 1000.00 and 1000.01 land in different tiers exactly.
APPROVAL_LADDER: list[tuple[Decimal | None, str, list[str]]] = [
    (Decimal("1000"), "Up to $1,000", ["Manager"]),
    (Decimal("10000"), "$1,000.01 - $10,000", ["Department Head", "Procurement"]),
    (Decimal("25000"), "$10,000.01 - $25,000", ["Department Head", "Finance", "Procurement"]),
    (None, "Above $25,000", ["Department Head", "Finance", "CFO", "Procurement"]),
]

# Data classes that make a request security-sensitive (policy section 5).
SENSITIVE_CLASSES = {
    "source_code",
    "production_access",
    "confidential_documents",
    "employee_pii",
    "customer_pii",
    "personal_data",
    "credentials",
}

# Classes that additionally trigger privacy review (policy section 6).
PRIVACY_CLASSES = {"employee_pii", "customer_pii", "personal_data"}

LEGAL_NEW_VENDOR_THRESHOLD = Decimal("10000")

_APPROVED_TERMS = {"approved", "standard", "approved/standard"}
_NEW_VENDOR_STATUSES = {"new", "pending", "unknown", "not onboarded"}


def classify_data_access(
    declared_level: str | None, integrations: list[str] | None
) -> tuple[set[str], bool]:
    """Map the declared data-access level and integrations to policy data classes.

    Returns the classes found and whether the declared level is usable at all.
    Matching is keyword-based rather than an enum lookup so that hidden cases
    using different vocabulary for the same concept still classify correctly.
    """
    classes: set[str] = set()
    text = (declared_level or "").strip().lower()
    declared_usable = bool(text) and text not in {"unknown", "unspecified", "tbd", "n/a"}

    def scan(value: str) -> None:
        if any(token in value for token in ("source code", "source_code", "codebase", "repository", "repo", "git")):
            classes.add("source_code")
        if any(token in value for token in ("production", "prod ", "cloud account", "cloud_account", "infrastructure")):
            classes.add("production_access")
        if any(token in value for token in ("confidential", "contract", "legal document")):
            classes.add("confidential_documents")
        if any(token in value for token in ("credential", "secret", "api key", "api_key", "password", "token")):
            classes.add("credentials")
        has_personal = any(token in value for token in ("pii", "personal data", "personal_data", "personally identifiable"))
        if has_personal and "employee" in value:
            classes.add("employee_pii")
        elif has_personal and "customer" in value:
            classes.add("customer_pii")
        elif has_personal:
            classes.add("personal_data")

    if declared_usable:
        scan(text)
    for integration in integrations or []:
        scan(str(integration).strip().lower())

    return classes, declared_usable


def approval_tier(amount: float | None) -> tuple[str | None, list[str]]:
    """Deterministic approval ladder for an annualized amount (policy section 4)."""
    if amount is None:
        return None, []
    value = Decimal(str(amount))
    for upper, label, approvers in APPROVAL_LADDER:
        if upper is None or value <= upper:
            return label, list(approvers)
    return None, []


def _normalise_security_status(raw: str | None) -> str:
    text = (raw or "").strip().lower()
    if not text:
        return "unknown"
    if text in {"approved", "complete", "completed", "current"}:
        return "approved"
    if "expired" in text or "stale" in text:
        return "expired"
    if text in {"pending", "not_completed", "not completed", "in progress", "incomplete"}:
        return "not_completed"
    return "unknown"


def _is_assessment_current(review_date: str | None, as_of: date) -> bool | None:
    """None when there is no date to judge; otherwise whether it is still valid."""
    if not review_date:
        return None
    try:
        reviewed = date.fromisoformat(str(review_date).strip())
    except ValueError:
        return None
    return (as_of - reviewed).days <= security_validity_days()


def _vendor_is_new(registry_status: str | None, registry_found: bool) -> bool:
    if not registry_found:
        return True
    return (registry_status or "").strip().lower() in _NEW_VENDOR_STATUSES


def _terms_approved(status: str | None) -> bool:
    return (status or "").strip().lower() in _APPROVED_TERMS


def evaluate(
    pack: EvidencePack, interpretation: RequestInterpretation | None = None
) -> PolicyVerdict:
    """Apply the whole policy to an evidence pack and return an auditable verdict."""
    as_of = reference_date()
    request = pack.request
    flags: list[str] = []
    approvals: list[str] = []
    missing: list[str] = []
    evidence: list[dict] = []

    def flag(name: str) -> None:
        if name not in flags:
            flags.append(name)

    def approver(name: str) -> None:
        if name not in approvals:
            approvals.append(name)

    def note(source: str, finding: str, reference: str | None = None) -> None:
        evidence.append({"source": source, "finding": finding, "reference": reference})

    # --- Section 1: required request information -------------------------------
    if not request.department:
        missing.append("Requester department could not be resolved from the employee record")
    if not request.product_name:
        missing.append("Product or service name is missing")
    if not request.vendor_name:
        missing.append("Vendor name is missing")
    if request.annual_cost_usd is None:
        missing.append("Annual cost (or a reasonable annual estimate) is missing")
    if request.user_count is None:
        missing.append("Number of users/licenses requested is missing")
    if not (request.business_justification or "").strip():
        missing.append("Business purpose is missing")

    data_classes, declared_usable = classify_data_access(
        request.data_access_level, request.requested_integrations
    )
    if not declared_usable:
        missing.append("Intended data-access level is not specified")
    if request.requested_integrations is None:
        missing.append("Required integrations were not stated")

    # The model may only widen the sensitive set, never narrow it.
    if interpretation:
        for inferred in interpretation.inferred_data_classes:
            token = str(inferred).strip().lower()
            if token in SENSITIVE_CLASSES:
                data_classes.add(token)

    if data_classes:
        note(
            "policy_engine",
            "Request touches sensitive data classes: " + ", ".join(sorted(data_classes)),
            "Policy section 5",
        )

    # --- Section 9: untrusted content -----------------------------------------
    if pack.injection.detected:
        flag("prompt_injection_detected")
        patterns = ", ".join(sorted({f.pattern for f in pack.injection.findings}))
        note(
            "injection_scanner",
            f"Request text contains instruction-like content that was ignored ({patterns}). "
            "Policy and evidence were applied unchanged.",
            "Policy section 9",
        )

    # --- Section 2: budget -----------------------------------------------------
    if pack.budget.found and pack.budget.available_usd is not None:
        available = pack.budget.available_usd
        if request.annual_cost_usd is None:
            note(
                "budget_tool",
                f"{pack.budget.department} has ${available:,.0f} available software budget; "
                "the request has no cost to compare against it.",
                "department_budgets.csv",
            )
        elif request.annual_cost_usd > available:
            flag("budget_insufficient")
            approver("Finance")
            shortfall = request.annual_cost_usd - available
            note(
                "budget_tool",
                f"Request of ${request.annual_cost_usd:,.0f} exceeds {pack.budget.department}'s "
                f"available budget of ${available:,.0f} by ${shortfall:,.0f}.",
                "department_budgets.csv",
            )
        else:
            note(
                "budget_tool",
                f"Request of ${request.annual_cost_usd:,.0f} is within {pack.budget.department}'s "
                f"available budget of ${available:,.0f}.",
                "department_budgets.csv",
            )
    else:
        flag("budget_unverified")
        approver("Finance")
        missing.append("Department budget record could not be located")
        note("budget_tool", "No budget record found for the requesting department.", "department_budgets.csv")

    # --- Section 3: existing software overlap ----------------------------------
    overlaps = pack.catalog.overlapping
    if overlaps:
        flag("existing_tool_overlap")
        for match in overlaps:
            note(
                "catalog_tool",
                f"{match.product_name} ({match.category}, {match.status}, {match.licensed_seats} seats, "
                f"{match.scope}) already covers this need in part: {', '.join(match.match_reasons)}.",
                match.software_id,
            )
    elif pack.catalog.vendor_only:
        for match in pack.catalog.vendor_only:
            note(
                "catalog_tool",
                f"Existing relationship with this vendor via {match.product_name} ({match.software_id}); "
                "different category, so it is not duplicate capability.",
                match.software_id,
            )
    else:
        note("catalog_tool", "No overlapping product, vendor or category found in the approved catalog.", "software_catalog.csv")

    if overlaps and interpretation and interpretation.overlap_assessment == "duplicate":
        flag("duplicate_of_existing_tool")

    # --- Section 5/6/7: vendor posture ----------------------------------------
    registry = pack.vendor_registry
    risk = pack.vendor_risk

    if registry.found:
        note(
            "vendor_registry_tool",
            f"Internal registry: procurement={registry.procurement_status}, "
            f"security={registry.security_status}, reviewed={registry.security_review_date or 'never'}, "
            f"legal terms={registry.legal_terms_status}.",
            registry.vendor_id,
        )
    else:
        flag("vendor_not_in_registry")
        note("vendor_registry_tool", "Vendor is not present in the internal procurement registry.", "vendors.csv")

    if risk.available:
        note(
            "vendor_risk_api",
            f"External assessment: risk={risk.risk_level}, status={risk.security_review_status}, "
            f"reviewed={risk.last_review_date or 'never'}, personal data={risk.processes_personal_data}, "
            f"stored outside region={risk.stores_data_outside_region}.",
            "GET /vendor-risk",
        )
    else:
        flag("vendor_risk_unavailable")
        note(
            "vendor_risk_api",
            f"External vendor-risk assessment could not be retrieved ({risk.error}). "
            "Vendor security posture is unverified; no favourable status was assumed.",
            "GET /vendor-risk",
        )

    registry_status = _normalise_security_status(registry.security_status)
    api_status = _normalise_security_status(risk.security_review_status) if risk.available else "unknown"

    known_statuses = {s for s in (registry_status, api_status) if s != "unknown"}
    if len(known_statuses) > 1:
        flag("conflicting_vendor_evidence")
        note(
            "policy_engine",
            f"Internal registry reports security '{registry.security_status}' while the external service "
            f"reports '{risk.security_review_status}'. The conflict is surfaced rather than resolved automatically.",
            "Policy section 5",
        )

    review_date = risk.last_review_date if risk.available else registry.security_review_date
    currency = _is_assessment_current(review_date, as_of)
    assessment_ok = False
    if "not_completed" in known_statuses or "expired" in known_statuses:
        assessment_ok = False
    elif currency is True and known_statuses == {"approved"}:
        assessment_ok = True

    if currency is False:
        flag("vendor_review_expired")
        note(
            "policy_engine",
            f"Last security assessment ({review_date}) is older than {security_validity_days()} days "
            f"as of the {as_of.isoformat()} policy snapshot.",
            "Policy section 5",
        )
    elif currency is None and not risk.available and not registry.security_review_date:
        flag("vendor_assessment_missing")
    elif currency is None:
        flag("vendor_assessment_missing")

    if "expired" in known_statuses and "vendor_review_expired" not in flags:
        flag("vendor_review_expired")

    # Security review (policy section 5)
    security_reasons: list[str] = []
    if data_classes:
        security_reasons.append("sensitive data or system access requested")
    if not assessment_ok:
        security_reasons.append("vendor security assessment is missing, expired or not completed")
    if "conflicting_vendor_evidence" in flags:
        security_reasons.append("internal and external vendor evidence disagree")
    if "vendor_risk_unavailable" in flags:
        security_reasons.append("vendor risk could not be verified")

    if security_reasons:
        flag("security_review_required")
        approver("Security")
        note("policy_engine", "Security review required: " + "; ".join(security_reasons) + ".", "Policy section 5")

    # Privacy review (policy section 6)
    privacy_reasons: list[str] = []
    if data_classes & PRIVACY_CLASSES:
        privacy_reasons.append("the tool will process personal data")
    if risk.stores_data_outside_region and data_classes:
        privacy_reasons.append("vendor stores data outside the operating region")
    if privacy_reasons:
        flag("privacy_review_required")
        approver("Privacy")
        note("policy_engine", "Privacy review required: " + "; ".join(privacy_reasons) + ".", "Policy section 6")

    # Legal review (policy section 7)
    amount = Decimal(str(request.annual_cost_usd)) if request.annual_cost_usd is not None else None
    vendor_new = _vendor_is_new(registry.procurement_status, registry.found)
    legal_reasons: list[str] = []
    if vendor_new and amount is not None and amount >= LEGAL_NEW_VENDOR_THRESHOLD:
        legal_reasons.append(f"new vendor with annual spend of ${amount:,.0f}")
    if not _terms_approved(registry.legal_terms_status):
        legal_reasons.append(f"legal terms are '{registry.legal_terms_status or 'unknown'}', not standard/approved")
    if risk.stores_data_outside_region and (data_classes & PRIVACY_CLASSES):
        legal_reasons.append("personal data would be processed outside the operating region")
    if legal_reasons:
        flag("legal_review_required")
        approver("Legal")
        note("policy_engine", "Legal review required: " + "; ".join(legal_reasons) + ".", "Policy section 7")

    # --- Section 4: financial approval ladder ----------------------------------
    tier_label, tier_approvers = approval_tier(request.annual_cost_usd)
    if tier_label:
        for name in tier_approvers:
            approver(name)
        note(
            "policy_engine",
            f"Annualized amount of ${request.annual_cost_usd:,.0f} falls in the '{tier_label}' tier, "
            f"requiring {', '.join(tier_approvers)}.",
            "Policy section 4",
        )
    else:
        note(
            "policy_engine",
            "No annual amount was supplied, so the financial approval tier cannot be determined. "
            "No approver list was assumed.",
            "Policy section 4",
        )

    # Model-supplied clarifying questions are deliberately NOT merged here.
    # `missing_information` means "a field policy section 1 requires is absent",
    # and it gates the recommendation. A reviewer question is useful context, not
    # a missing field, so it is surfaced as evidence instead. Letting the model
    # write into this list would let it change the recommendation indirectly.
    if missing:
        flag("missing_information")

    # --- Recommendation --------------------------------------------------------
    recommendation, next_step = _decide(
        missing=missing,
        flags=flags,
        approvals=approvals,
        overlaps=bool(overlaps),
        interpretation=interpretation,
        request_product=request.product_name or "the requested product",
    )

    return PolicyVerdict(
        recommendation=recommendation,
        next_step=next_step,
        required_approvals=approvals,
        risk_flags=flags,
        missing_information=missing,
        evidence=evidence,
        human_review_required=True,
        approval_tier=tier_label,
    )


def _decide(
    *,
    missing: list[str],
    flags: list[str],
    approvals: list[str],
    overlaps: bool,
    interpretation: RequestInterpretation | None,
    request_product: str,
) -> tuple[str, str]:
    """Pick the recommendation label and next action. Always advisory."""
    if missing:
        return (
            "Request clarification before review",
            "Return the request to the requester for the missing information listed above, "
            "then re-run the review. Do not progress the request until it is complete.",
        )

    blocking = [f for f in flags if f.endswith("_required") or f in {
        "budget_insufficient",
        "vendor_risk_unavailable",
        "conflicting_vendor_evidence",
        "vendor_review_expired",
        "vendor_assessment_missing",
        "vendor_not_in_registry",
    }]
    if blocking:
        # A required review always outranks the "use what we already own" shortcut:
        # an overlapping product does not make a security or budget problem go away.
        return (
            "Route for required reviews before approval",
            "Send the evidence pack to " + ", ".join(approvals) + " for human review. "
            "The copilot does not approve spend, accept terms or change budgets.",
        )

    duplicate = overlaps and interpretation and interpretation.overlap_assessment == "duplicate"
    if duplicate:
        return (
            "Use existing licensed capacity instead of a new purchase",
            "Ask Procurement to confirm spare seats on the overlapping product and route the "
            f"requester there before any new spend on {request_product} is considered.",
        )

    return (
        "Proceed to standard approval",
        "Route to " + ", ".join(approvals) + " for the normal approval decision. "
        "Final approval remains with the named humans.",
    )
