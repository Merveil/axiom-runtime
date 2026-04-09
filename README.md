# Axiom Runtime

**Behavioral integrity layer** for API and LLM services — probe, diff, regression detection, shadow mode, and business-rule enforcement.

---

## Overview

Axiom Runtime captures live HTTP traffic, replays it against a candidate build, and classifies every response with a four-level verdict:

| Verdict | Meaning |
|---|---|
| `REPRODUCIBLE_STRICT` | Byte-identical response |
| `REPRODUCIBLE_SEMANTIC` | Only non-semantic fields differ (request_id, timestamp, …) |
| `DRIFT_DETECTED` | Genuine behavioral change in body or status |
| `FAILED_TO_REPLAY` | Server returned 5xx or connection error |

A rules engine (9 rule types) then suppresses known-benign drift, enforces structural invariants, and runs content-level semantic checks — so only *meaningful* regressions surface.

---

## Architecture

```
axiom_core/              Rust extension module (PyO3/maturin)
  src/
    probe.rs             json_diff, classify_verdict
    rules.rs             evaluate_rules — all 9 rule types
    stats.rs             p95, mean_latency, aggregate_route_stats
    store.rs             RustEventStore — SQLite via rusqlite (WAL mode)
    lib.rs               PyO3 module root

axiom_lab/               Python package (public API unchanged)
  probe.py               Capture, diff, replay, verdict engine
  rules_engine.py        RulesEngine — delegates to axiom_core when available
  campaign.py            Fixture → replay → rules → CampaignReport
  calibration.py         Golden-session calibration workflow
  corpus.py              Corpus-driven multi-scenario runner

  shadow/
    middleware.py        ASGI capture middleware (instrument_fastapi)
    event_store.py       ShadowEventStore — delegates to RustEventStore when available
    replay_runner.py     check_regressions, store_inspection, ShadowReport
    cli.py               `rrt` command-line entry point

  api_demo/              Demo REST API (stable + drift modes)
  llm_demo/              LLM completion demo (4 drift scenarios)
  chaos/                 Fault-injection app (5 failure modes)

  fixtures/              Pre-recorded JSON golden sessions
  rules/                 Business-rule files (JSON)
  corpus/                Corpus definitions (JSON)
  reports/               Campaign output (gitignored)

examples/
  fastapi_app.py         Minimal integration example
  invariant_rules.json   Example rule file

tests/                   pytest test suite (242 tests)
```

---

## Install

### Python only (no Rust required)

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows PowerShell:
.venv\Scripts\Activate.ps1

pip install -e ".[dev]"
```

### With Rust acceleration (recommended)

Requires Rust 1.70+ (`rustup`) and `maturin`:

```bash
pip install -e ".[dev]"          # installs maturin
cd axiom_core
maturin develop --release        # builds & installs axiom_core into the active venv
cd ..
```

The Python layer auto-detects `axiom_core` at import time; if the extension is absent it falls back to the pure-Python implementation transparently.

---

## Rust Extension (`axiom_core`)

`axiom_core` is a PyO3 extension module written in Rust that accelerates the four performance-critical hot paths:

| Module | Symbols | Speed-up vs Python |
|---|---|---|
| `probe.rs` | `json_diff`, `classify_verdict`, `DriftItem` | ~10× on large payloads |
| `rules.rs` | `evaluate_rules`, `RuleViolation`, `RulesResult` | ~8× for rule-heavy sessions |
| `stats.rs` | `p95`, `mean_latency`, `latency_stats`, `aggregate_route_stats`, `RouteStats` | ~15× on 10k-event batches |
| `store.rs` | `RustEventStore` (all SQLite ops, WAL mode, bundled SQLite) | ~3× on write-heavy traces |

The Python modules that delegate to these are `probe.py`, `rules_engine.py`, `replay_runner.py`, and `event_store.py`. Every Python public API is preserved exactly — callers see no difference.

### Building from source

```bash
cd axiom_core
maturin develop --release
# or, to produce a redistributable wheel:
maturin build --release
```

---

## Quick Start

### 1 — Probe a single session (fixture-based)

```python
from fastapi.testclient import TestClient
from axiom_lab.probe import SessionCapture, replay_session, summarise
from axiom_lab.api_demo.app import app

