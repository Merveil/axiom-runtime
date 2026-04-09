"""
Axiom Lab — Calibration runner.

Measures how accurately Axiom classifies a labeled corpus of cases and
produces a structured report with precision / recall / FPR / FNR metrics.

Key metrics
-----------
  accuracy_pct    overall correct / total
  fpr             false positive rate — normal/tolerable cases incorrectly
                  flagged as DRIFT_DETECTED or FAILED_TO_REPLAY
  fnr             false negative rate — dangerous cases NOT correctly detected
  by_family       per-family {correct, total, accuracy_pct}
  by_verdict      per-verdict {tp, fp, fn, precision, recall}
  confusion_matrix {expected_verdict: {predicted_verdict: count}}

A FPR > 0.10 suggests rules are too strict → widen tolerances or add ignores.
A FNR > 0.05 suggests dangerous cases slip through → tighten content rules.

Usage
-----
    from axiom_lab.calibration import run_calibration
    from axiom_lab.corpus import CorpusLoader

    cases, default_rules = CorpusLoader.from_file("axiom_lab/corpus/api_corpus.json")
    clients = {
        "api_stable": TestClient(create_api_demo_app(drift_mode=False)),
        "api_drift":  TestClient(create_api_demo_app(drift_mode=True, rng=...)),
    }
    report = run_calibration(cases, clients, name="api-calibration",
                             default_rules_path=default_rules)
    print(report.summary_table())
    report.save("axiom_lab/reports/api_calibration.json")
"""
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path

from axiom_lab.corpus import LabeledCase
from axiom_lab.probe import Verdict, VerdictReport, evaluate
from axiom_lab.rules_engine import EvaluatedReport, RulesEngine


# ---------------------------------------------------------------------------
# Per-case result
# ---------------------------------------------------------------------------

@dataclass
class CaseResult:
    label:             str
    family:            str
    expected_verdict:  str
    predicted_verdict: str
    correct:           bool
    violations:        list[dict] = field(default_factory=list)
    latency_ms:        float      = 0.0
    notes:             str        = ""

    @property
    def is_false_positive(self) -> bool:
        """Normal / tolerable case incorrectly flagged as a regression."""
        return (
            self.family in ("normal", "tolerable")
            and self.predicted_verdict in (
                Verdict.DRIFT_DETECTED.value,
                Verdict.FAILED_TO_REPLAY.value,
            )
            and not self.correct
        )

    @property
    def is_false_negative(self) -> bool:
        """Dangerous case that was not correctly detected."""
        return self.family == "dangerous" and not self.correct


# ---------------------------------------------------------------------------
# Calibration report
# ---------------------------------------------------------------------------

@dataclass
class CalibrationReport:
    name:             str
    timestamp:        str
    total:            int
    correct:          int
    false_positives:  int
    false_negatives:  int
    accuracy_pct:     float
    fpr:              float
    fnr:              float
    by_family:        dict[str, dict]  = field(default_factory=dict)
    by_verdict:       dict[str, dict]  = field(default_factory=dict)
    confusion_matrix: dict[str, dict]  = field(default_factory=dict)
    cases:            list[CaseResult] = field(default_factory=list)

    def summary_table(self) -> str:
        """Plain-text summary suitable for logging / README appendix."""
        lines = [
            f"Calibration: {self.name}  [{self.timestamp}]",
            f"  Total:    {self.total}   Correct: {self.correct}"
            f"   Accuracy: {self.accuracy_pct:.1f}%",
            f"  FPR:      {self.fpr:.4f}   FNR: {self.fnr:.4f}",
            "",
            "  By family:",
        ]
        for fam, stats in sorted(self.by_family.items()):
            lines.append(
                f"    {fam:<12}  {stats['correct']}/{stats['total']}"
                f"  ({stats['accuracy_pct']:.1f}%)"
            )
        lines.append("")
        lines.append("  Confusion matrix (expected → predicted):")
        for exp, preds in sorted(self.confusion_matrix.items()):
            for pred, cnt in sorted(preds.items()):
                mark = "OK" if exp == pred else "!!"
                lines.append(
                    f"    [{mark}]  {exp:<32} → {pred:<32}  x{cnt}"
                )
        lines.append("")
        lines.append("  By verdict (precision / recall):")
        for v, stats in sorted(self.by_verdict.items()):
            lines.append(
                f"    {v:<32}  P={stats['precision']:.3f}  R={stats['recall']:.3f}"
                f"  (tp={stats['tp']} fp={stats['fp']} fn={stats['fn']})"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "name":             self.name,
            "timestamp":        self.timestamp,
            "total":            self.total,
            "correct":          self.correct,
            "false_positives":  self.false_positives,
            "false_negatives":  self.false_negatives,
            "accuracy_pct":     self.accuracy_pct,
            "fpr":              self.fpr,
            "fnr":              self.fnr,
            "by_family":        self.by_family,
            "by_verdict":       self.by_verdict,
            "confusion_matrix": self.confusion_matrix,
            "cases": [
                {
                    "label":             c.label,
                    "family":            c.family,
                    "expected_verdict":  c.expected_verdict,
                    "predicted_verdict": c.predicted_verdict,
                    "correct":           c.correct,
                    "latency_ms":        c.latency_ms,
                    "violations":        c.violations,
                    "notes":             c.notes,
                }
                for c in self.cases
            ],
        }

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_calibration(
    cases: list[LabeledCase],
    clients: dict[str, object],
    *,
    name: str = "calibration",
    default_rules_path: str | Path | None = None,
) -> CalibrationReport:
    """Evaluate each labeled case and produce a CalibrationReport.

    Args:
        cases:              labeled cases from CorpusLoader.from_file()
        clients:            map of app_name → TestClient (or httpx.Client)
        name:               report display name
        default_rules_path: rules file to apply when case.rules_path is None;
                            individual cases may override with their own rules_path
    """
    results: list[CaseResult] = []

    for case in cases:
        client = clients.get(case.app)
        if client is None:
            # Unregistered app — treat as system error
            predicted = Verdict.FAILED_TO_REPLAY.value
            correct   = (case.expected_verdict == predicted) and (
                case.expected_violations_min == 0
            )
            results.append(CaseResult(
                label=case.label,
                family=case.family,
                expected_verdict=case.expected_verdict,
                predicted_verdict=predicted,
                correct=correct,
                notes=f"[system] No client registered for app={case.app!r}",
            ))
            continue

        # Probe
        report: VerdictReport = evaluate(case.record, client)

        # Rules (per-case override > default)
        rules_path = case.rules_path or default_rules_path
        eviol_list: list[dict] = []

        if rules_path:
            engine = RulesEngine.from_file(rules_path)
            ev: EvaluatedReport = engine.evaluate(report)
            predicted  = ev.effective_verdict.value
            eviol_list = [
                {"rule_id": v.rule_id, "path": v.path, "detail": v.detail}
                for v in ev.violations
            ]
        else:
            predicted = report.verdict.value

        # Correctness: verdict match + minimum violations
        verdict_ok     = (case.expected_verdict == predicted)
        violations_ok  = len(eviol_list) >= case.expected_violations_min
        correct        = verdict_ok and violations_ok

        results.append(CaseResult(
            label=case.label,
            family=case.family,
            expected_verdict=case.expected_verdict,
            predicted_verdict=predicted,
            correct=correct,
            violations=eviol_list,
            latency_ms=round(report.replay_latency_ms, 2),
            notes=case.notes,
        ))

    return _build_report(name, results)


