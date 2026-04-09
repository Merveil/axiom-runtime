"""
MediAssist AI × Axiom Runtime — Clinical Safety Validation
===========================================================

Scenario
--------
A hospital network is preparing to promote MediAssist AI v2.0-rc1 to
production across 14 clinical sites (≈ 10 000 patient interactions / day).
Before go-live, the Clinical Safety Board commissions Axiom Runtime to:

  Phase 1 — Capture a golden baseline from v1.4 (validated production)
  Phase 2 — Replay all sessions against v2.0-rc1 (the release candidate)
  Phase 3 — Apply 15 clinical safety rules (all 9 Axiom rule types)
  Phase 4 — Executive report + FDA/HIPAA incident analysis

Regulatory context
------------------
  FDA   : Software as a Medical Device (SaMD) — Class II (substantial risk)
  EU    : EU AI Act Article 14 — High-risk AI, mandatory human oversight
  HIPAA : §164.502 — PHI data leakage prohibition
  JCAHO : Sentinel Event policy — near-miss reporting obligation

Run
---
  python examples/medical_ai_simulation.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT     = Path(__file__).parent.parent
_EXAMPLES = Path(__file__).parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_EXAMPLES))

from fastapi.testclient import TestClient

from medical_ai_demo.stable    import app as stable_app
from medical_ai_demo.candidate import app as candidate_app

from axiom_lab.probe        import SessionCapture, replay_session, Verdict
from axiom_lab.rules_engine import RulesEngine

# ── Visual layout ─────────────────────────────────────────────────────────────

W = 72

VERDICT_BADGE = {
    Verdict.REPRODUCIBLE_STRICT:   "✅  PASS — STRICT          ",
    Verdict.REPRODUCIBLE_SEMANTIC: "🟡  PASS — SEMANTIC        ",
    Verdict.DRIFT_DETECTED:        "🔴  REGRESSION DETECTED    ",
    Verdict.FAILED_TO_REPLAY:      "💀  ENDPOINT FAILURE       ",
}

SEVERITY_LABEL = {
    "MED_CR_01": "CRITICAL",
    "MED_CR_02": "CRITICAL",
    "MED_RF_03": "CRITICAL",
    "MED_FC_01": "CRITICAL",
    "MED_PF_01": "HIGH",
    "MED_PF_02": "HIGH",
    "MED_NC_01": "HIGH",
    "MED_CK_01": "HIGH",
    "MED_RF_01": "CRITICAL",
    "MED_RF_02": "CRITICAL",
    "MED_VS_01": "HIGH",
    "MED_VS_02": "HIGH",
    "MED_VS_03": "HIGH",
    "MED_NT_01": "HIGH",
    "MED_IF_01": "INFO",
    "MED_IF_02": "INFO",
}

# Maps drift field path to bug code + one-liner
_BUG_MAP = {
    "/confidence_score":      ("BUG-M01", "Confidence calibration regression — model below clinical threshold"),
    "/dosage_mg":             ("BUG-M02", "5× dosage overdose — unit normalisation fault"),
    "/weight_based_dose":     ("BUG-M02", "Dose-calculation mode changed incorrectly"),
    "/risk_level":            ("BUG-M03", "Patient risk downgraded — classifier threshold regression"),
    "/risk_score":            ("BUG-M03", "Risk score regression"),
    "/triage_priority":       ("BUG-M04", "FSM routing bug — STEMI patient sent to DELAYED queue"),
    "/interaction_severity":  ("BUG-M05", "Severity lookup table corrupted — CONTRAINDICATED→MINOR"),
    "/mechanism":             ("BUG-M05", "Interaction description changed"),
    "/primary_diagnosis":     ("BUG-M01", "UNKNOWN label surfaced — confidence gate bypassed"),
    "/icd10_code":            ("BUG-M01", "ICD-10 code changed to unspecified"),
    "/raw_logits":            ("BUG-M06", "Internal logits leaked — HIPAA risk"),
    "/recommendation":        ("BUG-M01", "Recommendation degraded — physician escalation removed"),
    "/suspected_condition":   ("BUG-M04", "Suspected condition changed"),
    "/model_version":         ("BUG-M01", "Unvalidated model version deployed"),
}

def _hr(c: str = "─") -> None:
    print(c * W)

def _section(title: str, char: str = "═") -> None:
    print()
    print(char * W)
    print(f"  {title}")
    print(char * W)

def _rust_active() -> bool:
    try:
        import axiom_core  # noqa: F401
        return True
    except ImportError:
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # ── Banner ────────────────────────────────────────────────────────────────
    print()
    print("╔" + "═" * (W - 2) + "╗")
    print("║" + "  AXIOM RUNTIME  ×  MediAssist AI  —  Clinical Safety Validation".center(W - 2) + "║")
    print("║" + "  Detecting patient-safety regressions before production go-live".center(W - 2) + "║")
    print("╚" + "═" * (W - 2) + "╝")
    print(f"""
  Organisation  : Regional Hospital Network — 14 clinical sites
  System        : MediAssist AI  — Clinical Decision Support (SaMD Class II)
  Daily volume  : ≈ 10 000 patient interactions / day at full scale
  Regulatory    : FDA SaMD · EU AI Act Art. 14 · HIPAA §164.502 · JCAHO

  Stable build  : v1.4       (clinically validated — in production)
  Candidate     : v2.0-rc1   (release candidate — under evaluation)
  Rules file    : examples/medical_ai_rules.json  (15 rules — all 9 types)
