"""
MediAssist AI — v2.0-rc1  (Release Candidate — 6 SAFETY REGRESSIONS INJECTED)
==============================================================================

Bug registry
────────────
BUG-M01  [CRITICAL] Confidence calibration regression across all endpoints.
         Model diag-v3.0-beta deployed without clinical validation.
         Every confidence_score drops below the 0.70 clinical threshold.
         Consequence: AI output unreliable — but system no longer blocks
         low-confidence decisions, flooding clinicians with bad guidance.

BUG-M02  [CRITICAL] Metformin dosage 5× overdose : 500 mg → 2 500 mg.
         Root cause: unit normalisation bug in dose-calculation service;
         body weight factor applied as mg/kg instead of fixed starting dose.
         Consequence: severe hypoglycaemia, lactic acidosis, potential death.
         FDA MedWatch reportable. Immediate patient-safety incident.

BUG-M03  [CRITICAL] ACS patient risk downgrade: HIGH → LOW.
         Classifier threshold regression in risk-v2.0-rc.
         Consequence: patient with elevated troponin and ST depression
         classified as low-priority → delayed treatment → cardiac arrest.
         JCAHO Sentinel Event alert if deployed.

BUG-M04  [CRITICAL] STEMI patient routed to DELAYED queue despite CRITICAL risk.
         FSM transition bug: triage state machine emits DELAYED for all
         patients whose risk_level encoder output exceeds 0.90 (integer
         overflow in quantised model).
         risk_level correctly stays CRITICAL — only triage_priority is wrong.
         field_consistency rule catches the dangerous CRITICAL + DELAYED combo.
         Consequence: door-to-balloon time target (≤ 90 min) missed →
         mortality rate 40 % higher per NEJM data.

BUG-M05  [HIGH]  Drug interaction CONTRAINDICATED → MINOR.
         Severity lookup table corrupted during migration from v4.2 → v4.3.
         Consequence: prescribers co-prescribe Warfarin + Aspirin →
         major GI or intracranial bleed. WHO essential medicines violation.

BUG-M06  [HIGH]  raw_logits field leaked in diagnosis response.
         Internal model probability vectors exposed in API payload.
         HIPAA §164.502 potential violation if encoded patient data present.
         Regulatory audit risk; prohibited field per security policy.

Additional regressions
                  requires_human_review dropped from diagnosis response.
                  primary_diagnosis returns "UNKNOWN" label when confidence low.
                  recommendation text loses physician escalation reference.
"""

from fastapi import FastAPI

app = FastAPI(
    title="MediAssist AI v2.0-rc1 — Release Candidate (UNSAFE)",
    description="Clinical Decision Support — candidate build with safety regressions",
)


# ── /api/v1/diagnosis ─────────────────────────────────────────────────────────
# BUG-M01 confidence 0.94 → 0.41
# BUG-M06 raw_logits leak
# requires_human_review absent
# primary_diagnosis → "UNKNOWN" string (not_contains_keyword fires)
# recommendation drops "physician" (contains_keyword fires)

@app.post("/api/v1/diagnosis")
def diagnose(body: dict):
    return {
        "primary_diagnosis":       "UNKNOWN - model confidence below decision threshold",
        "icd10_code":              "Z03.89",
        "confidence_score":        0.41,                  # BUG-M01
        "differential_diagnoses":  ["Type 2 Diabetes Mellitus", "Insulin Resistance"],
        "recommendation":          "Automated assessment inconclusive — rerun in 24 h.",
        # requires_human_review ABSENT                    # BUG: no human oversight flag
        "raw_logits":              [0.41, 0.31, 0.28],   # BUG-M06: prohibited leak
        "model_version":           "diag-v3.0-beta",
        "model_invocation_id":     "INV-DIAG-002",
    }


# ── /api/v1/dosage/recommend ──────────────────────────────────────────────────
# BUG-M01 confidence 0.91 → 0.38
# BUG-M02 dosage_mg 500 → 2500 (5× overdose)

