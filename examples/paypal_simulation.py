"""
PayPal × Axiom Runtime — Deployment Validation Simulation
==========================================================

Scenario
--------
PayPal is preparing to promote v2 of its Payments API to production.
Before the release, the team runs Axiom Runtime to:

  Phase 1 — Capture a golden baseline against v1 (stable production)
  Phase 2 — Replay all captured sessions against v2 (candidate build)
  Phase 3 — Apply 12 PayPal business rules (all 9 Axiom rule types)
  Phase 4 — Executive report + root-cause analysis

Run
---
    python examples/paypal_simulation.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# Make axiom_lab and the paypal_demo package importable
_ROOT     = Path(__file__).parent.parent
_EXAMPLES = Path(__file__).parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_EXAMPLES))

from fastapi.testclient import TestClient

from paypal_demo.stable    import app as stable_app
from paypal_demo.candidate import app as candidate_app

from axiom_lab.probe        import SessionCapture, replay_session, Verdict
from axiom_lab.rules_engine import RulesEngine

# ── Cosmetics ─────────────────────────────────────────────────────────────────

W = 72

VERDICT_ICON = {
    Verdict.REPRODUCIBLE_STRICT:   "✅  STRICT           ",
    Verdict.REPRODUCIBLE_SEMANTIC: "🟡  SEMANTIC         ",
    Verdict.DRIFT_DETECTED:        "🔴  DRIFT DETECTED   ",
    Verdict.FAILED_TO_REPLAY:      "💀  FAILED TO REPLAY ",
}

# Maps a field name (without leading /) to the bug that causes it
_BUG = {
    "fee_amount":    "BUG-01",
    "net_amount":    "BUG-01",
    "fraud_score":   "BUG-02",
    "score":         "BUG-02",
    "decision":      "BUG-02",
    "model_version": "BUG-02",
    "status":        "BUG-03",
    "currency":      "BUG-04",
    "amount":        "BUG-05",
}


def _hr(c: str = "─") -> None:
    print(c * W)


def _section(title: str, char: str = "═") -> None:
    print()
    print(char * W)
    print(f"  {title}")
    print(char * W)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # Banner
    print()
    print("╔" + "═" * (W - 2) + "╗")
    print("║" + "  AXIOM RUNTIME  ×  PayPal  —  Deployment Validation".center(W - 2) + "║")
    print("║" + "  Detecting regressions before they reach production".center(W - 2) + "║")
    print("╚" + "═" * (W - 2) + "╝")
    print()
    print("  Context")
    print("  ───────")
    print("  PayPal is preparing to promote v2 of its Payments API to production.")
    print("  Objective: detect anomalies, reproduce bugs, validate the deployment.")
    print()
    print("  Rules file : examples/paypal_rules.json  (12 rules, all 9 types)")
    print("  Stable     : PayPal API v1  (production baseline)")
    print("  Candidate  : PayPal API v2  (release candidate — 5 regressions injected)")

    rules_path = _EXAMPLES / "paypal_rules.json"
    engine     = RulesEngine.from_file(rules_path)

    stable_c    = TestClient(stable_app,    raise_server_exceptions=False)
    candidate_c = TestClient(candidate_app, raise_server_exceptions=False)

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 1 — Baseline Capture
    # ──────────────────────────────────────────────────────────────────────────
    _section("Phase 1 — Baseline Capture  (v1 Production)", "═")

    cap = SessionCapture(stable_c)
    cap.post(
        "/v1/payments/create",
        {"amount": 299.99, "currency": "USD", "merchant_id": "MERCH-FLAGSHIP-42"},
        label="Create payment intent",
    )
    cap.post(
        "/v1/payments/PAY-FLAGSHIP-001/capture",
        {},
        label="Capture / settle payment",
    )
    cap.get(
        "/v1/transactions/TXN-FLAGSHIP-001",
        label="Fetch transaction record",
    )
    cap.get(
        "/v1/accounts/ACC-BUSINESS-42/balance",
        label="Account balance check",
    )
    cap.post(
        "/v1/fraud/score",
        {"transaction_id": "TXN-FLAGSHIP-001"},
        label="Fraud risk assessment",
    )

    print(f"\n  Captured {len(cap.records)} exchanges from stable v1:\n")
    _hr()
    for rec in cap.records:
        b     = rec.expected_body
        notes = []
        for fld in ("status", "amount", "currency", "fee_amount",
                    "net_amount", "fraud_score", "score", "decision", "available_balance"):
            if fld in b:
                notes.append(f"{fld}={b[fld]!r}")
        print(f"  {rec.method:<4}  {rec.uri}")
        print(f"        {' | '.join(notes[:5])}")
    _hr()

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 2 — Candidate Replay
    # ──────────────────────────────────────────────────────────────────────────
    _section("Phase 2 — Candidate Replay  (v2 Build — under test)", "═")

    print("\n  Replaying 5 sessions against v2 candidate…\n")
    t0        = time.perf_counter()
    reports   = replay_session(cap.records, candidate_c)
    elapsed   = (time.perf_counter() - t0) * 1_000
    evaluated = [engine.evaluate(r) for r in reports]
    print(f"  Done — {len(reports)} calls in {elapsed:.1f} ms  "
          f"(Rust extension: {'active' if _rust_active() else 'fallback'})")

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 3 — Per-endpoint regression detail
    # ──────────────────────────────────────────────────────────────────────────
    _section("Phase 3 — Regression Report  (per endpoint)", "═")

    totals = {"strict": 0, "semantic": 0, "drift": 0, "failed": 0, "viol": 0}

    for rec, rpt, ev in zip(cap.records, reports, evaluated):
        v = ev.effective_verdict
        print()
        _hr("─")
        print(f"  {VERDICT_ICON[v]}  {rec.method}  {rec.uri}")
        print(f"  Label : {rec.label}")
        _hr("─")

        if v is Verdict.REPRODUCIBLE_STRICT:
            totals["strict"] += 1
            print("  → Byte-identical — no issues detected.")

        elif v is Verdict.REPRODUCIBLE_SEMANTIC:
            totals["semantic"] += 1
            print("  → Only non-semantic fields differ (suppressed by rules).")

        elif v is Verdict.DRIFT_DETECTED:
            totals["drift"] += 1

        elif v is Verdict.FAILED_TO_REPLAY:
            totals["failed"] += 1
            print(f"  → {rpt.summary}")

        # Suppressed drift
        surviving_paths = {d.path for d in ev.surviving_drift}
        suppressed = [d for d in rpt.drift if d.path not in surviving_paths]
        if suppressed:
            print(f"\n  Suppressed by ignore rules ({len(suppressed)}):")
            for d in suppressed:
                print(f"    {d.path:<28}  suppressed — non-semantic timestamp")

        # Surviving drift
        if ev.surviving_drift:
            print(f"\n  Drift detected ({len(ev.surviving_drift)} field(s)):")
            for d in ev.surviving_drift:
                bug = _BUG.get(d.path.lstrip("/"), "")
                tag = f"  ← {bug}" if bug else ""
                print(f"    {d.path:<28}  {str(d.original):<16} →  {str(d.replayed)}{tag}")

        # Rule violations
        if ev.violations:
            totals["viol"] += len(ev.violations)
            print(f"\n  Rule violations ({len(ev.violations)}):")
            for viol in ev.violations:
                print(f"    [{viol.rule_id}]  {viol.detail}")
        else:
            if v not in (Verdict.REPRODUCIBLE_STRICT, Verdict.REPRODUCIBLE_SEMANTIC):
                print("\n  Rule violations: none (drift only, no content invariant broken)")

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 4 — Executive Summary + Root-Cause Analysis
    # ──────────────────────────────────────────────────────────────────────────
    _section("Phase 4 — Executive Summary", "═")

    n        = len(reports)
    reg_rate = (totals["drift"] + totals["failed"]) / n * 100 if n else 0.0

    print(f"""
  Endpoints tested    {n}
  ✅  Strict          {totals['strict']}
  🟡  Semantic        {totals['semantic']}
  🔴  Drift detected  {totals['drift']}
  💀  Failed replay   {totals['failed']}
  Rule violations     {totals['viol']}
  Regression rate     {reg_rate:.0f} %