""")
    _hr("═")

    rules_path = _EXAMPLES / "medical_ai_rules.json"
    engine     = RulesEngine.from_file(rules_path)
    stable_c   = TestClient(stable_app,    raise_server_exceptions=False)
    cand_c     = TestClient(candidate_app, raise_server_exceptions=False)

    # ── Phase 1 — Baseline Capture ────────────────────────────────────────────
    _section("Phase 1 — Baseline Capture  (v1.4 Production — clinically validated)", "═")

    cap = SessionCapture(stable_c)

    cap.post(
        "/api/v1/diagnosis",
        {"symptoms": ["polyuria", "polydipsia", "fatigue", "blurred_vision"],
         "age": 54, "bmi": 31.2, "fasting_glucose_mmol": 8.4},
        label="Diagnosis — suspected Type 2 DM",
    )
    cap.post(
        "/api/v1/dosage/recommend",
        {"drug": "Metformin", "weight_kg": 84.0, "egfr": 72, "indication": "T2DM"},
        label="Dosage — Metformin initiation",
    )
    cap.post(
        "/api/v1/patient/risk",
        {"troponin_ug_l": 1.8, "st_change_mm": -2.1,
         "symptoms": ["chest_pain", "diaphoresis", "radiation_left_arm"],
         "age": 61, "history": ["HTN", "hyperlipidaemia"]},
        label="Risk stratification — ACS presentation",
    )
    cap.post(
        "/api/v1/drug-interactions/check",
        {"drug_a": "Warfarin", "drug_b": "Aspirin",
         "patient_id": "PT-8821", "indication_a": "AF", "indication_b": "antiplatelet"},
        label="Drug interaction — Warfarin + Aspirin",
    )
    cap.post(
        "/api/v1/triage",
        {"presenting_complaint": "chest_pain_radiating_left_arm",
         "vitals": {"bp_systolic": 88, "hr": 118, "spo2": 94},
         "ecg_finding": "ST_elevation_V2_V5",
         "symptom_onset_min": 40},
        label="Emergency triage — probable STEMI",
    )

    print(f"\n  {len(cap.records)} clinical sessions captured from v1.4 baseline:\n")
    _hr()
    for rec in cap.records:
        b = rec.expected_body
        tags = []
        for f in ("confidence_score", "risk_level", "triage_priority",
                  "dosage_mg", "interaction_severity", "primary_diagnosis"):
            if f in b:
                tags.append(f"{f}={b[f]!r}")
        print(f"  {rec.method:<4}  {rec.uri}")
        print(f"        {' | '.join(tags[:4])}")
        if len(tags) > 4:
            print(f"        {' | '.join(tags[4:])}")
    _hr()

    # ── Phase 2 — Candidate Replay ────────────────────────────────────────────
    _section("Phase 2 — Candidate Replay  (v2.0-rc1 — under clinical evaluation)", "═")

    print("\n  Replaying 5 clinical sessions against v2.0-rc1…\n")
    t0        = time.perf_counter()
    reports   = replay_session(cap.records, cand_c)
    elapsed   = (time.perf_counter() - t0) * 1_000
    evaluated = [engine.evaluate(r) for r in reports]
    rust_tag  = "Rust extension active" if _rust_active() else "Python fallback"
    print(f"  Completed in {elapsed:.1f} ms  ({rust_tag})")

    # ── Phase 3 — Regression detail ───────────────────────────────────────────
    _section("Phase 3 — Clinical Regression Report  (per endpoint)", "═")

    totals = {"strict": 0, "semantic": 0, "drift": 0, "failed": 0,
              "violations": 0, "critical_viol": 0}

    for rec, rpt, ev in zip(cap.records, reports, evaluated):
        v = ev.effective_verdict
        print()
        _hr("─")
        print(f"  {VERDICT_BADGE[v]}  {rec.method}  {rec.uri}")
        print(f"  Clinical context : {rec.label}")
        _hr("─")

        if v is Verdict.REPRODUCIBLE_STRICT:
            totals["strict"] += 1
            print("  ✅  Response byte-identical — no degradation detected.")

        elif v is Verdict.REPRODUCIBLE_SEMANTIC:
            totals["semantic"] += 1
            print("  🟡  Only non-semantic fields differ — suppressed by ignore rules.")

        elif v is Verdict.DRIFT_DETECTED:
            totals["drift"] += 1

        elif v is Verdict.FAILED_TO_REPLAY:
            totals["failed"] += 1
            print(f"  ❌  {rpt.summary}")

        # Suppressed drift
        surviving_paths = {d.path for d in ev.surviving_drift}
        suppressed = [d for d in rpt.drift if d.path not in surviving_paths]
        if suppressed:
            print(f"\n  Suppressed (ignored) fields ({len(suppressed)}):")
            for d in suppressed:
                print(f"    {d.path:<30} → ignored (non-semantic)")

        # Surviving drift — annotated with bug codes
        if ev.surviving_drift:
            print(f"\n  Drift detected ({len(ev.surviving_drift)} field(s)):  "
                  f"[original → candidate]")
            for d in ev.surviving_drift:
                bug_code, bug_desc = _BUG_MAP.get(d.path, ("", ""))
                tag = f"  [{bug_code}]  {bug_desc}" if bug_code else ""
                orig  = d.original[:22].ljust(24) if len(d.original) > 22 else d.original.ljust(24)
                repl  = d.replayed[:28] if len(d.replayed) > 28 else d.replayed
                print(f"    {d.path:<30}  {orig} →  {repl}{tag}")

        # Rule violations
        if ev.violations:
            viol_count = len(ev.violations)
            crit_count = sum(
                1 for viol in ev.violations
                if SEVERITY_LABEL.get(viol.rule_id, "") == "CRITICAL"
            )
            totals["violations"]      += viol_count
            totals["critical_viol"]   += crit_count
            print(f"\n  Clinical rule violations ({viol_count}):  "
                  f"[{crit_count} CRITICAL]")
            for viol in ev.violations:
                sev = SEVERITY_LABEL.get(viol.rule_id, "INFO")
                print(f"    [{viol.rule_id}]  [{sev:<8}]  {viol.detail}")

    # ── Phase 4 — Executive Summary ───────────────────────────────────────────
    _section("Phase 4 — Executive Summary & Safety Analysis", "═")

    n        = len(reports)
    reg_rate = (totals["drift"] + totals["failed"]) / n * 100 if n else 0.0

    print(f"""
  ┌────────────────────────────────────────┐
  │  DEPLOYMENT READINESS SCORECARD        │
  ├────────────────────────────────────────┤
  │  Endpoints evaluated      {n:<3}           │
  │  ✅  Strict pass          {totals['strict']:<3}           │
  │  🟡  Semantic pass        {totals['semantic']:<3}           │
  │  🔴  Regression detected  {totals['drift']:<3}           │
  │  💀  Endpoint failure     {totals['failed']:<3}           │
  │  ─────────────────────────────────     │
  │  Total rule violations    {totals['violations']:<3}           │
  │  of which CRITICAL        {totals['critical_viol']:<3}           │
  │  Regression rate          {reg_rate:.0f} %          │
  └────────────────────────────────────────┘
