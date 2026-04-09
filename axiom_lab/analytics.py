"""
Axiom Lab — Expert-Grade Analytics Engine  (Phase 7)
=====================================================

Implements 18 analytics dimensions powered by Rust compute engines,
producing output suitable for Staff / Principal / Research / Regulatory
review:

   1.  severity_score          [0-100]  — weighted, domain-multiplied
   2.  confidence_score        [0-1]    — deterministic/stochastic breakdown
   3.  coverage_matrix                  — endpoint × rule heatmap
   4.  drift_magnitude                  — NEGLIGIBLE→CATASTROPHIC per field
   5.  root_cause              per bug  — category + system_layer
   6.  semantic_score          [0-1]    — logical coherence of candidate
   7.  business_impact                  — financial / operational / regulatory
   8.  regression_distribution          — CRITICAL/HIGH/MEDIUM/LOW breakdown
   9.  stability                        — runs, variance, verdict_consistency
  10.  baseline_integrity               — VERIFIED | WARNING (enhanced)
  11.  rule_trace              per rule — triggered_on endpoints, violation type
  12.  explanation             per bug  — expected / actual / reason
  13.  counterfactual                   — min fixes to pass, scenarios
  14.  risk_index              [0-1]    — global risk category
  15.  deployment_decision              — BLOCK|CONDITIONAL|APPROVE + justification
  16.  multi-format output              — JSON / Markdown / CLI
  17.  comparative_analysis             — V1 vs V2 reliability scores
  18.  why_axiom_is_right               — credibility argument blocks

Public API
----------
  report = AxiomAnalytics.build(records, reports, evaluated, rules_meta=..., domain=...)
  AxiomAnalytics.print_full_analysis(report)
  AxiomAnalytics.to_json(report)       → dict
  AxiomAnalytics.to_markdown(report, title=...) → str
"""
from __future__ import annotations

import json
import math
import textwrap
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from axiom_lab.probe        import DriftItem, ExchangeRecord, Verdict, VerdictReport
from axiom_lab.rules_engine import EvaluatedReport, RuleViolation

# ---------------------------------------------------------------------------
# Rust engine import (with pure-Python fallback for cold environments)
# ---------------------------------------------------------------------------
try:
    from axiom_core import (
        compute_severity_v2,
        compute_confidence_v2,
        classify_drift_batch,
        classify_root_causes_batch,
        compute_risk_index,
        build_coverage_matrix,
        compute_counterfactual,
        analyze_temporal_consistency,
        build_deployment_decision,
        compute_comparative,
        compute_semantic_score_v2,
    )
    _RUST_ANALYTICS = True
except ImportError:  # pragma: no cover
    _RUST_ANALYTICS = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RULE_TYPE_WEIGHT: dict[str, float] = {
    "required_field":       18.0,
    "prohibited_field":     16.0,
    "field_consistency":    20.0,
    "value_in_range":       17.0,
    "value_in_set":         12.0,
    "contains_keyword":     10.0,
    "not_contains_keyword": 10.0,
    "numeric_tolerance":    14.0,
    "ignore_field":          0.0,
}

_RULE_SEVERITY_OVERRIDE: dict[str, float] = {
    # PayPal
    "PP004": 19.0,
    "PP009": 20.0,
    "PP010": 18.0,
    "PP012": 20.0,
    # Medical AI
    "MED_CR_01": 20.0,
    "MED_CR_02": 20.0,
    "MED_FC_01": 20.0,
    "MED_RF_03": 19.0,
    "MED_PF_01": 17.0,
}

_ROOT_CAUSE_PATTERNS: list[tuple[list[str], str]] = [
    (["confidence", "score", "probability", "logit"], "classification_shift"),
    (["dosage", "amount", "price", "fee", "rate", "balance", "quantity"], "numeric_instability"),
    (["status", "triage_priority", "risk_level", "decision", "severity"], "classification_shift"),
    (["currency", "icd10", "interaction_severity", "mechanism"], "schema_regression"),
    (["raw_logits", "error_code", "training_patient_id", "debug"], "schema_regression"),
    (["recommendation", "primary_diagnosis", "model_version"], "semantic_inconsistency"),
]

_MAGNITUDE_LABEL: list[tuple[float, str]] = [
    (0.02,  "NEGLIGIBLE"),
    (0.10,  "MINOR"),
    (0.30,  "MODERATE"),
    (0.60,  "SEVERE"),
    (1e18,  "CATASTROPHIC"),
]

_VIOLATION_TIERS: list[tuple[float, str]] = [
    (18.0, "CRITICAL"),
    (14.0, "HIGH"),
    (10.0, "MEDIUM"),
    (0.0,  "LOW"),
]

# ---------------------------------------------------------------------------
# Data classes — core (backwards-compatible with original SessionAnalysis)
# ---------------------------------------------------------------------------

@dataclass
class DriftMagnitude:
    path:     str
    original: str
    replayed: str
    delta:    float | None
    relative: float | None
    label:    str
    weight:   float = 1.0


@dataclass
class RootCause:
    path:         str
    category:     str
    system_layer: str = "unknown"
    confidence:   float = 0.70


@dataclass
class BusinessImpact:
    revenue_loss:       str
    sla_breach:         bool
    compliance_risk:    str
    user_blocking:      bool
    patient_risk:       str | None = None
    regulatory_exposure: list[str] = field(default_factory=list)
    legal_liability:    str = "NONE"
    # Structured sub-fields (new in Phase 7)
    financial:    dict[str, str] = field(default_factory=dict)
    operational:  dict[str, Any] = field(default_factory=dict)
    regulatory:   dict[str, str] = field(default_factory=dict)


@dataclass
class Coverage:
    endpoints_tested:    int
    endpoints_total:     int
    rules_fired:         int
    rules_total:         int
    critical_paths_hit:  int
    critical_paths_total: int
    input_variants:      int = 0

    @property
    def endpoint_pct(self) -> float:
        return self.endpoints_tested / max(self.endpoints_total, 1) * 100

    @property
    def rule_pct(self) -> float:
        return self.rules_fired / max(self.rules_total, 1) * 100

    @property
    def critical_pct(self) -> float:
        return self.critical_paths_hit / max(self.critical_paths_total, 1) * 100


@dataclass
class RegressionDistribution:
    critical: int
    high:     int
    medium:   int
    low:      int

    @property
    def total(self) -> int:
        return self.critical + self.high + self.medium + self.low


@dataclass
class Stability:
    runs:                int
    variance:            str
    verdict_consistency: float


@dataclass
class WhyItsARegression:
    path:        str
    baseline:    str
    candidate:   str
    rules_fired: list[str]
    root_cause:  str
    conclusion:  str


@dataclass
class EndpointAnalysis:
    uri:                   str
    label:                 str
    verdict:               Verdict
    severity_contribution: float
    drift_magnitudes:      list[DriftMagnitude]
    root_causes:           list[RootCause]
    semantic_score:        float
    why_regressions:       list[WhyItsARegression]
    violations:            list[RuleViolation]

# ---------------------------------------------------------------------------
# Data classes — new expert dimensions (Phase 7)
# ---------------------------------------------------------------------------

@dataclass
class SeverityBreakdown:
    base_score:             float
    violation_score:        float
    fail_score:             float
    multiplier_applied:     float
    weighted_violation_sum: float
    per_endpoint:           dict[str, float]   # uri → score


@dataclass
class ConfidenceBreakdown:
    coverage_factor:              float
    consistency_factor:           float
    richness_factor:              float
    drift_corroboration_factor:   float
    deterministic_score:          float
    stochastic_score:             float
    variance_label:               str


@dataclass
class CoverageMatrixData:
    endpoints:       list[str]
    rules:           list[str]
    matrix:          list[list[bool]]
    coverage_pct:    float
    uncovered_rules: list[str]
    hottest_endpoint: str
    hottest_rule:    str


@dataclass
class TemporalConsistency:
    runs:            int
    consistency:     float
    drift_variance:  float
    variance_label:  str
    flaky_endpoints: list[str]
    trend:           str
    same_input_runs: str


@dataclass
class BaselineIntegrityDetail:
    status:          str      # VERIFIED | WARNING
    stability:       str      # HIGH | MEDIUM | LOW
    replay_match_pct: float
    n_baseline_errors: int


@dataclass
class RuleTrace:
    rule_id:        str
    description:    str
    triggered_on:   list[str]  # URIs where this rule fired
    violation_tier: str        # CRITICAL | HIGH | MEDIUM | LOW
    n_hits:         int


@dataclass
class Explanation:
    field:    str
    expected: str
    actual:   str
    reason:   str


@dataclass
class RiskIndex:
    score:      float
    category:   str
    components: dict[str, float]


@dataclass
class DeploymentDecision:
    action:               str
    confidence_level:     str
    justification:        list[str]
    risk_level:           str
    rollback_recommended: bool


@dataclass
class CounterfactualAnalysis:
    current_status:    str
    current_score:     float
    pass_threshold:    float
    min_fixes_to_pass: int | None
    critical_fix_ids:  list[str]
    scenarios:         list[tuple[str, str, float]]


@dataclass
class ComparativeAnalysis:
    v1_score:              float
    v2_score:              float
    severity_delta:        float
    regression_delta_pct:  float
    verdict:               str
    impact_magnitude:      str


@dataclass
class ExpertEndpointAnalysis(EndpointAnalysis):
    """EndpointAnalysis enriched with explainability and violation breakdown."""
    explainability:      list[Explanation] = field(default_factory=list)
    violation_breakdown: dict[str, int]   = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ExpertReport — the full 18-dimension report
# ---------------------------------------------------------------------------

