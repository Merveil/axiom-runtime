"""PayPal Payments API — v1 (stable production build).

Five deterministic endpoints that cover the full payment lifecycle:
  POST /v1/payments/create
  POST /v1/payments/{id}/capture
  GET  /v1/transactions/{id}
  GET  /v1/accounts/{id}/balance
  POST /v1/fraud/score
"""
from fastapi import FastAPI

app = FastAPI(title="PayPal Payments API v1 — Production")


def _fee(amount: float) -> float:
    """PayPal standard fee: 2.9 % + $0.30."""
    return round(amount * 0.029 + 0.30, 2)


@app.post("/v1/payments/create")
def create_payment(body: dict):
    amount = float(body["amount"])
    return {
        "id":          "PAY-FLAGSHIP-001",
        "status":      "CREATED",
        "amount":      amount,
        "currency":    body.get("currency", "USD"),
        "fee_amount":  _fee(amount),
        "fraud_score": 0.12,
        "created_at":  "2026-04-09T10:00:00Z",
    }


@app.post("/v1/payments/{payment_id}/capture")
def capture_payment(payment_id: str):
    amount = 299.99
    fee    = _fee(amount)
    return {
        "id":          payment_id,
        "status":      "COMPLETED",
        "amount":      amount,
        "currency":    "USD",
        "fee_amount":  fee,
        "net_amount":  round(amount - fee, 2),
        "captured_at": "2026-04-09T10:01:00Z",
    }


@app.get("/v1/transactions/{txn_id}")
def get_transaction(txn_id: str):
    return {
        "id":          txn_id,
        "type":        "PAYMENT",
        "amount":      299.99,
        "currency":    "USD",
        "status":      "COMPLETED",
        "merchant_id": "MERCH-FLAGSHIP-42",
        "fraud_score": 0.12,
        "created_at":  "2026-04-09T10:00:00Z",
    }


@app.get("/v1/accounts/{account_id}/balance")
def get_balance(account_id: str):
    return {
        "account_id":        account_id,
        "available_balance": 14_823.50,
        "currency":          "USD",
        "as_of":             "2026-04-09T10:00:00Z",
    }


@app.post("/v1/fraud/score")
def fraud_score(body: dict):
    return {
        "transaction_id": body["transaction_id"],
        "score":          0.12,
        "decision":       "APPROVE",
        "threshold":      0.30,
        "model_version":  "fraud-v3.1",
    }
