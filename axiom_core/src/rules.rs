// axiom_core::rules — High-performance rules engine
//
// All 9 rule types ported from axiom_lab/rules_engine.py.
// The hot path is:
//   _suppress (O(drift × rules)) → _check_invariants → _check_content_rules
//   _check_content_rules iterates rules × string/numeric operations per rule.
// Moving to Rust eliminates:
//   - Python attribute lookups per loop iteration
//   - String .lower() allocation overhead
//   - float() conversion boxing
//   - Python generator overhead (yield)

use pyo3::prelude::*;
use serde_json::Value;
use std::collections::HashSet;

use crate::probe::{value_str, DriftItem};

// ---------------------------------------------------------------------------
// Rule violation
// ---------------------------------------------------------------------------

#[pyclass(module = "axiom_core")]
#[derive(Clone, Debug)]
pub struct RuleViolation {
    #[pyo3(get)]
    pub rule_id: String,
    #[pyo3(get)]
    pub description: String,
    #[pyo3(get)]
    pub path: String,
    #[pyo3(get)]
    pub detail: String,
}

#[pymethods]
impl RuleViolation {
    #[new]
    pub fn new(rule_id: String, description: String, path: String, detail: String) -> Self {
        RuleViolation { rule_id, description, path, detail }
    }
    pub fn __repr__(&self) -> String {
        format!("RuleViolation(rule_id={:?}, path={:?})", self.rule_id, self.path)
    }
}

// ---------------------------------------------------------------------------
// Evaluation result
// ---------------------------------------------------------------------------

#[pyclass(module = "axiom_core")]
pub struct RulesResult {
    #[pyo3(get)]
    pub violations: Vec<RuleViolation>,
    #[pyo3(get)]
    pub surviving_drift: Vec<DriftItem>,
    #[pyo3(get)]
    pub effective_verdict: String,
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/// Normalise "usage.total_tokens" → "/usage/total_tokens"
fn to_path(field: &str) -> String {
    format!("/{}", field.replace('.', "/"))
}

/// Walk a dot-notation path into a serde_json Value.
/// Returns None when any segment is missing.
fn resolve_field<'a>(body: &'a Value, field: &str) -> Option<&'a Value> {
    let parts: Vec<&str> = field.split('.').collect();
    let mut node = body;
    for part in parts {
        match node {
            Value::Object(map) => {
                node = map.get(part)?;
            }
            Value::Array(arr) => {
                let idx: usize = part.parse().ok()?;
                node = arr.get(idx)?;
            }
            _ => return None,
        }
    }
    Some(node)
}

fn rule_id(rule: &Value) -> String {
    rule.get("id")
        .and_then(|v| v.as_str())
        .unwrap_or("?")
        .to_string()
}

