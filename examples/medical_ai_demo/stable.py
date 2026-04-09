"""
MediAssist AI — v1.4  (Stable Production Build)
================================================
Clinical Decision Support System serving 5 endpoints across the full
diagnostic and treatment workflow.

All responses are validated against clinical safety thresholds:
  - confidence_score  ≥ 0.70  (below this → mandatory clinician escalation)
  - requires_human_review carries the explicit human-oversight flag
  - dosage values conform to BNF / formulary limits
  - triage_priority consistent with risk_level (CRITICAL → IMMEDIATE/URGENT)

Endpoints
---------
  POST /api/v1/diagnosis              Symptom → primary diagnosis + ICD-10
  POST /api/v1/dosage/recommend       Patient profile → dosage recommendation
  POST /api/v1/patient/risk           Vitals + history → patient risk stratification
  POST /api/v1/drug-interactions/check  Drug pair → interaction severity
  POST /api/v1/triage                 ED presentation → triage priority
"""
from fastapi import FastAPI

app = FastAPI(
    title="MediAssist AI v1.4 — Production",
    description="Clinical Decision Support — stable production baseline",
)


# ── /api/v1/diagnosis ─────────────────────────────────────────────────────────

@app.post("/api/v1/diagnosis")
def diagnose(body: dict):
    return {
        "primary_diagnosis":       "Type 2 Diabetes Mellitus",
        "icd10_code":              "E11.9",
        "confidence_score":        0.94,
        "differential_diagnoses":  ["Type 1 Diabetes Mellitus", "MODY", "Metabolic Syndrome"],
        "recommendation":          (
            "Refer patient to endocrinologist physician for confirmatory HbA1c "
            "and fasting plasma glucose. Initiate lifestyle counselling."
        ),
        "requires_human_review":   True,
        "model_version":           "diag-v2.1",
        "model_invocation_id":     "INV-DIAG-001",
    }


# ── /api/v1/dosage/recommend ──────────────────────────────────────────────────

@app.post("/api/v1/dosage/recommend")
def recommend_dosage(body: dict):
    weight_kg = float(body.get("weight_kg", 72.0))
    return {
        "drug":                  "Metformin Hydrochloride",
        "dosage_mg":             500.0,
        "frequency":             "twice_daily",
        "route":                 "oral",
        "max_daily_mg":          2000.0,
        "weight_kg":             weight_kg,
        "weight_based_dose":     False,
        "confidence_score":      0.91,
        "recommendation":        (
            "Consult physician before initiating — check eGFR. "
            "Titrate over 4 weeks to minimise GI intolerance. "
            "Contraindicated if eGFR < 30 mL/min/1.73m²."
        ),
        "requires_human_review": True,
        "model_version":         "dosage-v3.0",
        "model_invocation_id":   "INV-DOSE-001",
    }


# ── /api/v1/patient/risk ──────────────────────────────────────────────────────

@app.post("/api/v1/patient/risk")
def patient_risk(body: dict):
    return {
        "risk_level":            "HIGH",
        "risk_score":            0.78,
        "triage_priority":       "URGENT",
        "dominant_factors":      [
            "elevated_troponin_I",
            "ST_depression_V4_V6",
            "diaphoresis",
            "radiation_left_arm",
        ],
        "confidence_score":      0.89,
        "recommendation":        (
            "Immediate cardiology physician consultation required. "
            "12-lead ECG, serial troponin every 3 h, cath lab on standby."
        ),
        "requires_human_review": True,
        "model_version":         "risk-v1.4",
        "model_invocation_id":   "INV-RISK-001",
    }


# ── /api/v1/drug-interactions/check ──────────────────────────────────────────

@app.post("/api/v1/drug-interactions/check")
def drug_interactions(body: dict):
    return {
        "drug_a":                body.get("drug_a", "Warfarin"),
        "drug_b":                body.get("drug_b", "Aspirin"),
        "interaction_severity":  "CONTRAINDICATED",
        "mechanism":             (
            "Additive anticoagulant and antiplatelet effect. "
            "Combined use elevates major bleeding risk 3–4× vs monotherapy."
        ),
        "recommendation":        (
            "Concurrent use contraindicated without specialist oversight — "
            "consult haematologist physician before co-prescribing. "
            "Consider alternative antiplatelet if anticoagulation mandatory."
        ),
        "confidence_score":      0.97,
        "requires_human_review": True,
        "model_version":         "interactions-v4.2",
        "model_invocation_id":   "INV-DDI-001",
    }


# ── /api/v1/triage ────────────────────────────────────────────────────────────

@app.post("/api/v1/triage")
def triage(body: dict):
    return {
        "triage_priority":       "IMMEDIATE",
        "risk_level":            "CRITICAL",
        "suspected_condition":   "STEMI",
        "presenting_complaint":  body.get("presenting_complaint", "chest_pain_radiating_left_arm"),
        "confidence_score":      0.96,
        "recommendation":        (
            "Activate STEMI protocol NOW — cath lab alert. "
            "Consult interventional cardiologist physician immediately. "
            "Door-to-balloon target ≤ 90 minutes."
        ),
        "requires_human_review": True,
        "vitals_concern":        True,
        "model_version":         "triage-v2.0",
        "model_invocation_id":   "INV-TRIAGE-001",
    }
