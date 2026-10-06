# Procurement Request Copilot

An internal copilot that reviews a software purchase request: it gathers
evidence with tools, applies the written procurement policy deterministically,
and recommends a next action — while every approval stays with a human.

Built for FDE Assessment 3. Two architectures were built and measured on the same
cases; the evidence and the ship decision are below.

---

## Quick start

```bash
git clone https://github.com/Talin12/procurement-request-copilot.git
cd procurement-request-copilot

python3.12 -m venv .venv && source .venv/bin/activate   # Python 3.11 or 3.12
python -m pip install -r requirements.txt

python run_local.py          # mock service on :8001, reviewer console on :8501
```

**No API key is required.** With no `ANTHROPIC_API_KEY` the copilot uses a local
deterministic reasoner and runs end to end. Set a key in `.env` (copy from
`.env.example`) to use the hosted model instead — nothing else changes.

```bash
python verify_setup.py       # starter pre-flight
python -m pytest tests/ -q   # 81 tests
python evals/run_eval.py     # full evaluation, no service needed
```

`make verify`, `make test`, `make eval`, `make run` wrap these.

---

## The product

The console is built for the **procurement reviewer**, not the requester:

1. **Purchase request** — what was asked for. The justification is shown
   verbatim and labelled as untrusted requester text.
2. **Copilot assessment** — recommendation, risk flags, missing information,
   approvals required.
3. **Evidence** — every finding with the tool or policy section it came from.
4. **Human decision** — *Send to reviewers*, *Return to requester*, *Override
   with reason*. The copilot records an outcome; it never applies one.

Output conforms to `ProcurementDecision`: recommendation, evidence, required
approvals, missing information, risk flags, next step, human-review flag,
telemetry.

---

## Workflow

```
Request → resolve requester → agent gathers evidence (4 tools)
        → untrusted-text scan (code)     ← cannot be skipped
        → model interprets free text
        → deterministic policy engine (code)
        → model narrates
        → output filter (code)           ← cannot be skipped
        → ProcurementDecision → human reviewer
```

Full diagrams, failure matrix and assumptions: **[docs/architecture.md](docs/architecture.md)**.

### AI / code / human

| Concern | Owner |
|---|---|
| Reading intent from free text; writing the narrative | Model |
| Approval ladder, budget, review triggers, staleness, conflicts, missing fields, injection scan, output filter | **Code** |
| Approving spend, accepting terms, changing budgets | **Human** |

`human_review_required` is a constant `True`; no code path changes it.

### Tools — 7, of which 6 are deterministic

`get_request_context` · `check_department_budget` · `search_software_catalog` ·
`get_vendor_registry_record` · **`check_vendor_risk_api`** (external HTTP) ·
`scan_untrusted_text` · `evaluate_procurement_policy`

---

## The two architectures

| | A — single agent *(shipped)* | B — staged, two agents |
|---|---|---|
| Model contexts | 1 | 2 (Analyst, Reviewer) |
| Tool selection | agent-driven | agent-driven (stage 1) |
| Policy engine | run by the orchestrator | run by the orchestrator |
| Reviewer sees requester free text | yes | **no** — stage 1 keeps it |

A third configuration, **A as drawn in the brief** (policy engine exposed as a
fifth tool), is evaluated as an ablation but not shipped.

---

## Evaluation

`python evals/run_eval.py` — one command, no service to start first. Three suites,
22 cases, run against all three variants:

- **public (6)** — the cases shipped with the assessment, scored by its own checker
- **behavioural (10)** — one per request, expectations derived by hand from the
  written policy, each naming the clause it tests
- **adversarial (6)** — injection in request text, injection arriving through the
  external service's response, a dead dependency, a threshold boundary, an
  unknown vendor, and a service that contradicts the registry

### Results

| Metric | A as drawn | **A shipped** | B staged |
|---|---:|---:|---:|
| Public (6) | 6/6 | **6/6** | 6/6 |
| Behavioural (10) | 10/10 | **10/10** | 10/10 |
| Adversarial (6) | 6/6 | **6/6** | 6/6 |
| Mean reasoner calls | 5.0 | **4.0** | 4.0 |
| Mean tool calls | 8.0 | **7.0** | 7.0 |

Regenerated into [`evals/results/`](evals/results/) on every run, which also
records latency. Latency is **not** used as a differentiator here: with the
offline reasoner every variant completes in 1–2 ms, so the differences are
run-to-run noise on a loaded machine rather than signal. Reasoner and tool calls
are the provider-independent cost metric and are exactly reproducible.

### What the evaluation found

**The suite caught a real defect.** `BEH-06` failed *identically in both*
architectures: the evidence panel quoted the requester's justification verbatim,
putting the attacker's instructions back into the output where a downstream
reader could act on them. Fixed with a deterministic output filter
(`src/sanitize.py`) that re-applies the detection patterns to the finished
decision. A regression test asserts the filter never redacts legitimate output.

**Accuracy is identical across all three variants** — it comes from the
deterministic engine, not from orchestration.

**The one cost difference was not agent count.** Making the policy engine
model-callable costs a model turn and a redundant evaluation per request and
changes no outcome. Removing it makes A and B indistinguishable on every metric.

## Ship decision

**Ship A — the single agent, with the policy engine run by the orchestrator.**

B's genuine advantage is containment: its reviewer context never reads requester
free text. But the evaluation shows that property was available more cheaply —
the output filter fixed the only containment failure found, in both
architectures, for no model calls. B costs a second prompt to maintain, a handoff
schema to version, and a second place to look when output is wrong, while moving
no number on the board.

Full reasoning: **[docs/architecture_decision.md](docs/architecture_decision.md)** (485 words).

---

## Known limitations

- **Published numbers used the offline reasoner** (no API key at run time).
  Reasoner- and tool-call counts are provider-independent; latency excludes model
  inference. The hosted Anthropic path is implemented against the same interface
  and covered by tests, but is **not** measured under real model variance.
- **Injection detection is pattern-based** and will miss novel phrasings. It is
  the outer layer only — correctness is held by the engine, not the scanner.
- **22 synthetic cases, 13 vendors.** Before production: repeated trials against
  a hosted model to measure variance, and a Security review of the patterns.
- **Privacy triggers on the request's declared data class**, not on a vendor's
  general `processes_personal_data` attribute. Deliberate; see assumptions.
- **No write path.** The copilot cannot create a PO or change a budget.

## Scaffold issues found and fixed

- `.gitignore` excluded `evals/results_*.csv`, so the required evaluation results
  could not be committed. Results now go to `evals/results/`, which is tracked.
- The public harness assumes the vendor-risk service is already running — without
  it `PUB-01` fails on a `vendor_risk_unavailable` flag it forbids. `run_eval.py`
  calls the same app in-process so a run needs no service.
- `src/vendor_client.py` raises on a 503; the vendor tool was rewritten around a
  transport that returns outages as evidence rather than exceptions.
- `handle_request` was unimplemented; `src/solution.py` is the adapter.

## Layout

```
app.py                  reviewer console (Streamlit)
run_local.py            one-command start
src/
  config.py             snapshot date + validity window, parsed from the policy
  policy.py             the deterministic engine
  models.py             evidence pack, interpretation, verdict
  sanitize.py           output filter
  vendor_transport.py   http | asgi
  tools/                7 tools + registry and telemetry
  llm/                  reasoner interface, anthropic + offline backends
  agents/               single.py, staged.py, common.py
evals/
  run_eval.py           one-command evaluation
  cases_behavioural.json, adversarial.py, results/
tests/                  81 tests
docs/                   architecture.md, architecture_decision.md
```

All data is synthetic and ships with the assessment.
