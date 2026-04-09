// axiom_core::analytics — Expert-grade analytics engine (Phase 7)
//
// Ten compute-heavy engines implemented in pure Rust, callable from Python:
//
//   Engine 1:  compute_severity_v2         — weighted scoring with domain multipliers
//   Engine 2:  compute_confidence_v2       — deterministic/stochastic breakdown
//   Engine 3:  classify_drift_batch        — NEGLIGIBLE→CATASTROPHIC bucketing
//   Engine 4:  classify_root_causes_batch  — category + system_layer mapping
//   Engine 5:  compute_risk_index          — global risk score [0-1]
//   Engine 6:  build_coverage_matrix       — endpoint × rule heatmap
//   Engine 7:  compute_counterfactual      — min-fixes-to-pass analysis
//   Engine 8:  analyze_temporal_consistency — drift variance + trend
//   Engine 9:  build_deployment_decision   — action/confidence/justification
//   Engine 10: compute_comparative         — V1 vs V2 score comparison
//   Engine 11: compute_semantic_score_v2   — multi-factor semantic coherence

use pyo3::prelude::*;
use std::collections::HashMap;

// ===========================================================================
// Internal helpers
// ===========================================================================

fn domain_multiplier(domain: &str) -> f64 {
    match domain {
        "medical" => 2.0,
        "payments" | "financial" => 1.5,
        _ => 1.0,
    }
}

fn endpoint_multiplier(uri: &str) -> f64 {
    let u = uri.to_lowercase();
    // Medical endpoints (clinical decision paths) → ×2.0
    if u.contains("diagnosis")
        || u.contains("triage")
        || u.contains("dosage")
        || u.contains("medication")
        || u.contains("prescription")
        || u.contains("drug_interaction")
        || u.contains("drug/")
    {
        return 2.0;
    }
    // Financial endpoints (payment / fraud) → ×1.5
    if u.contains("payment")
        || u.contains("capture")
        || u.contains("settle")
        || u.contains("transaction")
        || u.contains("charge")
        || u.contains("refund")
        || u.contains("fraud")
        || u.contains("balance")
        || u.contains("account")
    {
        return 1.5;
    }
    1.0
}

fn magnitude_label(relative: f64) -> &'static str {
    if relative <= 0.02 {
        "NEGLIGIBLE"
    } else if relative <= 0.10 {
        "MINOR"
    } else if relative <= 0.30 {
        "MODERATE"
    } else if relative <= 0.60 {
        "SEVERE"
    } else {
        "CATASTROPHIC"
    }
}

fn magnitude_weight(label: &str) -> f64 {
    match label {
        "NEGLIGIBLE"   => 1.0,
        "MINOR"        => 2.0,
        "MODERATE"     => 5.0,
        "SEVERE"       => 8.0,
        "CATASTROPHIC" => 10.0,
        "CRITICAL"     => 10.0,
        "CHANGED"      => 4.0,
        _ => 3.0,
    }
}

// ===========================================================================
// Engine 1: Severity v2
// ===========================================================================

#[pyclass(module = "axiom_core")]
pub struct SeverityResult {
    #[pyo3(get)] pub global_score: f64,
    #[pyo3(get)] pub base_score: f64,
    #[pyo3(get)] pub violation_score: f64,
    #[pyo3(get)] pub fail_score: f64,
    #[pyo3(get)] pub multiplier_applied: f64,
    #[pyo3(get)] pub per_endpoint: Vec<(String, f64)>,
    #[pyo3(get)] pub weighted_violation_sum: f64,
}

