# Architecture comparison

Reasoner: `offline (local deterministic reasoner)` | vendor-risk transport: `asgi` | regenerate with `python evals/run_eval.py`

| Metric | A as drawn: policy engine is a tool | A shipped: policy engine in code | B: staged, two agents |
|---|---:|---:|---:|
| Public cases passed | 6/6 | 6/6 | 6/6 |
| Behavioural cases passed | 10/10 | 10/10 | 10/10 |
| Adversarial cases passed | 6/6 | 6/6 | 6/6 |
| **Total passed** | **22/22** | **22/22** | **22/22** |
| Mean reasoner calls | 5.0 | 4.0 | 4.0 |
| Mean tool calls | 8.0 | 7.0 | 7.0 |
| Mean latency (ms) | 1.5 | 1.37 | 1.69 |
| p95 latency (ms) | 1.97 | 1.64 | 2.01 |
| Mean evidence items | 9.27 | 9.27 | 9.27 |

Latency is wall-clock for the orchestration and tool path only; the offline
reasoner performs no network inference, so compare architectures on reasoner
and tool calls, which are provider-independent.

No case failed in any variant.
