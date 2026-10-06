"""Unit tests for the deterministic policy engine."""
from __future__ import annotations

import re
from datetime import date

import pytest

from src import config, policy
from src.models import (
    BudgetFacts,
    CatalogFacts,
    EvidencePack,
    InjectionFacts,
    RequestFacts,
    VendorRegistryFacts,
    VendorRiskFacts,
)


def make_pack(**overrides) -> EvidencePack:
    request = RequestFacts(
        request_id="T-1",
        requester_id="E004",
        department="Finance",
        product_name="Widget",
        vendor_name="Acme",
        category="Widgets",
        annual_cost_usd=500,
        user_count=5,
        business_justification="A clear business purpose.",
        data_access_level="none",
        requested_integrations=[],
        **overrides.pop("request", {}),
    )
    pack = EvidencePack(
        request=request,
        budget=BudgetFacts(department="Finance", found=True, available_usd=29000),
        catalog=CatalogFacts(),
        vendor_registry=VendorRegistryFacts(
            found=True,
            procurement_status="Approved",
            security_status="Approved",
            security_review_date="2026-06-01",
            legal_terms_status="Approved",
        ),
        vendor_risk=VendorRiskFacts(
            available=True,
            risk_level="low",
            security_review_status="approved",
            last_review_date="2026-06-01",
            processes_personal_data=False,
            stores_data_outside_region=False,
        ),
        injection=InjectionFacts(),
    )
    for key, value in overrides.items():
        setattr(pack, key, value)
    return pack


class TestApprovalLadder:
    @pytest.mark.parametrize(
        "amount,expected",
        [
            (0, ["Manager"]),
            (1000, ["Manager"]),
            (1000.01, ["Department Head", "Procurement"]),
            (10000, ["Department Head", "Procurement"]),
            (10000.01, ["Department Head", "Finance", "Procurement"]),
            (25000, ["Department Head", "Finance", "Procurement"]),
            (25000.01, ["Department Head", "Finance", "CFO", "Procurement"]),
            (1_000_000, ["Department Head", "Finance", "CFO", "Procurement"]),
        ],
    )
    def test_boundaries_are_exact(self, amount, expected):
        assert policy.approval_tier(amount)[1] == expected

    def test_no_amount_invents_no_approvers(self):
        tier, approvers = policy.approval_tier(None)
        assert tier is None and approvers == []

    def test_ladder_matches_the_written_policy_table(self):
        """The code ladder and the policy markdown must not drift apart."""
        # Every ladder row names a dollar amount; the header and rule row do not.
        rows = re.findall(
            r"^\|\s*([^|]*\$[^|]*?)\s*\|\s*([^|]+?)\s*\|\s*$", config.policy_text(), re.MULTILINE
        )
        documented = [[name.strip() for name in approvers.split("+")] for _amount, approvers in rows]
        in_code = [approvers for _upper, _label, approvers in policy.APPROVAL_LADDER]
        assert documented == in_code


class TestDataClassification:
    @pytest.mark.parametrize(
        "level,integrations,expected",
        [
            ("source_code", ["Git repositories"], {"source_code"}),
            ("customer_pii", ["CRM"], {"customer_pii"}),
            ("employee_pii", [], {"employee_pii"}),
            ("confidential_documents", [], {"confidential_documents"}),
            ("production_telemetry", ["Production cloud account"], {"production_access"}),
            ("none", [], set()),
            ("internal_marketing", ["SSO"], set()),
        ],
    )
    def test_classification(self, level, integrations, expected):
        assert policy.classify_data_access(level, integrations)[0] == expected

    @pytest.mark.parametrize("level", ["unknown", "", None, "TBD", "n/a"])
    def test_unusable_levels_are_reported_not_guessed(self, level):
        classes, usable = policy.classify_data_access(level, [])
        assert classes == set() and usable is False