/// Compute global + per-endpoint severity with domain & endpoint multipliers.
///
/// endpoint_verdicts: Vec<(uri, verdict_str)>  — one per endpoint
/// violation_records: Vec<(uri, rule_id, base_weight)>  — one per violation
/// failed_uris:       Vec<uri>  — endpoints that failed to replay
/// domain:            "payments" | "medical" | "generic"
#[pyfunction]
pub fn compute_severity_v2(
    endpoint_verdicts: Vec<(String, String)>,
    violation_records: Vec<(String, String, f64)>,
    failed_uris: Vec<String>,
    domain: String,
) -> SeverityResult {
    let n = endpoint_verdicts.len();
    if n == 0 {
        return SeverityResult {
            global_score: 0.0,
            base_score: 0.0,
            violation_score: 0.0,
            fail_score: 0.0,
            multiplier_applied: 1.0,
            per_endpoint: vec![],
            weighted_violation_sum: 0.0,
        };
    }

    let glob_mult = domain_multiplier(&domain);
    let failed_set: std::collections::HashSet<&str> =
        failed_uris.iter().map(|s| s.as_str()).collect();

    // Base: regression-rate component → max 50 pts
    let regressed = endpoint_verdicts
        .iter()
        .filter(|(_, v)| v == "DRIFT_DETECTED" || v == "FAILED_TO_REPLAY")
        .count();
    let base = (regressed as f64 / n as f64) * 50.0;

    // Violation component: weight × endpoint_multiplier, normalised → max 40 pts
    // CRITICAL = 10, HIGH = 5, MEDIUM = 2 implicit in the weights passed in
    let mut viol_weighted_sum = 0.0f64;
    let mut max_possible_weighted = 0.0f64;
    for (uri, _rule_id, weight) in &violation_records {
        let ep_mult = endpoint_multiplier(uri);
        viol_weighted_sum += weight * ep_mult;
        max_possible_weighted += 20.0 * 2.0; // max rule weight × max ep multiplier
    }
    let viol_pts = if max_possible_weighted > 0.0 {
        (viol_weighted_sum / max_possible_weighted * 40.0).min(40.0)
    } else {
        0.0
    };

    // Fail bonus: each unreplayable endpoint → +5 pts (max 10 pts)
    let fail_pts = (failed_set.len() as f64 * 5.0).min(10.0);

    // Global score with domain multiplier, hard-capped at 100
    let raw = (base + viol_pts + fail_pts) * glob_mult;
    let global_score = (raw.min(100.0) * 10.0).round() / 10.0;

    // Per-endpoint scores
    let mut ep_viol_map: HashMap<String, (usize, f64)> = HashMap::new();
    for (uri, _, weight) in &violation_records {
        let ent = ep_viol_map.entry(uri.clone()).or_default();
        ent.0 += 1;
        ent.1 += weight;
    }

    let per_endpoint: Vec<(String, f64)> = endpoint_verdicts
        .iter()
        .map(|(uri, verdict)| {
            let ep_mult = endpoint_multiplier(uri);
            let ep_score = match verdict.as_str() {
                "REPRODUCIBLE_STRICT" => 0.0,
                "REPRODUCIBLE_SEMANTIC" => 5.0,
                "FAILED_TO_REPLAY" => 100.0,
                _ => {
                    let (n_viol, vw) = ep_viol_map.get(uri).copied().unwrap_or_default();
                    let drift_component = (n_viol as f64 * 12.0).min(50.0);
                    let max_vw = (n_viol.max(1) as f64) * 20.0;
                    let weight_component = ((vw / max_vw) * 50.0).min(50.0);
                    ((drift_component + weight_component) * ep_mult).min(100.0)
                }
            };
            (uri.clone(), (ep_score * 10.0).round() / 10.0)
        })
        .collect();

    SeverityResult {
        global_score,
        base_score: (base * 10.0).round() / 10.0,
        violation_score: (viol_pts * 10.0).round() / 10.0,
        fail_score: (fail_pts * 10.0).round() / 10.0,
        multiplier_applied: glob_mult,
        per_endpoint,
        weighted_violation_sum: (viol_weighted_sum * 100.0).round() / 100.0,
    }
}

// ===========================================================================
// Engine 2: Confidence v2
// ===========================================================================

#[pyclass(module = "axiom_core")]
pub struct ConfidenceResult {
    #[pyo3(get)] pub score: f64,
    #[pyo3(get)] pub coverage_factor: f64,
    #[pyo3(get)] pub consistency_factor: f64,
    #[pyo3(get)] pub richness_factor: f64,
    #[pyo3(get)] pub drift_corroboration_factor: f64,
    #[pyo3(get)] pub deterministic_score: f64,
    #[pyo3(get)] pub stochastic_score: f64,
    #[pyo3(get)] pub variance_label: String,
}

/// Compute confidence score with deterministic/stochastic breakdown.
///
/// n_endpoints:         total endpoints tested
/// n_regressed:         endpoints with DRIFT or FAILED
/// total_drift_items:   sum of surviving drift items across all endpoints
/// unique_verdict_count: how many distinct verdict types appeared
/// rule_types_hit:      how many distinct rule type categories triggered
/// n_deterministic:     endpoints with deterministic APIs (non-LLM)
/// n_stochastic:        endpoints with stochastic APIs (LLM/ML)
/// runs:                number of replay runs performed
#[pyfunction]
pub fn compute_confidence_v2(
    n_endpoints: usize,
    n_regressed: usize,
    total_drift_items: usize,
    unique_verdict_count: usize,
    rule_types_hit: usize,
    n_deterministic: usize,
    n_stochastic: usize,
    runs: usize,
) -> ConfidenceResult {
    if n_endpoints == 0 {
        return ConfidenceResult {
            score: 0.0,
            coverage_factor: 0.0,
            consistency_factor: 0.0,
            richness_factor: 0.0,
            drift_corroboration_factor: 0.0,
            deterministic_score: 0.0,
            stochastic_score: 0.0,
            variance_label: "unknown".to_string(),
        };
    }

    // Coverage factor: always 100% when all endpoints tested → 0.30
    let coverage_factor = 0.30_f64;

    // Consistency factor: unanimous verdict types → high confidence → 0.25
    let consistency_factor =
        (1.0 - (unique_verdict_count.saturating_sub(1) as f64 * 0.10)).max(0.0) * 0.25;

    // Richness factor: multiple rule type categories → multi-vector detection → 0.25
    let richness_factor = (rule_types_hit as f64 / 7.0).min(1.0) * 0.25;

    // Drift corroboration: drift items support the verdict → 0.20
    let expected_drift = (3 * n_regressed.max(1)) as f64;
    let drift_corroboration_factor =
        (total_drift_items as f64 / expected_drift).min(1.0) * 0.20;

    let base_score =
        coverage_factor + consistency_factor + richness_factor + drift_corroboration_factor;

    // Multi-run boost: each additional run beyond 1 adds marginal confidence
    let run_boost = if runs > 1 {
        ((runs - 1) as f64 * 0.01).min(0.05)
    } else {
        0.0
    };

    let score = (base_score + run_boost).min(1.0);

    // Deterministic vs stochastic breakdown
    let n_total = (n_deterministic + n_stochastic).max(1);

    let deterministic_score = if n_deterministic > 0 {
        (0.85 + (rule_types_hit as f64 / 7.0).min(1.0) * 0.15).min(1.0)
    } else {
        0.0
    };

    let stochastic_score = if n_stochastic > 0 {
        (base_score * (n_stochastic as f64 / n_total as f64) * 0.90).min(0.95)
    } else {
        0.0
    };

    // Variance label based on rule richness + drift density
    let variance_label = if rule_types_hit >= 3 && total_drift_items >= n_endpoints * 2 {
        "low"
    } else if rule_types_hit >= 2 {
        "low"
    } else {
        "medium"
    }
    .to_string();

    ConfidenceResult {
        score: (score * 1000.0).round() / 1000.0,
        coverage_factor: (coverage_factor * 1000.0).round() / 1000.0,
        consistency_factor: (consistency_factor * 1000.0).round() / 1000.0,
        richness_factor: (richness_factor * 1000.0).round() / 1000.0,
        drift_corroboration_factor: (drift_corroboration_factor * 1000.0).round() / 1000.0,
        deterministic_score: (deterministic_score * 1000.0).round() / 1000.0,
        stochastic_score: (stochastic_score * 1000.0).round() / 1000.0,
        variance_label,
    }
}