fn rule_desc(rule: &Value) -> String {
    rule.get("description")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

fn rule_field(rule: &Value) -> String {
    rule.get("field")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

// ---------------------------------------------------------------------------
// Suppression phase  O(drift × rules)
// ---------------------------------------------------------------------------

fn suppresses(rule: &Value, drift: &DriftItem) -> bool {
    let rtype = match rule.get("type").and_then(|v| v.as_str()) {
        Some(t) => t,
        None => return false,
    };
    let rpath = to_path(&rule_field(rule));

    match rtype {
        "ignore_field" => drift.path == rpath,
        "numeric_tolerance" => {
            if drift.path != rpath {
                return false;
            }
            let tolerance = match rule.get("tolerance").and_then(|v| v.as_f64()) {
                Some(t) => t,
                None => return false,
            };
            let orig: f64 = drift.original.parse().ok().unwrap_or(f64::NAN);
            let repl: f64 = drift.replayed.parse().ok().unwrap_or(f64::NAN);
            if orig.is_nan() || repl.is_nan() {
                return false;
            }
            (orig - repl).abs() <= tolerance
        }
        _ => false,
    }
}

fn suppress_drifts(rules: &[Value], drifts: &[DriftItem]) -> Vec<DriftItem> {
    drifts
        .iter()
        .filter(|d| !rules.iter().any(|r| suppresses(r, d)))
        .cloned()
        .collect()
}

// ---------------------------------------------------------------------------
// Structural invariants: required_field / prohibited_field
// ---------------------------------------------------------------------------

fn check_invariants(rules: &[Value], body: &Value) -> Vec<RuleViolation> {
    let mut violations = Vec::new();
    let obj = match body.as_object() {
        Some(m) => m,
        None => {
            // Empty-ish body — fire required_field for all
            for rule in rules {
                if rule.get("type").and_then(|v| v.as_str()) == Some("required_field") {
                    let f = rule_field(rule);
                    violations.push(RuleViolation {
                        rule_id: rule_id(rule),
                        description: rule_desc(rule),
                        path: to_path(&f),
                        detail: format!(
                            "Required field '{}' absent (empty response body)", f
                        ),
                    });
                }
            }
            return violations;
        }
    };

    for rule in rules {
        let rtype = match rule.get("type").and_then(|v| v.as_str()) {
            Some(t) => t,
            None => continue,
        };
        let f = rule_field(rule);
        let top = f.split('.').next().unwrap_or(&f);

        match rtype {
            "required_field" => {
                if !obj.contains_key(top) {
                    violations.push(RuleViolation {
                        rule_id: rule_id(rule),
                        description: rule_desc(rule),
                        path: to_path(&f),
                        detail: format!("Required field '{}' absent from response", f),
                    });
                }
            }
            "prohibited_field" => {
                if obj.contains_key(top) {
                    violations.push(RuleViolation {
                        rule_id: rule_id(rule),
                        description: rule_desc(rule),
                        path: to_path(&f),
                        detail: format!("Prohibited field '{}' present in response", f),
                    });
                }
            }
            _ => {}
        }
    }
    violations
}

// ---------------------------------------------------------------------------
// Content-level semantic rules
// ---------------------------------------------------------------------------

fn check_content_rules(rules: &[Value], body: &Value) -> Vec<RuleViolation> {
    let mut violations = Vec::new();

    if body.is_null() || (body.is_object() && body.as_object().unwrap().is_empty()) {
        return violations;
    }

    for rule in rules {
        let rtype = match rule.get("type").and_then(|v| v.as_str()) {
            Some(t) => t,
            None => continue,
        };
        let rid   = rule_id(rule);
        let rdesc = rule_desc(rule);

        match rtype {
            "contains_keyword" => {
                let field   = rule_field(rule);
                let keyword = rule.get("keyword").and_then(|v| v.as_str()).unwrap_or("");
                let case_s  = rule.get("case_sensitive")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false);
                let value = match resolve_field(body, &field) {
                    Some(v) => value_str(v),
                    None => continue,
                };
                let (needle, hay) = if case_s {
                    (keyword.to_string(), value.clone())
                } else {
                    (keyword.to_lowercase(), value.to_lowercase())
                };
                if !hay.contains(&needle as &str) {
                    violations.push(RuleViolation {
                        rule_id: rid,
                        description: rdesc,
                        path: to_path(&field),
                        detail: format!(
                            "Field '{}' does not contain required keyword '{}' (value={:?})",
                            field, keyword, value
                        ),
                    });
                }
            }

            "not_contains_keyword" => {
                let field   = rule_field(rule);
                let keyword = rule.get("keyword").and_then(|v| v.as_str()).unwrap_or("");
                let case_s  = rule.get("case_sensitive")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false);
                let value = match resolve_field(body, &field) {
                    Some(v) => value_str(v),
                    None => continue,
                };
                let (needle, hay) = if case_s {
                    (keyword.to_string(), value.clone())
                } else {
                    (keyword.to_lowercase(), value.to_lowercase())
                };
                if hay.contains(&needle as &str) {
                    violations.push(RuleViolation {
                        rule_id: rid,
                        description: rdesc,
                        path: to_path(&field),
                        detail: format!(
                            "Field '{}' contains prohibited keyword '{}' (value={:?})",
                            field, keyword, value
                        ),
                    });
                }
            }

            "value_in_range" => {
                let field = rule_field(rule);
                let value = match resolve_field(body, &field) {
                    Some(v) => v,
                    None => continue,
                };
                let fv: f64 = match value {
                    Value::Number(n) => match n.as_f64() {
                        Some(f) => f,
                        None => continue,
                    },
                    Value::String(s) => match s.parse::<f64>() {
                        Ok(f) => f,
                        Err(_) => continue,
                    },
                    _ => continue,
                };
                let lo = rule.get("min").and_then(|v| v.as_f64());
                let hi = rule.get("max").and_then(|v| v.as_f64());
                match (lo, hi) {
                    (Some(lo), Some(hi)) => {
                        if !(lo <= fv && fv <= hi) {
                            violations.push(RuleViolation {
                                rule_id: rid,
                                description: rdesc,
                                path: to_path(&field),
                                detail: format!(
                                    "Field '{}' value {} out of range [{}, {}]",
                                    field, fv, lo, hi
                                ),
                            });
                        }
                    }
                    _ => continue,
                }
            }

            "value_in_set" => {
                let field   = rule_field(rule);
                let allowed: HashSet<String> = rule
                    .get("allowed")
                    .and_then(|v| v.as_array())
                    .map(|arr| {
                        arr.iter()
                            .map(|v| value_str(v))
                            .collect()
                    })
                    .unwrap_or_default();
                let value = match resolve_field(body, &field) {
                    Some(v) => value_str(v),
                    None => continue,
                };
                if !allowed.contains(&value) {
                    let mut sorted: Vec<&String> = allowed.iter().collect();
                    sorted.sort();
                    violations.push(RuleViolation {
                        rule_id: rid,
                        description: rdesc,
                        path: to_path(&field),
                        detail: format!(
                            "Field '{}' value {:?} not in allowed set {:?}",
                            field, value, sorted
                        ),
                    });
                }
            }

            "field_consistency" => {
                let cond_field = rule
                    .get("condition_field")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let cond_val = rule.get("condition_value");
                let tgt_field = rule
                    .get("target_field")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let constraint = rule
                    .get("constraint")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");

                // Check condition
                let cond_actual = match resolve_field(body, cond_field) {
                    Some(v) => v,
                    None => continue,
                };
                // Compare condition value
                let cond_matches = match cond_val {
                    Some(cv) => {
                        // Compare strings normalised
                        value_str(cond_actual) == value_str(cv)
                    }
                    None => continue,
                };
                if !cond_matches {
                    continue;
                }

                // Condition met — check target field
                let tgt_actual = match resolve_field(body, tgt_field) {
                    Some(v) => v,
                    None => {
                        violations.push(RuleViolation {
                            rule_id: rid.clone(),
                            description: rdesc.clone(),
                            path: to_path(tgt_field),
                            detail: format!(
                                "Consistency rule: when '{}'=={:?}, field '{}' must be present but is absent",
                                cond_field,
                                value_str(cond_val.unwrap()),
                                tgt_field
                            ),
                        });
                        continue;
                    }
                };

                match constraint {
                    "value_in_range" => {
                        let fv: f64 = match tgt_actual {
                            Value::Number(n) => n.as_f64().unwrap_or(f64::NAN),
                            Value::String(s) => s.parse().unwrap_or(f64::NAN),
                            _ => f64::NAN,
                        };
                        if fv.is_nan() { continue; }
                        let lo = rule.get("min").and_then(|v| v.as_f64());
                        let hi = rule.get("max").and_then(|v| v.as_f64());
                        if let (Some(lo), Some(hi)) = (lo, hi) {
                            if !(lo <= fv && fv <= hi) {
                                violations.push(RuleViolation {
                                    rule_id: rid,
                                    description: rdesc,
                                    path: to_path(tgt_field),
                                    detail: format!(
                                        "Consistency rule: when '{}'=={:?}, '{}' must be in [{}, {}] but is {}",
                                        cond_field,
                                        value_str(cond_val.unwrap()),
                                        tgt_field, lo, hi, fv
                                    ),
                                });
                            }
                        }
                    }
                    "value_in_set" => {
                        let allowed: HashSet<String> = rule
                            .get("allowed")
                            .and_then(|v| v.as_array())
                            .map(|arr| arr.iter().map(|v| value_str(v)).collect())
                            .unwrap_or_default();
                        let actual_str = value_str(tgt_actual);
                        if !allowed.contains(&actual_str) {
                            let mut sorted: Vec<&String> = allowed.iter().collect();
                            sorted.sort();
                            violations.push(RuleViolation {
                                rule_id: rid,
                                description: rdesc,
                                path: to_path(tgt_field),
                                detail: format!(
                                    "Consistency rule: when '{}'=={:?}, '{}' must be in {:?} but is {:?}",
                                    cond_field,
                                    value_str(cond_val.unwrap()),
                                    tgt_field, sorted, actual_str
                                ),
                            });
                        }
                    }
                    _ => {}
                }
            }
            _ => {}
        }
    }
    violations
}