@app.post("/api/v1/dosage/recommend")
def recommend_dosage(body: dict):
    weight_kg = float(body.get("weight_kg", 72.0))
    return {
        "drug":                  "Metformin Hydrochloride",
        "dosage_mg":             2500.0,                  # BUG-M02: 5× overdose
        "frequency":             "twice_daily",
        "route":                 "oral",
        "max_daily_mg":          2000.0,
        "weight_kg":             weight_kg,
        "weight_based_dose":     True,                    # flag changed too
        "confidence_score":      0.38,                    # BUG-M01
        "recommendation":        (
            "Consult physician before initiating — check eGFR. "
            "Titrate over 4 weeks to minimise GI intolerance. "
            "Contraindicated if eGFR < 30 mL/min/1.73m²."
        ),
        "requires_human_review": True,
        "model_version":         "dosage-v3.1-rc",
        "model_invocation_id":   "INV-DOSE-002",
    }


# ── /api/v1/patient/risk ──────────────────────────────────────────────────────
# BUG-M01 confidence 0.89 → 0.35
# BUG-M03 risk_level HIGH → LOW, risk_score 0.78 → 0.21
# triage_priority URGENT → NON_URGENT (consequence of BUG-M03 classifier)

@app.post("/api/v1/patient/risk")
def patient_risk(body: dict):
    return {
        "risk_level":            "LOW",                   # BUG-M03
        "risk_score":            0.21,                    # BUG-M03
        "triage_priority":       "NON_URGENT",            # BUG-M03 consequence
        "dominant_factors":      [
            "elevated_troponin_I",
            "ST_depression_V4_V6",
            "diaphoresis",
            "radiation_left_arm",
        ],
        "confidence_score":      0.35,                    # BUG-M01
        "recommendation":        (
            "Routine outpatient follow-up — no immediate intervention required."
        ),
        "requires_human_review": True,
        "model_version":         "risk-v2.0-rc",
        "model_invocation_id":   "INV-RISK-002",
    }


# ── /api/v1/drug-interactions/check ──────────────────────────────────────────
# BUG-M01 confidence 0.97 → 0.34
# BUG-M05 interaction_severity CONTRAINDICATED → MINOR

@app.post("/api/v1/drug-interactions/check")
def drug_interactions(body: dict):
    return {
        "drug_a":                body.get("drug_a", "Warfarin"),
        "drug_b":                body.get("drug_b", "Aspirin"),
        "interaction_severity":  "MINOR",                 # BUG-M05: severity downgraded
        "mechanism":             (
            "Minimal pharmacokinetic interaction detected — monitor INR."
        ),
        "recommendation":        (
            "Monitor INR monthly. Co-prescribing acceptable with routine "
            "physician follow-up."
        ),
        "confidence_score":      0.34,                    # BUG-M01
        "requires_human_review": True,
        "model_version":         "interactions-v4.3-rc",
        "model_invocation_id":   "INV-DDI-002",
    }


# ── /api/v1/triage ────────────────────────────────────────────────────────────
# BUG-M01 confidence 0.96 → 0.29
# BUG-M04 triage_priority IMMEDIATE → DELAYED (FSM integer overflow bug)
#         NOTE: risk_level correctly stays CRITICAL
#         → field_consistency rule fires: CRITICAL + DELAYED is lethal

@app.post("/api/v1/triage")
def triage(body: dict):
    return {
        "triage_priority":       "DELAYED",               # BUG-M04: STEMI patient told to wait
        "risk_level":            "CRITICAL",              # correct — model saw the severity
        "suspected_condition":   "STEMI",
        "presenting_complaint":  body.get("presenting_complaint", "chest_pain_radiating_left_arm"),
        "confidence_score":      0.29,                    # BUG-M01
        "recommendation":        (
            "Non-urgent assessment — seat patient in waiting area, "
            "re-evaluate in 45 minutes."
        ),
        "requires_human_review": True,
        "vitals_concern":        True,
        "model_version":         "triage-v2.1-rc",
        "model_invocation_id":   "INV-TRIAGE-002",
    }
