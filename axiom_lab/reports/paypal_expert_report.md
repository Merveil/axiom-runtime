# PayPal API v2 — Release Validation Report

> **Verdict: 🚫 BLOCK**

## Executive Scorecard
| Metric | Value |
|---|---|
| Severity Score | **100.0 / 100** |
| Confidence | **0.812** (81%) |
| Risk Index | **0.953** — CRITICAL |
| Coverage — Endpoints | 5/5 (100%) |
| Coverage — Rules | 6/12 (50%) |
| Coverage — Critical Paths | 5/5 |
| Regression Rate | 100% |
| Baseline Integrity | VERIFIED |
| Domain Multiplier | ×1.5 |

## Violation Distribution
| CRITICAL | HIGH | MEDIUM | LOW | TOTAL |
|---|---|---|---|---|
| 14 | 0 | 0 | 0 | 14 |

## Top Root Causes
- `classification_shift` (6)
- `missing_field` (3)
- `schema_regression` (3)
- `numeric_instability` (1)
- `semantic_inconsistency` (1)

## Severity Breakdown
| Component | Score |
|---|---|
| Regression rate | 50.0 pts |
| Weighted violations | 28.0 pts |
| Failed endpoints | 0.0 pts |
| Domain multiplier applied | ×1.5 |

## Confidence Breakdown
```
coverage_factor:            0.300  (×0.30)
consistency_factor:         0.250  (×0.25)
richness_factor:            0.036  (×0.25)
drift_corroboration_factor: 0.187  (×0.20)
deterministic_endpoints:    score=0.871
stochastic_endpoints:       score=0.000
variance:                   medium
```

## Business / Regulatory Impact
| Dimension | Value |
|---|---|
| Revenue loss | CRITICAL |
| SLA breach | YES |
| Compliance risk | CRITICAL |
| User blocking | YES |
| Legal liability | HIGH |
| PCI | **FAIL** |
| PSD2 | **FAIL** |
| SWIFT | **FAIL** |

> **Regulatory exposure:** PCI-DSS v4 · PSD2 Article 45 · SWIFT routing failure

## Endpoint Intelligence
### 🔴 `/v1/payments/create`
*Create payment intent*

| Metric | Value |
|---|---|
| Severity contribution | 100/100 |
| Semantic score | 0.300 |
| Violations | CRITICAL 2 · HIGH 0 · MEDIUM 0 |

**Drift magnitude:**
| Field | Label | Baseline | Candidate | Delta |
|---|---|---|---|---|
| `/fee_amount` | **CATASTROPHIC** | 9.0 | MISSING | N/A |
| `/fraud_score` | **CATASTROPHIC** | 0.12 | 0.87 | +0.75 |

**Explainability:**
```yaml
- field: /fee_amount
  expected: 'fee_amount' must be present in response
  actual:   fee_amount = MISSING
  reason:   'fee_amount' was removed from response schema — missing_field regression
- field: /fraud_score
  expected: Field 'fraud_score' value 0.87 out of range [0, 0.35]
  actual:   fraud_score = 0.87
  reason:   'fraud_score' increased by Δ=+0.75 (×625.0% relative) — classification_shift
```

### 🔴 `/v1/payments/PAY-FLAGSHIP-001/capture`
*Capture / settle payment*

| Metric | Value |
|---|---|
| Severity contribution | 100/100 |
| Semantic score | 0.060 |
| Violations | CRITICAL 3 · HIGH 0 · MEDIUM 0 |

**Drift magnitude:**
| Field | Label | Baseline | Candidate | Delta |
|---|---|---|---|---|
| `/amount` | **NEGLIGIBLE** | 299.99 | 300.0 | +0.01 |
| `/currency` | **CHANGED** | USD | usd | N/A |
| `/fee_amount` | **CATASTROPHIC** | 9.0 | MISSING | N/A |
| `/net_amount` | **CATASTROPHIC** | 290.99 | MISSING | N/A |
| `/status` | **CHANGED** | COMPLETED | PROCESSING | N/A |

