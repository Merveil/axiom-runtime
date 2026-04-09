"""
Axiom Shadow — FastAPI instrumentation middleware.

Attaches a low-overhead capture layer to any FastAPI application.
Every HTTP exchange is recorded into a ShadowEventStore.  The store
write is synchronous but stays sub-millisecond (SQLite WAL mode) so it
never meaningfully impacts the real-user response path.

Usage
-----
    from fastapi import FastAPI
    from axiom_lab.shadow import instrument_fastapi, ShadowEventStore

    app   = FastAPI()
    store = instrument_fastapi(app)          # in-memory by default

    # Or attach an explicit store (e.g. persistent file):
    store = ShadowEventStore("build/shadow.db", max_events=5_000)
    instrument_fastapi(app, store, app_name="my-service")

Design constraints (shadow mode policy)
----------------------------------------
  - Capture is LOCAL only: middleware records only exchanges that pass
    through this process.  No external traffic is touched.
  - Non-blocking contract: the store write completes before the response
    is returned, but is fast enough (~0.1 ms) not to require async
    fire-and-forget complexity.
  - Response fidelity: the original response bytes are forwarded
    unchanged to the caller.
  - Configurable exclusions: health-check / metrics / docs paths are
    excluded by default.
"""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Any

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from axiom_lab.shadow.event_store import ShadowEvent, ShadowEventStore, _make_event

logger = logging.getLogger(__name__)

# Paths that are never worth capturing (infrastructure endpoints)
_DEFAULT_EXCLUDE: frozenset[str] = frozenset({
    "/healthz", "/readyz", "/metrics",
    "/docs", "/redoc", "/openapi.json",
    "/favicon.ico",
})


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class _ShadowMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that records each exchange into a ShadowEventStore."""

    def __init__(
        self,
        app:             ASGIApp,
        store:           ShadowEventStore,
        app_name:        str,
        exclude_paths:   frozenset[str],
        sample_rate:     float,
        allowlist:       frozenset[str] | None,
        capture_methods: frozenset[str] | None,
    ) -> None:
        super().__init__(app)
        self._store           = store
        self._app_name        = app_name
        self._exclude         = exclude_paths
        self._sample_rate     = sample_rate
        self._allowlist       = allowlist
        self._capture_methods = capture_methods

    async def dispatch(self, request: Request, call_next) -> Response:
        path   = request.url.path
        method = request.method.upper()

        # ── Apply capture policy ───────────────────────────────────────────
        if path in self._exclude:
            # Infrastructure paths: never count as policy-ignored
            return await call_next(request)

        should_capture = True
        if self._allowlist is not None and path not in self._allowlist:
            should_capture = False
        elif self._capture_methods is not None and method not in self._capture_methods:
            should_capture = False
        elif self._sample_rate < 1.0 and random.random() >= self._sample_rate:
            should_capture = False

        if not should_capture:
            self._store.record_ignored(path)
            return await call_next(request)

        # ── Snapshot request body ──────────────────────────────────────────
        req_body_bytes = await request.body()
        req_body: dict[str, Any] | None = None
        if req_body_bytes:
            try:
                req_body = json.loads(req_body_bytes)
            except Exception:
                req_body = None   # non-JSON body — skip capture of body

        # ── Forward to real handler ────────────────────────────────────────
        response = await call_next(request)

        # ── Buffer response so we can capture AND stream it ────────────────
        resp_chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            resp_chunks.append(chunk)
        resp_body_bytes = b"".join(resp_chunks)

        resp_body: dict[str, Any] = {}
        if resp_body_bytes:
            try:
                resp_body = json.loads(resp_body_bytes)
            except Exception:
                resp_body = {}

        # ── Write to store (synchronous, sub-ms) ──────────────────────────
        t_write = time.perf_counter()
        event = _make_event(
            method=request.method,
            uri=request.url.path,
            request_body=req_body,
            response_status=response.status_code,
            response_body=resp_body,
            app_name=self._app_name,
        )
        try:
            self._store.add_event(event)
            event.capture_overhead_ms = round(
                (time.perf_counter() - t_write) * 1000, 3
            )
        except Exception as exc:
            logger.warning("shadow: write failed for %s %s: %s",
                           request.method, request.url.path, exc)

        # ── Return original response, byte-identical ───────────────────────
        return Response(
            content=resp_body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def instrument_fastapi(
    app:             FastAPI,
    store:           ShadowEventStore | None = None,
    *,
    app_name:        str       = "default",
    max_events:      int       = 5_000,
    exclude_paths:   list[str] | None = None,
    store_path:      str       = ":memory:",
    sample_rate:     float     = 1.0,
    allowlist:       list[str] | None = None,
    capture_methods: list[str] | None = None,
) -> ShadowEventStore:
    """Attach the shadow-capture middleware to a FastAPI application.

    The middleware intercepts every request/response pair (except excluded
    paths) and writes the exchange into *store*.  If no store is provided,
    a new in-memory store is created.

    Args:
        app:             FastAPI application to instrument.
        store:           Existing ShadowEventStore to write into.  A new
                         in-memory store is created when omitted.
        app_name:        Label attached to every captured event.
        max_events:      Soft cap passed to the auto-created store (ignored
                         when *store* is provided explicitly).
        exclude_paths:   Additional URI paths to never capture (extends
                         the default infra set: /healthz, /metrics, …).
        store_path:      Filesystem path for the auto-created store.
        sample_rate:     Fraction [0.0, 1.0] of eligible requests to capture.
                         1.0 means capture all.  Skipped requests are counted
                         in the ignored total.
        allowlist:       If provided, capture ONLY these exact URI paths.
                         Requests to other paths are counted as policy-ignored.
        capture_methods: If provided, capture ONLY these HTTP methods
                         (e.g. ``["POST", "PUT"]``).  Other methods on
                         eligible paths are counted as policy-ignored.

    Returns:
        The ShadowEventStore that events are written into.
    """
    if store is None:
        store = ShadowEventStore(path=store_path, max_events=max_events)

    exclude = _DEFAULT_EXCLUDE | frozenset(exclude_paths or [])
    app.add_middleware(
        _ShadowMiddleware,
        store=store,
        app_name=app_name,
        exclude_paths=exclude,
        sample_rate=float(sample_rate),
        allowlist=frozenset(allowlist) if allowlist is not None else None,
        capture_methods=(
            frozenset(m.upper() for m in capture_methods)
            if capture_methods is not None else None
        ),
    )
    return store
