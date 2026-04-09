# Axiom Lab — Evaluation Discipline

A structured behavioral-integrity test laboratory for Axiom.
It answers three questions about any API or model:

| Question | Tool |
|---|---|
| Où Axiom est **fiable** ? | `probe.py` — STRICT / SEMANTIC verdicts |
| Où les **faux positifs** apparaissent ? | `rules_engine.py` — suppress known-benign drift |
| Où il est encore **naïf** ? | content-level rules — catch schema-valid but semantically broken responses |

---

## Architecture

```
axiom_lab/
  probe.py            Domain-agnostic probe — capture, diff, replay
  rules_engine.py     JSON rule evaluation — 9 rule types
  campaign.py         Fixture → replay → rules → CampaignReport

  api_demo/           Demo API (4 endpoints, stable + drift modes)
  llm_demo/           LLM completion demo (4 drift scenarios)
  chaos/              Fault injection (5 failure modes)

  fixtures/           Pre-recorded golden sessions
  rules/              Business-rule files (JSON)
  reports/            Campaign output (gitignored)
```

---

## Verdicts

| Verdict | Meaning |
|---|---|
| `REPRODUCIBLE_STRICT` | Byte-identical response — nothing changed |
| `REPRODUCIBLE_SEMANTIC` | Only non-semantic fields differ (request_id, id, timestamp, …) |
| `DRIFT_DETECTED` | Genuine behavioral change in the body |
| `FAILED_TO_REPLAY` | Server returned 5xx or connection error |

---

## Rule Types

### Structural rules

| Type | Purpose | Key fields |
|---|---|---|
| `ignore_field` | Suppress drift on a known non-semantic path | `field` |
| `numeric_tolerance` | Allow small numeric variations | `field`, `tolerance` |
| `required_field` | Invariant: field must be present | `field` |
| `prohibited_field` | Invariant: field must be absent | `field` |

### Content-level semantic rules (V1.10)

| Type | Purpose | Key fields |
|---|---|---|
| `contains_keyword` | String field must contain a substring | `field`, `keyword`, `case_sensitive` |
| `not_contains_keyword` | String field must NOT contain a substring | `field`, `keyword` |
| `value_in_range` | Numeric field within `[min, max]` | `field`, `min`, `max` |
| `value_in_set` | Field value must be one of `allowed` | `field`, `allowed` |
| `field_consistency` | When field A = X, field B must satisfy a constraint | `condition_field`, `condition_value`, `target_field`, `constraint` |

#### Example — catch INCOHERENT LLM output

```json
{ "id": "L005", "type": "contains_keyword",
  "field": "choices.0.text", "keyword": " ",
  "description": "completion text must not be empty" },

{ "id": "L006", "type": "value_in_range",
  "field": "usage.total_tokens", "min": 1, "max": 100000,
  "description": "total_tokens must be at least 1" },

{ "id": "L007", "type": "value_in_set",
  "field": "choices.0.finish_reason", "allowed": ["stop","length","content_filter"],
  "description": "finish_reason must be a recognised stop signal" }
```

Rules L005–L007 together ensure that a response which has all the correct field
names but carries empty text, zero token counts, and an empty `finish_reason`
is flagged as a **content violation** — not silently passed as SEMANTIC.

---

## Demo Apps

### `api_demo` (stable + drift modes)

| Endpoint | Stable verdict | Drift verdict | Why |
|---|---|---|---|
| `GET /health` | STRICT | STRICT | Deterministic static body |
| `POST /echo` | SEMANTIC | SEMANTIC | `request_id` ignored by `_NON_SEMANTIC_FIELDS` |
| `POST /drift` | SEMANTIC | DRIFT_DETECTED | `score` and `tag` change with RNG seed |
| `POST /boom` | FAILED_TO_REPLAY | FAILED_TO_REPLAY | Always raises 500 |

### `llm_demo` — four drift scenarios