// ---------------------------------------------------------------------------
// Verdict derivation
// ---------------------------------------------------------------------------

fn derive_verdict(
    original_verdict: &str,
    surviving_drift: &[DriftItem],
) -> String {
    if original_verdict == "FAILED_TO_REPLAY" {
        return "FAILED_TO_REPLAY".into();
    }
    if !surviving_drift.is_empty() {
        return "DRIFT_DETECTED".into();
    }
    match original_verdict {
        "REPRODUCIBLE_STRICT" | "REPRODUCIBLE_SEMANTIC" => original_verdict.into(),
        // Was DRIFT_DETECTED but all suppressed
        _ => "REPRODUCIBLE_SEMANTIC".into(),
    }
}

// ---------------------------------------------------------------------------
// Python-callable entry point
// ---------------------------------------------------------------------------

/// Apply rules to a VerdictReport-like structure.
///
/// Args:
///   rules_json       – JSON string of the rules array
///   original_verdict – verdict string from the probe
///   drift_items      – list of DriftItem objects from json_diff
///   replay_body_json – JSON string of the replay response body
///
/// Returns RulesResult with violations, surviving_drift, effective_verdict.
#[pyfunction]
pub fn evaluate_rules(
    rules_json: &str,
    original_verdict: &str,
    drift_items: Vec<DriftItem>,
    replay_body_json: &str,
) -> PyResult<RulesResult> {
    let rules: Vec<Value> = serde_json::from_str(rules_json)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(
            format!("Invalid rules JSON: {}", e)
        ))?;

    let body: Value = if replay_body_json.is_empty() || replay_body_json == "{}" {
        Value::Object(Default::default())
    } else {
        serde_json::from_str(replay_body_json)
            .unwrap_or(Value::Object(Default::default()))
    };

    let surviving_drift = suppress_drifts(&rules, &drift_items);
    let mut violations = check_invariants(&rules, &body);
    violations.extend(check_content_rules(&rules, &body));

    let effective_verdict = derive_verdict(original_verdict, &surviving_drift);

    Ok(RulesResult {
        violations,
        surviving_drift,
        effective_verdict,
    })
}