""")

    _hr("─")
    print("  Root-cause analysis")
    _hr("─")
    print("""
  BUG-01  [CRITICAL]  fee_amount / net_amount absent from payment responses
    Rules : PP004 — required_field: fee_amount
    Cause : Schema migration (v1→v2) removed field from PaymentResponse model.
    Impact: Merchants cannot verify net settlement amounts → revenue leakage,
            failed reconciliation, potential regulatory fine.
    Fix   : Restore fee_amount and net_amount to PaymentResponseV2 schema.

  BUG-02  [CRITICAL]  fraud_score 0.12 → 0.87 across all transactions
    Rules : PP009 — value_in_range: fraud_score ∈ [0.0, 0.35]
            PP010 — contains_keyword: decision must contain "APPROVE"
    Cause : fraud-v4.0 model deployed without threshold recalibration on
            production data distribution. Every score lands above 0.35.
    Impact: 100 % of transactions flagged for manual review → operations
            overload; SLA breach; legitimate users blocked at checkout.
    Fix   : Rollback to fraud-v3.1 OR recalibrate fraud-v4.0 thresholds.

  BUG-03  [CRITICAL]  Payment capture stuck in PROCESSING instead of COMPLETED
    Rules : PP007 — value_in_set: status ∈ {CREATED,COMPLETED,FAILED,…}
    Cause : State machine missing final commit() after funds settlement.
    Impact: Merchants see payments hang; automatic chargebacks triggered;
            accounting records unreconciled indefinitely.
    Fix   : Add status = "COMPLETED" transition after settlement confirmation.

  BUG-04  [HIGH]      Currency lowercase: "USD" → "usd" on every endpoint
    Rules : PP008 — value_in_set: currency ∈ {USD,EUR,GBP,JPY,CAD,AUD}
    Cause : Currency normalisation middleware removed in v2 API-gateway refactor.
    Impact: SWIFT / ISO 4217 routing is case-sensitive. Downstream forex
            conversion and ledger systems reject lowercase currency codes.
    Fix   : Re-add .upper() to CurrencyResponseSerializer.

  BUG-05  [HIGH]      Amount precision loss: 299.99 → 300.0 on capture
    Rules : PP011 — numeric_tolerance: amount drift ≤ 0.001
    Cause : Settlement DB column changed from DECIMAL(12,4) to FLOAT.
            IEEE-754 cannot represent 299.99 exactly in 32-bit float.
    Impact: 1-cent discrepancy per transaction → reconciliation audits fail;
            PCI-DSS compliance breach if sustained at scale.
    Fix   : Revert column type to DECIMAL(12,4); add DB migration + test.
""")

    _hr("═")
    if totals["drift"] + totals["failed"] > 0:
        print()
        print("  🚫  DEPLOYMENT BLOCKED")
        print(f"      v2 has regressions on {totals['drift'] + totals['failed']}/{n} endpoints "
              f"and {totals['viol']} rule violations.")
        print("      Do NOT promote to production. Remediate BUG-01 through BUG-05 first.")
    else:
        print()
        print("  ✅  DEPLOYMENT APPROVED — all endpoints pass.")
    print()
    _hr("═")
    print()


def _rust_active() -> bool:
    try:
        import axiom_core  # noqa: F401
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    main()
