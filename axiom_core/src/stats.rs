// axiom_core::stats — Replay latency statistics
//
// p95 and mean on Vec<f64>; replaces Python `statistics` module calls
// inside hot replay loops.

use pyo3::prelude::*;

/// Compute the 95th-percentile of a latency list (nearest-rank).
/// Returns 0.0 for an empty list.
#[pyfunction]
pub fn p95(values: Vec<f64>) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut sorted = values.clone();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let idx = (sorted.len() * 95 / 100).saturating_sub(1).max(0);
    sorted[idx]
}

/// Arithmetic mean of a latency list. Returns 0.0 for empty.
#[pyfunction]
pub fn mean_latency(values: Vec<f64>) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let sum: f64 = values.iter().sum();
    sum / values.len() as f64
}

/// Combined: return (mean, p95) in a single call to avoid two FFI round-trips.
#[pyfunction]
pub fn latency_stats(values: Vec<f64>) -> (f64, f64) {
    if values.is_empty() {
        return (0.0, 0.0);
    }
    let sum: f64 = values.iter().sum();
    let mean = sum / values.len() as f64;
    let mut sorted = values;
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let idx = (sorted.len() * 95 / 100).saturating_sub(1).max(0);
    (mean, sorted[idx])
}

/// Accumulate per-route stats in Rust, returning a dict-friendly structure.
///
/// Input: list of (uri, verdict_str, latency_ms) tuples
/// Output: dict-like Vec of (uri, total, strict, semantic, drift, failed,
///         regression_rate_pct, avg_latency_ms)
#[pyclass(module = "axiom_core")]
pub struct RouteStats {
    #[pyo3(get)]
    pub uri: String,
    #[pyo3(get)]
    pub total: usize,
    #[pyo3(get)]
    pub strict: usize,
    #[pyo3(get)]
    pub semantic: usize,
    #[pyo3(get)]
    pub drift: usize,
    #[pyo3(get)]
    pub failed: usize,
    #[pyo3(get)]
    pub regression_rate_pct: f64,
    #[pyo3(get)]
    pub avg_latency_ms: f64,
}

/// Compute per-route stats in a single Rust pass.
#[pyfunction]
pub fn aggregate_route_stats(
    rows: Vec<(String, String, f64)>,  // (uri, verdict, latency_ms)
) -> Vec<RouteStats> {
    use std::collections::HashMap;

    #[derive(Default)]
    struct Acc {
        total:    usize,
        strict:   usize,
        semantic: usize,
        drift:    usize,
        failed:   usize,
        lat_sum:  f64,
    }

    let mut map: HashMap<String, Acc> = HashMap::new();
    for (uri, verdict, lat) in rows {
        let acc = map.entry(uri.clone()).or_default();
        acc.total   += 1;
        acc.lat_sum += lat;
        match verdict.as_str() {
            "REPRODUCIBLE_STRICT"   => acc.strict   += 1,
            "REPRODUCIBLE_SEMANTIC" => acc.semantic  += 1,
            "DRIFT_DETECTED"        => acc.drift     += 1,
            "FAILED_TO_REPLAY"      => acc.failed    += 1,
            _ => {}
        }
    }

    let mut result: Vec<RouteStats> = map
        .into_iter()
        .map(|(uri, acc)| {
            let avg_lat = if acc.total > 0 {
                acc.lat_sum / acc.total as f64
            } else {
                0.0
            };
            let regression_rate_pct = if acc.total > 0 {
                (acc.drift + acc.failed) as f64 / acc.total as f64 * 100.0
            } else {
                0.0
            };
            RouteStats {
                uri,
                total: acc.total,
                strict: acc.strict,
                semantic: acc.semantic,
                drift: acc.drift,
                failed: acc.failed,
                regression_rate_pct,
                avg_latency_ms: avg_lat,
            }
        })
        .collect();

    result.sort_by(|a, b| a.uri.cmp(&b.uri));
    result
}
