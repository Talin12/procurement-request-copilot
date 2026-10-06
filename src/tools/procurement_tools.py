"""The seven tools the copilot can use.

Six are deterministic reads or pure computation over local records; one
(`check_vendor_risk_api`) is a real HTTP call to an external service that is
expected to fail sometimes. Failure is returned as evidence, never smoothed
over into a neutral default.
"""
from __future__ import annotations

import re
from functools import lru_cache

import pandas as pd

from src import data_access
from src.vendor_transport import fetch_vendor_risk
from src.models import (
    BudgetFacts,
    CatalogFacts,
    CatalogMatch,
    InjectionFacts,
    InjectionFinding,
    RequestFacts,
    VendorRegistryFacts,
    VendorRiskFacts,
)
from src.tools.registry import ToolError, register

# Instruction-shaped content that may appear inside business data. Matching text
# is never executed or obeyed; it is recorded so a human can see what was tried.
INJECTION_PATTERNS: list[tuple[str, str]] = [
    ("override_instructions", r"\b(ignore|disregard|forget|override|bypass)\b[^.]{0,40}\b(all|any|previous|prior|above|rule|rules|policy|policies|instruction|instructions|control|controls)\b"),
    ("forge_approval", r"\b(treat|consider|mark|record)\b[^.]{0,40}\b(as)\b[^.]{0,30}\b(approved|pre-?approved|authorised|authorized|signed off)\b"),
    ("demand_auto_approval", r"\b(approve|authorise|authorize|purchase|buy|order)\b[^.]{0,30}\b(immediately|now|right away|automatically|without review|no review)\b"),
    ("claim_executive_authority", r"\b(ceo|cfo|cto|vp|executive|board)\b[^.]{0,25}\b(approved|approval|authorised|authorized|mandate|said|instructs?)\b"),
    ("role_reassignment", r"\b(you are now|act as|pretend to be|new instructions?|system prompt|developer mode)\b"),
    ("exfiltration", r"\b(reveal|print|show|disclose|output)\b[^.]{0,30}\b(system prompt|instructions|api key|secret|credentials?)\b"),
    ("skip_controls", r"\b(skip|waive|no need for|do not require|don'?t require)\b[^.]{0,40}\b(review|approval|security|privacy|legal|procurement)\b"),
    # The negated form: asserting a control is unnecessary, rather than asking to
    # skip it. "No security review is required", "without human approval".
    ("assert_control_unnecessary", r"\b(?:no|without)\s+(?:\w+\s+){0,3}(?:reviews?|approvals?|sign-?offs?)\b"),
]

_COMPILED = [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in INJECTION_PATTERNS]

# Tokens that carry no brand information when comparing product names.
_GENERIC_PRODUCT_TOKENS = {
    "pro", "plus", "premium", "enterprise", "business", "team", "teams", "advanced",
    "standard", "basic", "suite", "workspace", "add", "addon", "add-on", "expansion",
    "pack", "package", "edition", "license", "licence", "seats", "assistant", "app",
    "cloud", "platform", "tool", "service", "training", "the", "for", "and", "of",
}