class TestReviewTriggers:
    def test_clean_request_needs_no_special_review(self):
        verdict = policy.evaluate(make_pack())
        assert "security_review_required" not in verdict.risk_flags
        assert verdict.required_approvals == ["Manager"]

    def test_sensitive_data_triggers_security(self):
        pack = make_pack()
        pack.request.data_access_level = "source_code"
        verdict = policy.evaluate(pack)
        assert "security_review_required" in verdict.risk_flags
        assert "Security" in verdict.required_approvals

    def test_pii_triggers_privacy(self):
        pack = make_pack()
        pack.request.data_access_level = "customer_pii"
        verdict = policy.evaluate(pack)
        assert "privacy_review_required" in verdict.risk_flags
        assert "Privacy" in verdict.required_approvals

    def test_new_vendor_above_ten_thousand_triggers_legal(self):
        pack = make_pack()
        pack.request.annual_cost_usd = 10000.01
        pack.vendor_registry.procurement_status = "New"
        verdict = policy.evaluate(pack)
        assert "legal_review_required" in verdict.risk_flags

    def test_new_vendor_below_ten_thousand_does_not_trigger_legal_on_spend(self):
        pack = make_pack()
        pack.request.annual_cost_usd = 9999
        pack.vendor_registry.procurement_status = "New"
        pack.vendor_registry.legal_terms_status = "Approved"
        verdict = policy.evaluate(pack)
        assert "legal_review_required" not in verdict.risk_flags

    def test_budget_shortfall_routes_to_finance(self):
        pack = make_pack()
        pack.request.annual_cost_usd = 50000
        verdict = policy.evaluate(pack)
        assert "budget_insufficient" in verdict.risk_flags
        assert "Finance" in verdict.required_approvals


class TestStalenessUsesPolicySnapshot:
    def test_snapshot_date_comes_from_the_policy_file(self):
        assert config.reference_date() == date(2026, 9, 30)

    def test_assessment_just_inside_the_window_is_current(self):
        pack = make_pack()
        reviewed = date(2026, 9, 30).toordinal() - config.security_validity_days()
        pack.vendor_risk.last_review_date = date.fromordinal(reviewed).isoformat()
        verdict = policy.evaluate(pack)
        assert "vendor_review_expired" not in verdict.risk_flags

    def test_assessment_one_day_past_the_window_is_expired(self):
        pack = make_pack()
        reviewed = date(2026, 9, 30).toordinal() - config.security_validity_days() - 1
        pack.vendor_risk.last_review_date = date.fromordinal(reviewed).isoformat()
        verdict = policy.evaluate(pack)
        assert "vendor_review_expired" in verdict.risk_flags
        assert "security_review_required" in verdict.risk_flags


class TestEvidenceConflicts:
    def test_disagreement_is_surfaced_not_resolved(self):
        pack = make_pack()
        pack.vendor_registry.security_status = "Approved"
        pack.vendor_risk.security_review_status = "expired"
        verdict = policy.evaluate(pack)
        assert "conflicting_vendor_evidence" in verdict.risk_flags
        assert "Security" in verdict.required_approvals

    def test_unavailable_service_is_never_read_as_a_pass(self):
        pack = make_pack()
        pack.vendor_risk = VendorRiskFacts(available=False, error="HTTP 503")
        verdict = policy.evaluate(pack)
        assert "vendor_risk_unavailable" in verdict.risk_flags
        assert "security_review_required" in verdict.risk_flags

    def test_vendor_with_no_records_is_unverified_not_clean(self):
        pack = make_pack()
        pack.vendor_registry = VendorRegistryFacts(found=False)
        pack.vendor_risk = VendorRiskFacts(available=False, error="HTTP 404")
        verdict = policy.evaluate(pack)
        assert "vendor_not_in_registry" in verdict.risk_flags
        assert "security_review_required" in verdict.risk_flags


class TestMissingInformation:
    def test_absent_required_fields_are_listed(self):
        pack = make_pack()
        pack.request.annual_cost_usd = None
        pack.request.user_count = None
        pack.request.data_access_level = "unknown"
        verdict = policy.evaluate(pack)
        joined = " ".join(verdict.missing_information).lower()
        assert "cost" in joined and "user" in joined and "data-access" in joined
        assert "missing_information" in verdict.risk_flags

    def test_empty_integration_list_is_an_answer_not_a_gap(self):
        verdict = policy.evaluate(make_pack())
        assert verdict.missing_information == []

    def test_null_integrations_is_a_gap(self):
        pack = make_pack()
        pack.request.requested_integrations = None
        verdict = policy.evaluate(pack)
        assert any("integration" in item.lower() for item in verdict.missing_information)


def test_human_review_is_always_required():
    assert policy.evaluate(make_pack()).human_review_required is True
