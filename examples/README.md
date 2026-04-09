# Axiom Runtime — Demo Playbook

> Replay production traffic. Catch regressions. Block bad deployments.  
> Two industry-grade demos. Total run time: under 2 minutes.

---

## The idea in one paragraph

Axiom Runtime captures real API traffic against a known-good baseline, then replays it against a release candidate. Every response is evaluated against a declarative rule set that encodes the system's behavioral contract — required fields, forbidden values, numeric ranges, keyword constraints, field consistency invariants. Any deviation that breaks a rule blocks the deployment, with a precise root-cause trace.

No instrumentation in the candidate. No mocks. No test doubles. Just real traffic, replayed.

---

## Demo 1 — PayPal Payments API

> *"Before shipping v2, Axiom detected five critical regressions that would have impacted merchant fees, fraud decisions, payment completion, currency routing, and PCI auditability."*

**Scenario:** PayPal is preparing to promote v2 of its Payments API to production (28M transactions/day). The team runs Axiom to validate the candidate build before any traffic is switched.

```
Here is v1 traffic.           → baseline captured from production-equivalent API

Here is candidate v2.         → same 5 call patterns replayed against release candidate

python examples/paypal_simulation.py
```

**What Axiom finds in < 50 ms:**

| Endpoint | Drift | Business impact |
|---|---|---|
| `POST /v1/payments/create` | `fee_amount` absent | Settlement reconciliation fails for all merchants |
| `POST /v1/payments/{id}/capture` | `status` = `PROCESSING` (not `COMPLETED`) | Payments never settle; merchants receive no funds |
| `GET /v1/transactions/{id}` | `currency` = `"usd"` (lowercase) | SWIFT routing rejected; forex systems reject unknown code |
| `GET /v1/accounts/{id}/balance` | `currency` = `"usd"` | Balance display corrupted |
| `POST /v1/fraud/score` | `fraud_score` 0.12 → 0.87 | 100% of transactions flagged — checkout completely blocked |

**Verdict:** 🚫 DEPLOYMENT BLOCKED — 5/5 endpoints · 14 rule violations · 100% regression rate

```bash
python examples/paypal_simulation.py
```

→ Full report: [`examples/paypal_report.md`](paypal_report.md)  
→ Rules (12 — all 9 types): [`examples/paypal_rules.json`](paypal_rules.json)  
→ Demo README: [`examples/paypal_demo/README.md`](paypal_demo/README.md)

---

## Demo 2 — MediAssist AI (Clinical Decision Support)

> *"Axiom caught six clinical regressions — including a 5× medication overdose and a missed STEMI — before a single patient was exposed to the candidate build."*

**Scenario:** A regional hospital network (14 sites, ≈ 10 000 patient interactions/day) is evaluating MediAssist AI v2.0-rc1 for production rollout across 5 clinical endpoints. The Clinical Safety Board runs Axiom before go-live.

```
Here is v1.4 clinical traffic. → baseline from validated production system

Here is v2.0-rc1.              → same 5 call patterns replayed against release candidate

python examples/medical_ai_simulation.py
```

**What Axiom finds in 42 ms:**

| Endpoint | Regression | Clinical impact | Rule |
|---|---|---|---|
| `POST /api/v1/diagnosis` | Confidence 0.94 → 0.41; `raw_logits` leaked; `requires_human_review` dropped | HIPAA breach; no human oversight; "UNKNOWN" diagnosis surfaces | MED_CR_01 · MED_PF_01 · MED_RF_03 |
| `POST /api/v1/dosage/recommend` | `dosage_mg` 500 → **2500** (5× overdose) | Lactic acidosis risk; FDA MedWatch reportable | MED_CR_02 |
| `POST /api/v1/patient/risk` | Risk level HIGH → LOW (ACS patient) | Missed cardiac event → JCAHO Sentinel Event | MED_CR_01 · drift |
| `POST /api/v1/drug-interactions/check` | Warfarin+Aspirin CONTRAINDICATED → MINOR | Major GI bleed or intracranial haemorrhage | MED_CR_01 · drift |
| `POST /api/v1/triage` | STEMI patient: IMMEDIATE → **DELAYED** (risk_level still CRITICAL) | Door-to-balloon > 90 min → +11% mortality | **MED_FC_01** (field consistency) |

**Verdict:** 🚫 DEPLOYMENT BLOCKED — 5/5 endpoints · 13 rule violations · 8 CRITICAL · 100% regression rate

```bash
python examples/medical_ai_simulation.py
```

→ Full report + regulatory analysis: [`examples/medical_ai_report.md`](medical_ai_report.md)  
→ Rules (15 — all 9 types): [`examples/medical_ai_rules.json`](medical_ai_rules.json)  
→ Demo README: [`examples/medical_ai_demo/README.md`](medical_ai_demo/README.md)

---

## Quick reference

| | PayPal | MediAssist AI |
|---|---|---|
| **Endpoints** | 5 | 5 |
| **Rules** | 12 | 15 |
| **Bugs injected** | 5 | 6 |
| **Violations found** | 14 | 13 (8 CRITICAL) |
| **Run time** | < 50 ms | 42 ms |
| **Rule types covered** | All 9 | All 9 |
| **Regression rate** | 100% | 100% |

**All 9 Axiom rule types exercised across both demos:**

| Rule type | PayPal | Medical AI |
|---|---|---|
| `ignore_field` | PP001–PP003 (timestamps) | MED_IF_01/02 (UUID, latency) |
| `required_field` | PP004/PP005 (fee, currency) | MED_RF_01–03 (confidence, recommendation, human review) |
| `prohibited_field` | PP006 (error_code) | MED_PF_01/02 (raw_logits, patient ID) |
| `value_in_set` | PP007/PP008 (status, currency) | MED_VS_01–03 (risk, triage, interaction) |
| `value_in_range` | PP009 (fraud_score) | MED_CR_01/02 (confidence, dosage) |
| `contains_keyword` | PP010 (APPROVE) | MED_CK_01 (physician) |
| `not_contains_keyword` | — | MED_NC_01 (UNKNOWN) |
| `numeric_tolerance` | PP011 (amount) | MED_NT_01 (dosage_mg) |
| `field_consistency` | PP012 (COMPLETED → net_amount) | MED_FC_01 (CRITICAL → triage) |

---

## Run both demos end-to-end

```bash
pip install -e ".[dev]"                          # first time only

python examples/paypal_simulation.py             # PayPal — ~40 ms
python examples/medical_ai_simulation.py         # Medical AI — ~42 ms
```

Total wall time: under 2 minutes including install.

---

## How to adapt to your own system

1. Copy `examples/paypal_rules.json` or `examples/medical_ai_rules.json` as a starting template
2. Swap `stable_app` and `candidate_app` for your own FastAPI (or HTTPX-compatible) apps
3. Change the `cap.post()` and `cap.get()` calls to your actual API endpoints and payloads
4. Edit the rules to match your system's behavioral contract
5. Run — Axiom does the rest

See [`axiom_lab/`](../axiom_lab/README.md) for the full API reference.