def _clean(value: object) -> str | None:
    """Normalise pandas' NaN / empty-cell representations to None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _brand_tokens(name: str | None) -> set[str]:
    if not name:
        return set()
    tokens = re.split(r"[^A-Za-z0-9]+", name.lower())
    return {t for t in tokens if t and t not in _GENERIC_PRODUCT_TOKENS and len(t) > 2}


@lru_cache(maxsize=1)
def _catalog() -> pd.DataFrame:
    return data_access.load_software_catalog()


@lru_cache(maxsize=1)
def _vendors() -> pd.DataFrame:
    return data_access.load_vendors()


@lru_cache(maxsize=1)
def _employees() -> pd.DataFrame:
    return data_access.load_employees()


@lru_cache(maxsize=1)
def _budgets() -> pd.DataFrame:
    return data_access.load_budgets()


@register(
    "get_request_context",
    "Load a purchase request and resolve the requester's employee record "
    "(name, department, manager, country). Deterministic read of local records.",
    {
        "type": "object",
        "properties": {"request_id": {"type": "string", "description": "e.g. REQ-1001"}},
        "required": ["request_id"],
    },
    deterministic=True,
)
def get_request_context(request_id: str) -> RequestFacts:
    try:
        raw = data_access.get_request(request_id)
    except KeyError as exc:
        raise ToolError(str(exc)) from exc

    employees = _employees()
    match = employees[employees["employee_id"] == raw.get("requester_id")]
    employee = match.iloc[0].to_dict() if not match.empty else {}

    integrations = raw.get("requested_integrations")
    return RequestFacts(
        request_id=raw["request_id"],
        requester_id=raw.get("requester_id", ""),
        requester_name=_clean(employee.get("name")),
        department=_clean(employee.get("department")),
        manager_id=_clean(employee.get("manager_id")),
        country=_clean(employee.get("country")),
        product_name=_clean(raw.get("product_name")),
        vendor_name=_clean(raw.get("vendor_name")),
        category=_clean(raw.get("category")),
        annual_cost_usd=raw.get("annual_cost_usd"),
        user_count=raw.get("user_count"),
        business_justification=_clean(raw.get("business_justification")),
        data_access_level=_clean(raw.get("data_access_level")),
        requested_integrations=list(integrations) if isinstance(integrations, list) else None,
        urgency=_clean(raw.get("urgency")),
    )


@register(
    "check_department_budget",
    "Return the requesting department's annual, committed and available software "
    "budget. Deterministic read; does not decide anything.",
    {
        "type": "object",
        "properties": {"department": {"type": "string"}},
        "required": ["department"],
    },
    deterministic=True,
)
def check_department_budget(department: str | None) -> BudgetFacts:
    if not department:
        return BudgetFacts(found=False)
    budgets = _budgets()
    match = budgets[budgets["department"].str.strip().str.lower() == department.strip().lower()]
    if match.empty:
        return BudgetFacts(department=department, found=False)
    row = match.iloc[0]
    return BudgetFacts(
        department=department,
        found=True,
        annual_software_budget_usd=float(row["annual_software_budget_usd"]),
        committed_usd=float(row["committed_usd"]),
        available_usd=float(row["available_usd"]),
    )


@register(
    "search_software_catalog",
    "Search the approved software catalog for products that overlap with a request "
    "by product brand, category or vendor. Deterministic; returns candidates only.",
    {
        "type": "object",
        "properties": {
            "product_name": {"type": "string"},
            "category": {"type": "string"},
            "vendor_name": {"type": "string"},
        },
        "required": [],
    },
    deterministic=True,
)
def search_software_catalog(
    product_name: str | None = None,
    category: str | None = None,
    vendor_name: str | None = None,
) -> CatalogFacts:
    catalog = _catalog()
    request_tokens = _brand_tokens(product_name)
    wanted_category = (category or "").strip().lower()
    wanted_vendor = (vendor_name or "").strip().lower()

    product_matches: list[CatalogMatch] = []
    category_matches: list[CatalogMatch] = []
    vendor_matches: list[CatalogMatch] = []

    for _, row in catalog.iterrows():
        row_category = str(row["category"]).strip().lower()
        row_vendor = str(row["vendor_name"]).strip().lower()
        reasons: list[str] = []

        brand_hit = bool(request_tokens & _brand_tokens(str(row["product_name"])))
        category_hit = bool(wanted_category) and row_category == wanted_category
        vendor_hit = bool(wanted_vendor) and row_vendor == wanted_vendor

        if brand_hit:
            reasons.append("same product family")
        if category_hit:
            reasons.append(f"same category ({row['category']})")
        if vendor_hit:
            reasons.append("same vendor")
        if not reasons:
            continue

        item = CatalogMatch(
            software_id=str(row["software_id"]),
            product_name=str(row["product_name"]),
            category=str(row["category"]),
            vendor_name=str(row["vendor_name"]),
            status=str(row["status"]),
            annual_cost_usd=float(row["annual_cost_usd"]),
            licensed_seats=int(row["licensed_seats"]),
            scope=str(row["scope"]),
            notes=_clean(row["notes"]) or "",
            match_reasons=reasons,
        )
        if brand_hit:
            product_matches.append(item)
        if category_hit:
            category_matches.append(item)
        if vendor_hit:
            vendor_matches.append(item)

    # Duplicate capability = anything already serving this category. A same-brand
    # or same-vendor row in another category is a relationship, not an overlap.
    overlapping: dict[str, CatalogMatch] = {m.software_id: m for m in category_matches}
    for match in product_matches:
        if wanted_category and match.category.strip().lower() == wanted_category:
            overlapping.setdefault(match.software_id, match)
    vendor_only = [m for m in vendor_matches if m.software_id not in overlapping]

    return CatalogFacts(
        product_matches=product_matches,
        category_matches=category_matches,
        vendor_matches=vendor_matches,
        overlapping=list(overlapping.values()),
        vendor_only=vendor_only,
    )


@register(
    "get_vendor_registry_record",
    "Read the internal procurement/vendor registry entry for a vendor "
    "(procurement status, security status and review date, legal terms). "
    "Deterministic; this record can be stale.",
    {
        "type": "object",
        "properties": {"vendor_name": {"type": "string"}},
        "required": ["vendor_name"],
    },
    deterministic=True,
)
def get_vendor_registry_record(vendor_name: str | None) -> VendorRegistryFacts:
    if not vendor_name:
        return VendorRegistryFacts(found=False)
    vendors = _vendors()
    match = vendors[vendors["vendor_name"].str.strip().str.lower() == vendor_name.strip().lower()]
    if match.empty:
        return VendorRegistryFacts(found=False)
    row = match.iloc[0]
    return VendorRegistryFacts(
        found=True,
        vendor_id=_clean(row["vendor_id"]),
        procurement_status=_clean(row["procurement_status"]),
        security_status=_clean(row["security_status"]),
        security_review_date=_clean(row["security_review_date"]),
        legal_terms_status=_clean(row["legal_terms_status"]),
        notes=_clean(row["notes"]),
    )


@register(
    "check_vendor_risk_api",
    "Call the external vendor-risk service for an independent security assessment. "
    "This is a live network call and may be unavailable; unavailability is "
    "returned as a fact, never as a pass.",
    {
        "type": "object",
        "properties": {"vendor_name": {"type": "string"}},
        "required": ["vendor_name"],
    },
    deterministic=False,
)
def check_vendor_risk_api(vendor_name: str | None) -> VendorRiskFacts:
    if not vendor_name:
        return VendorRiskFacts(available=False, error="no vendor name supplied")

    response = fetch_vendor_risk(vendor_name)
    if not response.ok:
        return VendorRiskFacts(available=False, error=response.error)

    payload = response.payload or {}
    return VendorRiskFacts(
        available=True,
        risk_level=_clean(payload.get("risk_level")),
        security_review_status=_clean(payload.get("security_review_status")),
        last_review_date=_clean(payload.get("last_review_date")),
        processes_personal_data=payload.get("processes_personal_data"),
        stores_data_outside_region=payload.get("stores_data_outside_region"),
        notes=_clean(payload.get("notes")),
    )


@register(
    "scan_untrusted_text",
    "Scan request text and vendor notes for embedded instructions that try to "
    "change the copilot's behaviour. Deterministic pattern match; reports only.",
    {
        "type": "object",
        "properties": {
            "fields": {
                "type": "object",
                "description": "Map of field name to the untrusted text to scan.",
            }
        },
        "required": ["fields"],
    },
    deterministic=True,
)
def scan_untrusted_text(fields: dict[str, str | None]) -> InjectionFacts:
    findings: list[InjectionFinding] = []
    for field_name, value in (fields or {}).items():
        if not value:
            continue
        text = str(value)
        for pattern_name, compiled in _COMPILED:
            for hit in compiled.finditer(text):
                findings.append(
                    InjectionFinding(
                        field=field_name,
                        pattern=pattern_name,
                        excerpt=text[max(0, hit.start() - 20) : hit.end() + 20].strip(),
                    )
                )
    return InjectionFacts(detected=bool(findings), findings=findings)
