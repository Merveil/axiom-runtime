"""
Axiom Lab — Campaign runner.

Orchestrates a full evaluation cycle:
  1. Load a fixture (pre-recorded stable session)
  2. Replay every exchange against a target client
  3. Apply optional business rules
  4. Produce and optionally persist a CampaignReport

Usage (programmatic):
    from axiom_lab.campaign import CampaignConfig, run_campaign
    from fastapi.testclient import TestClient
    from axiom_lab.api_demo.app import create_api_demo_app

    config = CampaignConfig(
        name="api-demo-regression",
        fixture_path="axiom_lab/fixtures/api_demo_stable.json",
        rules_path="axiom_lab/rules/api_demo.json",
    )
    client = TestClient(create_api_demo_app(drift_mode=True))
    report = run_campaign(config, client)
    print(report.regression_rate_pct)
"""
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path

from axiom_lab.probe import (
    ExchangeRecord,
    SessionCapture,
    Verdict,
    VerdictReport,
    replay_session,
)
from axiom_lab.rules_engine import EvaluatedReport, RulesEngine


# ---------------------------------------------------------------------------
# Config & Report
# ---------------------------------------------------------------------------

@dataclass
class CampaignConfig:
    name:         str
    fixture_path: str | Path
    rules_path:   str | Path | None = None


@dataclass
class CampaignReport:
    name:               str
    timestamp:          str
    total:              int
    strict:             int
    semantic:           int
    drift:              int
    failed:             int
    rule_violations:    int
    routes_with_issues: list[str]
    details:            list[dict] = field(default_factory=list)
    # V1.11 — breakdown tables
    by_route:           dict[str, dict] = field(default_factory=dict)
    by_verdict:         dict[str, int]  = field(default_factory=dict)
    by_rule_class:      dict[str, int]  = field(default_factory=dict)

    @property
    def regression_rate_pct(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.drift + self.failed) / self.total * 100.0

    def to_dict(self) -> dict:
        return {
            "name":                self.name,
            "timestamp":           self.timestamp,
            "total":               self.total,
            "strict":              self.strict,
            "semantic":            self.semantic,
            "drift":               self.drift,
            "failed":              self.failed,
            "rule_violations":     self.rule_violations,
            "regression_rate_pct": self.regression_rate_pct,
            "routes_with_issues":  self.routes_with_issues,
            "by_route":            self.by_route,
            "by_verdict":          self.by_verdict,
            "by_rule_class":       self.by_rule_class,
        }

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_campaign(
    config: CampaignConfig,
    replay_client,
) -> CampaignReport:
    """Run a full campaign from a fixture file against a live or test client."""
    records: list[ExchangeRecord] = SessionCapture.load(config.fixture_path)
    reports: list[VerdictReport]  = replay_session(records, replay_client)

    rules = RulesEngine.from_file(config.rules_path) if config.rules_path else None

    strict = semantic = drift = failed = violations = 0
    bad_routes: list[str] = []
    details: list[dict]   = []

    # V1.11 breakdown accumulators
    by_route: dict[str, dict]  = {}
    by_verdict: dict[str, int] = {}
    by_rule_class: dict[str, int] = {}

    for record, report in zip(records, reports):
        if rules:
            evaluated  = rules.evaluate(report)
            v          = evaluated.effective_verdict
            eviol      = evaluated.violations
            violations += len(eviol)
            # tally by rule class (rule_id prefix before first digit)
            for viol in eviol:
                cls = viol.rule_id[0] if viol.rule_id else "?"
                by_rule_class[cls] = by_rule_class.get(cls, 0) + 1
        else:
            v    = report.verdict
            eviol = []

        # tally verdict totals
        if v == Verdict.REPRODUCIBLE_STRICT:
            strict += 1
        elif v == Verdict.REPRODUCIBLE_SEMANTIC:
            semantic += 1
        elif v == Verdict.DRIFT_DETECTED:
            drift += 1
            bad_routes.append(record.uri)
        elif v == Verdict.FAILED_TO_REPLAY:
            failed += 1
            bad_routes.append(record.uri)

        # by_verdict global counter
        vkey = v.value
        by_verdict[vkey] = by_verdict.get(vkey, 0) + 1

        # by_route per-route breakdown
        uri = record.uri
        if uri not in by_route:
            by_route[uri] = {
                "total": 0,
                "strict": 0,
                "semantic": 0,
                "drift": 0,
                "failed": 0,
                "violations": 0,
            }
        by_route[uri]["total"] += 1
        by_route[uri][v.value.lower().split("_")[-1]] = by_route[uri].get(
            v.value.lower().split("_")[-1], 0
        ) + 1
        by_route[uri]["violations"] += len(eviol)

        detail_entry: dict = {
            "label":      record.label,
            "uri":        uri,
            "verdict":    vkey,
            "latency_ms": round(report.replay_latency_ms, 2),
        }
        if eviol:
            detail_entry["rule_violations"] = [
                {"rule_id": rv.rule_id, "path": rv.path, "detail": rv.detail}
                for rv in eviol
            ]
        details.append(detail_entry)

    return CampaignReport(
        name=config.name,
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        total=len(records),
        strict=strict,
        semantic=semantic,
        drift=drift,
        failed=failed,
        rule_violations=violations,
        routes_with_issues=sorted(set(bad_routes)),
        details=details,
        by_route=by_route,
        by_verdict=by_verdict,
        by_rule_class=by_rule_class,
    )