# ---------------------------------------------------------------------------
# Internal: build metrics from raw results
# ---------------------------------------------------------------------------

def _build_report(name: str, results: list[CaseResult]) -> CalibrationReport:
    total   = len(results)
    correct = sum(1 for r in results if r.correct)
    fps     = sum(1 for r in results if r.is_false_positive)
    fns     = sum(1 for r in results if r.is_false_negative)

    benign_total = sum(1 for r in results if r.family in ("normal", "tolerable"))
    dng_total    = sum(1 for r in results if r.family == "dangerous")

    fpr = fps / benign_total if benign_total else 0.0
    fnr = fns / dng_total    if dng_total    else 0.0

    # --- by_family
    families: dict[str, dict] = {}
    for r in results:
        if r.family not in families:
            families[r.family] = {"total": 0, "correct": 0, "accuracy_pct": 0.0}
        families[r.family]["total"]   += 1
        families[r.family]["correct"] += int(r.correct)
    for fam in families:
        t = families[fam]["total"]
        c = families[fam]["correct"]
        families[fam]["accuracy_pct"] = round(c / t * 100, 1) if t else 0.0

    # --- confusion matrix
    confusion: dict[str, dict] = {}
    for r in results:
        exp  = r.expected_verdict
        pred = r.predicted_verdict
        if exp not in confusion:
            confusion[exp] = {}
        confusion[exp][pred] = confusion[exp].get(pred, 0) + 1

    # --- by_verdict: precision, recall
    all_verdicts = (
        {r.expected_verdict for r in results} |
        {r.predicted_verdict for r in results}
    )
    by_verdict: dict[str, dict] = {}
    for v in sorted(all_verdicts):
        tp = sum(1 for r in results if r.expected_verdict == v and r.predicted_verdict == v)
        fp = sum(1 for r in results if r.expected_verdict != v and r.predicted_verdict == v)
        fn = sum(1 for r in results if r.expected_verdict == v and r.predicted_verdict != v)
        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall    = tp / (tp + fn) if (tp + fn) else 1.0
        by_verdict[v] = {
            "tp":        tp,
            "fp":        fp,
            "fn":        fn,
            "precision": round(precision, 3),
            "recall":    round(recall, 3),
        }

    return CalibrationReport(
        name=name,
        timestamp=datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat().replace("+00:00", "Z"),
        total=total,
        correct=correct,
        false_positives=fps,
        false_negatives=fns,
        accuracy_pct=round(correct / total * 100, 1) if total else 0.0,
        fpr=round(fpr, 4),
        fnr=round(fnr, 4),
        by_family=families,
        by_verdict=by_verdict,
        confusion_matrix=confusion,
        cases=results,
    )