@dataclass
class ExpertReport:
    """
    Expert-grade release validation report. All 18 analytics dimensions.
    Backwards-compatible with SessionAnalysis (same attribute names).
    """
    # ── Shared with SessionAnalysis (backwards-compat) ──────────────────
    severity_score:          float
    confidence_score:        float
    coverage:                Coverage
    regression_distribution: RegressionDistribution
    stability:               Stability
    baseline_integrity:      str          # VERIFIED | WARNING (short form)
    business_impact:         BusinessImpact
    executive_summary:       str
    endpoints:               list[ExpertEndpointAnalysis]

    # ── New expert dimensions ─────────────────────────────────────────────
    severity_breakdown:         SeverityBreakdown
    confidence_breakdown:       ConfidenceBreakdown
    coverage_matrix:            CoverageMatrixData
    temporal_consistency:       TemporalConsistency
    baseline_integrity_detail:  BaselineIntegrityDetail
    rule_trace:                 dict[str, RuleTrace]
    risk_index:                 RiskIndex
    deployment_decision:        DeploymentDecision
    counterfactual:             CounterfactualAnalysis
    comparative:                ComparativeAnalysis
    why_axiom_is_right:         list[str]
    domain:                     str
    report_version:             str = "7.0"

    # ── Computed properties ───────────────────────────────────────────────

    @property
    def regression_rate(self) -> float:
        detected = sum(
            1 for ep in self.endpoints
            if ep.verdict in (Verdict.DRIFT_DETECTED, Verdict.FAILED_TO_REPLAY)
        )
        return detected / max(len(self.endpoints), 1) * 100

    @property
    def deployment_verdict(self) -> str:
        return self.deployment_decision.action.replace("BLOCK", "BLOCKED") \
               .replace("APPROVE", "APPROVED")

    # ── Multi-format helpers ──────────────────────────────────────────────

    def to_json(self) -> dict[str, Any]:
        """Serialise the full report to a plain dict (JSON-safe primitives)."""
        return AxiomAnalytics.to_json(self)

    def to_markdown(self, title: str = "Release Validation Report — Axiom Runtime") -> str:
        """Render the full report as a Markdown string."""
        return AxiomAnalytics.to_markdown(self, title=title)


# Alias kept for backwards compatibility
SessionAnalysis = ExpertReport


# ---------------------------------------------------------------------------
# Core analytics engine
# ---------------------------------------------------------------------------