| Scenario | Roll range | What changes | Caught by |
|---|---|---|---|
| VARIABLE_CONTENT | < 0.35 | Different wording in `choices[0].text` | Drift probe |
| MISSING_FIELD | 0.35–0.55 | `choices` absent entirely | L002 required_field |
| SCHEMA_CHANGE | 0.55–0.75 | `text` renamed to `content` | Drift probe + L002 |
| INCOHERENT | ≥ 0.75 | Empty text, zero tokens, empty finish_reason | **L005, L006, L007** |

### `chaos` — fault injection

| Endpoint | Chaos effect | Axiom verdict |
|---|---|---|
| `GET /slow` | `time.sleep(slow_ms)` → still 200 | SEMANTIC (latency spike visible in `details`) |
| `POST /flaky` | 500 at `error_rate` probability | FAILED_TO_REPLAY |
| `GET /empty` | Empty body `b""` | DRIFT_DETECTED |
| `GET /malformed` | Plain-text `"I am not JSON"` | DRIFT_DETECTED |
| `GET /down` | 503 Service Unavailable | FAILED_TO_REPLAY |

---

## Campaign Report — V1.11 Enriched Output

`run_campaign()` now produces three breakdown tables in addition to the top-level counters:

```json
{
  "name": "llm-regression",
  "total": 2,
  "strict": 0,
  "semantic": 2,
  "drift": 0,
  "failed": 0,
  "rule_violations": 0,
  "regression_rate_pct": 0.0,
  "by_verdict": { "REPRODUCIBLE_SEMANTIC": 2 },
  "by_route": {
    "/v1/completions": {
      "total": 2, "strict": 0, "semantic": 2,
      "drift": 0, "failed": 0, "violations": 0
    }
  },
  "by_rule_class": {}
}
```

`by_rule_class` groups violations by the first character of the rule ID
(e.g. `"L"` for LLM rules, `"R"` for API rules), making it easy to
identify which rule family is generating the most noise.

---

## Quick Start

```python
from fastapi.testclient import TestClient
from axiom_lab.campaign import CampaignConfig, run_campaign
from axiom_lab.llm_demo.app import create_llm_demo_app
from pathlib import Path

# Stable campaign — expect 0% regression
config = CampaignConfig(
    name="llm-stable",
    fixture_path=Path("axiom_lab/fixtures/llm_demo_stable.json"),
    rules_path=Path("axiom_lab/rules/llm_demo.json"),
)
client = TestClient(create_llm_demo_app(drift_mode=False))
report = run_campaign(config, client)
print(report.regression_rate_pct)   # 0.0
print(report.rule_violations)       # 0

# Drifted campaign — INCOHERENT mode detected by content rules
import random
client_drifted = TestClient(
    create_llm_demo_app(drift_mode=True, rng=random.Random(99))
)
report_drifted = run_campaign(config, client_drifted)
print(report_drifted.rule_violations)   # > 0 when INCOHERENT mode hit
```

---

## Test Coverage

```
tests/test_axiom_lab.py  — 75 tests, 7 classes
  TestProbeCore              json_diff, evaluate, serialisation, summary
  TestApiDemoEndpoints       raw HTTP correctness in both modes
  TestApiDemoAxiomReplay     full verdict spectrum
  TestLlmDemo                LLM endpoints + Axiom replay + drift modes
  TestChaosScenarios         all fault modes detected
  TestRulesEngine            all 9 rule types, multi-rule, from_file
  TestCampaignRunner         fixture→report end-to-end + breakdowns
```

Run with: `pytest tests/test_axiom_lab.py -v`

---

## What the lab proves

1. **Structural integrity**: Axiom correctly classifies every response as
   STRICT / SEMANTIC / DRIFT / FAILED across all demo apps.

2. **False-positive suppression**: `_NON_SEMANTIC_FIELDS` and `ignore_field`
   rules prevent per-call noise (UUIDs, timestamps) from triggering regressions.

3. **Content-level safety net**: The four LLM drift modes are all caught —
   including INCOHERENT, which passes schema validation but violates content rules.

4. **Campaign traceability**: Every report is JSON-serialisable, route-addressable,
   and includes per-rule-class violation counts for triage.
