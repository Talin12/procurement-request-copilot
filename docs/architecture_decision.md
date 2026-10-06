# Architecture decision memo

## Decision

**Ship the single agent (A), with the policy engine called by the orchestrator
rather than exposed as a tool.** The second agent does not earn its place.

## Evidence

Three variants, same 22 cases, same tools, same engine. Column one is the
baseline as drawn; column two is that agent with one change.

| Metric | A as drawn | **A shipped** | B staged (2 agents) |
|---|---:|---:|---:|
| Public cases (6) | 6/6 | **6/6** | 6/6 |
| Behavioural cases (10) | 10/10 | **10/10** | 10/10 |
| Adversarial cases (6) | 6/6 | **6/6** | 6/6 |
| Mean reasoner calls | 5.0 | **4.0** | 4.0 |
| Mean tool calls | 8.0 | **7.0** | 7.0 |

Latency is excluded: every variant finishes in 1–2 ms offline, so differences
are noise. Call counts are exact.

Two findings decide it.

**Accuracy is identical everywhere.** It comes from the deterministic policy
engine, not from orchestration. Agent count moved no correctness metric at all.

**The only cost difference came from one choice, and it was not agent count.**
Exposing the policy engine as a model-callable tool costs one model turn and one
redundant evaluation per request and changes no outcome — the orchestrator must
recompute the verdict anyway, since an agent that forgot to call it would emit an
unchecked decision. Remove that, and A and B are indistinguishable. Without the
ablation column, the gap would have looked like the price of the second agent.

## Trade-offs

B's one real advantage is containment: its reviewer context never sees requester
free text, so an injection surviving stage 1 has no path into the narrative. That
is genuine, but the evaluation shows it is already delivered more cheaply. `BEH-06`
failed *identically in both* architectures — both echoed the attacker's text into
the evidence panel — and both were fixed by one deterministic output filter that
costs no model calls. A structural property bought with a second prompt was
available for twenty lines of code.

What B costs: two prompts to keep consistent, a handoff schema to version, and
two places to look when output is wrong.

## Risks and limitations

- Published numbers used the **offline deterministic reasoner** (no API key at
  run time). Call counts are provider-independent; latency excludes model
  inference. The hosted path is implemented against the same interface and
  tested, but **not** measured under real model variance.
- Injection detection is pattern-based. It will miss novel phrasings. It is the
  outer layer only — the engine, not the scanner, is what keeps flags correct.
- 22 synthetic cases, 13 vendors. Before production: re-run both against a
  hosted model over repeated trials to measure variance, and have Security
  review the detection patterns.

## Why this is the right MVP

The client's risk is a wrong recommendation on a sensitive purchase. That risk is
retired by moving every gating decision into code and keeping approval with
humans — which both architectures already do. Adding a second agent adds a
prompt, a handoff and a failure mode while moving no number on the board.