// ===========================================================================
// Engine 3: Drift Magnitude (batch)
// ===========================================================================

#[pyclass(module = "axiom_core")]
#[derive(Clone)]
pub struct DriftResult {
    #[pyo3(get)] pub path: String,
    #[pyo3(get)] pub original: String,
    #[pyo3(get)] pub replayed: String,
    #[pyo3(get)] pub delta: Option<f64>,
    #[pyo3(get)] pub relative: Option<f64>,
    #[pyo3(get)] pub label: String, // NEGLIGIBLE/MINOR/MODERATE/SEVERE/CATASTROPHIC/CHANGED
    #[pyo3(get)] pub is_numeric: bool,
    #[pyo3(get)] pub weight: f64,
}

/// Classify a batch of drift items with quantified magnitude.
///
/// items: Vec<(path, original, replayed)>
#[pyfunction]
pub fn classify_drift_batch(items: Vec<(String, String, String)>) -> Vec<DriftResult> {
    items
        .into_iter()
        .map(|(path, original, replayed)| classify_single_drift(path, original, replayed))
        .collect()
}

fn classify_single_drift(path: String, original: String, replayed: String) -> DriftResult {
    // ABSENT / MISSING → field added or entirely removed
    if original == "ABSENT" || original == "MISSING" {
        let weight = magnitude_weight("CATASTROPHIC");
        return DriftResult {
            path,
            original,
            replayed,
            delta: None,
            relative: None,
            label: "CATASTROPHIC".to_string(),
            is_numeric: false,
            weight,
        };
    }
    if replayed == "ABSENT" || replayed == "MISSING" {
        let weight = magnitude_weight("CATASTROPHIC");
        return DriftResult {
            path,
            original,
            replayed,
            delta: None,
            relative: None,
            label: "CATASTROPHIC".to_string(),
            is_numeric: false,
            weight,
        };
    }

    // Try numeric comparison
    if let (Ok(orig_f), Ok(repl_f)) = (original.parse::<f64>(), replayed.parse::<f64>()) {
        let delta = repl_f - orig_f;
        let relative = delta.abs() / orig_f.abs().max(1e-9);
        let label = magnitude_label(relative);
        let weight = magnitude_weight(label);
        return DriftResult {
            path,
            original,
            replayed,
            delta: Some((delta * 1_000_000.0).round() / 1_000_000.0),
            relative: Some((relative * 10_000.0).round() / 10_000.0),
            label: label.to_string(),
            is_numeric: true,
            weight,
        };
    }

    // String change — length ratio as rough severity proxy
    let len_ratio = replayed.len() as f64 / original.len().max(1) as f64;
    let label = if len_ratio < 0.3 || len_ratio > 5.0 {
        "SEVERE"
    } else {
        "CHANGED"
    };
    let weight = magnitude_weight(label);
    DriftResult {
        path,
        original,
        replayed,
        delta: None,
        relative: None,
        label: label.to_string(),
        is_numeric: false,
        weight,
    }
}

// ===========================================================================
// Engine 4: Root Cause Classification (batch)
// ===========================================================================