**Explainability:**
```yaml
- field: /amount
  expected: 'amount' must be present in response
  actual:   amount = 300.0
  reason:   'amount' drifted by Δ=+0.01 — numeric_instability
- field: /currency
  expected: Field 'currency' value "usd" not in allowed set ["AUD", "CAD", "EUR", "GBP", "JP
  actual:   currency = usd
  reason:   'currency' string value changed — schema_regression
- field: /fee_amount
  expected: 'fee_amount' must be present in response
  actual:   fee_amount = MISSING
  reason:   'fee_amount' was removed from response schema — missing_field regression
- field: /net_amount
  expected: net_amount ≈ 291 (baseline value)
  actual:   net_amount = MISSING
  reason:   'net_amount' was removed from response schema — missing_field regression
- field: /status
  expected: Field 'status' value "PROCESSING" not in allowed set ["COMPLETED", "CREATED", "F
  actual:   status = PROCESSING
  reason:   'status' string value changed — classification_shift
```

### 🔴 `/v1/transactions/TXN-FLAGSHIP-001`
*Fetch transaction record*

| Metric | Value |
|---|---|
| Severity contribution | 100/100 |
| Semantic score | 0.020 |
| Violations | CRITICAL 4 · HIGH 0 · MEDIUM 0 |

**Drift magnitude:**
| Field | Label | Baseline | Candidate | Delta |
|---|---|---|---|---|
| `/currency` | **CHANGED** | USD | usd | N/A |
| `/fraud_score` | **CATASTROPHIC** | 0.12 | 0.87 | +0.75 |
| `/status` | **CHANGED** | COMPLETED | PROCESSING | N/A |

**Explainability:**
```yaml
- field: /currency
  expected: Field 'currency' value "usd" not in allowed set ["AUD", "CAD", "EUR", "GBP", "JP
  actual:   currency = usd
  reason:   'currency' string value changed — schema_regression
- field: /fraud_score
  expected: Field 'fraud_score' value 0.87 out of range [0, 0.35]
  actual:   fraud_score = 0.87
  reason:   'fraud_score' increased by Δ=+0.75 (×625.0% relative) — classification_shift
- field: /status
  expected: Field 'status' value "PROCESSING" not in allowed set ["COMPLETED", "CREATED", "F
  actual:   status = PROCESSING
  reason:   'status' string value changed — classification_shift
```

### 🔴 `/v1/accounts/ACC-BUSINESS-42/balance`
*Account balance check*

| Metric | Value |
|---|---|
| Severity contribution | 100/100 |
| Semantic score | 0.480 |
| Violations | CRITICAL 2 · HIGH 0 · MEDIUM 0 |

**Drift magnitude:**
| Field | Label | Baseline | Candidate | Delta |
|---|---|---|---|---|
| `/currency` | **CHANGED** | USD | usd | N/A |

**Explainability:**
```yaml
- field: /currency
  expected: Field 'currency' value "usd" not in allowed set ["AUD", "CAD", "EUR", "GBP", "JP
  actual:   currency = usd
  reason:   'currency' string value changed — schema_regression
```

### 🔴 `/v1/fraud/score`
*Fraud risk assessment*

| Metric | Value |
|---|---|
| Severity contribution | 100/100 |
| Semantic score | 0.000 |
| Violations | CRITICAL 3 · HIGH 0 · MEDIUM 0 |

**Drift magnitude:**
| Field | Label | Baseline | Candidate | Delta |
|---|---|---|---|---|
| `/decision` | **CHANGED** | APPROVE | REVIEW | N/A |
| `/model_version` | **CHANGED** | fraud-v3.1 | fraud-v4.0 | N/A |
| `/score` | **CATASTROPHIC** | 0.12 | 0.87 | +0.75 |

