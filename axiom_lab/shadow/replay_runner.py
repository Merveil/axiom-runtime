"""
Axiom Shadow — Offline regression checker.

Replays shadow-captured events against a candidate client and produces
a ShadowReport with:

  regression_rate_pct   (DRIFT + FAILED) / total
  strict_rate_pct       STRICT / total
  semantic_rate_pct     SEMANTIC / total
  avg_replay_latency_ms average replay call latency
  p95_replay_latency_ms 95th-percentile replay call latency
  avg_capture_overhead_ms  overhead added per request during capture
  by_route              per-route breakdown of verdicts and latency
  drift_sample          up to 10 annotated drift cases for inspection

Usage
-----
    from axiom_lab.shadow import check_regressions, ShadowEventStore
    from fastapi.testclient import TestClient

    report = check_regressions(
        store,
        TestClient(my_candidate_app),
        name="v2-shadow",
        limit=100,
        rules_path="axiom_lab/rules/api_demo.json",
    )
    print(report.summary_table())
    report.save("build/shadow_report.json")
"""
from __future__ import annotations

import datetime
import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from axiom_lab.probe import Verdict, VerdictReport, evaluate
from axiom_lab.rules_engine import RulesEngine
from axiom_lab.shadow.event_store import ShadowEventStore

# ---------------------------------------------------------------------------
# Optional Rust acceleration (axiom_core extension module)
# ---------------------------------------------------------------------------
try:
    import axiom_core as _rust
    _RUST_AVAILABLE = True
except ImportError:  # pragma: no cover
    _rust = None  # type: ignore[assignment]
    _RUST_AVAILABLE = False


# ---------------------------------------------------------------------------
# Store inspection report (no replay required)
# ---------------------------------------------------------------------------

@dataclass
class ShadowStoreReport:
    """Read-only introspection of a ShadowEventStore — no replay needed."""
    total_captured:      int
    total_ignored:       int
    replay_candidates:   int   # events captured within *since_hours* window
    drift_detected:      int   # from previous replay runs (0 if never replayed)
    top_drift_routes:    list[tuple[str, int]]   # (uri, drift_count)
    top_capture_routes:  list[tuple[str, int]]   # (uri, total_count)
    verdict_summary:     dict[str, int]           # verdict → count
    since_hours:         float

    def summary_table(self) -> str:
        lines = [
            f"Shadow Store Report",
            f"  Captured events:   {self.total_captured}",
            f"  Ignored events:    {self.total_ignored}  "
            f"(policy-rejected, last window)",
            f"  Replay candidates: {self.replay_candidates}  "
            f"(last {self.since_hours:.0f}h)",
            f"  Drift detected:    {self.drift_detected}  "
            f"(from prior replay runs)",
        ]
        if self.top_drift_routes:
            lines.append("")
            lines.append("  Top drift routes:")
            for uri, cnt in self.top_drift_routes:
                lines.append(f"    - {uri}  ({cnt} drift)")
        if self.top_capture_routes:
            lines.append("")
            lines.append("  Top capture routes:")
            for uri, cnt in self.top_capture_routes:
                lines.append(f"    - {uri}  ({cnt} captured)")
        if self.verdict_summary:
            lines.append("")
            lines.append("  Verdict breakdown (all replays):")
            for v, n in sorted(self.verdict_summary.items()):
                lines.append(f"    {v:<30} {n}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "total_captured":     self.total_captured,
            "total_ignored":      self.total_ignored,
            "replay_candidates":  self.replay_candidates,
            "drift_detected":     self.drift_detected,
            "top_drift_routes":   self.top_drift_routes,
            "top_capture_routes": self.top_capture_routes,
            "verdict_summary":    self.verdict_summary,
            "since_hours":        self.since_hours,
        }


def store_inspection(
    store:        ShadowEventStore,
    *,
    since_hours:  float = 24.0,
) -> ShadowStoreReport:
    """Produce a ``ShadowStoreReport`` without running any replay.

    Args:
        store:       Store to inspect.
        since_hours: Time window for counting replay candidates.
                     Defaults to 24 hours.

    Returns:
        ShadowStoreReport with store counts, ignored totals, and any
        drift info cached from previous ``check_regressions`` calls.
    """
    cutoff    = time.time() - (since_hours * 3600)
    recent    = store.get_events(limit=100_000, since=cutoff)
    vs        = store.get_verdict_summary()
    drift_n   = vs.get("DRIFT_DETECTED", 0)

    return ShadowStoreReport(
        total_captured=store.count(),
        total_ignored=store.get_ignored_total(),
        replay_candidates=len(recent),
        drift_detected=drift_n,
        top_drift_routes=store.get_drift_routes(limit=5),
        top_capture_routes=store.get_top_routes(limit=5),
        verdict_summary=vs,
        since_hours=since_hours,
    )


# ---------------------------------------------------------------------------
# Per-route accumulator (internal)
# ---------------------------------------------------------------------------