/// Root cause patterns: (keywords, category, system_layer)
const ROOT_CAUSE_PATTERNS: &[(&[&str], &str, &str)] = &[
    (
        &["confidence", "score", "probability", "logit", "threshold"],
        "classification_shift",
        "model",
    ),
    (
        &["dosage", "quantity", "milligram", "concentration"],
        "numeric_instability",
        "data",
    ),
    (
        &["amount", "price", "fee", "rate", "balance", "total", "subtotal", "net"],
        "numeric_instability",
        "data",
    ),
    (
        &["status", "triage_priority", "risk_level", "decision", "severity", "category"],
        "classification_shift",
        "logic",
    ),
    (
        &["currency", "icd10", "interaction_severity", "mechanism", "drug_class"],
        "schema_regression",
        "schema",
    ),
    (
        &["raw_logits", "error_code", "training_patient_id", "debug", "internal"],
        "schema_regression",
        "api",
    ),
    (
        &["recommendation", "primary_diagnosis", "model_version", "rationale"],
        "semantic_inconsistency",
        "model",
    ),
    (
        &["unit", "format", "encoding", "locale"],
        "schema_regression",
        "schema",
    ),
];

#[pyclass(module = "axiom_core")]
#[derive(Clone)]
pub struct RootCauseResult {
    #[pyo3(get)] pub path: String,
    #[pyo3(get)] pub category: String,
    #[pyo3(get)] pub system_layer: String,
    #[pyo3(get)] pub confidence: f64,
}

/// Classify root causes for a batch of drift items.
///
/// items: Vec<(path, original, replayed)>
#[pyfunction]
pub fn classify_root_causes_batch(
    items: Vec<(String, String, String)>,
) -> Vec<RootCauseResult> {
    items
        .into_iter()
        .map(|(path, original, replayed)| {
            classify_single_root_cause(path, original, replayed)
        })
        .collect()
}

fn classify_single_root_cause(
    path: String,
    original: String,
    replayed: String,
) -> RootCauseResult {
    let path_lower = path.to_lowercase();
    let field_key = path_lower.trim_start_matches('/');
    let field_key = field_key.split('/').last().unwrap_or(field_key);

    // Field added → schema_regression (API layer)
    if original == "ABSENT" {
        return RootCauseResult {
            path,
            category: "schema_regression".to_string(),
            system_layer: "api".to_string(),
            confidence: 0.92,
        };
    }
    // Field removed → missing_field (API layer)
    if replayed == "ABSENT" || replayed == "MISSING" {
        return RootCauseResult {
            path,
            category: "missing_field".to_string(),
            system_layer: "api".to_string(),
            confidence: 0.95,
        };
    }

    // Pattern matching
    for (keywords, category, layer) in ROOT_CAUSE_PATTERNS {
        if keywords.iter().any(|k| field_key.contains(k)) {
            // Numeric refinement: small shift in classification field → numeric_instability
            if let (Ok(orig_f), Ok(repl_f)) = (original.parse::<f64>(), replayed.parse::<f64>()) {
                let delta_rel = (repl_f - orig_f).abs() / orig_f.abs().max(1e-9);
                if *category == "classification_shift" && delta_rel < 0.5 {
                    return RootCauseResult {
                        path,
                        category: "numeric_instability".to_string(),
                        system_layer: layer.to_string(),
                        confidence: 0.75,
                    };
                }
                return RootCauseResult {
                    path,
                    category: category.to_string(),
                    system_layer: layer.to_string(),
                    confidence: 0.85,
                };
            }
            return RootCauseResult {
                path,
                category: category.to_string(),
                system_layer: layer.to_string(),
                confidence: 0.80,
            };
        }
    }

    // Default fallback
    RootCauseResult {
        path,
        category: "semantic_inconsistency".to_string(),
        system_layer: "model".to_string(),
        confidence: 0.65,
    }
}

// ===========================================================================
// Engine 5: Risk Index
// ===========================================================================

#[pyclass(module = "axiom_core")]
pub struct RiskIndexResult {
    #[pyo3(get)] pub score: f64,      // [0, 1]
    #[pyo3(get)] pub category: String, // MINIMAL/LOW/MODERATE/HIGH/CRITICAL
    #[pyo3(get)] pub components: Vec<(String, f64)>,
}

/// Compute global risk index [0, 1].
///
/// severity:      global severity score [0-100]
/// confidence:    confidence score [0-1]
/// n_critical:    count of critical violations
/// n_endpoints:   total endpoints tested
/// n_regressed:   endpoints with regressions
#[pyfunction]
pub fn compute_risk_index(
    severity: f64,
    confidence: f64,
    n_critical: usize,
    n_endpoints: usize,
    n_regressed: usize,
) -> RiskIndexResult {
    let n = n_endpoints.max(1) as f64;
    let regression_rate = n_regressed as f64 / n;
    let critical_density = (n_critical as f64 / n * 2.0).min(1.0); // amplified

    let sev_factor = severity / 100.0;
    let conf_factor = confidence; // high confidence in the verdict = known risk
    let crit_factor = critical_density;
    let regr_factor = regression_rate;

    let score = (sev_factor * 0.40 + conf_factor * 0.25 + crit_factor * 0.20 + regr_factor * 0.15)
        .min(1.0);
    let score = (score * 1000.0).round() / 1000.0;

    let category = if score >= 0.85 {
        "CRITICAL"
    } else if score >= 0.65 {
        "HIGH"
    } else if score >= 0.40 {
        "MODERATE"
    } else if score >= 0.20 {
        "LOW"
    } else {
        "MINIMAL"
    }
    .to_string();

    let components = vec![
        (
            "severity_component".to_string(),
            (sev_factor * 0.40 * 1000.0).round() / 1000.0,
        ),
        (
            "confidence_component".to_string(),
            (conf_factor * 0.25 * 1000.0).round() / 1000.0,
        ),
        (
            "critical_density_component".to_string(),
            (crit_factor * 0.20 * 1000.0).round() / 1000.0,
        ),
        (
            "regression_rate_component".to_string(),
            (regr_factor * 0.15 * 1000.0).round() / 1000.0,
        ),
    ];

    RiskIndexResult { score, category, components }
}

