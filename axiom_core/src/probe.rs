// axiom_core::probe — JSON diff, Verdict, DriftItem, VerdictReport
//
// This is the hottest path in Axiom: every replay call runs
// _json_diff (recursive) then classify_verdict.  Moving this to
// Rust removes Python interpreter overhead on the inner loops.

use pyo3::prelude::*;
use serde_json::Value;
use std::collections::BTreeSet;

/// Non-semantic fields that should never trigger a drift verdict.
/// Mirrors axiom_lab.probe._NON_SEMANTIC_FIELDS.
const NON_SEMANTIC: &[&str] = &[
    "request_id",
    "id",
    "trace_id",
    "created_at",
    "timestamp",
    "system_fingerprint",
    "x_request_id",
];

#[inline]
fn is_non_semantic(key: &str) -> bool {
    NON_SEMANTIC.contains(&key)
}

/// A single detected drift between original and replayed JSON body.
#[pyclass(module = "axiom_core")]
#[derive(Clone, Debug)]
pub struct DriftItem {
    #[pyo3(get)]
    pub path: String,
    #[pyo3(get)]
    pub original: String,
    #[pyo3(get)]
    pub replayed: String,
    #[pyo3(get)]
    pub reason: String,
}

#[pymethods]
impl DriftItem {
    #[new]
    pub fn new(path: String, original: String, replayed: String, reason: String) -> Self {
        DriftItem { path, original, replayed, reason }
    }

    pub fn __repr__(&self) -> String {
        format!("DriftItem(path={:?}, reason={:?})", self.path, self.reason)
    }
}

/// Recursive JSON deep-diff.
///
/// Returns a Vec of DriftItems for every changed / added / removed
/// field, skipping non-semantic keys.  Lists are compared by value
/// equality (not element-wise recursion) to match Python behaviour.
pub fn json_diff_inner(
    original: &Value,
    replayed: &Value,
    path_prefix: &str,
    diffs: &mut Vec<DriftItem>,
) {
    match (original, replayed) {
        (Value::Object(o), Value::Object(r)) => {
            let all_keys: BTreeSet<&String> =
                o.keys().chain(r.keys()).collect();

            for key in all_keys {
                if is_non_semantic(key) {
                    continue;
                }
                let path = format!("{}/{}", path_prefix, key);
                match (o.get(key), r.get(key)) {
                    (None, Some(rv)) => {
                        diffs.push(DriftItem {
                            path,
                            original: "ABSENT".into(),
                            replayed: value_str(rv),
                            reason: "field added".into(),
                        });
                    }
                    (Some(ov), None) => {
                        diffs.push(DriftItem {
                            path,
                            original: value_str(ov),
                            replayed: "MISSING".into(),
                            reason: "field removed".into(),
                        });
                    }
                    (Some(ov), Some(rv)) => {
                        // Recurse into nested objects; compare everything else
                        // by equality (mirrors Python behaviour for lists).
                        if ov.is_object() && rv.is_object() {
                            json_diff_inner(ov, rv, &path, diffs);
                        } else if ov != rv {
                            diffs.push(DriftItem {
                                path,
                                original: value_str(ov),
                                replayed: value_str(rv),
                                reason: "value changed".into(),
                            });
                        }
                    }
                    (None, None) => unreachable!(),
                }
            }
        }
        // Top-level comparison of non-objects
        _ => {
            if original != replayed {
                diffs.push(DriftItem {
                    path: path_prefix.to_string(),
                    original: value_str(original),
                    replayed: value_str(replayed),
                    reason: "value changed".into(),
                });
            }
        }
    }
}

/// Convert a serde_json Value to the same string representation
/// that Python's `str()` would produce (no surrounding quotes for
/// strings, so diffs match the Python `str(ov)` call pattern).
pub fn value_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True".to_string() } else { "False".to_string() },
        Value::Null => "None".to_string(),
        Value::Number(n) => n.to_string(),
        // Arrays and objects: use compact JSON
        other => other.to_string(),
    }
}

/// Python-callable: compute the deep diff between two JSON dicts.
///
/// args:
///   original  – dict parsed from Python (any JSON-serialisable object)
///   replayed  – dict parsed from Python
///
/// Returns a list of DriftItem objects.
#[pyfunction]
pub fn json_diff(
    original: &Bound<'_, PyAny>,
    replayed: &Bound<'_, PyAny>,
) -> PyResult<Vec<DriftItem>> {
    let orig_str: String = original
        .call_method0("__str__")
        .and_then(|s| s.extract::<String>())
        .unwrap_or_default();

    // Convert Python dicts to serde_json Values via JSON round-trip
    // using pythons own json.dumps (most correct approach):
    let py = original.py();
    let json_mod = py.import("json")?;

    let o_json: String = json_mod
        .call_method1("dumps", (original,))?
        .extract()?;
    let r_json: String = json_mod
        .call_method1("dumps", (replayed,))?
        .extract()?;

    let _ = orig_str; // suppress warning

    let o_val: Value = serde_json::from_str(&o_json)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
    let r_val: Value = serde_json::from_str(&r_json)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

    let mut diffs = Vec::new();
    json_diff_inner(&o_val, &r_val, "", &mut diffs);
    Ok(diffs)
}

/// Python-callable: classify verdict given status codes and drift list.
///
/// Returns one of: REPRODUCIBLE_STRICT, REPRODUCIBLE_SEMANTIC,
///                 DRIFT_DETECTED, FAILED_TO_REPLAY
#[pyfunction]
pub fn classify_verdict(
    original_status: u16,
    replay_status: u16,
    expected_body_json: &str,
    replay_body_json: &str,
    drifts: Vec<DriftItem>,
) -> PyResult<String> {
    if replay_status >= 500 {
        return Ok("FAILED_TO_REPLAY".into());
    }
    if replay_status != original_status {
        return Ok("DRIFT_DETECTED".into());
    }
    if !drifts.is_empty() {
        return Ok("DRIFT_DETECTED".into());
    }
    // Strict check: byte-identical JSON bodies
    if expected_body_json == replay_body_json {
        return Ok("REPRODUCIBLE_STRICT".into());
    }
    // Same semantic content but some non-semantic field differed
    // (already filtered out in json_diff, but bodies differ as strings)
    // -> SEMANTIC
    Ok("REPRODUCIBLE_SEMANTIC".into())
}
