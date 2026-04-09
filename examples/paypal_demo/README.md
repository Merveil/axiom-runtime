# PayPal Payments API — Axiom Demo

> **One sentence:** Axiom caught five critical payment regressions by replaying production traffic against a candidate release — before it ever reached production.

---

## What it does

Simulates a real PayPal deployment gate in four phases:

1. **Capture** a golden baseline from the stable v1 API (5 representative payment flows)
2. **Replay** all sessions against v2 (the release candidate) using Axiom Runtime
3. **Evaluate** every response against 12 business-integrity rules (all 9 Axiom rule types)
4. **Report** per-endpoint regressions with root-cause attribution and business impact

Five regressions are injected into v2, covering every layer of the payment stack:

| Bug | What breaks |
|---|---|
| BUG-01 | `fee_amount` / `net_amount` absent — settlement reconciliation fails |
| BUG-02 | `fraud_score` 0.12 → 0.87 — 100% of transactions sent to manual review |
| BUG-03 | `status` stuck at `PROCESSING` — payments never settle |
| BUG-04 | `currency` lowercase `"usd"` — SWIFT routing rejects all transactions |
| BUG-05 | `amount` float rounding drift — PCI-DSS reconciliation breaks |

---

## How to run

```bash
python examples/paypal_simulation.py
```

No server setup. No external dependencies beyond the dev install.

```bash
# First time only
pip install -e ".[dev]"
```

---

## Expected outcome

```
╔══════════════════════════════════════════════════════════════════════╗
║       AXIOM RUNTIME  ×  PayPal  —  Deployment Validation            ║
╚══════════════════════════════════════════════════════════════════════╝

Phase 1 — Baseline Capture  (v1 Production)
  5 exchanges captured from stable v1.

Phase 2 — Candidate Replay  (v2 Build — under test)
  Done — 5 calls in ~40 ms  (Rust extension: active)

Phase 3 — Regression Report  (per endpoint)
  🔴 DRIFT DETECTED   POST  /v1/payments/create
  🔴 DRIFT DETECTED   POST  /v1/payments/{id}/capture
  🔴 DRIFT DETECTED   GET   /v1/transactions/{id}
  🔴 DRIFT DETECTED   GET   /v1/accounts/{id}/balance
  🔴 DRIFT DETECTED   POST  /v1/fraud/score

Phase 4 — Executive Summary
  Regression rate : 100 %
  Rule violations : 14
  Verdict         : 🚫 DEPLOYMENT BLOCKED
```

Full pre-rendered output: [`examples/paypal_report.md`](../paypal_report.md)

---

## Why it matters

Traditional integration tests would have to _know_ what to look for. Axiom does not.

It captures what v1 _actually_ returns, replays the same traffic through v2, and flags every divergence that violates a business rule — without touching the candidate build.

The rules file (`examples/paypal_rules.json`) is the only configuration needed. It encodes the contract in 12 declarative rules, covering required fields, FSM state constraints, fraud score bounds, currency normalisation, float precision, and PCI settlement integrity.

**The 14 violations found in < 50 ms would have caused:**
- Settlement reconciliation failures for every merchant
- 100% false-positive fraud flags — complete checkout blockage
- SWIFT routing rejections across all currency pairs
- PCI-DSS audit findings → potential licence suspension

---

## Files

| File | Purpose |
|---|---|
| `examples/paypal_simulation.py` | 4-phase simulation runner |
| `examples/paypal_rules.json` | 12 canonical business-integrity rules |
| `examples/paypal_report.md` | Pre-rendered executive report |
| `examples/paypal_demo/stable.py` | PayPal v1 — production baseline FastAPI app |
| `examples/paypal_demo/candidate.py` | PayPal v2 — 5 regressions injected |
