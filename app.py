"""Procurement Request Copilot - reviewer console.

The product is for the procurement reviewer, not the requester. The screen is
laid out the way the decision is actually made: what was asked for, what the
copilot found and where each fact came from, what the policy requires, and then
the action the human takes. Nothing on this page approves anything.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

from src.contracts import ProcurementDecision
from src.llm import describe_provider, get_reasoner
from src.solution import handle_request
from src.tools import all_specs

ROOT = Path(__file__).resolve().parent
REQUESTS = json.loads((ROOT / "data" / "requests.json").read_text(encoding="utf-8"))
BY_ID = {r["request_id"]: r for r in REQUESTS}

FLAG_LABELS = {
    "existing_tool_overlap": ("Overlaps an approved tool", "warning"),
    "duplicate_of_existing_tool": ("Duplicate of existing capability", "warning"),
    "budget_insufficient": ("Exceeds available budget", "error"),
    "budget_unverified": ("Budget could not be verified", "error"),
    "security_review_required": ("Security review required", "error"),
    "privacy_review_required": ("Privacy review required", "error"),
    "legal_review_required": ("Legal review required", "error"),
    "vendor_review_expired": ("Vendor assessment expired", "error"),
    "vendor_assessment_missing": ("No vendor assessment on file", "error"),
    "vendor_not_in_registry": ("Vendor not in registry", "error"),
    "conflicting_vendor_evidence": ("Vendor records disagree", "error"),
    "vendor_risk_unavailable": ("Vendor-risk service unavailable", "error"),
    "prompt_injection_detected": ("Untrusted instructions in request data", "error"),
    "missing_information": ("Required information missing", "warning"),
}

st.set_page_config(page_title="Procurement Request Copilot", layout="wide", page_icon="‣")

st.title("Procurement Request Copilot")
st.caption(
    "Gathers evidence, applies the written procurement policy, and recommends a next action. "
    "It does not approve spend, accept vendor terms, or change budgets."
)

with st.sidebar:
    st.subheader("Request")
    request_id = st.selectbox(
        "Select a request",
        list(BY_ID),
        format_func=lambda rid: f"{rid} · {BY_ID[rid]['product_name']}",
        label_visibility="collapsed",
    )
    architecture = st.radio(
        "Architecture",
        ["single", "staged"],
        format_func=lambda a: "A · single agent" if a == "single" else "B · staged (2 agents)",
        help="Both produce the same policy verdict. See evals/results/comparison.md.",
    )
    st.divider()
    st.caption(f"**Reasoner**  \n{describe_provider(get_reasoner())}")
    st.caption(f"**Vendor-risk transport**  \n`{os.getenv('VENDOR_RISK_TRANSPORT', 'http')}`")
    with st.expander(f"Tools ({len(all_specs())})"):
        for spec in all_specs():
            st.caption(f"{'◆' if spec.deterministic else '◇'} `{spec.name}`")
        st.caption("◆ deterministic · ◇ external call")

request = BY_ID[request_id]
run = st.button("Analyse request", type="primary", use_container_width=True)

left, right = st.columns([0.9, 1.1], gap="large")

with left:
    st.subheader("Purchase request")
    cost = request.get("annual_cost_usd")
    seats = request.get("user_count")
    a, b = st.columns(2)
    a.metric("Annual cost", f"${cost:,.0f}" if cost is not None else "Not stated")
    b.metric("Users", seats if seats is not None else "Not stated")
    st.write(f"**Product** {request['product_name']}  ·  **Vendor** {request['vendor_name']}")
    st.write(f"**Category** {request['category']}  ·  **Data access** `{request['data_access_level']}`")
    st.write(f"**Integrations** {', '.join(request['requested_integrations']) or 'none'}")
    st.markdown("**Stated business justification**")
    # Shown verbatim and labelled: the reviewer must be able to see exactly what
    # the requester wrote, including anything hostile in it.
    st.info(request["business_justification"], icon="❝")
    st.caption("Requester-authored text. Treated as untrusted data, never as instructions.")

with right:
    st.subheader("Copilot assessment")
    if not run:
        st.info("Select a request and choose **Analyse request**.")
    else:
        with st.spinner("Gathering evidence and applying policy…"):
            try:
                decision: ProcurementDecision = handle_request(request_id, architecture=architecture)
            except Exception as exc:
                st.error(f"The run failed: {type(exc).__name__}: {exc}")
                st.stop()

        st.success(decision.recommendation, icon="✓")

        if decision.risk_flags:
            st.markdown("**Risk flags**")
            for flag in decision.risk_flags:
                label, level = FLAG_LABELS.get(flag, (flag.replace("_", " ").capitalize(), "warning"))
                (st.error if level == "error" else st.warning)(label, icon="!")

        if decision.missing_information:
            st.markdown("**Missing information**")
            for item in decision.missing_information:
                st.write(f"- {item}")

        st.markdown("**Approvals required**")
        if decision.required_approvals:
            st.write("  ".join(f"`{name}`" for name in decision.required_approvals))
        else:
            st.write("_Not determinable until the request is complete._")

        st.markdown("**Evidence**")
        st.caption("Every line below came from a named tool or policy section.")
        for item in decision.evidence:
            with st.container(border=True):
                st.write(item.finding)
                st.caption(f"{item.source}" + (f" · {item.reference}" if item.reference else ""))

        st.divider()
        st.markdown("**Next step**")
        st.write(decision.next_step)

        st.markdown("**Human decision**")
        st.caption("The copilot records an outcome; it never applies one.")
        c1, c2, c3 = st.columns(3)
        if c1.button("Send to reviewers", use_container_width=True):
            st.toast(f"Routed to {', '.join(decision.required_approvals) or 'procurement'}.")
        if c2.button("Return to requester", use_container_width=True):
            st.toast("Returned for clarification.")
        if c3.button("Override with reason", use_container_width=True):
            st.toast("Override requires a written reason and a named approver.")

        tel = decision.telemetry
        if tel:
            st.caption(
                f"Reasoner calls {tel.llm_calls} · tool calls {tel.tool_calls} · "
                f"tools used: {', '.join(dict.fromkeys(tel.tool_names))}"
            )
        with st.expander("Raw decision (ProcurementDecision)"):
            st.json(decision.model_dump())

st.divider()
st.caption(
    "Recommendations are advisory. Final approval, vendor terms and budget changes remain with "
    "the named human approvers."
)