@dataclass
class _RouteStats:
    total:    int = 0
    strict:   int = 0
    semantic: int = 0
    drift:    int = 0
    failed:   int = 0
    latencies: list[float] = field(default_factory=list)

    @property
    def regression_rate_pct(self) -> float:
        return (self.drift + self.failed) / self.total * 100 if self.total else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return statistics.mean(self.latencies) if self.latencies else 0.0


# ---------------------------------------------------------------------------
# Public report
# ---------------------------------------------------------------------------

@dataclass
class ShadowReport:
    """Summary of a shadow-mode offline replay run."""
    name:                    str
    timestamp:               str
    total_captured:          int
    total_replayed:          int
    strict:                  int
    semantic:                int
    drift:                   int
    failed:                  int
    strict_rate_pct:         float
    semantic_rate_pct:       float
    regression_rate_pct:     float
    avg_replay_latency_ms:   float
    p95_replay_latency_ms:   float
    avg_capture_overhead_ms: float
    by_route:                dict[str, dict]
    by_verdict:              dict[str, int]
    drift_sample:            list[dict]   # up to 10 annotated drift cases

    def summary_table(self) -> str:
        lines = [
            f"Shadow Replay: {self.name}  [{self.timestamp}]",
            f"  Captured:   {self.total_captured}   Replayed: {self.total_replayed}",
            f"  STRICT:     {self.strict:4d}  ({self.strict_rate_pct:.1f}%)",
            f"  SEMANTIC:   {self.semantic:4d}  ({self.semantic_rate_pct:.1f}%)",
            f"  DRIFT:      {self.drift:4d}",
            f"  FAILED:     {self.failed:4d}",
            f"  Regression rate:        {self.regression_rate_pct:.2f}%",
            f"  Avg replay latency:     {self.avg_replay_latency_ms:.1f} ms",
            f"  P95 replay latency:     {self.p95_replay_latency_ms:.1f} ms",
            f"  Avg capture overhead:   {self.avg_capture_overhead_ms:.3f} ms",
            "",
            "  By route:",
        ]
        for route, stats in sorted(self.by_route.items()):
            lines.append(
                f"    {route:<40}  total={stats['total']:3d}"
                f"  drift={stats['drift']:2d}"
                f"  failed={stats['failed']:2d}"
                f"  regression={stats['regression_rate_pct']:.1f}%"
                f"  avg_lat={stats['avg_latency_ms']:.1f}ms"
            )
        if self.drift_sample:
            lines += [
                "",
                f"  Drift sample ({len(self.drift_sample)} cases):",
            ]
            for d in self.drift_sample:
                lines.append(
                    f"    [{d['verdict']:<22}]  "
                    f"{d['method']} {d['uri']}"
                    + (f"  — {d['summary']}" if d.get("summary") else "")
                )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "name":                    self.name,
            "timestamp":               self.timestamp,
            "total_captured":          self.total_captured,
            "total_replayed":          self.total_replayed,
            "strict":                  self.strict,
            "semantic":                self.semantic,
            "drift":                   self.drift,
            "failed":                  self.failed,
            "strict_rate_pct":         self.strict_rate_pct,
            "semantic_rate_pct":       self.semantic_rate_pct,
            "regression_rate_pct":     self.regression_rate_pct,
            "avg_replay_latency_ms":   self.avg_replay_latency_ms,
            "p95_replay_latency_ms":   self.p95_replay_latency_ms,
            "avg_capture_overhead_ms": self.avg_capture_overhead_ms,
            "by_route":                self.by_route,
            "by_verdict":              self.by_verdict,
            "drift_sample":            self.drift_sample,
        }

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def check_regressions(
    store:         ShadowEventStore,
    client:        object,
    *,
    name:          str              = "shadow",
    limit:         int              = 100,
    rules_path:    str | Path | None = None,
    exclude_paths: list[str] | None  = None,
    strict_mode:   bool             = False,
) -> ShadowReport:
    """Replay captured events and classify each one as STRICT / SEMANTIC /
    DRIFT_DETECTED / FAILED_TO_REPLAY.

    Args:
        store:         Store populated by ``instrument_fastapi``.
        client:        TestClient or httpx.Client to replay against.
        name:          Display name for the report.
        limit:         Maximum number of events to replay (default 100).
        rules_path:    Optional JSON rules file applied after the probe
                       verdict.
        exclude_paths: URI paths to skip in this replay run.
        strict_mode:   When True, REPRODUCIBLE_SEMANTIC is treated as
                       DRIFT_DETECTED (only exact matches pass).

    Returns:
        ShadowReport with regression rate, latency stats, and drift sample.
    """
    exclude = frozenset(exclude_paths or [])
    events  = store.get_events(limit=limit)
    events  = [e for e in events if e.uri not in exclude]

    engine: RulesEngine | None = (
        RulesEngine.from_file(rules_path) if rules_path else None
    )

    latencies:    list[float]              = []
    by_route:     dict[str, _RouteStats]   = {}
    by_verdict:   dict[str, int]           = {}
    drift_sample: list[dict]               = []

    for event in events:
        record  = event.to_record()
        report: VerdictReport = evaluate(record, client)
        predicted = report.verdict

        if engine:
            ev        = engine.evaluate(report)
            predicted = ev.effective_verdict

        # In strict mode SEMANTIC counts as drift (only exact matches pass)
        if strict_mode and predicted == Verdict.REPRODUCIBLE_SEMANTIC:
            predicted = Verdict.DRIFT_DETECTED

        v_str = predicted.value
        # Persist verdict for shadow-report inspection
        store.record_verdict(event.id, v_str)

        latencies.append(report.replay_latency_ms)
        by_verdict[v_str] = by_verdict.get(v_str, 0) + 1

        # Per-route accumulation
        rs = by_route.setdefault(event.uri, _RouteStats())
        rs.total += 1
        rs.latencies.append(report.replay_latency_ms)
        if predicted == Verdict.REPRODUCIBLE_STRICT:
            rs.strict   += 1
        elif predicted == Verdict.REPRODUCIBLE_SEMANTIC:
            rs.semantic += 1
        elif predicted == Verdict.DRIFT_DETECTED:
            rs.drift    += 1
            if len(drift_sample) < 10:
                drift_sample.append({
                    "id":      event.id,
                    "method":  event.method,
                    "uri":     event.uri,
                    "verdict": v_str,
                    "summary": report.summary,
                    "drifts": [
                        {
                            "path":      d.path,
                            "original":  d.original,
                            "replayed":  d.replayed,
                        }
                        for d in report.drift[:5]
                    ],
                })
        elif predicted == Verdict.FAILED_TO_REPLAY:
            rs.failed   += 1

    total_r = len(events)
    strict  = by_verdict.get(Verdict.REPRODUCIBLE_STRICT.value,   0)
    sem     = by_verdict.get(Verdict.REPRODUCIBLE_SEMANTIC.value,  0)
    drift_n = by_verdict.get(Verdict.DRIFT_DETECTED.value,         0)
    failed  = by_verdict.get(Verdict.FAILED_TO_REPLAY.value,       0)

    avg_lat = statistics.mean(latencies) if latencies else 0.0
    p95_lat = _p95(latencies)

    return ShadowReport(
        name=name,
        timestamp=datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat().replace("+00:00", "Z"),
        total_captured=store.count(),
        total_replayed=total_r,
        strict=strict,
        semantic=sem,
        drift=drift_n,
        failed=failed,
        strict_rate_pct=   round(strict  / total_r * 100, 2) if total_r else 0.0,
        semantic_rate_pct= round(sem     / total_r * 100, 2) if total_r else 0.0,
        regression_rate_pct=round(
            (drift_n + failed) / total_r * 100, 2
        ) if total_r else 0.0,
        avg_replay_latency_ms=round(avg_lat, 2),
        p95_replay_latency_ms=round(p95_lat, 2),
        avg_capture_overhead_ms=round(store.avg_capture_overhead_ms(), 3),
        by_route={
            route: {
                "total":               rs.total,
                "strict":              rs.strict,
                "semantic":            rs.semantic,
                "drift":               rs.drift,
                "failed":              rs.failed,
                "regression_rate_pct": round(rs.regression_rate_pct, 2),
                "avg_latency_ms":      round(rs.avg_latency_ms, 2),
            }
            for route, rs in by_route.items()
        },
        by_verdict=by_verdict,
        drift_sample=drift_sample,
    )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _p95(values: list[float]) -> float:
    """Return the 95th-percentile of *values* (nearest-rank method).

    Delegates to the Rust implementation when axiom_core is available.
    """
    if _RUST_AVAILABLE:
        return _rust.p95(values)
    if not values:
        return 0.0
    sv  = sorted(values)
    idx = max(0, int(len(sv) * 0.95) - 1)
    return sv[idx]


