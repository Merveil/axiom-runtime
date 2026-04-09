"""
Axiom Lab — Rules engine.

Evaluates DriftItems from a VerdictReport against a set of JSON business rules.

Rule types (structural):
  ignore_field       — suppress drift on a specific path
  numeric_tolerance  — allow numeric differences within a threshold
  required_field     — flag if a top-level field is absent from the replay body
  prohibited_field   — flag if a top-level field appears in the replay body

Rule types (content-level semantic):
  contains_keyword   — a string field must contain a given substring (case-insensitive
                       by default; set "case_sensitive": true to override)
  not_contains_keyword — a string field must NOT contain a given substring
  value_in_range     — a numeric field must satisfy min <= value <= max
  value_in_set       — a field's value must belong to an allowed set
  field_consistency  — when a condition field equals a trigger value, a target
                       field must satisfy a constraint (supports: value_in_set,
                       value_in_range)

Rule field paths follow the same format as DriftItem.path: "/" + key,
or "/" + "a" + "/" + "b" for nested keys.  For convenience, rules may
also use dot notation ("usage.total_tokens") which is normalised to "/usage/total_tokens".

JSON rule file schema:
  {
    "name": "my_rules",
    "version": "1.0",
    "rules": [
      {"id": "R001", "type": "ignore_field",        "field": "request_id"},
      {"id": "R002", "type": "numeric_tolerance",   "field": "score",   "tolerance": 0.05},
      {"id": "R003", "type": "required_field",      "field": "status"},
      {"id": "R004", "type": "prohibited_field",    "field": "debug_token"},
      {"id": "R005", "type": "contains_keyword",    "field": "choices.0.text",
                     "keyword": "answer"},
      {"id": "R006", "type": "not_contains_keyword","field": "choices.0.text",
                     "keyword": "error"},
      {"id": "R007", "type": "value_in_range",      "field": "usage.total_tokens",
                     "min": 1, "max": 10000},
      {"id": "R008", "type": "value_in_set",        "field": "choices.0.finish_reason",
                     "allowed": ["stop", "length", "content_filter"]},
      {"id": "R009", "type": "field_consistency",
                     "condition_field": "risk_level", "condition_value": "high",
                     "target_field": "confidence",
                     "constraint": "value_in_range", "min": 0.7, "max": 1.0}
    ]
  }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from axiom_lab.probe import DriftItem, Verdict, VerdictReport

# ---------------------------------------------------------------------------
# Optional Rust acceleration (axiom_core extension module)
# ---------------------------------------------------------------------------
try:
    import axiom_core as _rust
    _RUST_AVAILABLE = True
except ImportError:  # pragma: no cover
    _rust = None  # type: ignore[assignment]
    _RUST_AVAILABLE = False


def _to_path(field_name: str) -> str:
    """Normalise a rule field name to a DriftItem-compatible path."""
    return "/" + field_name.replace(".", "/")


def _resolve_field(body: dict, field_name: str):
    """Walk dot-notation path into *body* and return the leaf value, or *_MISSING*."""
    parts = field_name.split(".")
    node: object = body
    for part in parts:
        if isinstance(node, dict):
            if part not in node:
                return _MISSING
            node = node[part]
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return _MISSING
        else:
            return _MISSING
    return node


_MISSING = object()  # sentinel for absent path


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

@dataclass
class RuleViolation:
    rule_id:     str
    description: str
    path:        str
    detail:      str = ""


@dataclass
class EvaluatedReport:
    """VerdictReport annotated with rule-based suppressions and violations."""
    original:          VerdictReport
    violations:        list[RuleViolation]
    surviving_drift:   list[DriftItem]
    effective_verdict: Verdict

    @property
    def is_clean(self) -> bool:
        return (
            not self.violations
            and self.effective_verdict in (
                Verdict.REPRODUCIBLE_STRICT,
                Verdict.REPRODUCIBLE_SEMANTIC,
            )
        )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class RulesEngine:
    def __init__(self, rules: list[dict]) -> None:
        self._rules = rules

    @classmethod
    def from_file(cls, path: str | Path) -> "RulesEngine":
        with open(path) as f:
            data = json.load(f)
        return cls(data.get("rules", []))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, report: VerdictReport) -> EvaluatedReport:
        """Apply all rules to a VerdictReport.

        Returns an EvaluatedReport with:
          - surviving_drift  : DriftItems not suppressed by any rule
          - violations       : structural + content invariant failures
          - effective_verdict: possibly downgraded if all drift was suppressed

        When axiom_core is available the heavy evaluation loop runs in Rust
        (all 9 rule types) and results are adapted back to Python types.
        """
        if _RUST_AVAILABLE and self._rules and report.replay_body is not None:
            rules_json   = json.dumps(self._rules)
            replay_json  = json.dumps(report.replay_body)
            drift_items  = [
                _rust.DriftItem(
                    path=d.path,
                    original=d.original,
                    replayed=d.replayed,
                    reason=d.reason,
                )
                for d in report.drift
            ]
            result = _rust.evaluate_rules(
                rules_json,
                report.verdict.value,
                drift_items,
                replay_json,
            )
            surviving_drift = [
                DriftItem(
                    path=item.path,
                    original=item.original,
                    replayed=item.replayed,
                    reason=item.reason,
                )
                for item in result.surviving_drift
            ]
            violations = [
                RuleViolation(
                    rule_id=v.rule_id,
                    description=v.description,
                    path=v.path,
                    detail=v.detail,
                )
                for v in result.violations
            ]
            effective = Verdict(result.effective_verdict)
            return EvaluatedReport(
                original=report,
                violations=violations,
                surviving_drift=surviving_drift,
                effective_verdict=effective,
            )

        # Pure-Python fallback
        surviving_drift = list(self._suppress(report.drift))
        violations      = (
            list(self._check_invariants(report.replay_body))
            + list(self._check_content_rules(report.replay_body))
        )
        effective       = self._derive_verdict(report, surviving_drift)
        return EvaluatedReport(
            original=report,
            violations=violations,
            surviving_drift=surviving_drift,
            effective_verdict=effective,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _suppress(self, drifts: list[DriftItem]):
        """Yield DriftItems that are NOT suppressed by any rule."""
        for drift in drifts:
            if not any(self._suppresses(rule, drift) for rule in self._rules):
                yield drift

    def _suppresses(self, rule: dict, drift: DriftItem) -> bool:
        rtype = rule.get("type")
        rpath = _to_path(rule.get("field", ""))

        if rtype == "ignore_field":
            return drift.path == rpath

        if rtype == "numeric_tolerance":
            if drift.path != rpath:
                return False
            try:
                diff = abs(float(drift.replayed) - float(drift.original))
                return diff <= float(rule["tolerance"])
            except (ValueError, TypeError, KeyError):
                return False

        return False

    def _check_invariants(self, body: dict):
        """Yield RuleViolations for required_field / prohibited_field."""
        if not body:
            # Empty body — required_field violations will fire for every required field
            for rule in self._rules:
                if rule.get("type") == "required_field":
                    yield RuleViolation(
                        rule_id=rule.get("id", "?"),
                        description=rule.get("description", ""),
                        path=_to_path(rule["field"]),
                        detail=f"Required field '{rule['field']}' absent (empty response body)",
                    )
            return

        for rule in self._rules:
            rtype = rule.get("type")
            f     = rule.get("field", "")

            if rtype == "required_field":
                # Support top-level only for now
                top = f.split(".")[0]
                if top not in body:
                    yield RuleViolation(
                        rule_id=rule.get("id", "?"),
                        description=rule.get("description", ""),
                        path=_to_path(f),
                        detail=f"Required field '{f}' absent from response",
                    )

            elif rtype == "prohibited_field":
                top = f.split(".")[0]
                if top in body:
                    yield RuleViolation(
                        rule_id=rule.get("id", "?"),
                        description=rule.get("description", ""),
                        path=_to_path(f),
                        detail=f"Prohibited field '{f}' present in response",
                    )

    def _check_content_rules(self, body: dict):
        """Yield RuleViolations for content-level semantic rules.

        Handles: contains_keyword, not_contains_keyword, value_in_range,
                 value_in_set, field_consistency.
        """
        if not body:
            return

        for rule in self._rules:
            rtype = rule.get("type")
            rid   = rule.get("id", "?")
            rdesc = rule.get("description", "")

            if rtype == "contains_keyword":
                field   = rule.get("field", "")
                keyword = rule.get("keyword", "")
                case_s  = rule.get("case_sensitive", False)
                value   = _resolve_field(body, field)
                if value is _MISSING:
                    continue  # structural absence already caught by required_field
                text   = str(value)
                needle = keyword if case_s else keyword.lower()
                hay    = text if case_s else text.lower()
                if needle not in hay:
                    yield RuleViolation(
                        rule_id=rid, description=rdesc,
                        path=_to_path(field),
                        detail=f"Field '{field}' does not contain required keyword '{keyword}' (value={text!r})",
                    )

            elif rtype == "not_contains_keyword":
                field   = rule.get("field", "")
                keyword = rule.get("keyword", "")
                case_s  = rule.get("case_sensitive", False)
                value   = _resolve_field(body, field)
                if value is _MISSING:
                    continue
                text   = str(value)
                needle = keyword if case_s else keyword.lower()
                hay    = text if case_s else text.lower()
                if needle in hay:
                    yield RuleViolation(
                        rule_id=rid, description=rdesc,
                        path=_to_path(field),
                        detail=f"Field '{field}' contains prohibited keyword '{keyword}' (value={text!r})",
                    )

            elif rtype == "value_in_range":
                field = rule.get("field", "")
                value = _resolve_field(body, field)
                if value is _MISSING:
                    continue
                try:
                    v   = float(value)
                    lo  = float(rule["min"])
                    hi  = float(rule["max"])
                except (TypeError, ValueError, KeyError):
                    continue
                if not (lo <= v <= hi):
                    yield RuleViolation(
                        rule_id=rid, description=rdesc,
                        path=_to_path(field),
                        detail=f"Field '{field}' value {v} out of range [{lo}, {hi}]",
                    )

            elif rtype == "value_in_set":
                field   = rule.get("field", "")
                allowed = set(rule.get("allowed", []))
                value   = _resolve_field(body, field)
                if value is _MISSING:
                    continue
                if value not in allowed:
                    yield RuleViolation(
                        rule_id=rid, description=rdesc,
                        path=_to_path(field),
                        detail=f"Field '{field}' value {value!r} not in allowed set {sorted(allowed)}",
                    )

            elif rtype == "field_consistency":
                cond_field = rule.get("condition_field", "")
                cond_val   = rule.get("condition_value")
                tgt_field  = rule.get("target_field", "")
                constraint = rule.get("constraint", "")

                cond_actual = _resolve_field(body, cond_field)
                if cond_actual is _MISSING or cond_actual != cond_val:
                    continue  # condition not met — rule inactive

                tgt_actual = _resolve_field(body, tgt_field)
                if tgt_actual is _MISSING:
                    yield RuleViolation(
                        rule_id=rid, description=rdesc,
                        path=_to_path(tgt_field),
                        detail=(
                            f"Consistency rule: when '{cond_field}'=={cond_val!r}, "
                            f"field '{tgt_field}' must be present but is absent"
                        ),
                    )
                    continue

                if constraint == "value_in_range":
                    try:
                        v  = float(tgt_actual)
                        lo = float(rule["min"])
                        hi = float(rule["max"])
                    except (TypeError, ValueError, KeyError):
                        continue
                    if not (lo <= v <= hi):
                        yield RuleViolation(
                            rule_id=rid, description=rdesc,
                            path=_to_path(tgt_field),
                            detail=(
                                f"Consistency rule: when '{cond_field}'=={cond_val!r}, "
                                f"'{tgt_field}' must be in [{lo}, {hi}] but is {v}"
                            ),
                        )

                elif constraint == "value_in_set":
                    allowed = set(rule.get("allowed", []))
                    if tgt_actual not in allowed:
                        yield RuleViolation(
                            rule_id=rid, description=rdesc,
                            path=_to_path(tgt_field),
                            detail=(
                                f"Consistency rule: when '{cond_field}'=={cond_val!r}, "
                                f"'{tgt_field}' must be in {sorted(allowed)} but is {tgt_actual!r}"
                            ),
                        )

    def _derive_verdict(self, report: VerdictReport, surviving: list[DriftItem]) -> Verdict:
        if report.verdict == Verdict.FAILED_TO_REPLAY:
            return Verdict.FAILED_TO_REPLAY
        if surviving:
            return Verdict.DRIFT_DETECTED
        if report.verdict in (Verdict.REPRODUCIBLE_STRICT, Verdict.REPRODUCIBLE_SEMANTIC):
            # Preserve original granularity — no drift was suppressed
            return report.verdict
        # Was DRIFT_DETECTED but all drifts suppressed by rules
        return Verdict.REPRODUCIBLE_SEMANTIC