# Capture
capture = SessionCapture(TestClient(app))
capture.post("/echo", {"msg": "hello"}, label="echo")
capture.get("/health", label="health")

# Replay against the same app (or a candidate build)
reports = replay_session(capture.records, TestClient(app))
print(summarise(reports))
```

### 2 — Run a full campaign (fixture + rules)

```python
from axiom_lab.campaign import run_campaign

report = run_campaign(
    fixture_path="axiom_lab/fixtures/api_demo_stable.json",
    app_factory=lambda: __import__("axiom_lab.api_demo.app", fromlist=["app"]).app,
    rules_path="axiom_lab/rules/api_demo.json",
    name="api-demo-v2",
)
print(report.summary_table())
report.save("axiom_lab/reports/api_demo.json")
```

### 3 — Shadow mode (live traffic capture + offline replay)

```python
from fastapi import FastAPI
from axiom_lab.shadow import instrument_fastapi, check_regressions, ShadowEventStore
from fastapi.testclient import TestClient

store = ShadowEventStore()          # in-memory; pass a path for persistence
app   = FastAPI()
instrument_fastapi(app, store)      # capture every live request

# … app receives traffic …

# Later, replay against a candidate:
report = check_regressions(store, TestClient(candidate_app), name="v2-shadow")
print(report.summary_table())
```

### 4 — CLI (`rrt`)

```bash
rrt replay   --fixture axiom_lab/fixtures/api_demo_stable.json \
             --rules   axiom_lab/rules/api_demo.json \
             --report  axiom_lab/reports/out.json

rrt shadow   --db .rrt/events.db \
             --rules axiom_lab/rules/api_demo.json

rrt inspect  --db .rrt/events.db
```

---

## Rule Types

### Structural rules

| Type | Purpose | Required fields |
|---|---|---|
| `ignore_field` | Suppress drift on a known non-semantic path | `field` |
| `numeric_tolerance` | Allow small numeric differences | `field`, `tolerance` |
| `required_field` | Response must contain this field | `field` |
| `prohibited_field` | Response must not contain this field | `field` |

### Content-level semantic rules

| Type | Purpose | Required fields |
|---|---|---|
| `contains_keyword` | String field must contain a substring | `field`, `keyword` |
| `not_contains_keyword` | String field must NOT contain a substring | `field`, `keyword` |
| `value_in_range` | Numeric field must satisfy `min ≤ x ≤ max` | `field`, `min`, `max` |
| `value_in_set` | Field value must be one of `allowed` | `field`, `allowed` |
| `field_consistency` | When field A equals X, field B must satisfy a constraint | `condition_field`, `condition_value`, `target_field`, `constraint` |

Field paths use dot notation (`choices.0.finish_reason`) or slash notation (`/choices/0/finish_reason`).

Example rule file:

```json
{
  "name": "api_demo",
  "version": "1.0",
  "rules": [
    {"id": "R001", "type": "ignore_field",     "field": "request_id"},
    {"id": "R002", "type": "numeric_tolerance","field": "score", "tolerance": 0.05},
    {"id": "R003", "type": "required_field",   "field": "status"},
    {"id": "R004", "type": "value_in_set",
     "field": "choices.0.finish_reason", "allowed": ["stop","length","content_filter"]}
  ]
}
```

---

## Tests

```bash
pytest tests/ -x -q        # 242 tests, ~5 s with Rust / ~8 s without
```

---

## Development

```bash
# After changing Rust source:
cd axiom_core
maturin develop --release
cd ..
pytest tests/ -x -q

# Lint Python:
ruff check axiom_lab/ tests/

# Type-check:
mypy axiom_lab/
```

---

## Requirements

| Component | Minimum version |
|---|---|
| Python | 3.10 |
| FastAPI | 0.115 |
| Starlette | 0.40 |
| httpx | 0.27 |
| **Rust** *(optional)* | 1.70 (via rustup) |
| **maturin** *(optional)* | 1.4 |