# ---------------------------------------------------------------------------
# Shadow campaign (Option 3)
# ---------------------------------------------------------------------------

def run_shadow_campaign(
    store:         ShadowEventStore,
    client:        object,
    *,
    mode:          str               = "semantic",
    last:          int               = 50,
    name:          str               = "shadow-campaign",
    rules_path:    str | Path | None = None,
    exclude_paths: list[str] | None  = None,
) -> ShadowReport:
    """Run a campaign directly against shadow-captured events.

    This is a thin wrapper around :func:`check_regressions` that surfaces
    two evaluation modes and defaults to a smaller replay window (*last*).

    Args:
        store:         Populated ShadowEventStore.
        client:        TestClient or httpx.Client to replay against.
        mode:          ``"semantic"`` — SEMANTIC counts as passing (default).
                       ``"strict"``  — only exact matches pass; SEMANTIC
                       events are classified as DRIFT_DETECTED.
        last:          Number of most-recent events to replay (default 50).
        name:          Report display name.
        rules_path:    Optional JSON rules file.
        exclude_paths: URI paths to skip.

    Returns:
        ShadowReport identical to ``check_regressions`` output.
    """
    if mode not in ("semantic", "strict"):
        raise ValueError(f"mode must be 'semantic' or 'strict', got {mode!r}")
    return check_regressions(
        store,
        client,
        name=name,
        limit=last,
        rules_path=rules_path,
        exclude_paths=exclude_paths,
        strict_mode=(mode == "strict"),
    )
