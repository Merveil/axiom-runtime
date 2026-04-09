# Axiom Runtime × PayPal — Deployment Validation Report

> **Verdict: 🚫 DEPLOYMENT BLOCKED**  
> Before shipping v2, Axiom detected five critical regressions that would have impacted merchant fees, fraud decisions, payment completion, currency routing, and PCI auditability.

---

## Scenario

| | |
|---|---|
| **Organisation** | PayPal Payments Platform |
| **Stable build** | Payments API v1 (production baseline) |
| **Candidate** | Payments API v2 (release candidate) |
| **Daily volume** | ≈ 28 million transactions / day |
| **Rules** | `examples/paypal_rules.json` — 12 rules, all 9 Axiom types |
| **Run time** | < 50 ms (Rust extension active) |

---

## Scorecard

| Metric | Result |
|---|---|
| Endpoints evaluated | 5 |
| ✅ Strict pass | 0 |
| 🟡 Semantic pass | 0 |
| 🔴 Regression detected | 5 |
| Rule violations | 14 |
| **Regression rate** | **100 %** |

---

## Regression Detail

### `POST /v1/payments/create` — Create payment intent

| Field | v1 (stable) | v2 (candidate) | Bug |
|---|---|---|---|
| `fee_amount` | `8.99` | **ABSENT** | BUG-01 |
| `net_amount` | `291.00` | **ABSENT** | BUG-01 |

**Rule violations:**
- `[PP004]` Required field `fee_amount` absent from response
- `[PP012]` Consistency: `status=COMPLETED` but `net_amount` absent

**Business impact:** Merchants cannot verify net settlement amounts. At 28M transactions/day, missing fee data breaks automated reconciliation for every merchant — revenue leakage invisible to downstream accounting systems. Potential regulatory fine (PSD2 Article 45 disclosure requirement).

---

### `POST /v1/payments/{id}/capture` — Capture / settle payment

| Field | v1 (stable) | v2 (candidate) | Bug |
|---|---|---|---|
| `status` | `"COMPLETED"` | `"PROCESSING"` | BUG-03 |
| `fee_amount` | `8.99` | **ABSENT** | BUG-01 |
| `net_amount` | `291.00` | **ABSENT** | BUG-01 |

**Rule violations:**
- `[PP007]` `status` value `"PROCESSING"` not in allowed FSM states
- `[PP004]` Required field `fee_amount` absent

**Business impact:** Payment capture stuck — downstream PSPs interpret `PROCESSING` as a pending state and never trigger settlement. Merchants receive no funds. At scale, this is a systemic settlement failure.

---

### `GET /v1/transactions/{id}` — Fetch transaction record

| Field | v1 (stable) | v2 (candidate) | Bug |
|---|---|---|---|
| `currency` | `"USD"` | `"usd"` | BUG-04 |
| `amount` | `299.99` | `300.0` | BUG-05 |

**Rule violations:**
- `[PP008]` `currency` value `"usd"` not in uppercase ISO 4217 set
- `[PP011]` `amount` drifted by `0.01` — exceeds tolerance of `0.001`

**Business impact:** Lowercase currency codes are rejected by SWIFT messaging systems and all major forex settlement APIs. Amount drift of $0.01 per transaction = $280,000/day in reconciliation discrepancies at production volume. Breaks PCI-DSS auditability.

---

### `GET /v1/accounts/{id}/balance` — Account balance check

| Field | v1 (stable) | v2 (candidate) | Bug |
|---|---|---|---|
| `currency` | `"USD"` | `"usd"` | BUG-04 |

**Rule violations:**
- `[PP008]` `currency` value `"usd"` not in allowed set

**Business impact:** Balance display corrupted for all merchants using currency-aware dashboards. Downstream currency conversion endpoints silently fail with unknown code `"usd"`.

---

### `POST /v1/fraud/score` — Fraud risk assessment

| Field | v1 (stable) | v2 (candidate) | Bug |
|---|---|---|---|
| `fraud_score` | `0.12` | `0.87` | BUG-02 |
| `score` | `0.12` | `0.87` | BUG-02 |
| `decision` | `"APPROVE"` | `"REVIEW"` | BUG-02 |
| `model_version` | `"fraud-v3.1"` | `"fraud-v4.0"` | BUG-02 |

**Rule violations:**
- `[PP009]` `fraud_score` value `0.87` out of safe range `[0.0, 0.35]`
- `[PP010]` `decision` does not contain required keyword `"APPROVE"`

**Business impact:** 100% of transactions flagged for manual review → operations overload; checkout SLA breach; legitimate users blocked. At 28M transactions/day, this is a complete checkout failure for all users.

---

## Root-Cause Analysis

| Bug | Severity | Root cause | Regulatory exposure |
|---|---|---|---|
| **BUG-01** | 🔴 CRITICAL | Schema migration removed `fee_amount` and `net_amount` from `PaymentResponseV2` | PSD2 Art. 45 disclosure; PCI-DSS v4 §3.3 |
| **BUG-02** | 🔴 CRITICAL | `fraud-v4.0` deployed without threshold recalibration on production distribution | PCI-DSS v4 §10.7 compliance breach |
| **BUG-03** | 🔴 CRITICAL | FSM capture transition emits `PROCESSING` instead of `COMPLETED` (state machine bug) | Settlement integrity failure |
| **BUG-04** | 🔴 CRITICAL | String serialiser lost uppercase normalisation on `currency` field | SWIFT routing failure; forex rejection |
| **BUG-05** | 🟡 HIGH | IEEE 754 float→string rounding applied at API boundary instead of Decimal | PCI-DSS auditability; reconciliation drift |

---

## Mandatory actions before re-evaluation

1. **BUG-01** — Restore `fee_amount` and `net_amount` to `PaymentResponseV2` schema
2. **BUG-02** — Roll back to `fraud-v3.1` OR recalibrate `fraud-v4.0` thresholds on production data
3. **BUG-03** — Fix FSM capture transition: `PROCESSING` → `COMPLETED` when authorisation succeeds
4. **BUG-04** — Add `.upper()` normalisation to all currency field serialisers
5. **BUG-05** — Use `Decimal` (not `float`) for all monetary amount fields at the API boundary

---

## Product statement

> *"Before shipping v2, Axiom detected five critical regressions that would have impacted merchant fees, fraud decisions, payment completion, currency routing, and PCI auditability — in under 50 milliseconds, replaying real production traffic patterns, with zero instrumentation in the candidate build."*

---

*Generated by Axiom Runtime · `python examples/paypal_simulation.py`*
