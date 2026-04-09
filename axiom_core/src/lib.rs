// axiom_core — PyO3 extension module root
//
// Registers all public Rust functions and classes for Python import:
//
//   from axiom_core import (
//       json_diff, classify_verdict, DriftItem,
//       evaluate_rules, RuleViolation, RulesResult,
//       p95, mean_latency, latency_stats,
//       aggregate_route_stats, RouteStats,
//       RustEventStore,
//   )

use pyo3::prelude::*;

mod probe;
mod rules;
mod stats;
mod store;

#[pymodule]
fn axiom_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // ── probe (json diff + verdict) ────────────────────────────────────────
    m.add_class::<probe::DriftItem>()?;
    m.add_function(wrap_pyfunction!(probe::json_diff, m)?)?;
    m.add_function(wrap_pyfunction!(probe::classify_verdict, m)?)?;

    // ── rules engine ──────────────────────────────────────────────────────
    m.add_class::<rules::RuleViolation>()?;
    m.add_class::<rules::RulesResult>()?;
    m.add_function(wrap_pyfunction!(rules::evaluate_rules, m)?)?;

    // ── statistics ────────────────────────────────────────────────────────
    m.add_class::<stats::RouteStats>()?;
    m.add_function(wrap_pyfunction!(stats::p95, m)?)?;
    m.add_function(wrap_pyfunction!(stats::mean_latency, m)?)?;
    m.add_function(wrap_pyfunction!(stats::latency_stats, m)?)?;
    m.add_function(wrap_pyfunction!(stats::aggregate_route_stats, m)?)?;

    // ── SQLite store ──────────────────────────────────────────────────────
    m.add_class::<store::RustEventStore>()?;

    Ok(())
}