""")

    _hr("─")
    print("  Bug-by-bug safety analysis")
    _hr("─")
    print("""
  BUG-M01  ▸ [CRITICAL]  Confidence calibration regression — all 5 endpoints
  ─────────────────────────────────────────────────────────────────────────────
  Rule     : MED_CR_01 — value_in_range: confidence_score ∈ [0.70, 1.0]
  Observed : 0.94 → 0.41  (diagnosis)  |  0.91 → 0.38  (dosage)
             0.89 → 0.35  (risk)       |  0.97 → 0.34  (interactions)
             0.96 → 0.29  (triage)
  Root cause: Model diag-v3.0-beta deployed without clinical calibration
              validation. All 5 sub-tasks produce confidence below 0.70.
  Impact   : Every AI output from v2 falls below the clinical decision
             threshold. At 10 000 patients/day this means every AI-assisted
             decision is unreliable. Clinicians cannot distinguish high- from
             low-confidence outputs → systematic over-reliance on bad guidance.
  Regulatory: FDA SaMD guidance: ML model performance must be validated on
             representative clinical data before deployment. Failure is a
             §513(f)(2) substantial risk determinant.
  Fix      : Do NOT deploy diag-v3.0-beta. Retrain with calibrated
             temperature scaling on held-out clinical validation set.
             Minimum clinical threshold: 0.80 on all five sub-tasks.

  BUG-M02  ▸ [CRITICAL]  Metformin dosage 500 mg → 2 500 mg  (5× overdose)
  ─────────────────────────────────────────────────────────────────────────────
  Rule     : MED_CR_02 — value_in_range: dosage_mg ∈ [0.1, 1000.0]
             MED_NT_01 — numeric_tolerance: dosage_mg tolerance 0.1 mg
  Observed : dosage_mg = 2 500.0  (BNF maximum for Metformin: 3 000 mg/day)
             At twice-daily, this prescribes 5 000 mg/day — 2.5× the BNF cap.
  Root cause: Weight-based dose calculation introduced in dosage-v3.1-rc
              applies body weight (kg) as a dose multiplier instead of
              using the standard fixed starting dose of 500 mg.
              Patient weight 84 kg × 29.76 mg/kg ≈ 2 500 mg.
  Impact   : Immediate toxicity risk — Metformin overdose causes lactic
             acidosis (mortality ~45 % if untreated). GI haemorrhage,
             acute kidney injury secondary to dehydration.
  Regulatory: FDA MedWatch reportable. Potential criminal liability for
             prescribing clinician who follows AI recommendation.
  Fix      : Revert dose-calculation service to fixed-start-dose logic.
             Add integration test: dosage_mg must be 500 for standard T2DM
             initiation regardless of body weight.

  BUG-M03  ▸ [CRITICAL]  ACS patient risk downgrade — HIGH → LOW
  ─────────────────────────────────────────────────────────────────────────────
  Rule     : MED_VS_01 — value_in_set: risk_level not wrong value per se
             (drift caught by probe; LOW is a valid FSM state, just wrong here)
  Observed : risk_level HIGH → LOW  |  risk_score 0.78 → 0.21
             triage_priority URGENT → NON_URGENT
             Dominant factors unchanged: elevated troponin, ST depression
  Root cause: Classifier threshold regression in risk-v2.0-rc. The model
              correctly identifies troponin elevation and ST depression as
              high-weight features but the output layer threshold was shifted
              from 0.5 to 0.85 during quantisation — compressing all scores
              below the HIGH boundary.
  Impact   : Patient presenting with probable ACS (Acute Coronary Syndrome)
             is classified LOW risk and routed to non-urgent outpatient
             follow-up. Without immediate intervention:
               — Myocardial infarction: untreated within 6 hours in ~35 %.
               — Sudden cardiac death: 8 % within 30 days (GRACE registry).
             Any patient harmed would be a JCAHO Sentinel Event.
  Fix      : Re-calibrate risk-v2.0-rc threshold on ACS validation cohort.
             Acceptance criterion: sensitivity ≥ 0.98 on CRITICAL/HIGH class.

  BUG-M04  ▸ [CRITICAL]  STEMI patient: triage_priority IMMEDIATE → DELAYED
  ─────────────────────────────────────────────────────────────────────────────
  Rules    : MED_FC_01 — field_consistency:
               when risk_level = CRITICAL → triage_priority ∈ {IMMEDIATE, URGENT}
             Observed: risk_level = "CRITICAL" (CORRECT) + triage_priority = "DELAYED"
             → This specific combination is lethal and is caught by field_consistency.
  Observed : The model correctly identifies a STEMI (risk_level = CRITICAL).
             But the FSM routing layer emits DELAYED for all patients whose
             risk encoder output exceeds 0.90 due to a signed/unsigned
             integer overflow in the quantised triage-v2.1-rc model.
             Result: the system is self-contradictory — it knows the patient
             is critical but tells staff to make them wait.
  Impact   : ACC/AHA STEMI guideline: door-to-balloon time ≤ 90 minutes.
             DELAYED triage adds ≥ 45 minutes before a cardiologist sees
             the patient.
             Mortality increase per NEJM (Nallamothu et al. 2007):
               — Every 30 min delay → +7.5 % 30-day mortality.
               — 45-min delay → +11.3 % excess mortality.
             At 10 000 patients/day, even 1 STEMI/day missed = tens of
             preventable deaths per month.
  Fix      : Fix integer overflow in triage encoder (uint8 → int16).
             Add invariant test: risk_level=CRITICAL must never produce
             triage_priority DELAYED or NON_URGENT.

  BUG-M05  ▸ [HIGH]  Drug interaction CONTRAINDICATED → MINOR (Warfarin + Aspirin)
  ─────────────────────────────────────────────────────────────────────────────
  Rule     : MED_VS_03 — value_in_set (drift; MINOR is valid, just wrong here)
  Observed : interaction_severity "CONTRAINDICATED" → "MINOR"
             Mechanism description sanitised — bleeding risk language removed.
  Root cause: Severity lookup table corrupted during interactions v4.2→v4.3
              schema migration. Drug pair hash collision maps Warfarin+Aspirin
              to the Paracetamol+Aspirin entry (MINOR interaction).
  Impact   : Prescribing clinicians will co-administer Warfarin + Aspirin.
             Combined anticoagulant + antiplatelet therapy:
               — Major GI bleed risk: ×3.5 vs monotherapy (Hylek et al. 2001)
               — Intracranial haemorrhage risk: ×5.8 (ISTH registry)
             WHO Model Formulary marks this pair as absolutely contraindicated
             except in specific supervised post-ACS settings.
  Fix      : Restore interactions v4.2 lookup table. Run regression tests
             against full WHO interaction contraindication list (173 pairs).

  BUG-M06  ▸ [HIGH]   raw_logits field leaked in diagnosis response
  ─────────────────────────────────────────────────────────────────────────────
  Rule     : MED_PF_01 — prohibited_field: raw_logits
  Observed : "raw_logits": [0.41, 0.31, 0.28] present in response body
  Root cause: Debug serialisation flag left enabled in diag-v3.0-beta.
              The serialiser now includes the full softmax output vector.
  Impact   : If probability vectors encode patient-specific training data
             (membership inference attack), this is a HIPAA §164.502 breach.
             Even without PHI, exposing model internals allows adversaries
             to craft adversarial inputs that systematically fool the model.
  Regulatory: OCR HIPAA enforcement: fines up to $1.9M per violation category.
  Fix      : Set DEBUG_SERIALISE=False in production config.
             Add automated test: response schema must not contain raw_logits.
