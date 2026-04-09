"""
Axiom Lab — API Demo

Four endpoints covering every Axiom verdict type in a single session:

  GET  /health  → deterministic; always REPRODUCIBLE_STRICT
  POST /echo    → mirrors input + adds request_id; REPRODUCIBLE_SEMANTIC
  POST /drift   → stable mode: fixed score + tag; REPRODUCIBLE_SEMANTIC
                  drift mode:  variable score + tag; DRIFT_DETECTED
  POST /boom    → always raises; FAILED_TO_REPLAY

Factory:
    create_api_demo_app(drift_mode=False)  → stable (golden capture)
    create_api_demo_app(drift_mode=True, rng=random.Random(42))  → drifted replay
"""
from __future__ import annotations

import random
import uuid
from typing import Any

from fastapi import FastAPI

_STABLE_SCORE = 0.92
_STABLE_TAG   = "stable"
_DRIFT_TAGS   = ["alpha", "beta", "gamma", "delta"]


def create_api_demo_app(
    *,
    drift_mode: bool = False,
    rng: random.Random | None = None,
) -> FastAPI:
    app  = FastAPI(title="Axiom Lab — API Demo")
    _rng = rng or random.Random(0)

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "1.0.0"}

    @app.post("/echo")
    def echo(body: dict[str, Any] = {}):
        return {"request_id": str(uuid.uuid4()), **body}

    @app.post("/drift")
    def drift(body: dict[str, Any] = {}):
        if drift_mode:
            score = round(0.50 + _rng.random() * 0.49, 4)  # varies 0.50–0.99
            tag   = _rng.choice(_DRIFT_TAGS)
        else:
            score = _STABLE_SCORE
            tag   = _STABLE_TAG
        return {
            "request_id": str(uuid.uuid4()),
            "input_echo": body,
            "score":      score,
            "tag":        tag,
            "processed":  True,
        }

    @app.post("/boom")
    def boom(body: dict[str, Any] = {}):
        raise RuntimeError("Simulated server error — always fails")

    return app