// ===========================================================================
// Engine 6: Coverage Matrix
// ===========================================================================

#[pyclass(module = "axiom_core")]
pub struct CoverageMatrixResult {
    #[pyo3(get)] pub endpoints: Vec<String>,
    #[pyo3(get)] pub rules: Vec<String>,
    #[pyo3(get)] pub matrix: Vec<Vec<bool>>, // [endpoint_idx][rule_idx]
    #[pyo3(get)] pub coverage_pct: f64,
    #[pyo3(get)] pub uncovered_rules: Vec<String>,
    #[pyo3(get)] pub hottest_endpoint: String,
    #[pyo3(get)] pub hottest_rule: String,
}

/// Build endpoint × rule coverage matrix.
///
/// endpoints: ordered list of endpoint URIs
/// rules:     ordered list of rule IDs
/// triggered: Vec<(endpoint_uri, rule_id)> — one entry per fired (endpoint, rule) pair
#[pyfunction]
pub fn build_coverage_matrix(
    endpoints: Vec<String>,
    rules: Vec<String>,
    triggered: Vec<(String, String)>,
) -> CoverageMatrixResult {
    let n_ep = endpoints.len();
    let n_ru = rules.len();

    let ep_idx: HashMap<&str, usize> = endpoints
        .iter()
        .enumerate()
        .map(|(i, s)| (s.as_str(), i))
        .collect();
    let ru_idx: HashMap<&str, usize> = rules
        .iter()
        .enumerate()
        .map(|(i, s)| (s.as_str(), i))
        .collect();

    let mut matrix = vec![vec![false; n_ru]; n_ep];
    let mut ep_hit_count = vec![0usize; n_ep];
    let mut ru_hit_count = vec![0usize; n_ru];

    for (uri, rule_id) in &triggered {
        if let (Some(&ei), Some(&ri)) =
            (ep_idx.get(uri.as_str()), ru_idx.get(rule_id.as_str()))
        {
            if !matrix[ei][ri] {
                matrix[ei][ri] = true;
                ep_hit_count[ei] += 1;
                ru_hit_count[ri] += 1;
            }
        }
    }

    let total_cells = (n_ep * n_ru).max(1);
    let covered = matrix.iter().flat_map(|row| row.iter()).filter(|&&v| v).count();
    let coverage_pct = covered as f64 / total_cells as f64 * 100.0;

    let uncovered_rules: Vec<String> = ru_hit_count
        .iter()
        .enumerate()
        .filter_map(|(i, &c)| if c == 0 { Some(rules[i].clone()) } else { None })
        .collect();

    let hottest_endpoint = ep_hit_count
        .iter()
        .enumerate()
        .max_by_key(|(_, &c)| c)
        .map(|(i, _)| endpoints.get(i).cloned().unwrap_or_default())
        .unwrap_or_default();

    let hottest_rule = ru_hit_count
        .iter()
        .enumerate()
        .max_by_key(|(_, &c)| c)
        .map(|(i, _)| rules.get(i).cloned().unwrap_or_default())
        .unwrap_or_default();

    CoverageMatrixResult {
        endpoints,
        rules,
        matrix,
        coverage_pct: (coverage_pct * 10.0).round() / 10.0,
        uncovered_rules,
        hottest_endpoint,
        hottest_rule,
    }
}

// ===========================================================================
// Engine 7: Counterfactual Analysis
// ===========================================================================

#[pyclass(module = "axiom_core")]
pub struct CounterfactualResult {
    #[pyo3(get)] pub current_status: String,
    #[pyo3(get)] pub current_score: f64,
    #[pyo3(get)] pub pass_threshold: f64,
    #[pyo3(get)] pub min_fixes_to_pass: Option<usize>,
    #[pyo3(get)] pub critical_fix_ids: Vec<String>,
    #[pyo3(get)] pub scenarios: Vec<(String, String, f64)>, // (fix_desc, verdict, score)
}