""")

    _hr("═")
    print()
    if totals["drift"] + totals["failed"] > 0:
        print("  🚫  CLINICAL DEPLOYMENT BLOCKED")
        print()
        print(f"      MediAssist AI v2.0-rc1 has regressions on "
              f"{totals['drift'] + totals['failed']}/{n} endpoints")
        print(f"      and {totals['violations']} rule violations "
              f"({totals['critical_viol']} CRITICAL).")
        print()
        print("      NO patient interaction must occur with v2.0-rc1.")
        print("      Mandatory actions before re-evaluation:")
        print("        1. Fix BUG-M01 — recalibrate all 5 model sub-tasks")
        print("        2. Fix BUG-M02 — revert dose-calculation to fixed start dose")
        print("        3. Fix BUG-M03 — recalibrate risk classifier threshold")
        print("        4. Fix BUG-M04 — fix triage FSM integer overflow")
        print("        5. Fix BUG-M05 — restore interactions lookup table v4.2")
        print("        6. Fix BUG-M06 — disable debug serialisation flag")
        print()
        print("      Re-submit for Clinical Safety Board review after all fixes.")
        print("      Estimated re-validation: 3 clinical test cycles × 48 h.")
    else:
        print("  ✅  CLINICAL DEPLOYMENT APPROVED — all safety checks pass.")
    print()
    _hr("═")
    print()


if __name__ == "__main__":
    main()