**Explainability:**
```yaml
- field: /decision
  expected: 'decision' must be present in response
  actual:   decision = REVIEW
  reason:   'decision' string value changed — classification_shift
- field: /model_version
  expected: model_version = 'fraud-v3.1' (baseline value)
  actual:   model_version = fraud-v4.0
  reason:   'model_version' string value changed — semantic_inconsistency
- field: /score
  expected: score ≈ 0.12 (baseline value)
  actual:   score = 0.87
  reason:   'score' increased by Δ=+0.75 (×625.0% relative) — classification_shift
```

## Rule Traceability
| Rule ID | Tier | Triggered On | Hits | Description |
|---|---|---|---|---|
| `PP004` | **CRITICAL** | `/v1/payments/create`, `/v1/payments/PAY-FLAGSHIP-001/capture` +3 | 5 | PayPal processing fee MUST be returned on every pa |
| `PP008` | **CRITICAL** | `/v1/payments/PAY-FLAGSHIP-001/capture`, `/v1/transactions/TXN-FLAGSHIP-001` +1 | 3 | Currency must be uppercase ISO 4217. Lowercase cod |
| `PP009` | **CRITICAL** | `/v1/payments/create`, `/v1/transactions/TXN-FLAGSHIP-001` | 2 | fraud_score above 0.35 triggers mandatory manual r |
| `PP007` | **CRITICAL** | `/v1/payments/PAY-FLAGSHIP-001/capture`, `/v1/transactions/TXN-FLAGSHIP-001` | 2 | Payment FSM state must be a documented value. Undo |
| `PP005` | **CRITICAL** | `/v1/fraud/score` | 1 | ISO 4217 currency code is mandatory on all monetar |
| `PP010` | **CRITICAL** | `/v1/fraud/score` | 1 | Low-risk transactions must carry the literal APPRO |

## Counterfactual Analysis
**Current status:** BLOCKED (score 100.0)

| Fixes Applied | New Score | New Verdict |
|---|---|---|
| fix [PP009] | 95.4 | BLOCKED |
| fix [PP009 + PP009] | 90.8 | BLOCKED |
| fix [PP009 + PP009 + PP004] | 86.4 | BLOCKED |
| fix [PP009 + PP009 + PP004 + PP004] | 82.1 | BLOCKED |


## Temporal Consistency
```yaml
runs:             5
consistency:      100%
drift_variance:   0.000
same_input_runs:  stable
drift_over_time:  stable
```

## Comparative Analysis (V1 vs V2)
| Version | Reliability Score | Verdict |
|---|---|---|
| V1 (baseline) | **100.0%** | ✅ STABLE |
| V2 (candidate) | **0.0%** | 🔴 REGRESSION |

> Impact magnitude: **CATASTROPHIC** · Regression delta: +100.0%

## Why Axiom is Right
- Baseline verified across 5 endpoints — VERIFIED
- Regressions are consistent across 5/5 endpoints (not isolated noise)
- Detected via 1 independent rule categories: required_field
- Root cause distribution: classification_shift ×6; missing_field ×3; schema_regression ×3
- Confidence 81% based on multi-vector corroboration (drift + rule + verdict consistency)
- Severity 100/100 — exceeds BLOCK threshold (70)
- Violations span 14 rule checks across critical business/safety paths

## Deployment Decision
```yaml
action:               BLOCK
confidence:           MEDIUM
risk_level:           CRITICAL
rollback_recommended: TRUE
justification:
  - all 5 endpoints exhibit behavioral regression
  - 14 critical rule violation(s) detected
  - severity 100/100 exceeds BLOCK threshold (70)
  - risk index 0.953 — CRITICAL
```

## Executive Summary
> Axiom detected 5 critical payment regression(s) across 5 endpoints, affecting: merchant fee integrity, fraud scoring accuracy, payment completion FSM, currency routing correctness. Severity: 100/100. Confidence: 81%. Verdict: DEPLOYMENT BLOCKED.