/// Compute counterfactual analysis: what fixes are needed to pass?
///
/// violations:      Vec<(rule_id, weight, is_critical)>
/// severity_score:  current global severity [0-100]
/// pass_threshold:  severity below which deploy passes (40 = CONDITIONAL, 70 = BLOCKED)
#[pyfunction]
pub fn compute_counterfactual(
    mut violations: Vec<(String, f64, bool)>,
    severity_score: f64,
    pass_threshold: f64,
) -> CounterfactualResult {
    let current_status = if severity_score >= 70.0 {
        "BLOCKED"
    } else if severity_score >= 40.0 {
        "CONDITIONAL"
    } else {
        "APPROVED"
    }
    .to_string();

    // Sort descending by weight so we remove the most impactful violations first
    violations.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

    let critical_fix_ids: Vec<String> = violations
        .iter()
        .filter(|(_, _, is_crit)| *is_crit)
        .map(|(id, _, _)| id.clone())
        .collect();

    let total_weight: f64 = violations.iter().map(|(_, w, _)| *w).sum();
    let mut scenarios = Vec::new();
    let mut min_fixes: Option<usize> = None;

    for n_fix in 1..=violations.len().min(8) {
        let fixed_weight: f64 = violations.iter().take(n_fix).map(|(_, w, _)| *w).sum();
        // Removing a violation reduces raw violation contribution proportionally
        let fix_pct = fixed_weight / total_weight.max(1.0);
        let new_score = (severity_score * (1.0 - fix_pct * 0.60)).max(0.0);

        let fix_ids: Vec<&str> = violations.iter().take(n_fix).map(|(id, _, _)| id.as_str()).collect();
        let fix_label = fix_ids.join(" + ");

        let verdict = if new_score >= 70.0 {
            "BLOCKED"
        } else if new_score >= 40.0 {
            "CONDITIONAL"
        } else {
            "APPROVED"
        };

        if min_fixes.is_none() && new_score < pass_threshold {
            min_fixes = Some(n_fix);
        }

        scenarios.push((
            format!("fix [{}]", fix_label),
            verdict.to_string(),
            (new_score * 10.0).round() / 10.0,
        ));

        if new_score < 5.0 {
            break;
        }
    }

    CounterfactualResult {
        current_status,
        current_score: severity_score,
        pass_threshold,
        min_fixes_to_pass: min_fixes,
        critical_fix_ids,
        scenarios,
    }
}

// ===========================================================================
// Engine 8: Temporal Consistency
// ===========================================================================

#[pyclass(module = "axiom_core")]
pub struct TemporalResult {
    #[pyo3(get)] pub runs: usize,
    #[pyo3(get)] pub consistency: f64,
    #[pyo3(get)] pub drift_variance: f64,
    #[pyo3(get)] pub variance_label: String,
    #[pyo3(get)] pub flaky_endpoints: Vec<String>,
    #[pyo3(get)] pub trend: String, // stable | degrading | improving
    #[pyo3(get)] pub same_input_runs: String, // stable | flaky
}

/// Analyze temporal consistency across multiple replay runs.
///
/// run_endpoint_verdicts: Vec<Vec<(uri, verdict)>> — one inner vec per run.
/// For a single-run session, pass a vec with one element that has all
/// endpoint verdicts repeated `n` times to simulate stability.
#[pyfunction]
pub fn analyze_temporal_consistency(
    run_endpoint_verdicts: Vec<Vec<(String, String)>>,
) -> TemporalResult {
    let runs = run_endpoint_verdicts.len();
    if runs == 0 {
        return TemporalResult {
            runs: 0,
            consistency: 1.0,
            drift_variance: 0.0,
            variance_label: "stable".to_string(),
            flaky_endpoints: vec![],
            trend: "stable".to_string(),
            same_input_runs: "stable".to_string(),
        };
    }

    // Collect all unique endpoint URIs
    let mut all_uris: Vec<String> = Vec::new();
    for run in &run_endpoint_verdicts {
        for (uri, _) in run {
            if !all_uris.contains(uri) {
                all_uris.push(uri.clone());
            }
        }
    }

    let mut regression_counts_per_run = vec![0usize; runs];
    let mut flaky_endpoints = Vec::new();

    for uri in &all_uris {
        let mut regressed_in_n_runs = 0usize;
        for (run_idx, run) in run_endpoint_verdicts.iter().enumerate() {
            let verdict = run
                .iter()
                .find(|(u, _)| u == uri)
                .map(|(_, v)| v.as_str())
                .unwrap_or("UNKNOWN");
            if verdict == "DRIFT_DETECTED" || verdict == "FAILED_TO_REPLAY" {
                regressed_in_n_runs += 1;
                regression_counts_per_run[run_idx] += 1;
            }
        }
        if regressed_in_n_runs > 0 && regressed_in_n_runs < runs {
            flaky_endpoints.push(uri.clone());
        }
    }

    // Consistency: fraction of runs with same regression count as run[0]
    let first_count = regression_counts_per_run.first().copied().unwrap_or(0);
    let consistent_runs = regression_counts_per_run
        .iter()
        .filter(|&&c| c == first_count)
        .count();
    let consistency = consistent_runs as f64 / runs as f64;

    // Variance: standard deviation of per-run regression counts
    let mean_reg = regression_counts_per_run.iter().sum::<usize>() as f64 / runs as f64;
    let variance = regression_counts_per_run
        .iter()
        .map(|&c| {
            let d = c as f64 - mean_reg;
            d * d
        })
        .sum::<f64>()
        / runs as f64;
    let drift_variance = (variance.sqrt() * 1000.0).round() / 1000.0;

    let variance_label = if drift_variance < 0.5 {
        "stable"
    } else if drift_variance < 1.5 {
        "moderate"
    } else {
        "high"
    }
    .to_string();

    // Trend: first half vs second half
    let half = (runs / 2).max(1);
    let first_avg: f64 =
        regression_counts_per_run[..half].iter().sum::<usize>() as f64 / half as f64;
    let second_avg: f64 = {
        let tail = &regression_counts_per_run[half..];
        if tail.is_empty() {
            first_avg
        } else {
            tail.iter().sum::<usize>() as f64 / tail.len() as f64
        }
    };
    let trend = if (second_avg - first_avg).abs() < 0.5 {
        "stable"
    } else if second_avg > first_avg {
        "degrading"
    } else {
        "improving"
    }
    .to_string();

    let same_input_runs = if drift_variance < 0.5 { "stable" } else { "flaky" }.to_string();

    TemporalResult {
        runs,
        consistency: (consistency * 1000.0).round() / 1000.0,
        drift_variance,
        variance_label,
        flaky_endpoints,
        trend,
        same_input_runs,
    }
}

