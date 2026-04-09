"""
Axiom Shadow — Production shadow mode for Axiom.

Captures every HTTP exchange asynchronously into a local SQLite store
without blocking the real request path, then replays offline to measure
regression rate (DRIFT / FAILED), FPR, FNR, and latency overhead.

Quick start
-----------
    from fastapi import FastAPI
    from axiom_lab.shadow import instrument_fastapi, check_regressions

    app = FastAPI()
    store = instrument_fastapi(app)          # attach capture middleware

    # … run your app normally (tests, scripts, simulated users) …

    report = check_regressions(store, replay_client, limit=100)
    print(report.summary_table())
    report.save("build/shadow_report.json")

CLI
---
    python -m axiom_lab.shadow check-regressions \\
        --store  build/shadow.db               \\
        --target http://localhost:8000         \\
        --limit  100                           \\
        --output build/shadow_report.json
"""
from axiom_lab.shadow.event_store import ShadowEvent, ShadowEventStore
from axiom_lab.shadow.middleware import instrument_fastapi
from axiom_lab.shadow.replay_runner import (
    ShadowReport,
    ShadowStoreReport,
    check_regressions,
    run_shadow_campaign,
    store_inspection,
)

__all__ = [
    "ShadowEvent",
    "ShadowEventStore",
    "instrument_fastapi",
    "ShadowReport",
    "ShadowStoreReport",
    "check_regressions",
    "run_shadow_campaign",
    "store_inspection",
]