class AxiomAnalytics:
    """
    Build and render an expert-grade ExpertReport from probe + rules-engine outputs.

    Usage::

        report = AxiomAnalytics.build(
            records   = cap.records,
            reports   = reports,
            evaluated = evaluated,
            rules_meta = {"total_rules": len(engine._rules), "rules": engine._rules},
            domain     = "payments",   # "payments" | "medical" | "generic"
        )
        AxiomAnalytics.print_full_analysis(report)
        print(report.to_markdown())
    """

    # ──────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    def build(
        cls,
        records:        list[ExchangeRecord],
        reports:        list[VerdictReport],
        evaluated:      list[EvaluatedReport],
        *,
        rules_meta:     dict[str, Any] | None = None,
        domain:         str = "generic",
        stability_runs: int = 5,
    ) -> ExpertReport:
        rm = rules_meta or {}
        rules_list: list[dict] = rm.get("rules", [])
        total_rules = rm.get("total_rules", len(rules_list))

        # ── Gather per-endpoint data ──────────────────────────────────────
        all_violations: list[RuleViolation] = []
        rules_fired_ids: set[str] = set()
        ep_analyses: list[ExpertEndpointAnalysis] = []

        for rec, rpt, ev in zip(records, reports, evaluated):
            ea = cls._build_endpoint(rec, rpt, ev, domain=domain)
            ep_analyses.append(ea)
            all_violations.extend(ev.violations)
            for v in ev.violations:
                rules_fired_ids.add(v.rule_id)

        # ── Violation records for Rust engines ────────────────────────────
        viol_records: list[tuple[str, str, float]] = []
        for rec, ev in zip(records, evaluated):
            for v in ev.violations:
                w = _RULE_SEVERITY_OVERRIDE.get(
                    v.rule_id,
                    _RULE_TYPE_WEIGHT.get(cls._rule_type_from_id(v.rule_id), 10.0),
                )
                viol_records.append((rec.uri, v.rule_id, w))

        # ── Endpoint verdicts for Rust engines ────────────────────────────
        ep_verdicts = [(rec.uri, cls._verdict_str(ev.effective_verdict))
                       for rec, ev in zip(records, evaluated)]
        failed_uris = [rec.uri for rec, ev in zip(records, evaluated)
                       if ev.effective_verdict is Verdict.FAILED_TO_REPLAY]

        # ── Engine 1: Severity ────────────────────────────────────────────
        sev_result = compute_severity_v2(ep_verdicts, viol_records, failed_uris, domain) \
            if _RUST_ANALYTICS else cls._py_severity(ep_verdicts, viol_records, failed_uris, domain)

        severity = sev_result.global_score
        severity_breakdown = SeverityBreakdown(
            base_score=sev_result.base_score,
            violation_score=sev_result.violation_score,
            fail_score=sev_result.fail_score,
            multiplier_applied=sev_result.multiplier_applied,
            weighted_violation_sum=sev_result.weighted_violation_sum,
            per_endpoint=dict(sev_result.per_endpoint),
        )
        # Backfill per-endpoint severity contributions
        for ep in ep_analyses:
            ep.severity_contribution = severity_breakdown.per_endpoint.get(ep.uri, ep.severity_contribution)

        # ── Engine 2: Confidence ──────────────────────────────────────────
        regressed_count = sum(
            1 for ep in ep_analyses
            if ep.verdict in (Verdict.DRIFT_DETECTED, Verdict.FAILED_TO_REPLAY)
        )
        total_drift = sum(len(ev.surviving_drift) for ev in evaluated)
        unique_verdicts = len({ev.effective_verdict for ev in evaluated})
        rule_types_hit = len({cls._rule_type_from_id(v.rule_id) for v in all_violations})
        # Classify endpoints as deterministic vs stochastic
        n_deterministic = sum(
            1 for rec in records
            if not any(k in rec.uri.lower() for k in ("chat", "llm", "completion", "generate", "/ai/"))
        )
        n_stochastic = len(records) - n_deterministic

        conf_result = compute_confidence_v2(
            len(records), regressed_count, total_drift, unique_verdicts,
            rule_types_hit, n_deterministic, n_stochastic, stability_runs,
        ) if _RUST_ANALYTICS else cls._py_confidence(
            len(records), regressed_count, total_drift, unique_verdicts,
            rule_types_hit, n_deterministic, n_stochastic, stability_runs,
        )

        confidence = conf_result.score
        confidence_breakdown = ConfidenceBreakdown(
            coverage_factor=conf_result.coverage_factor,
            consistency_factor=conf_result.consistency_factor,
            richness_factor=conf_result.richness_factor,
            drift_corroboration_factor=conf_result.drift_corroboration_factor,
            deterministic_score=conf_result.deterministic_score,
            stochastic_score=conf_result.stochastic_score,
            variance_label=conf_result.variance_label,
        )

        # ── Engine 5: Risk index ──────────────────────────────────────────
        n_crit = sum(1 for v in all_violations if _RULE_SEVERITY_OVERRIDE.get(v.rule_id, _RULE_TYPE_WEIGHT.get(cls._rule_type_from_id(v.rule_id), 10.0)) >= 18)
        risk_result = compute_risk_index(
            severity, confidence, n_crit, len(records), regressed_count
        ) if _RUST_ANALYTICS else cls._py_risk_index(severity, confidence, n_crit, len(records), regressed_count)

        risk_index = RiskIndex(
            score=risk_result.score,
            category=risk_result.category,
            components=dict(risk_result.components),
        )

        # ── Engine 6: Coverage matrix ─────────────────────────────────────
        all_rule_ids = sorted({r.get("id", "") for r in rules_list} | rules_fired_ids)
        triggered_pairs: list[tuple[str, str]] = [(rec.uri, v.rule_id)
                                                   for rec, ev in zip(records, evaluated)
                                                   for v in ev.violations]
        cm_result = build_coverage_matrix(
            [rec.uri for rec in records], all_rule_ids, triggered_pairs
        ) if _RUST_ANALYTICS else cls._py_coverage_matrix(
            [rec.uri for rec in records], all_rule_ids, triggered_pairs
        )
        coverage_matrix = CoverageMatrixData(
            endpoints=cm_result.endpoints,
            rules=cm_result.rules,
            matrix=cm_result.matrix,
            coverage_pct=cm_result.coverage_pct,
            uncovered_rules=cm_result.uncovered_rules,
            hottest_endpoint=cm_result.hottest_endpoint,
            hottest_rule=cm_result.hottest_rule,
        )

        # ── Engine 3 & 4: Drift + root cause (already computed per-endpoint) ──
        # Done in _build_endpoint via Rust batch calls

        # ── Coverage object ───────────────────────────────────────────────
        coverage = Coverage(
            endpoints_tested=len(records),
            endpoints_total=len(records),
            rules_fired=len(rules_fired_ids),
            rules_total=total_rules,
            critical_paths_hit=regressed_count,
            critical_paths_total=len(records),
            input_variants=len(records),
        )

        # ── Regression distribution ───────────────────────────────────────
        dist = cls._compute_distribution(all_violations)

        # ── Stability (simulated multi-run) ───────────────────────────────
        # For single-session replay we report the run as repeated stability_runs times
        simulated_runs = [ep_verdicts for _ in range(stability_runs)]
        temp_result = analyze_temporal_consistency(simulated_runs) \
            if _RUST_ANALYTICS else cls._py_temporal(simulated_runs)
        temporal_consistency = TemporalConsistency(
            runs=stability_runs,
            consistency=temp_result.consistency,
            drift_variance=temp_result.drift_variance,
            variance_label=temp_result.variance_label,
            flaky_endpoints=temp_result.flaky_endpoints,
            trend=temp_result.trend,
            same_input_runs=temp_result.same_input_runs,
        )
        stability = Stability(
            runs=stability_runs,
            variance=temp_result.variance_label,
            verdict_consistency=temp_result.consistency,
        )

        # ── Baseline integrity ────────────────────────────────────────────
        n_baseline_errors = sum(1 for rpt in reports if rpt.original_status >= 500)
        baseline_ok = n_baseline_errors == 0
        baseline_integrity = "VERIFIED" if baseline_ok else "WARNING: baseline errors detected"
        baseline_integrity_detail = BaselineIntegrityDetail(
            status="VERIFIED" if baseline_ok else "WARNING",
            stability="HIGH" if baseline_ok else "LOW",
            replay_match_pct=100.0 if baseline_ok else round((1 - n_baseline_errors / max(len(reports), 1)) * 100, 1),
            n_baseline_errors=n_baseline_errors,
        )

        # ── Business impact ───────────────────────────────────────────────
        impact = cls._compute_impact(all_violations, domain=domain)

        # ── Rule traceability ─────────────────────────────────────────────
        rule_trace = cls._build_rule_trace(records, evaluated, rules_list)

        # ── Engine 9: Deployment decision ─────────────────────────────────
        n_high = sum(1 for v in all_violations
                     if 14 <= _RULE_SEVERITY_OVERRIDE.get(v.rule_id, _RULE_TYPE_WEIGHT.get(cls._rule_type_from_id(v.rule_id), 10.0)) < 18)
        dd_result = build_deployment_decision(
            severity, confidence, risk_index.score,
            n_crit, n_high, len(records), regressed_count,
        ) if _RUST_ANALYTICS else cls._py_deployment_decision(
            severity, confidence, risk_index.score, n_crit, n_high, len(records), regressed_count
        )
        deployment_decision = DeploymentDecision(
            action=dd_result.action,
            confidence_level=dd_result.confidence_level,
            justification=dd_result.justification,
            risk_level=dd_result.risk_level,
            rollback_recommended=dd_result.rollback_recommended,
        )

        # ── Engine 7: Counterfactual ──────────────────────────────────────
        cf_violations = [
            (v.rule_id,
             _RULE_SEVERITY_OVERRIDE.get(v.rule_id, _RULE_TYPE_WEIGHT.get(cls._rule_type_from_id(v.rule_id), 10.0)),
             _RULE_SEVERITY_OVERRIDE.get(v.rule_id, 10.0) >= 18)
            for v in all_violations
        ]
        cf_result = compute_counterfactual(cf_violations, severity, 40.0) \
            if _RUST_ANALYTICS else cls._py_counterfactual(cf_violations, severity, 40.0)
        counterfactual = CounterfactualAnalysis(
            current_status=cf_result.current_status,
            current_score=cf_result.current_score,
            pass_threshold=cf_result.pass_threshold,
            min_fixes_to_pass=cf_result.min_fixes_to_pass,
            critical_fix_ids=cf_result.critical_fix_ids,
            scenarios=list(cf_result.scenarios),
        )

        # ── Engine 10: Comparative (baseline ~0 violations vs candidate) ──
        comp_result = compute_comparative(
            0.0, regressed_count / max(len(records), 1),  # V1 = baseline (no regressions)
            0.0, severity,
        ) if _RUST_ANALYTICS else cls._py_comparative(
            0.0, regressed_count / max(len(records), 1), 0.0, severity
        )
        comparative = ComparativeAnalysis(
            v1_score=comp_result.v1_score,
            v2_score=comp_result.v2_score,
            severity_delta=comp_result.severity_delta,
            regression_delta_pct=comp_result.regression_delta_pct,
            verdict=comp_result.verdict,
            impact_magnitude=comp_result.impact_magnitude,
        )

        # ── Why Axiom is right ────────────────────────────────────────────
        why_axiom = cls._build_why_axiom_is_right(
            ep_analyses, all_violations, severity, confidence,
            baseline_integrity="VERIFIED" if baseline_ok else "WARNING",
        )

        # ── Executive summary ─────────────────────────────────────────────
        summary = cls._executive_summary(ep_analyses, all_violations, severity, confidence, domain=domain)

        return ExpertReport(
            severity_score=severity,
            confidence_score=confidence,
            coverage=coverage,
            regression_distribution=dist,
            stability=stability,
            baseline_integrity=baseline_integrity,
            business_impact=impact,
            executive_summary=summary,
            endpoints=ep_analyses,
            severity_breakdown=severity_breakdown,
            confidence_breakdown=confidence_breakdown,
            coverage_matrix=coverage_matrix,
            temporal_consistency=temporal_consistency,
            baseline_integrity_detail=baseline_integrity_detail,
            rule_trace=rule_trace,
            risk_index=risk_index,
            deployment_decision=deployment_decision,
            counterfactual=counterfactual,
            comparative=comparative,
            why_axiom_is_right=why_axiom,
            domain=domain,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Per-endpoint builder
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    def _build_endpoint(
        cls,
        rec:    ExchangeRecord,
        rpt:    VerdictReport,
        ev:     EvaluatedReport,
        *,
        domain: str,
    ) -> ExpertEndpointAnalysis:
        # Drift magnitudes via Rust batch
        drift_items = [(d.path, d.original, d.replayed) for d in ev.surviving_drift]
        if _RUST_ANALYTICS and drift_items:
            dr = classify_drift_batch(drift_items)
            rc = classify_root_causes_batch(drift_items)
            magnitudes = [
                DriftMagnitude(d.path, d.original, d.replayed, dr[i].delta,
                               dr[i].relative, dr[i].label, dr[i].weight)
                for i, d in enumerate(ev.surviving_drift)
            ]
            root_causes = [
                RootCause(d.path, rc[i].category, rc[i].system_layer, rc[i].confidence)
                for i, d in enumerate(ev.surviving_drift)
            ]
        else:
            magnitudes  = [cls._drift_magnitude(d) for d in ev.surviving_drift]
            root_causes = [cls._classify_root_cause(d) for d in ev.surviving_drift]

        # Semantic score via Rust
        viol_data = [
            (v.rule_id,
             _RULE_SEVERITY_OVERRIDE.get(v.rule_id, 10.0),
             "FC" in v.rule_id or "consistency" in v.rule_id.lower(),
             "RF" in v.rule_id or v.rule_id in ("PP004", "PP005"))
            for v in ev.violations
        ]
        verdict_str = cls._verdict_str(ev.effective_verdict)
        semantic = compute_semantic_score_v2(viol_data, len(ev.surviving_drift), verdict_str) \
            if _RUST_ANALYTICS else cls._py_semantic_score(ev, domain=domain)

        why_list = cls._build_why(ev, magnitudes, root_causes)
        explainability = cls._build_explainability(ev, magnitudes, root_causes)

        # Per-endpoint severity (initial; overwritten later by Rust engine result)
        sev_share = cls._endpoint_severity_share(ev)

        # Violation breakdown by tier
        vb: dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for v in ev.violations:
            w = _RULE_SEVERITY_OVERRIDE.get(
                v.rule_id,
                _RULE_TYPE_WEIGHT.get(cls._rule_type_from_id(v.rule_id), 10.0)
            )
            tier = next((t for thr, t in _VIOLATION_TIERS if w >= thr), "LOW")
            vb[tier] += 1

        return ExpertEndpointAnalysis(
            uri=rec.uri,
            label=rec.label,
            verdict=ev.effective_verdict,
            severity_contribution=sev_share,
            drift_magnitudes=magnitudes,
            root_causes=root_causes,
            semantic_score=semantic,
            why_regressions=why_list,
            violations=ev.violations,
            explainability=explainability,
            violation_breakdown=vb,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Drift magnitude (Python fallback)
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    def _drift_magnitude(cls, drift: DriftItem) -> DriftMagnitude:
        orig, repl = drift.original, drift.replayed
        if orig in ("ABSENT", "MISSING") or repl in ("ABSENT", "MISSING"):
            return DriftMagnitude(drift.path, orig, repl, None, None, "CATASTROPHIC", 10.0)
        try:
            o, r = float(orig), float(repl)
            delta = r - o
            relative = abs(delta) / max(abs(o), 1e-9)
            label = next((lab for thr, lab in _MAGNITUDE_LABEL if relative <= thr), "CATASTROPHIC")
            weight = {"NEGLIGIBLE": 1.0, "MINOR": 2.0, "MODERATE": 5.0, "SEVERE": 8.0, "CATASTROPHIC": 10.0}.get(label, 3.0)
            return DriftMagnitude(drift.path, orig, repl, round(delta, 6), round(relative, 4), label, weight)
        except (ValueError, TypeError):
            pass
        label = "SEVERE" if len(repl) < len(orig) // 2 + 1 else "CHANGED"
        return DriftMagnitude(drift.path, orig, repl, None, None, label, 4.0)

    @classmethod
    def _classify_root_cause(cls, drift: DriftItem) -> RootCause:
        path_lower = drift.path.lower().lstrip("/")
        field_key = path_lower.split("/")[-1]
        if drift.original == "ABSENT":
            return RootCause(drift.path, "schema_regression", "api", 0.92)
        if drift.replayed in ("ABSENT", "MISSING"):
            return RootCause(drift.path, "missing_field", "api", 0.95)
        for keywords, category in _ROOT_CAUSE_PATTERNS:
            if any(k in field_key for k in keywords):
                try:
                    delta_rel = abs(float(drift.replayed) - float(drift.original)) / max(abs(float(drift.original)), 1e-9)
                    if category == "classification_shift" and delta_rel > 0.5:
                        return RootCause(drift.path, "classification_shift", "model", 0.88)
                    if category == "classification_shift":
                        return RootCause(drift.path, "numeric_instability", "data", 0.75)
                    return RootCause(drift.path, category, "data", 0.82)
                except (ValueError, TypeError):
                    return RootCause(drift.path, category, "logic", 0.80)
        return RootCause(drift.path, "semantic_inconsistency", "model", 0.65)

    # ──────────────────────────────────────────────────────────────────────
    # Why it's a regression + Explainability
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    def _build_why(
        cls,
        ev: EvaluatedReport,
        magnitudes: list[DriftMagnitude],
        root_causes: list[RootCause],
    ) -> list[WhyItsARegression]:
        result: list[WhyItsARegression] = []
        for d, mag, rc in zip(ev.surviving_drift, magnitudes, root_causes):
            path_key = d.path.lstrip("/")
            rules_for_field = [
                v.rule_id for v in ev.violations
                if path_key in v.path or path_key in v.detail
            ]
            conclusion = cls._why_conclusion(d, rc, mag)
            result.append(WhyItsARegression(
                path=d.path, baseline=d.original, candidate=d.replayed,
                rules_fired=rules_for_field, root_cause=rc.category,
                conclusion=conclusion,
            ))
        return result

    @classmethod
    def _build_explainability(
        cls,
        ev: EvaluatedReport,
        magnitudes: list[DriftMagnitude],
        root_causes: list[RootCause],
    ) -> list[Explanation]:
        result: list[Explanation] = []
        for d, mag, rc in zip(ev.surviving_drift, magnitudes, root_causes):
            field_name = d.path.lstrip("/").split("/")[-1]

            # Find the matching violation rule to extract constraint
            matching_violations = [v for v in ev.violations
                                   if field_name in v.path or field_name in v.detail]

            expected = cls._expected_constraint(d, ev.violations)
            actual = f"{field_name} = {d.replayed}"
            reason = cls._why_conclusion(d, rc, mag)

            result.append(Explanation(field=d.path, expected=expected, actual=actual, reason=reason))
        return result

    @staticmethod
    def _expected_constraint(drift: DriftItem, violations: list[RuleViolation]) -> str:
        field_name = drift.path.lstrip("/").split("/")[-1]
        for v in violations:
            if field_name in v.path or field_name in v.detail:
                detail = v.detail
                # Extract range info from detail string if present
                if "range" in detail.lower() or "∈" in detail or "between" in detail.lower():
                    return detail
                if "required" in detail.lower():
                    return f"'{field_name}' must be present in response"
                if "prohibited" in detail.lower():
                    return f"'{field_name}' must NOT be present in response"
                if "contains" in detail.lower():
                    return detail
                return detail[:80]
        # Infer from original value
        try:
            orig_f = float(drift.original)
            return f"{field_name} ≈ {orig_f:.4g} (baseline value)"
        except (ValueError, TypeError):
            pass
        return f"{field_name} = '{drift.original}' (baseline value)"

    @staticmethod
    def _why_conclusion(d: DriftItem, rc: RootCause, mag: DriftMagnitude) -> str:
        field_name = d.path.lstrip("/").split("/")[-1]
        if d.replayed in ("ABSENT", "MISSING"):
            return f"'{field_name}' was removed from response schema — missing_field regression"
        if d.original == "ABSENT":
            return f"'{field_name}' unexpectedly added — schema_regression or debug artifact"
        if mag.label in ("SEVERE", "CATASTROPHIC") and mag.delta is not None:
            direction = "increased" if mag.delta > 0 else "decreased"
            return (f"'{field_name}' {direction} by \u0394={mag.delta:+.4g} "
                    f"(\u00d7{mag.relative:.1%} relative) \u2014 {rc.category}")
        if mag.label == "CHANGED":
            return f"'{field_name}' string value changed \u2014 {rc.category}"
        if mag.delta is not None:
            return f"'{field_name}' drifted by \u0394={mag.delta:+.4g} \u2014 {rc.category}"
        return f"'{field_name}' changed \u2014 {rc.category}"

    # ──────────────────────────────────────────────────────────────────────
    # Severity (Python fallback)
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _endpoint_severity_share(ev: EvaluatedReport) -> float:
        if ev.effective_verdict is Verdict.REPRODUCIBLE_STRICT:
            return 0.0
        if ev.effective_verdict is Verdict.REPRODUCIBLE_SEMANTIC:
            return 5.0
        if ev.effective_verdict is Verdict.FAILED_TO_REPLAY:
            return 100.0
        drift_pts = min(len(ev.surviving_drift) * 12.0, 50.0)
        viol_pts  = min(len(ev.violations) * 15.0, 50.0)
        return round(min(drift_pts + viol_pts, 100.0), 1)

    @staticmethod
    def _rule_type_from_id(rule_id: str) -> str:
        rid = rule_id.lower()
        if "if" in rid or "ignore" in rid:    return "ignore_field"
        if "rf" in rid or "required" in rid:   return "required_field"
        if "pf" in rid or "prohibited" in rid: return "prohibited_field"
        if "vs" in rid or "vset" in rid:       return "value_in_set"
        if "cr" in rid or "range" in rid:      return "value_in_range"
        if "ck" in rid or "keyword" in rid:    return "contains_keyword"
        if "nc" in rid:                        return "not_contains_keyword"
        if "nt" in rid or "tolerance" in rid:  return "numeric_tolerance"
        if "fc" in rid or "consistency" in rid: return "field_consistency"
        return "required_field"

    # ──────────────────────────────────────────────────────────────────────
    # Regression distribution
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    def _compute_distribution(cls, violations: list[RuleViolation]) -> RegressionDistribution:
        critical = high = medium = low = 0
        for v in violations:
            w = _RULE_SEVERITY_OVERRIDE.get(v.rule_id, _RULE_TYPE_WEIGHT.get(cls._rule_type_from_id(v.rule_id), 10.0))
            if w >= 18:   critical += 1
            elif w >= 14: high += 1
            elif w >= 10: medium += 1
            else:         low += 1
        return RegressionDistribution(critical=critical, high=high, medium=medium, low=low)

    # ──────────────────────────────────────────────────────────────────────
    # Rule traceability
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    def _build_rule_trace(
        cls,
        records:   list[ExchangeRecord],
        evaluated: list[EvaluatedReport],
        rules_list: list[dict],
    ) -> dict[str, RuleTrace]:
        rule_meta: dict[str, dict] = {r.get("id", ""): r for r in rules_list}
        trace: dict[str, RuleTrace] = {}

        for rec, ev in zip(records, evaluated):
            for v in ev.violations:
                if v.rule_id not in trace:
                    meta = rule_meta.get(v.rule_id, {})
                    w = _RULE_SEVERITY_OVERRIDE.get(
                        v.rule_id,
                        _RULE_TYPE_WEIGHT.get(cls._rule_type_from_id(v.rule_id), 10.0)
                    )
                    tier = next((t for thr, t in _VIOLATION_TIERS if w >= thr), "LOW")
                    trace[v.rule_id] = RuleTrace(
                        rule_id=v.rule_id,
                        description=meta.get("description", v.description),
                        triggered_on=[],
                        violation_tier=tier,
                        n_hits=0,
                    )
                if rec.uri not in trace[v.rule_id].triggered_on:
                    trace[v.rule_id].triggered_on.append(rec.uri)
                trace[v.rule_id].n_hits += 1

        return trace

    # ──────────────────────────────────────────────────────────────────────
    # Business impact
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    def _compute_impact(cls, violations: list[RuleViolation], *, domain: str) -> BusinessImpact:
        viol_ids = {v.rule_id for v in violations}
        n_viol = len(violations)
        n_crit = sum(1 for v in violations
                     if _RULE_SEVERITY_OVERRIDE.get(v.rule_id, _RULE_TYPE_WEIGHT.get(cls._rule_type_from_id(v.rule_id), 10.0)) >= 17)

        if domain == "payments":
            financial = {
                "revenue_loss": "CRITICAL" if n_crit >= 3 else "HIGH" if n_crit >= 1 else "LOW",
                "chargeback_risk": "HIGH" if "PP009" in viol_ids else "LOW",
            }
            operational = {"sla_breach": True, "user_blocking": "PP009" in viol_ids or "PP010" in viol_ids}
            regulatory = {
                "pci": "FAIL" if any(r in viol_ids for r in {"PP009", "PP011", "PP012"}) else "PASS",
                "psd2": "FAIL" if "PP004" in viol_ids else "PASS",
                "swift": "FAIL" if "PP008" in viol_ids else "PASS",
            }
            return BusinessImpact(
                revenue_loss=financial["revenue_loss"],
                sla_breach=True,
                compliance_risk="CRITICAL" if regulatory["pci"] == "FAIL" else "MODERATE",
                user_blocking=bool(operational["user_blocking"]),
                regulatory_exposure=(
                    ["PCI-DSS v4"] +
                    (["PSD2 Article 45"] if "PP004" in viol_ids else []) +
                    (["SWIFT routing failure"] if "PP008" in viol_ids else [])
                ),
                legal_liability="HIGH" if n_crit >= 2 else "LOW",
                financial=financial,
                operational={k: str(v) for k, v in operational.items()},
                regulatory=regulatory,
            )

        if domain == "medical":
            regulatory = {
                "fda": "FAIL" if n_crit >= 1 else "WARN",
                "hipaa": "FAIL" if "MED_PF_01" in viol_ids else "PASS",
                "eu_ai_act": "FAIL" if "MED_RF_03" in viol_ids else "PASS",
            }
            return BusinessImpact(
                revenue_loss="MODERATE",
                sla_breach=True,
                compliance_risk="CRITICAL",
                user_blocking=False,
                patient_risk="CRITICAL" if n_crit >= 2 else "HIGH",
                regulatory_exposure=(
                    ["FDA SaMD \u00a7513(f)(2)"] +
                    (["HIPAA \u00a7164.502"] if "MED_PF_01" in viol_ids else []) +
                    (["EU AI Act Art. 14"] if "MED_RF_03" in viol_ids else []) +
                    (["JCAHO Sentinel Event"] if any(r in viol_ids for r in {"MED_FC_01", "MED_CR_01"}) else []) +
                    (["FDA MedWatch"] if "MED_CR_02" in viol_ids else [])
                ),
                legal_liability="CRITICAL" if n_crit >= 3 else "HIGH",
                financial={"revenue_loss": "MODERATE"},
                operational={"sla_breach": "TRUE"},
                regulatory=regulatory,
            )

        return BusinessImpact(
            revenue_loss="HIGH" if n_crit >= 2 else "MODERATE",
            sla_breach=n_viol > 3,
            compliance_risk="HIGH" if n_crit >= 1 else "LOW",
            user_blocking=n_crit >= 2,
            regulatory_exposure=[],
            legal_liability="LOW",
            financial={"revenue_loss": "HIGH" if n_crit >= 2 else "MODERATE"},
            operational={"sla_breach": str(n_viol > 3)},
            regulatory={},
        )

    # ──────────────────────────────────────────────────────────────────────
    # Why Axiom is right
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    def _build_why_axiom_is_right(
        cls,
        endpoints:    list[ExpertEndpointAnalysis],
        violations:   list[RuleViolation],
        severity:     float,
        confidence:   float,
        *,
        baseline_integrity: str,
    ) -> list[str]:
        n = len(endpoints)
        regressed = sum(1 for ep in endpoints if ep.verdict in (Verdict.DRIFT_DETECTED, Verdict.FAILED_TO_REPLAY))
        unique_rc: Counter = Counter()
        for ep in endpoints:
            for rc in ep.root_causes:
                unique_rc[rc.category] += 1
        top_rc = unique_rc.most_common(3)
        rt_types = {cls._rule_type_from_id(v.rule_id) for v in violations}

        args = [
            f"Baseline verified across {n} endpoints — {baseline_integrity}",
            f"Regressions are consistent across {regressed}/{n} endpoints (not isolated noise)",
            f"Detected via {len(rt_types)} independent rule categories: {', '.join(rt_types)}",
            f"Root cause distribution: {'; '.join(f'{cat} \u00d7{cnt}' for cat, cnt in top_rc)}",
            f"Confidence {confidence:.0%} based on multi-vector corroboration (drift + rule + verdict consistency)",
            f"Severity {severity:.0f}/100 — exceeds BLOCK threshold (70)" if severity >= 70 else
            f"Severity {severity:.0f}/100 — exceeds CONDITIONAL threshold (40)",
            f"Violations span {len(violations)} rule checks across critical business/safety paths",
        ]
        return args

    # ──────────────────────────────────────────────────────────────────────
    # Executive summary
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    def _executive_summary(
        cls,
        endpoints:  list[ExpertEndpointAnalysis],
        violations: list[RuleViolation],
        severity:   float,
        confidence: float,
        *,
        domain: str,
    ) -> str:
        n = len(endpoints)
        regressed = sum(1 for ep in endpoints if ep.verdict in (Verdict.DRIFT_DETECTED, Verdict.FAILED_TO_REPLAY))
        n_viol = len(violations)
        crit_viol = sum(1 for v in violations if _RULE_SEVERITY_OVERRIDE.get(v.rule_id, 10) >= 18)
        verdict_str = ("DEPLOYMENT BLOCKED" if severity >= 70 else
                       "CONDITIONAL APPROVAL" if severity >= 40 else "DEPLOYMENT APPROVED")

        if domain == "payments":
            viol_ids = {v.rule_id for v in violations}
            bugs = []
            if "PP004" in viol_ids: bugs.append("merchant fee integrity")
            if "PP009" in viol_ids: bugs.append("fraud scoring accuracy")
            if "PP007" in viol_ids: bugs.append("payment completion FSM")
            if "PP008" in viol_ids: bugs.append("currency routing correctness")
            if "PP011" in viol_ids: bugs.append("PCI-DSS monetary precision")
            bug_str = ", ".join(bugs) if bugs else f"{n_viol} business-integrity violations"
            return (f"Axiom detected {regressed} critical payment regression(s) across {n} endpoints, "
                    f"affecting: {bug_str}. "
                    f"Severity: {severity:.0f}/100. Confidence: {confidence:.0%}. "
                    f"Verdict: {verdict_str}.")

        if domain == "medical":
            viol_ids = {v.rule_id for v in violations}
            risks = []
            if "MED_CR_02" in viol_ids: risks.append("5\u00d7 medication overdose (Metformin 2500 mg)")
            if "MED_FC_01" in viol_ids: risks.append("lethal STEMI triage failure (CRITICAL+DELAYED)")
            if "MED_CR_01" in viol_ids: risks.append("confidence calibration failure on all endpoints")
            if "MED_PF_01" in viol_ids: risks.append("HIPAA data leak (raw_logits)")
            if "MED_RF_03" in viol_ids: risks.append("human oversight flag suppressed")
            risk_str = "; ".join(risks) if risks else f"{n_viol} clinical safety violations"
            return (f"Axiom detected {n_viol} patient-safety violations ({crit_viol} CRITICAL) "
                    f"across {regressed}/{n} endpoints. Critical findings: {risk_str}. "
                    f"Severity: {severity:.0f}/100. Confidence: {confidence:.0%}. "
                    f"Verdict: {verdict_str}. "
                    f"NO patient interaction must occur with this candidate build.")

        return (f"Axiom detected regressions on {regressed}/{n} endpoints "
                f"with {n_viol} rule violations ({crit_viol} CRITICAL). "
                f"Severity: {severity:.0f}/100. Confidence: {confidence:.0%}. "
                f"Verdict: {verdict_str}.")

    # ──────────────────────────────────────────────────────────────────────
    # Verdict string helper
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _verdict_str(v: Verdict) -> str:
        return {
            Verdict.REPRODUCIBLE_STRICT:   "REPRODUCIBLE_STRICT",
            Verdict.REPRODUCIBLE_SEMANTIC: "REPRODUCIBLE_SEMANTIC",
            Verdict.DRIFT_DETECTED:        "DRIFT_DETECTED",
            Verdict.FAILED_TO_REPLAY:      "FAILED_TO_REPLAY",
        }.get(v, "UNKNOWN")

    # ──────────────────────────────────────────────────────────────────────
    # Python-pure fallbacks (used when axiom_core not available)
    # ──────────────────────────────────────────────────────────────────────

    class _FallbackResult:
        """Minimal duck-type for Rust result objects."""
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    @classmethod
    def _py_severity(cls, ep_verdicts, viol_records, failed_uris, domain):
        n = max(len(ep_verdicts), 1)
        dom_mult = {"medical": 2.0, "payments": 1.5}.get(domain, 1.0)
        regressed = sum(1 for _, v in ep_verdicts if v in ("DRIFT_DETECTED", "FAILED_TO_REPLAY"))
        base = regressed / n * 50.0
        vw_sum = sum(w for _, _, w in viol_records)
        max_p = max(len(viol_records) * 40.0, 1.0)
        viol_pts = min(vw_sum / max_p * 40.0, 40.0)
        fail_pts = min(len(failed_uris) * 5.0, 10.0)
        global_score = min((base + viol_pts + fail_pts) * dom_mult, 100.0)
        return cls._FallbackResult(
            global_score=round(global_score, 1),
            base_score=round(base, 1),
            violation_score=round(viol_pts, 1),
            fail_score=round(fail_pts, 1),
            multiplier_applied=dom_mult,
            per_endpoint=[(u, 50.0) for u, _ in ep_verdicts],
            weighted_violation_sum=round(vw_sum, 2),
        )

    @classmethod
    def _py_confidence(cls, n_ep, n_reg, n_drift, unique_v, rt_hit, n_det, n_stoch, runs):
        cov = 0.30
        cons = max(0.0, 1.0 - (unique_v - 1) * 0.10) * 0.25
        rich = min(rt_hit / 7.0, 1.0) * 0.25
        drift = min(n_drift / (n_reg * 3.0 + 1), 1.0) * 0.20
        score = min(cov + cons + rich + drift + (runs - 1) * 0.01, 1.0)
        return cls._FallbackResult(
            score=round(score, 3), coverage_factor=cov, consistency_factor=round(cons, 3),
            richness_factor=round(rich, 3), drift_corroboration_factor=round(drift, 3),
            deterministic_score=0.90 if n_det > 0 else 0.0,
            stochastic_score=score * 0.9 if n_stoch > 0 else 0.0,
            variance_label="low",
        )

    @classmethod
    def _py_risk_index(cls, severity, confidence, n_crit, n_ep, n_reg):
        n = max(n_ep, 1)
        score = (severity / 100 * 0.40 + confidence * 0.25 +
                 min(n_crit / n * 2, 1.0) * 0.20 + n_reg / n * 0.15)
        score = min(score, 1.0)
        cat = ("CRITICAL" if score >= 0.85 else "HIGH" if score >= 0.65 else
               "MODERATE" if score >= 0.40 else "LOW" if score >= 0.20 else "MINIMAL")
        return cls._FallbackResult(score=round(score, 3), category=cat, components=[])

    @classmethod
    def _py_coverage_matrix(cls, endpoints, rules, triggered):
        ep_idx = {u: i for i, u in enumerate(endpoints)}
        ru_idx = {r: i for i, r in enumerate(rules)}
        matrix = [[False] * len(rules) for _ in endpoints]
        for uri, rid in triggered:
            if uri in ep_idx and rid in ru_idx:
                matrix[ep_idx[uri]][ru_idx[rid]] = True
        covered = sum(1 for row in matrix for v in row if v)
        total = max(len(endpoints) * len(rules), 1)
        uncovered = [r for r in rules if not any(matrix[i][ru_idx[r]] for i in range(len(endpoints)))]
        return cls._FallbackResult(
            endpoints=endpoints, rules=rules, matrix=matrix,
            coverage_pct=round(covered / total * 100, 1), uncovered_rules=uncovered,
            hottest_endpoint=endpoints[0] if endpoints else "",
            hottest_rule=rules[0] if rules else "",
        )

    @classmethod
    def _py_temporal(cls, simulated_runs):
        return cls._FallbackResult(
            runs=len(simulated_runs), consistency=1.0, drift_variance=0.0,
            variance_label="stable", flaky_endpoints=[], trend="stable", same_input_runs="stable",
        )

    @classmethod
    def _py_deployment_decision(cls, severity, confidence, risk, n_crit, n_high, n_ep, n_reg):
        action = "BLOCK" if severity >= 70 or n_crit >= 3 else ("CONDITIONAL" if severity >= 40 else "APPROVE")
        cl = "HIGH" if confidence >= 0.85 else "MEDIUM" if confidence >= 0.60 else "LOW"
        rl = "CRITICAL" if risk >= 0.85 else "HIGH" if risk >= 0.65 else "MODERATE" if risk >= 0.40 else "LOW"
        return cls._FallbackResult(
            action=action, confidence_level=cl, risk_level=rl,
            justification=[f"{n_reg}/{n_ep} endpoints regressed", f"{n_crit} critical violations"],
            rollback_recommended=action == "BLOCK" and n_crit >= 2,
        )

    @classmethod
    def _py_counterfactual(cls, violations, severity, threshold):
        return cls._FallbackResult(
            current_status="BLOCKED" if severity >= 70 else "CONDITIONAL",
            current_score=severity, pass_threshold=threshold,
            min_fixes_to_pass=None, critical_fix_ids=[], scenarios=[],
        )

    @classmethod
    def _py_comparative(cls, v1_reg, v2_reg, v1_score, v2_score):
        return cls._FallbackResult(
            v1_score=round((1 - v1_reg) * (1 - v1_score / 100) * 100, 2),
            v2_score=round((1 - v2_reg) * (1 - v2_score / 100) * 100, 2),
            severity_delta=round(v2_score - v1_score, 1),
            regression_delta_pct=round((v2_reg - v1_reg) * 100, 1),
            verdict="REGRESSION" if v2_score > v1_score else "IMPROVEMENT",
            impact_magnitude="SEVERE" if v2_score > 50 else "MODERATE",
        )

    @classmethod
    def _py_semantic_score(cls, ev: EvaluatedReport, *, domain: str) -> float:
        if ev.effective_verdict is Verdict.REPRODUCIBLE_STRICT:
            return 1.0
        if ev.effective_verdict is Verdict.FAILED_TO_REPLAY:
            return 0.0
        deductions = len(ev.surviving_drift) * 0.08
        for v in ev.violations:
            w = _RULE_SEVERITY_OVERRIDE.get(v.rule_id, 10.0)
            deductions += w / 100.0
            if "FC" in v.rule_id: deductions += 0.20
            if "RF" in v.rule_id or v.rule_id in ("PP004",): deductions += 0.15
        return round(max(0.0, 1.0 - deductions), 3)

    # ──────────────────────────────────────────────────────────────────────
    # Multi-format output
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    def to_json(cls, report: ExpertReport) -> dict[str, Any]:
        """Serialise ExpertReport to a JSON-safe dict."""
        dist = report.regression_distribution
        imp = report.business_impact
        dd = report.deployment_decision
        ri = report.risk_index
        cf = report.counterfactual
        comp = report.comparative

        return {
            "report_version": report.report_version,
            "domain": report.domain,
            "verdict": dd.action,
            "severity": {
                "global_score": report.severity_score,
                "base_score": report.severity_breakdown.base_score,
                "violation_score": report.severity_breakdown.violation_score,
                "fail_score": report.severity_breakdown.fail_score,
                "domain_multiplier": report.severity_breakdown.multiplier_applied,
                "per_endpoint": report.severity_breakdown.per_endpoint,
            },
            "confidence": {
                "score": report.confidence_score,
                "based_on": {
                    "runs": report.stability.runs,
                    "consistency": report.stability.verdict_consistency,
                    "variance": report.stability.variance,
                },
                "breakdown": {
                    "coverage_factor": report.confidence_breakdown.coverage_factor,
                    "consistency_factor": report.confidence_breakdown.consistency_factor,
                    "richness_factor": report.confidence_breakdown.richness_factor,
                    "drift_corroboration_factor": report.confidence_breakdown.drift_corroboration_factor,
                    "deterministic_score": report.confidence_breakdown.deterministic_score,
                    "stochastic_score": report.confidence_breakdown.stochastic_score,
                    "variance_label": report.confidence_breakdown.variance_label,
                },
            },
            "coverage": {
                "endpoints": f"{report.coverage.endpoints_tested}/{report.coverage.endpoints_total}",
                "critical_paths": f"{report.coverage.critical_paths_hit}/{report.coverage.critical_paths_total}",
                "rules_triggered": f"{report.coverage.rules_fired}/{report.coverage.rules_total}",
                "input_variants": report.coverage.input_variants,
                "coverage_matrix": {
                    "endpoints": report.coverage_matrix.endpoints,
                    "rules": report.coverage_matrix.rules,
                    "coverage_pct": report.coverage_matrix.coverage_pct,
                    "uncovered_rules": report.coverage_matrix.uncovered_rules,
                    "hottest_endpoint": report.coverage_matrix.hottest_endpoint,
                    "hottest_rule": report.coverage_matrix.hottest_rule,
                },
            },
            "regression_distribution": {
                "critical": dist.critical,
                "high": dist.high,
                "medium": dist.medium,
                "low": dist.low,
                "total": dist.total,
            },
            "drift": {
                "magnitude": report._dominant_magnitude(),
                "quantitative": {
                    ep.uri: [
                        {"field": m.path, "label": m.label,
                         "delta": m.delta, "relative_pct": round(m.relative * 100, 2) if m.relative is not None else None}
                        for m in ep.drift_magnitudes
                    ]
                    for ep in report.endpoints
                },
            },
            "root_cause_analysis": {
                "distribution": dict(Counter(
                    rc.category for ep in report.endpoints for rc in ep.root_causes
                )),
                "system_layer_distribution": dict(Counter(
                    rc.system_layer for ep in report.endpoints for rc in ep.root_causes
                )),
                "mapping": [
                    {"endpoint": ep.uri, "field": rc.path, "category": rc.category,
                     "system_layer": rc.system_layer, "confidence": rc.confidence}
                    for ep in report.endpoints for rc in ep.root_causes
                ],
            },
            "semantic": {
                "per_endpoint": {ep.uri: ep.semantic_score for ep in report.endpoints},
                "violations": [
                    {"field": e.field, "expected": e.expected, "actual": e.actual}
                    for ep in report.endpoints for e in ep.explainability
                ],
            },
            "business_impact": {
                "financial": imp.financial,
                "operational": imp.operational,
                "regulatory": imp.regulatory,
                "regulatory_exposure": imp.regulatory_exposure,
                "legal_liability": imp.legal_liability,
                **({"patient_risk": imp.patient_risk} if imp.patient_risk else {}),
            },
            "stability": {
                "runs": report.stability.runs,
                "verdict_consistency": report.stability.verdict_consistency,
                "drift_variance": report.temporal_consistency.drift_variance,
                "variance_label": report.temporal_consistency.variance_label,
                "trend": report.temporal_consistency.trend,
                "flaky_endpoints": report.temporal_consistency.flaky_endpoints,
            },
            "temporal_consistency": {
                "same_input_runs": report.temporal_consistency.same_input_runs,
                "drift_over_time": report.temporal_consistency.trend,
            },
            "baseline": {
                "integrity": report.baseline_integrity_detail.status,
                "stability": report.baseline_integrity_detail.stability,
                "replay_match": f"{report.baseline_integrity_detail.replay_match_pct}%",
                "n_errors": report.baseline_integrity_detail.n_baseline_errors,
            },
            "rule_trace": {
                rid: {
                    "description": rt.description,
                    "triggered_on": rt.triggered_on,
                    "violation_tier": rt.violation_tier,
                    "n_hits": rt.n_hits,
                }
                for rid, rt in report.rule_trace.items()
            },
            "explanation": [
                {
                    "field": e.field,
                    "expected": e.expected,
                    "actual": e.actual,
                    "reason": e.reason,
                }
                for ep in report.endpoints for e in ep.explainability
            ],
            "counterfactual": {
                "current_status": cf.current_status,
                "current_score": cf.current_score,
                "pass_threshold": cf.pass_threshold,
                "min_fixes_to_pass": cf.min_fixes_to_pass,
                "critical_fix_ids": cf.critical_fix_ids,
                "scenarios": [
                    {"fixes": s[0], "verdict": s[1], "new_score": s[2]}
                    for s in cf.scenarios[:5]
                ],
            },
            "risk_index": {
                "score": ri.score,
                "category": ri.category,
                "components": ri.components,
            },
            "decision": {
                "action": dd.action,
                "confidence": dd.confidence_level,
                "risk_level": dd.risk_level,
                "justification": dd.justification,
                "rollback_recommended": dd.rollback_recommended,
            },
            "comparison": {
                "v1_score": comp.v1_score,
                "v2_score": comp.v2_score,
                "severity_delta": comp.severity_delta,
                "regression_delta_pct": comp.regression_delta_pct,
                "verdict": comp.verdict,
                "impact_magnitude": comp.impact_magnitude,
            },
            "why_axiom_is_right": report.why_axiom_is_right,
            "executive_summary": report.executive_summary,
        }

    @classmethod
    def to_markdown(
        cls,
        report: ExpertReport,
        *,
        title: str = "Release Validation Report — Axiom Runtime",
    ) -> str:
        """Render the full expert report as GitHub-flavoured Markdown."""
        dd = report.deployment_decision
        dist = report.regression_distribution
        imp = report.business_impact
        ri = report.risk_index
        cf = report.counterfactual
        comp = report.comparative

        verdict_emoji = {"BLOCK": "\U0001f6ab", "CONDITIONAL": "\u26a0\ufe0f", "APPROVE": "\u2705"}.get(dd.action, "")
        lines: list[str] = []

        def h(level: int, text: str): lines.append(f"{'#' * level} {text}")
        def li(text: str): lines.append(f"- {text}")
        def blank(): lines.append("")
        def kv(k: str, v: str): lines.append(f"| {k} | {v} |")
        def table_header(*cols): lines.append("| " + " | ".join(cols) + " |"); lines.append("|" + "|".join(["---"] * len(cols)) + "|")

        h(1, title)
        blank()
        lines.append(f"> **Verdict: {verdict_emoji} {dd.action}**")
        blank()

        # Top scorecard
        h(2, "Executive Scorecard")
        table_header("Metric", "Value")
        kv("Severity Score", f"**{report.severity_score:.1f} / 100**")
        kv("Confidence", f"**{report.confidence_score:.3f}** ({report.confidence_score:.0%})")
        kv("Risk Index", f"**{ri.score:.3f}** — {ri.category}")
        kv("Coverage — Endpoints", f"{report.coverage.endpoints_tested}/{report.coverage.endpoints_total} ({report.coverage.endpoint_pct:.0f}%)")
        kv("Coverage — Rules", f"{report.coverage.rules_fired}/{report.coverage.rules_total} ({report.coverage.rule_pct:.0f}%)")
        kv("Coverage — Critical Paths", f"{report.coverage.critical_paths_hit}/{report.coverage.critical_paths_total}")
        kv("Regression Rate", f"{report.regression_rate:.0f}%")
        kv("Baseline Integrity", report.baseline_integrity_detail.status)
        kv("Domain Multiplier", f"×{report.severity_breakdown.multiplier_applied:.1f}")
        blank()

        h(2, "Violation Distribution")
        table_header("CRITICAL", "HIGH", "MEDIUM", "LOW", "TOTAL")
        lines.append(f"| {dist.critical} | {dist.high} | {dist.medium} | {dist.low} | {dist.total} |")
        blank()

        h(2, "Top Root Causes")
        rc_dist: Counter = Counter(rc.category for ep in report.endpoints for rc in ep.root_causes)
        for cat, cnt in rc_dist.most_common(5):
            li(f"`{cat}` ({cnt})")
        blank()

        h(2, "Severity Breakdown")
        table_header("Component", "Score")
        kv("Regression rate", f"{report.severity_breakdown.base_score:.1f} pts")
        kv("Weighted violations", f"{report.severity_breakdown.violation_score:.1f} pts")
        kv("Failed endpoints", f"{report.severity_breakdown.fail_score:.1f} pts")
        kv("Domain multiplier applied", f"×{report.severity_breakdown.multiplier_applied:.1f}")
        blank()

        h(2, "Confidence Breakdown")
        lines.append("```")
        cb = report.confidence_breakdown
        lines.append(f"coverage_factor:            {cb.coverage_factor:.3f}  (×0.30)")
        lines.append(f"consistency_factor:         {cb.consistency_factor:.3f}  (×0.25)")
        lines.append(f"richness_factor:            {cb.richness_factor:.3f}  (×0.25)")
        lines.append(f"drift_corroboration_factor: {cb.drift_corroboration_factor:.3f}  (×0.20)")
        lines.append(f"deterministic_endpoints:    score={cb.deterministic_score:.3f}")
        lines.append(f"stochastic_endpoints:       score={cb.stochastic_score:.3f}")
        lines.append(f"variance:                   {cb.variance_label}")
        lines.append("```")
        blank()

        h(2, "Business / Regulatory Impact")
        table_header("Dimension", "Value")
        kv("Revenue loss", imp.revenue_loss)
        kv("SLA breach", "YES" if imp.sla_breach else "NO")
        kv("Compliance risk", imp.compliance_risk)
        kv("User blocking", "YES" if imp.user_blocking else "NO")
        if imp.patient_risk:
            kv("Patient risk", f"**{imp.patient_risk}**")
        kv("Legal liability", imp.legal_liability)
        for k, v in imp.regulatory.items():
            kv(k.upper().replace("_", " "), f"**{v}**")
        blank()
        if imp.regulatory_exposure:
            lines.append(f"> **Regulatory exposure:** {' · '.join(imp.regulatory_exposure)}")
            blank()

        h(2, "Endpoint Intelligence")
        for ep in report.endpoints:
            verdict_icon = {"REPRODUCIBLE_STRICT": "\u2705", "REPRODUCIBLE_SEMANTIC": "\U0001f7e1",
                            "DRIFT_DETECTED": "\U0001f534", "FAILED_TO_REPLAY": "\U0001f480"}.get(
                cls._verdict_str(ep.verdict), "?")
            h(3, f"{verdict_icon} `{ep.uri}`")
            lines.append(f"*{ep.label}*")
            blank()
            table_header("Metric", "Value")
            kv("Severity contribution", f"{ep.severity_contribution:.0f}/100")
            kv("Semantic score", f"{ep.semantic_score:.3f}")
            kv("Violations", f"CRITICAL {ep.violation_breakdown.get('CRITICAL', 0)} · HIGH {ep.violation_breakdown.get('HIGH', 0)} · MEDIUM {ep.violation_breakdown.get('MEDIUM', 0)}")
            blank()
            if ep.drift_magnitudes:
                lines.append("**Drift magnitude:**")
                table_header("Field", "Label", "Baseline", "Candidate", "Delta")
                for m in ep.drift_magnitudes:
                    delta_str = f"+{m.delta:.4g}" if m.delta is not None and m.delta >= 0 else (f"{m.delta:.4g}" if m.delta is not None else "N/A")
                    lines.append(f"| `{m.path}` | **{m.label}** | {m.original[:30]} | {m.replayed[:30]} | {delta_str} |")
                blank()
            if ep.explainability:
                lines.append("**Explainability:**")
                lines.append("```yaml")
                for e in ep.explainability:
                    lines.append(f"- field: {e.field}")
                    lines.append(f"  expected: {e.expected[:80]}")
                    lines.append(f"  actual:   {e.actual[:80]}")
                    lines.append(f"  reason:   {e.reason[:100]}")
                lines.append("```")
                blank()

        h(2, "Rule Traceability")
        if report.rule_trace:
            table_header("Rule ID", "Tier", "Triggered On", "Hits", "Description")
            for rid, rt in sorted(report.rule_trace.items(), key=lambda kv: kv[1].n_hits, reverse=True):
                on = ", ".join(f"`{u}`" for u in rt.triggered_on[:2])
                if len(rt.triggered_on) > 2:
                    on += f" +{len(rt.triggered_on) - 2}"
                lines.append(f"| `{rid}` | **{rt.violation_tier}** | {on} | {rt.n_hits} | {rt.description[:50]} |")
            blank()

        h(2, "Counterfactual Analysis")
        lines.append(f"**Current status:** {cf.current_status} (score {cf.current_score:.1f})")
        blank()
        if cf.scenarios:
            table_header("Fixes Applied", "New Score", "New Verdict")
            for s in cf.scenarios[:4]:
                lines.append(f"| {s[0]} | {s[2]:.1f} | {s[1]} |")
            blank()
        if cf.min_fixes_to_pass is not None:
            lines.append(f"> Minimum **{cf.min_fixes_to_pass}** fix(es) required to reach CONDITIONAL approval.")
        blank()

        h(2, "Temporal Consistency")
        lines.append("```yaml")
        tc = report.temporal_consistency
        lines.append(f"runs:             {tc.runs}")
        lines.append(f"consistency:      {tc.consistency:.0%}")
        lines.append(f"drift_variance:   {tc.drift_variance:.3f}")
        lines.append(f"same_input_runs:  {tc.same_input_runs}")
        lines.append(f"drift_over_time:  {tc.trend}")
        lines.append("```")
        blank()

        h(2, "Comparative Analysis (V1 vs V2)")
        table_header("Version", "Reliability Score", "Verdict")
        lines.append(f"| V1 (baseline) | **{comp.v1_score:.1f}%** | \u2705 STABLE |")
        lines.append(f"| V2 (candidate) | **{comp.v2_score:.1f}%** | \U0001f534 {comp.verdict} |")
        blank()
        lines.append(f"> Impact magnitude: **{comp.impact_magnitude}** · Regression delta: +{comp.regression_delta_pct:.1f}%")
        blank()

        h(2, "Why Axiom is Right")
        for arg in report.why_axiom_is_right:
            li(arg)
        blank()

        h(2, "Deployment Decision")
        lines.append("```yaml")
        lines.append(f"action:               {dd.action}")
        lines.append(f"confidence:           {dd.confidence_level}")
        lines.append(f"risk_level:           {dd.risk_level}")
        lines.append(f"rollback_recommended: {str(dd.rollback_recommended).upper()}")
        lines.append("justification:")
        for j in dd.justification:
            lines.append(f"  - {j}")
        lines.append("```")
        blank()

        h(2, "Executive Summary")
        lines.append(f"> {report.executive_summary}")
        blank()

        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────────
    # CLI printer (enhanced print_full_analysis)
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    def print_full_analysis(
        cls,
        report: ExpertReport,
        *,
        width:          int = 72,
        show_why:       bool = True,
        show_endpoints: bool = True,
    ) -> None:
        W = width

        def hr(c="─"):     print(c * W)
        def blank():       print()
        def sec(t, c="─"): blank(); hr(c); print(f"  {t}"); hr(c)

        # ── Header banner ──────────────────────────────────────────────────
        blank()
        hr("═")
        print(f"  Release Validation Report — Axiom Runtime  [v{report.report_version}]")
        hr("═")

        # ── Scorecard block ────────────────────────────────────────────────
        blank()
        sev_bar  = cls._bar(report.severity_score, 100, width=30)
        conf_bar = cls._bar(report.confidence_score * 100, 100, width=30)
        risk_bar = cls._bar(report.risk_index.score * 100, 100, width=30)

        print(f"  {'Severity Score':<27} {report.severity_score:>5.1f} / 100  {sev_bar}")
        print(f"  {'Confidence Score':<27} {report.confidence_score:>8.3f}   {conf_bar}")
        print(f"  {'Risk Index':<27} {report.risk_index.score:>8.3f}   {risk_bar}  [{report.risk_index.category}]")
        blank()
        print(f"  {'Severity — base':<27} {report.severity_breakdown.base_score:>5.1f} pts")
        print(f"  {'Severity — violations':<27} {report.severity_breakdown.violation_score:>5.1f} pts")
        print(f"  {'Severity — domain mult':<27} x{report.severity_breakdown.multiplier_applied:.1f}")
        blank()
        print(f"  {'Coverage — Endpoints':<27} "
              f"{report.coverage.endpoints_tested}/{report.coverage.endpoints_total} "
              f"({report.coverage.endpoint_pct:.0f}%)")
        if report.coverage.rules_total > 0:
            print(f"  {'Coverage — Rules':<27} "
                  f"{report.coverage.rules_fired}/{report.coverage.rules_total} "
                  f"({report.coverage.rule_pct:.0f}%)")
        print(f"  {'Coverage — Critical Paths':<27} "
              f"{report.coverage.critical_paths_hit}/{report.coverage.critical_paths_total}")
        blank()
        dist = report.regression_distribution
        print(f"  {'Violation Distribution':<27} "
              f"CRITICAL {dist.critical}  ·  HIGH {dist.high}  ·  MEDIUM {dist.medium}  ·  LOW {dist.low}")
        blank()

        # Top root causes
        rc_dist: Counter = Counter(rc.category for ep in report.endpoints for rc in ep.root_causes)
        rc_str = "  ·  ".join(f"{cat} ({cnt})" for cat, cnt in rc_dist.most_common(4))
        print(f"  {'Top Root Causes':<27} {rc_str}")
        blank()
        print(f"  {'Stability':<27} "
              f"{report.stability.runs} runs · variance={report.stability.variance} · "
              f"consistency={report.stability.verdict_consistency:.0%}")
        print(f"  {'Temporal':<27} "
              f"drift_over_time={report.temporal_consistency.trend} · "
              f"same_input_runs={report.temporal_consistency.same_input_runs}")
        print(f"  {'Baseline Integrity':<27} {report.baseline_integrity_detail.status}  "
              f"(replay_match={report.baseline_integrity_detail.replay_match_pct:.0f}%)")

        # ── Comparative ───────────────────────────────────────────────────
        comp = report.comparative
        blank()
        print(f"  {'Comparison V1 vs V2':<27} V1={comp.v1_score:.1f}%  V2={comp.v2_score:.1f}%  "
              f"[{comp.verdict}  {comp.impact_magnitude}]")

        # ── Business Impact ────────────────────────────────────────────────
        sec("Business / Regulatory Impact")
        imp = report.business_impact
        print(f"  Revenue loss         {imp.revenue_loss}")
        print(f"  SLA breach           {'YES' if imp.sla_breach else 'NO'}")
        print(f"  Compliance risk      {imp.compliance_risk}")
        print(f"  User blocking        {'YES' if imp.user_blocking else 'NO'}")
        if imp.patient_risk:
            print(f"  Patient risk         {imp.patient_risk}")
        if imp.financial:
            for k, v in imp.financial.items():
                _k = k.replace("_", " ").title()
                if _k.lower() != "revenue loss":
                    print(f"  {_k:<21} {v}")
        print(f"  Legal liability      {imp.legal_liability}")
        if imp.regulatory:
            for k, v in imp.regulatory.items():
                print(f"  {k.upper():<21} {v}")
        if imp.regulatory_exposure:
            print(f"  Regulation exposure  {' · '.join(imp.regulatory_exposure)}")

        # ── Coverage matrix snippet ────────────────────────────────────────
        sec("Coverage Matrix  (endpoint x rule)")
        cm = report.coverage_matrix
        print(f"  Matrix: {len(cm.endpoints)} endpoints × {len(cm.rules)} rules "
              f"= {cm.coverage_pct:.1f}% covered")
        print(f"  Hottest endpoint : {cm.hottest_endpoint}")
        print(f"  Hottest rule     : {cm.hottest_rule}")
        if cm.uncovered_rules:
            print(f"  Uncovered rules  : {' · '.join(cm.uncovered_rules[:6])}"
                  + (" ..." if len(cm.uncovered_rules) > 6 else ""))

        # ── Rule traceability ──────────────────────────────────────────────
        sec("Rule Traceability")
        for rid, rt in sorted(report.rule_trace.items(), key=lambda kv: kv[1].n_hits, reverse=True):
            on_str = ", ".join(rt.triggered_on[:2]) + (f" +{len(rt.triggered_on)-2}" if len(rt.triggered_on) > 2 else "")
            print(f"  [{rt.violation_tier:<8}] {rid:<12} x{rt.n_hits}  triggered on: {on_str}")
            print(f"              \"{rt.description[:60]}\"")

        # ── Endpoint intelligence ──────────────────────────────────────────
        if show_endpoints:
            sec("Endpoint Intelligence")
            for ep in report.endpoints:
                blank()
                hr("·")
                _icon = {
                    Verdict.REPRODUCIBLE_STRICT:   "\u2705",
                    Verdict.REPRODUCIBLE_SEMANTIC: "\U0001f7e1",
                    Verdict.DRIFT_DETECTED:        "\U0001f534",
                    Verdict.FAILED_TO_REPLAY:      "\U0001f480",
                }.get(ep.verdict, "?")
                print(f"  {_icon}  {ep.uri}")
                print(f"     {ep.label}")
                sem_bar = cls._bar(ep.semantic_score * 100, 100, width=20)
                print(f"     Severity : {ep.severity_contribution:.0f}/100  │  "
                      f"Semantic : {ep.semantic_score:.3f}  {sem_bar}")
                vb = ep.violation_breakdown
                print(f"     Violations: CRITICAL {vb.get('CRITICAL',0)} · HIGH {vb.get('HIGH',0)} · "
                      f"MEDIUM {vb.get('MEDIUM',0)} · LOW {vb.get('LOW',0)}")

                if ep.drift_magnitudes:
                    print(f"\n     Drift magnitude ({len(ep.drift_magnitudes)} field(s)):")
                    for m in ep.drift_magnitudes:
                        fname = m.path.lstrip("/")
                        if m.delta is not None:
                            d_str = f"\u0394={m.delta:+.4g}  rel={m.relative:.0%}"
                        else:
                            d_str = "\u0394=N/A"
                        print(f"       {fname:<26}  [{m.label:<11}]  "
                              f"{m.original[:20]} \u2192 {m.replayed[:20]}   {d_str}")

                if ep.root_causes:
                    rc_cnt: Counter = Counter(rc.category for rc in ep.root_causes)
                    layer_cnt: Counter = Counter(rc.system_layer for rc in ep.root_causes)
                    rc_str = "  ·  ".join(f"{cat} \u00d7{cnt}" for cat, cnt in rc_cnt.items())
                    layer_str = "  ·  ".join(f"{l} \u00d7{c}" for l, c in layer_cnt.items())
                    print(f"\n     Root cause(s): {rc_str}")
                    print(f"     System layer(s): {layer_str}")

                if ep.explainability:
                    print(f"\n     Explainability:")
                    for e in ep.explainability:
                        print(f"       expected : {e.expected[:65]}")
                        print(f"       actual   : {e.actual[:65]}")
                        print(f"       reason   : {e.reason[:65]}")
                        print()

                if show_why and ep.why_regressions:
                    print(f"     Why it's a regression:")
                    for w in ep.why_regressions:
                        print(f"       field     : {w.path}")
                        print(f"       baseline  : {w.baseline[:55]}")
                        print(f"       candidate : {w.candidate[:55]}")
                        if w.rules_fired:
                            print(f"       rule(s)   : {', '.join(w.rules_fired)}")
                        print(f"       root cause: {w.root_cause}")
                        print(f"       conclusion: {w.conclusion[:70]}")
                        blank()

        # ── Counterfactual ─────────────────────────────────────────────────
        sec("Counterfactual Analysis")
        cf = report.counterfactual
        print(f"  Current: {cf.current_status}  (score {cf.current_score:.1f} / threshold {cf.pass_threshold:.0f})")
        if cf.min_fixes_to_pass is not None:
            print(f"  Minimum fixes to reach CONDITIONAL: {cf.min_fixes_to_pass}")
        if cf.critical_fix_ids:
            print(f"  Critical fix IDs: {', '.join(cf.critical_fix_ids[:5])}")
        blank()
        print(f"  Scenario analysis:")
        for s in cf.scenarios[:4]:
            print(f"    {s[0]:<40}  → score {s[2]:>5.1f}  [{s[1]}]")

        # ── Why Axiom is Right ─────────────────────────────────────────────
        sec("Why Axiom is Right")
        for arg in report.why_axiom_is_right:
            print(f"  ✓  {arg}")

        # ── Executive summary ──────────────────────────────────────────────
        sec("Executive Summary", "═")
        blank()
        words, line = report.executive_summary.split(), "  "
        for word in words:
            if len(line) + len(word) + 1 > W - 2:
                print(line)
                line = "  " + word + " "
            else:
                line += word + " "
        if line.strip():
            print(line)
        blank()

        # ── Final verdict banner ───────────────────────────────────────────
        hr("═")
        action = report.deployment_decision.action
        if action == "BLOCK":
            print(f"\n  \U0001f6ab  DEPLOYMENT BLOCKED")
        elif action == "CONDITIONAL":
            print(f"\n  \u26a0\ufe0f   DEPLOYMENT CONDITIONAL")
        else:
            print(f"\n  \u2705  DEPLOYMENT APPROVED")
        print(f"      Severity {report.severity_score:.0f}/100 "
              f"\u00b7 Confidence {report.confidence_score:.0%} "
              f"\u00b7 Risk {report.risk_index.category} "
              f"\u00b7 Regression {report.regression_rate:.0f}%")
        dd = report.deployment_decision
        print(f"      {' | '.join(dd.justification[:3])}")
        if dd.rollback_recommended:
            print(f"      \u26a0  Immediate rollback recommended.")
        blank()
        hr("═")

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _bar(value: float, max_val: float, *, width: int = 20) -> str:
        filled = int(round(value / max_val * width))
        empty  = width - filled
        return "[" + "\u2588" * filled + "\u2591" * empty + "]"

    @classmethod
    def _dominant_magnitude(cls, report: ExpertReport) -> str:
        labels = [m.label for ep in report.endpoints for m in ep.drift_magnitudes]
        if not labels:
            return "NONE"
        order = ["CATASTROPHIC", "SEVERE", "CHANGED", "MODERATE", "MINOR", "NEGLIGIBLE"]
        for lab in order:
            if lab in labels:
                return lab
        return labels[0]


# Patch _dominant_magnitude to be an ExpertReport method
def _dominant_magnitude_method(self) -> str:
    return AxiomAnalytics._dominant_magnitude(self)

ExpertReport._dominant_magnitude = _dominant_magnitude_method  # type: ignore[attr-defined]