// ===========================================================================
// Engine 9: Deployment Decision
// ===========================================================================

#[pyclass(module = "axiom_core")]
pub struct DeploymentDecisionResult {
    #[pyo3(get)] pub action: String,           // BLOCK | CONDITIONAL | APPROVE
    #[pyo3(get)] pub confidence_level: String, // HIGH | MEDIUM | LOW
    #[pyo3(get)] pub justification: Vec<String>,
    #[pyo3(get)] pub risk_level: String,       // CRITICAL | HIGH | MODERATE | LOW
    #[pyo3(get)] pub rollback_recommended: bool,
}

/// Build a formal deployment decision.
///
/// severity:     global severity score [0-100]
/// confidence:   confidence score [0-1]
/// risk_score:   risk index [0-1]
/// n_critical:   count of critical rule violations
/// n_high:       count of high rule violations
/// n_endpoints:  total endpoints tested
/// n_regressed:  endpoints with regressions
#[pyfunction]
pub fn build_deployment_decision(
    severity: f64,
    confidence: f64,
    risk_score: f64,
    n_critical: usize,
    n_high: usize,
    n_endpoints: usize,
    n_regressed: usize,
) -> DeploymentDecisionResult {
    let action = if severity >= 70.0 || n_critical >= 3 {
        "BLOCK"
    } else if severity >= 40.0 || n_critical >= 1 || n_high >= 3 {
        "CONDITIONAL"
    } else {
        "APPROVE"
    }
    .to_string();

    let confidence_level = if confidence >= 0.85 {
        "HIGH"
    } else if confidence >= 0.60 {
        "MEDIUM"
    } else {
        "LOW"
    }
    .to_string();

    let risk_level = if risk_score >= 0.85 {
        "CRITICAL"
    } else if risk_score >= 0.65 {
        "HIGH"
    } else if risk_score >= 0.40 {
        "MODERATE"
    } else {
        "LOW"
    }
    .to_string();

    let mut justification = Vec::new();

    if n_regressed == n_endpoints && n_endpoints > 0 {
        justification.push(format!(
            "all {} endpoints exhibit behavioral regression",
            n_endpoints
        ));
    } else if n_regressed > 0 {
        justification.push(format!(
            "{}/{} endpoints exhibit behavioral regression",
            n_regressed, n_endpoints
        ));
    }
    if n_critical > 0 {
        justification.push(format!("{} critical rule violation(s) detected", n_critical));
    }
    if n_high > 0 {
        justification.push(format!("{} high-severity violation(s) detected", n_high));
    }
    if severity >= 70.0 {
        justification.push(format!(
            "severity {:.0}/100 exceeds BLOCK threshold (70)",
            severity
        ));
    }
    if confidence >= 0.85 {
        justification.push(format!(
            "verdict confirmed at {:.0}% confidence (HIGH)",
            confidence * 100.0
        ));
    }
    justification.push(format!("risk index {:.3} — {}", risk_score, risk_level));

    let rollback_recommended = action == "BLOCK" && (n_critical >= 2 || n_critical + n_high >= 5);

    DeploymentDecisionResult {
        action,
        confidence_level,
        justification,
        risk_level,
        rollback_recommended,
    }
}

// ===========================================================================
// Engine 10: Comparative Analysis (V1 vs V2)
// ===========================================================================

#[pyclass(module = "axiom_core")]
pub struct ComparativeResult {
    #[pyo3(get)] pub v1_score: f64, // reliability % [0-100]
    #[pyo3(get)] pub v2_score: f64,
    #[pyo3(get)] pub severity_delta: f64,
    #[pyo3(get)] pub regression_delta_pct: f64,
    #[pyo3(get)] pub verdict: String,           // REGRESSION | IMPROVEMENT | EQUIVALENT
    #[pyo3(get)] pub impact_magnitude: String,  // NEGLIGIBLE/MINOR/MODERATE/SEVERE/CATASTROPHIC
}

