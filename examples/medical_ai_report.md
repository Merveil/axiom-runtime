# Axiom Runtime × MediAssist AI — Clinical Safety Validation Report

> **Verdict: 🚫 DEPLOYMENT BLOCKED — Patient safety incident risk**  
> Axiom caught six clinical regressions in MediAssist AI v2.0-rc1 before a single patient was exposed — including a 5× medication overdose, a missed STEMI, and a HIPAA data leak.

---

## Scenario

| | |
|---|---|
| **Organisation** | Regional Hospital Network — 14 clinical sites |
| **System** | MediAssist AI — Clinical Decision Support (FDA SaMD Class II) |
| **Stable build** | v1.4 (clinically validated — in production) |
| **Candidate** | v2.0-rc1 (release candidate — under evaluation) |
| **Daily volume** | ≈ 10 000 patient interactions / day at full rollout |
| **Rules** | `examples/medical_ai_rules.json` — 15 rules, all 9 Axiom types |
| **Run time** | 42 ms (Rust extension active) |
| **Regulatory** | FDA SaMD · EU AI Act Art. 14 · HIPAA §164.502 · JCAHO |

---

## Scorecard

| Metric | Result |
|---|---|
| Endpoints evaluated | 5 |
| ✅ Strict pass | 0 |
| 🟡 Semantic pass | 0 |
| 🔴 Regression detected | 5 |
| Rule violations — total | 13 |
| Rule violations — CRITICAL | **8** |
| **Regression rate** | **100 %** |

---

## Regression Detail

### `POST /api/v1/diagnosis` — Symptom → Primary Diagnosis

| Field | v1.4 (stable) | v2.0-rc1 (candidate) | Bug |
|---|---|---|---|
| `confidence_score` | `0.94` | `0.41` | BUG-M01 |
| `primary_diagnosis` | `"Type 2 Diabetes Mellitus"` | `"UNKNOWN - model confidence…"` | BUG-M01 |
| `icd10_code` | `"E11.9"` | `"Z03.89"` | BUG-M01 |
| `raw_logits` | **ABSENT** | `[0.41, 0.31, 0.28]` | BUG-M06 |
| `requires_human_review` | `true` | **ABSENT** | BUG-M06 |
| `recommendation` | *"…physician…"* | *"Automated assessment inconclusive"* | BUG-M06 |
| `model_version` | `"diag-v2.1"` | `"diag-v3.0-beta"` | BUG-M01 |

**Rule violations (5 — 2 CRITICAL):**
- `[MED_CR_01]` 🔴 CRITICAL — `confidence_score` `0.41` below clinical threshold `[0.70, 1.0]`
- `[MED_RF_03]` 🔴 CRITICAL — Required field `requires_human_review` absent
- `[MED_PF_01]` ⚠️ HIGH — Prohibited field `raw_logits` present (HIPAA risk)
- `[MED_CK_01]` ⚠️ HIGH — `recommendation` does not contain required keyword `"physician"`
- `[MED_NC_01]` ⚠️ HIGH — `primary_diagnosis` contains prohibited keyword `"UNKNOWN"`

**Clinical impact:** The diagnosis model surfaces `"UNKNOWN"` to clinical systems, disables human oversight, and exposes internal probability vectors. The ICD-10 code changes to an unspecified placeholder — downstream EHR coding and insurance billing fail silently.

---

### `POST /api/v1/dosage/recommend` — Dosage Recommendation

| Field | v1.4 (stable) | v2.0-rc1 (candidate) | Bug |
|---|---|---|---|
| `confidence_score` | `0.91` | `0.38` | BUG-M01 |
| `dosage_mg` | `500.0` | `2500.0` | BUG-M02 |
| `weight_based_dose` | `false` | `true` | BUG-M02 |
| `model_version` | `"dosage-v3.0"` | `"dosage-v3.1-rc"` | BUG-M01 |

**Rule violations (2 — 2 CRITICAL):**
- `[MED_CR_01]` 🔴 CRITICAL — `confidence_score` `0.38` below threshold
- `[MED_CR_02]` 🔴 CRITICAL — `dosage_mg` `2500` exceeds safe range `[0.1, 1000]`

**Clinical impact:** **5× Metformin overdose.** The dose-calculation service switched to a weight-based multiplier (84 kg × ~29.76 mg/kg = 2500 mg). At twice-daily frequency this prescribes 5000 mg/day — 2.5× the BNF maximum. Metformin overdose causes lactic acidosis (mortality ≈ 45% untreated). FDA MedWatch reportable.

---

### `POST /api/v1/patient/risk` — Patient Risk Stratification

| Field | v1.4 (stable) | v2.0-rc1 (candidate) | Bug |
|---|---|---|---|
| `confidence_score` | `0.89` | `0.35` | BUG-M01 |
| `risk_level` | `"HIGH"` | `"LOW"` | BUG-M03 |
| `risk_score` | `0.78` | `0.21` | BUG-M03 |
| `triage_priority` | `"URGENT"` | `"NON_URGENT"` | BUG-M03 |
| `recommendation` | *"Immediate cardiology…physician"* | *"Routine outpatient follow-up"* | BUG-M03 |

**Rule violations (2 — 1 CRITICAL):**
- `[MED_CR_01]` 🔴 CRITICAL — `confidence_score` `0.35` below threshold
- `[MED_CK_01]` ⚠️ HIGH — `recommendation` does not reference `"physician"`

**Clinical impact:** ACS patient (elevated troponin 1.8 µg/L, ST depression −2.1 mm) classified as LOW risk and routed to non-urgent outpatient follow-up. Without immediate intervention: myocardial infarction in ~35% within 6 hours; sudden cardiac death in ~8% within 30 days (GRACE registry). JCAHO Sentinel Event if deployed.

