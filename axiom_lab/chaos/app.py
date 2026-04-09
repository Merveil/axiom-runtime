"""
Axiom Lab — Chaos App

Controlled fault injection mapped to five endpoints:

  GET  /slow       → chaos: deliberate pause (slow_ms); stable: instant OK
  POST /flaky      → chaos: error_rate % → 500; stable: always 200
  GET  /empty      → chaos: HTTP 200 with empty body; stable: {status: ok}
  GET  /malformed  → chaos: HTTP 200 with non-JSON plain text; stable: {status: ok}
  GET  /down       → chaos: always 503; stable: {status: ok}

Factory:
    create_chaos_app(chaos_enabled=False)  → stable (golden capture)
    create_chaos_app(chaos_enabled=True)   → chaos injection

Tuning:
    slow_ms          ms to sleep on /slow when chaos_enabled=True (default 200)
    flaky_error_rate fraction of /flaky calls that return 500 (default 0.40)
    rng              optionally seeded Random for reproducible tests
"""
from __future__ import annotations

import random
import time

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response

_STABLE_BODY = {"status": "ok"}


def create_chaos_app(
    *,
    chaos_enabled: bool  = False,
    slow_ms:       int   = 200,
    flaky_error_rate: float = 0.40,
    rng: random.Random | None = None,
) -> FastAPI:
    app  = FastAPI(title="Axiom Lab — Chaos")
    _rng = rng or random.Random(0)

    @app.get("/slow")
    def slow():
        if chaos_enabled:
            time.sleep(slow_ms / 1000.0)
        return {"status": "ok", "delay_ms": slow_ms if chaos_enabled else 0}

    @app.post("/flaky")
    def flaky(body: dict = {}):
        if chaos_enabled and _rng.random() < flaky_error_rate:
            return Response(
                content='{"error": "internal server error"}',
                status_code=500,
                media_type="application/json",
            )
        return {"status": "ok", "processed": True}

    @app.get("/empty")
    def empty():
        if chaos_enabled:
            return Response(content=b"", status_code=200)
        return _STABLE_BODY

    @app.get("/malformed")
    def malformed():
        if chaos_enabled:
            return PlainTextResponse(content="I am not JSON", status_code=200)
        return _STABLE_BODY

    @app.get("/down")
    def down():
        if chaos_enabled:
            return Response(
                content='{"error": "service unavailable"}',
                status_code=503,
                media_type="application/json",
            )
        return _STABLE_BODY

    return app