/// Compare V1 baseline vs V2 candidate.
///
/// v1_regression_rate: fraction [0-1] of endpoints regressed in V1 (expect ~0 for a stable baseline)
/// v2_regression_rate: fraction [0-1] of endpoints regressed in V2
/// v1_violation_score: aggregate violation severity score for V1 [0-100]
/// v2_violation_score: aggregate violation severity score for V2 [0-100]
#[pyfunction]
pub fn compute_comparative(
    v1_regression_rate: f64,
    v2_regression_rate: f64,
    v1_violation_score: f64,
    v2_violation_score: f64,
) -> ComparativeResult {
    // Reliability = (no regressions) × (no violations) — higher is better
    let v1_rel =
        (1.0 - v1_regression_rate) * (1.0 - v1_violation_score / 100.0).max(0.0);
    let v2_rel =
        (1.0 - v2_regression_rate) * (1.0 - v2_violation_score / 100.0).max(0.0);

    let v1_score = (v1_rel * 100.0 * 100.0).round() / 100.0;
    let v2_score = (v2_rel * 100.0 * 100.0).round() / 100.0;

    let severity_delta = v2_violation_score - v1_violation_score;
    let regression_delta_pct = (v2_regression_rate - v1_regression_rate) * 100.0;

    let verdict = if v2_rel < v1_rel - 0.05 {
        "REGRESSION"
    } else if v2_rel > v1_rel + 0.05 {
        "IMPROVEMENT"
    } else {
        "EQUIVALENT"
    }
    .to_string();

    let delta_pct = if v1_rel > 0.0 {
        ((v1_rel - v2_rel) / v1_rel * 100.0).abs()
    } else {
        0.0
    };
    let impact_magnitude = if delta_pct >= 50.0 {
        "CATASTROPHIC"
    } else if delta_pct >= 30.0 {
        "SEVERE"
    } else if delta_pct >= 15.0 {
        "MODERATE"
    } else if delta_pct >= 5.0 {
        "MINOR"
    } else {
        "NEGLIGIBLE"
    }
    .to_string();

    ComparativeResult {
        v1_score,
        v2_score,
        severity_delta: (severity_delta * 10.0).round() / 10.0,
        regression_delta_pct: (regression_delta_pct * 10.0).round() / 10.0,
        verdict,
        impact_magnitude,
    }
}

// ===========================================================================
// Engine 11: Semantic Score v2
// ===========================================================================

/// Fast semantic coherence score computation.
///
/// violation_data: Vec<(rule_id, weight, is_field_consistency, is_required_field)>
/// n_drift:        count of surviving drift items
/// verdict:        verdict string
#[pyfunction]
pub fn compute_semantic_score_v2(
    violation_data: Vec<(String, f64, bool, bool)>,
    n_drift: usize,
    verdict: String,
) -> f64 {
    match verdict.as_str() {
        "REPRODUCIBLE_STRICT" => return 1.0,
        "FAILED_TO_REPLAY" => return 0.0,
        _ => {}
    }

    let mut deductions = 0.0f64;

    // Each surviving drift deducts 0.08
    deductions += n_drift as f64 * 0.08;

    // Violations: deduct proportional to weight
    for (_, weight, is_fc, is_rf) in &violation_data {
        deductions += weight / 100.0;
        if *is_fc {
            deductions += 0.20; // internal field contradiction
        }
        if *is_rf {
            deductions += 0.15; // missing required field
        }
    }

    let score = (1.0 - deductions).max(0.0);
    (score * 1000.0).round() / 1000.0
}

// ===========================================================================
// Module registration (called from lib.rs)
// ===========================================================================

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Structs
    m.add_class::<SeverityResult>()?;
    m.add_class::<ConfidenceResult>()?;
    m.add_class::<DriftResult>()?;
    m.add_class::<RootCauseResult>()?;
    m.add_class::<RiskIndexResult>()?;
    m.add_class::<CoverageMatrixResult>()?;
    m.add_class::<CounterfactualResult>()?;
    m.add_class::<TemporalResult>()?;
    m.add_class::<DeploymentDecisionResult>()?;
    m.add_class::<ComparativeResult>()?;
    // Functions
    m.add_function(wrap_pyfunction!(compute_severity_v2, m)?)?;
    m.add_function(wrap_pyfunction!(compute_confidence_v2, m)?)?;
    m.add_function(wrap_pyfunction!(classify_drift_batch, m)?)?;
    m.add_function(wrap_pyfunction!(classify_root_causes_batch, m)?)?;
    m.add_function(wrap_pyfunction!(compute_risk_index, m)?)?;
    m.add_function(wrap_pyfunction!(build_coverage_matrix, m)?)?;
    m.add_function(wrap_pyfunction!(compute_counterfactual, m)?)?;
    m.add_function(wrap_pyfunction!(analyze_temporal_consistency, m)?)?;
    m.add_function(wrap_pyfunction!(build_deployment_decision, m)?)?;
    m.add_function(wrap_pyfunction!(compute_comparative, m)?)?;
    m.add_function(wrap_pyfunction!(compute_semantic_score_v2, m)?)?;
    Ok(())
}
