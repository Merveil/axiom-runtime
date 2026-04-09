"""PayPal Payments API — v2 (candidate build — contains 5 regressions).

Bug registry
────────────
BUG-01  fee_amount / net_amount dropped from payment responses.
        Cause : Schema migration (v1→v2) removed field from PaymentResponse model.
        Impact: Merchants cannot verify net settlement amounts.

BUG-02  fraud_score inflated: 0.12 → 0.87 on every transaction.
        Cause : fraud-v4.0 model deployed without threshold recalibration.
        Impact: 100 % of transactions flagged for manual review → ops overload.

BUG-03  Payment capture state machine regression: COMPLETED → PROCESSING.
        Cause : capture() endpoint missing final commit() after funds settlement.
        Impact: Payments appear stuck; triggers chargebacks.

BUG-04  Currency not normalised to uppercase: "USD" → "usd".
        Cause : Normalisation middleware removed during v2 API-gateway refactor.
        Impact: Downstream SWIFT / forex routing fails (case-sensitive).

BUG-05  Amount float-precision loss on capture: 299.99 → 300.0.
        Cause : Settlement DB column changed DECIMAL(12,4) → FLOAT.
        Impact: 1-cent discrepancies break reconciliation audits, PCI-DSS.
"""
from fastapi import FastAPI

app = FastAPI(title="PayPal Payments API v2 — Candidate (BUGGY)")


@app.post("/v1/payments/create")
def create_payment(body: dict):
    amount = float(body["amount"])
    return {
        "id":          "PAY-FLAGSHIP-001",
        "status":      "CREATED",
        "amount":      amount,
        "currency":    body.get("currency", "USD"),
        # BUG-01: fee_amount omitted (schema migration removed it)
        "fraud_score": 0.87,                    # BUG-02: inflated score
        "created_at":  "2026-04-09T10:00:01Z",  # 1-second timestamp drift
    }


@app.post("/v1/payments/{payment_id}/capture")
def capture_payment(payment_id: str):
    return {
        "id":          payment_id,
        "status":      "PROCESSING",             # BUG-03: should be COMPLETED
        "amount":      300.0,                    # BUG-05: float32 precision loss
        "currency":    "usd",                    # BUG-04: lowercase
        # BUG-01: fee_amount and net_amount absent
        "captured_at": "2026-04-09T10:01:01Z",
    }


@app.get("/v1/transactions/{txn_id}")
def get_transaction(txn_id: str):
    return {
        "id":          txn_id,
        "type":        "PAYMENT",
        "amount":      299.99,
        "currency":    "usd",                    # BUG-04
        "status":      "PROCESSING",             # BUG-03
        "merchant_id": "MERCH-FLAGSHIP-42",
        "fraud_score": 0.87,                     # BUG-02
        "created_at":  "2026-04-09T10:00:01Z",
    }


@app.get("/v1/accounts/{account_id}/balance")
def get_balance(account_id: str):
    return {
        "account_id":        account_id,
        "available_balance": 14_823.50,
        "currency":          "usd",              # BUG-04
        "as_of":             "2026-04-09T10:00:01Z",
    }


@app.post("/v1/fraud/score")
def fraud_score(body: dict):
    return {
        "transaction_id": body["transaction_id"],
        "score":          0.87,                  # BUG-02
        "decision":       "REVIEW",              # consequence of BUG-02
        "threshold":      0.30,
        "model_version":  "fraud-v4.0",          # uncalibrated model
    }