---

### `POST /api/v1/drug-interactions/check` — Drug Interaction Check

| Field | v1.4 (stable) | v2.0-rc1 (candidate) | Bug |
|---|---|---|---|
| `confidence_score` | `0.97` | `0.34` | BUG-M01 |
| `interaction_severity` | `"CONTRAINDICATED"` | `"MINOR"` | BUG-M05 |
| `mechanism` | *"Additive anticoagulant…"* | *"Minimal pharmacokinetic…"* | BUG-M05 |
| `recommendation` | *"Concurrent use contraindicated…"* | *"Monitor INR monthly. Co-prescribe…"* | BUG-M05 |

**Rule violations (1 — 1 CRITICAL):**
- `[MED_CR_01]` 🔴 CRITICAL — `confidence_score` `0.34` below threshold

**Clinical impact:** Warfarin + Aspirin downgraded from CONTRAINDICATED to MINOR. Prescribers will co-administer: major GI bleed risk ×3.5 vs monotherapy (Hylek et al. 2001); intracranial haemorrhage ×5.8 (ISTH registry). WHO Model Formulary marks this pair as absolutely contraindicated outside supervised post-ACS settings.

---

### `POST /api/v1/triage` — Emergency Triage (STEMI Presentation)

| Field | v1.4 (stable) | v2.0-rc1 (candidate) | Bug |
|---|---|---|---|
| `confidence_score` | `0.96` | `0.29` | BUG-M01 |
| `triage_priority` | `"IMMEDIATE"` | `"DELAYED"` | BUG-M04 |
| `risk_level` | `"CRITICAL"` | `"CRITICAL"` *(unchanged)* | — |
| `recommendation` | *"Activate STEMI protocol…"* | *"Non-urgent assessment — wait 45 min"* | BUG-M04 |

**Rule violations (3 — 2 CRITICAL):**
- `[MED_CR_01]` 🔴 CRITICAL — `confidence_score` `0.29` below threshold
- `[MED_FC_01]` 🔴 CRITICAL — `risk_level=CRITICAL` but `triage_priority="DELAYED"` (must be `IMMEDIATE` or `URGENT`)
- `[MED_CK_01]` ⚠️ HIGH — `recommendation` does not reference `"physician"`

**Clinical impact:** The model correctly identifies a STEMI (`risk_level=CRITICAL`) but routes the patient to a delayed queue (FSM integer overflow in `triage-v2.1-rc`). ACC/AHA guideline: door-to-balloon time ≤ 90 minutes. 45-minute delay → +11.3% 30-day mortality (NEJM, Nallamothu et al. 2007). The `field_consistency` rule (`MED_FC_01`) is the only mechanism that catches this lethal self-contradiction.

---

## Root-Cause Analysis

| Bug | Severity | Root cause | Regulatory exposure |
|---|---|---|---|
| **BUG-M01** | 🔴 CRITICAL | `diag-v3.0-beta` deployed without clinical calibration validation — all 5 sub-tasks produce confidence < 0.70 | FDA SaMD §513(f)(2) substantial risk |
| **BUG-M02** | 🔴 CRITICAL | `dosage-v3.1-rc` applies body weight as mg/kg multiplier instead of fixed start dose | FDA MedWatch reportable; criminal liability |
| **BUG-M03** | 🔴 CRITICAL | `risk-v2.0-rc` output-layer threshold shifted from 0.50 → 0.85 during quantisation | JCAHO Sentinel Event obligation |
| **BUG-M04** | 🔴 CRITICAL | Signed/unsigned integer overflow in `triage-v2.1-rc` FSM routes risk > 0.90 → DELAYED | ACC/AHA STEMI protocol breach |
| **BUG-M05** | ⚠️ HIGH | Hash collision in interactions v4.2→v4.3 migration maps Warfarin+Aspirin to Paracetamol+Aspirin entry | WHO essential medicines policy; WHO Model Formulary |
| **BUG-M06** | ⚠️ HIGH | Debug serialisation flag left enabled in `diag-v3.0-beta`; `requires_human_review` dropped from schema | HIPAA §164.502; EU AI Act Art. 14 |

---

## Mandatory actions before re-evaluation

1. **BUG-M01** — Do not deploy `diag-v3.0-beta`. Retrain with temperature scaling on a held-out clinical validation set. Minimum target: confidence ≥ 0.80 on all 5 sub-tasks.
2. **BUG-M02** — Revert dose-calculation to fixed-start-dose logic. Add integration test: `dosage_mg == 500` for standard T2DM initiation regardless of body weight.
3. **BUG-M03** — Recalibrate `risk-v2.0-rc` threshold on ACS validation cohort. Acceptance criterion: sensitivity ≥ 0.98 on CRITICAL/HIGH class.
4. **BUG-M04** — Fix integer overflow in triage encoder (`uint8` → `int16`). Add invariant test: `risk_level=CRITICAL` must never produce `triage_priority=DELAYED`.
5. **BUG-M05** — Restore interactions lookup table v4.2. Run regression suite against full WHO contraindication list (173 drug pairs).
6. **BUG-M06** — Set `DEBUG_SERIALISE=False` in production config. Restore `requires_human_review` to `DiagnosisResponseV2` schema.

---

## Product statement

> *"Axiom caught six clinical regressions in MediAssist AI v2.0-rc1 before a single patient was exposed — including a 5× medication overdose, a missed STEMI that would have breached door-to-balloon time, a misclassified ACS patient, a corrupted drug interaction table, and a HIPAA data leak — all in 42 milliseconds, replaying real clinical traffic against the candidate build with no instrumentation required."*

---

*Generated by Axiom Runtime · `python examples/medical_ai_simulation.py`*
