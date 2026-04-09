# MediAssist AI — Axiom Clinical Safety Demo

> **One sentence:** Axiom caught six clinical regressions — including a 5× overdose and a missed STEMI — before a single patient was exposed to the candidate build.

---

## What it does

Simulates a mandatory clinical safety gate for a hospital network evaluating MediAssist AI v2.0-rc1 across 14 sites (≈ 10 000 patient interactions/day). Runs in four phases:

1. **Capture** a golden baseline from v1.4 (5 validated clinical call patterns)
2. **Replay** all sessions against v2.0-rc1 using Axiom Runtime
3. **Evaluate** every response against 15 clinical safety rules (all 9 Axiom rule types)
4. **Report** per-endpoint regressions with FDA/HIPAA/JCAHO regulatory attribution

Six regressions are injected into v2.0-rc1, spanning every clinical sub-system:

| Bug | What breaks | Severity |
|---|---|---|
| BUG-M01 | Confidence miscalibrated below 0.70 on all 5 endpoints | 🔴 CRITICAL |
| BUG-M02 | Metformin 500 mg → 2500 mg (5× overdose, lactic acidosis risk) | 🔴 CRITICAL |
| BUG-M03 | ACS patient HIGH → LOW risk (missed cardiac event) | 🔴 CRITICAL |
| BUG-M04 | STEMI patient IMMEDIATE → DELAYED triage (door-to-balloon missed) | 🔴 CRITICAL |
| BUG-M05 | Drug interaction CONTRAINDICATED → MINOR (bleeding risk) | ⚠️ HIGH |
| BUG-M06 | `raw_logits` leaked + `requires_human_review` dropped (HIPAA) | ⚠️ HIGH |

---

## How to run

```bash
python examples/medical_ai_simulation.py
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
║   AXIOM RUNTIME  ×  MediAssist AI  —  Clinical Safety Validation    ║
╚══════════════════════════════════════════════════════════════════════╝

Phase 1 — Baseline Capture  (v1.4 Production — clinically validated)
  5 clinical sessions captured.

Phase 2 — Candidate Replay  (v2.0-rc1 — under evaluation)
  Completed in ~42 ms  (Rust extension active)

Phase 3 — Clinical Regression Report  (per endpoint)
  🔴 REGRESSION DETECTED   POST  /api/v1/diagnosis           (5 violations)
  🔴 REGRESSION DETECTED   POST  /api/v1/dosage/recommend    (2 violations — 5× overdose)
  🔴 REGRESSION DETECTED   POST  /api/v1/patient/risk        (ACS missed)
  🔴 REGRESSION DETECTED   POST  /api/v1/drug-interactions/check
  🔴 REGRESSION DETECTED   POST  /api/v1/triage              (STEMI → DELAYED)

Phase 4 — Executive Summary
  Regression rate     : 100 %
  Rule violations     : 13  (8 CRITICAL)
  Verdict             : 🚫 DEPLOYMENT BLOCKED — patient safety incident risk
```

Full pre-rendered output with regulatory analysis: [`examples/medical_ai_report.md`](../medical_ai_report.md)

---

## Why it matters

Clinical AI systems cannot be validated by traditional integration tests alone — the regressions that matter most are _behavioural_ and _contextual_. A dosage endpoint that returns HTTP 200 with a valid JSON schema can still prescribe a lethal dose.

Axiom captures the _semantic contract_ of the production system and validates every candidate response against it:

- `value_in_range` catches the 5× Metformin overdose
- `field_consistency` catches the lethal CRITICAL+DELAYED triage combination that no individual field check would find
- `prohibited_field` catches the HIPAA data leak before it reaches external callers
- `not_contains_keyword` catches "UNKNOWN" diagnoses surfacing to patients

The rules file (`examples/medical_ai_rules.json`) is the only configuration needed — 15 declarative rules covering all clinical safety dimensions, readable by engineers and clinical safety officers alike.

**The 13 violations found in 42 ms would have caused:**
- Systematic miscalibration across all 5 clinical tasks → over-reliance on bad AI guidance
- Metformin overdose reported to FDA MedWatch
- ACS patient cardiac arrest → JCAHO Sentinel Event
- STEMI mortality +11.3% per missed door-to-balloon window (NEJM 2007)
- HIPAA investigation (raw_logits field) → potential $1.9M fine per violation
- EU AI Act Art. 14 breach (human oversight flag absent) → regulatory action

---

## Files

| File | Purpose |
|---|---|
| `examples/medical_ai_simulation.py` | 4-phase clinical safety simulation |
| `examples/medical_ai_rules.json` | 15 canonical clinical safety rules |
| `examples/medical_ai_report.md` | Pre-rendered executive + regulatory report |
| `examples/medical_ai_demo/stable.py` | MediAssist AI v1.4 — validated production baseline |
| `examples/medical_ai_demo/candidate.py` | v2.0-rc1 — 6 safety regressions injected |
