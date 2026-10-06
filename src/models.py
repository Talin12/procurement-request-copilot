"""Internal data structures passed between tools, the policy engine and agents.

`src/contracts.py` is the assessment's external contract and is left untouched.
These models are the internal wire format: the evidence pack an analyst produces,
the interpretation a model produces, and the verdict the deterministic engine
produces. Keeping them explicit is what makes the staged architecture's handoff
inspectable rather than a blob of prose.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SecurityStatus = Literal["approved", "expired", "not_completed", "unknown"]


class RequestFacts(BaseModel):
    request_id: str
    requester_id: str
    requester_name: str | None = None
    department: str | None = None
    manager_id: str | None = None
    country: str | None = None
    product_name: str | None = None
    vendor_name: str | None = None
    category: str | None = None
    annual_cost_usd: float | None = None
    user_count: int | None = None
    business_justification: str | None = None
    data_access_level: str | None = None
    requested_integrations: list[str] | None = None
    urgency: str | None = None


class BudgetFacts(BaseModel):
    department: str | None = None
    found: bool = False
    annual_software_budget_usd: float | None = None
    committed_usd: float | None = None
    available_usd: float | None = None


class CatalogMatch(BaseModel):
    software_id: str
    product_name: str
    category: str
    vendor_name: str
    status: str
    annual_cost_usd: float
    licensed_seats: int
    scope: str
    notes: str
    match_reasons: list[str] = Field(default_factory=list)


class CatalogFacts(BaseModel):
    """Catalog lookup split by *why* each row matched.

    `overlapping` is the set that counts as duplicate capability under policy
    section 3: anything in the same category, plus same-brand products inside
    that category. A same-vendor row in a *different* category (a training
    package from a vendor whose licences we already hold) is reported in
    `vendor_only` as context - it is a relationship, not duplicate capability.
    """

    product_matches: list[CatalogMatch] = Field(default_factory=list)
    category_matches: list[CatalogMatch] = Field(default_factory=list)
    vendor_matches: list[CatalogMatch] = Field(default_factory=list)
    overlapping: list[CatalogMatch] = Field(default_factory=list)
    vendor_only: list[CatalogMatch] = Field(default_factory=list)


class VendorRegistryFacts(BaseModel):
    found: bool = False
    vendor_id: str | None = None
    procurement_status: str | None = None
    security_status: str | None = None
    security_review_date: str | None = None
    legal_terms_status: str | None = None
    notes: str | None = None


class VendorRiskFacts(BaseModel):
    available: bool = False
    error: str | None = None
    risk_level: str | None = None
    security_review_status: str | None = None
    last_review_date: str | None = None
    processes_personal_data: bool | None = None
    stores_data_outside_region: bool | None = None
    notes: str | None = None


class InjectionFinding(BaseModel):
    field: str
    pattern: str
    excerpt: str


class InjectionFacts(BaseModel):
    detected: bool = False
    findings: list[InjectionFinding] = Field(default_factory=list)


class EvidencePack(BaseModel):
    """Everything gathered before any judgement is applied.

    This is the handoff format between stage 1 and stage 2 of architecture B,
    and the accumulated tool state in architecture A.
    """

    request: RequestFacts
    budget: BudgetFacts = Field(default_factory=BudgetFacts)
    catalog: CatalogFacts = Field(default_factory=CatalogFacts)
    vendor_registry: VendorRegistryFacts = Field(default_factory=VendorRegistryFacts)
    vendor_risk: VendorRiskFacts = Field(default_factory=VendorRiskFacts)
    injection: InjectionFacts = Field(default_factory=InjectionFacts)
    tools_run: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RequestInterpretation(BaseModel):
    """The model's contribution: reading intent out of free text.

    The model never sets approvals, thresholds or flags. It reports what the
    request appears to mean so the deterministic engine has better inputs.
    """

    inferred_data_classes: list[str] = Field(default_factory=list)
    overlap_assessment: Literal["credible_gap", "duplicate", "unclear", "not_applicable"] = (
        "unclear"
    )
    overlap_rationale: str = ""
    clarifying_questions: list[str] = Field(default_factory=list)
    untrusted_instruction_summary: str | None = None
    summary: str = ""


class PolicyVerdict(BaseModel):
    """Output of the deterministic engine. Code owns every field here."""

    recommendation: str
    next_step: str
    required_approvals: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    human_review_required: bool = True
    approval_tier: str | None = None
