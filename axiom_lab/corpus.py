"""
Axiom Lab — Labeled corpus loader.

A LabeledCase bundles an ExchangeRecord (the golden/original capture)
with the expected Axiom verdict, the target app to replay against, and
the case family for calibration metric grouping.

Families
--------
  normal     replay should be STRICT or SEMANTIC — no drift, just baseline
             behaviour; these cases prove Axiom does not over-fire
  tolerable  acceptable variation (wording drift, representation noise)
             probe may return DRIFT or SEMANTIC; rules may suppress drift
  dangerous  genuine regression: missing field, type change, 500, empty
             content, incoherent scores — expected DRIFT_DETECTED or
             FAILED_TO_REPLAY (or SEMANTIC + rule violations for subtle bugs)

Correctness definition
----------------------
A case is "correct" when:
  1. predicted_verdict == expected_verdict, AND
  2. len(rule_violations) >= expected_violations_min (default 0)

This allows testing that INCOHERENT bodies trigger content rules even
when the structural verdict is SEMANTIC.

Corpus JSON schema
------------------
{
  "name": "my_corpus",
  "version": "1.0",
  "rules_path": "../rules/api_demo.json",   // optional, relative to corpus file
  "cases": [
    {
      "label":                  "health-normal-strict",
      "family":                 "normal",
      "app":                    "api_stable",
      "expected_verdict":       "REPRODUCIBLE_STRICT",
      "expected_violations_min": 0,         // optional, default 0
      "notes":                  "Static GET",
      "record": {
        "method": "GET", "uri": "/health", "body": null,
        "expected_status": 200,
        "expected_body":   {"status": "ok", "version": "1.0.0"},
        "label": "health"
      }
    }
  ]
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from axiom_lab.probe import ExchangeRecord, Verdict

_VALID_FAMILIES = frozenset({"normal", "tolerable", "dangerous"})


@dataclass
class LabeledCase:
    label:                   str
    family:                  str            # "normal" | "tolerable" | "dangerous"
    record:                  ExchangeRecord
    expected_verdict:        str            # Verdict.value
    app:                     str            # key in the clients dict
    rules_path:              str | None = None
    expected_violations_min: int        = 0
    notes:                   str        = ""

    @property
    def expected(self) -> Verdict:
        return Verdict(self.expected_verdict)

    @property
    def is_regression_expected(self) -> bool:
        """True for dangerous cases where DRIFT or FAILED is the right answer."""
        return self.expected in (Verdict.DRIFT_DETECTED, Verdict.FAILED_TO_REPLAY)


class CorpusLoader:
    """Load labeled cases from a JSON corpus file."""

    @staticmethod
    def from_file(path: str | Path) -> tuple[list[LabeledCase], str | None]:
        """Load corpus JSON and return (cases, default_rules_path).

        Relative paths in rules_path fields are resolved relative to the
        directory containing the corpus file.
        """
        corpus_path = Path(path).resolve()
        corpus_dir  = corpus_path.parent

        with open(corpus_path) as f:
            data = json.load(f)

        def _resolve(rp: str | None) -> str | None:
            if rp is None:
                return None
            p = Path(rp)
            if p.is_absolute():
                return str(p)
            return str((corpus_dir / rp).resolve())

        default_rules = _resolve(data.get("rules_path"))

        cases: list[LabeledCase] = []
        for raw in data.get("cases", []):
            record = ExchangeRecord.from_dict(raw["record"])
            cases.append(LabeledCase(
                label=raw["label"],
                family=raw.get("family", "normal"),
                record=record,
                expected_verdict=raw["expected_verdict"],
                app=raw.get("app", ""),
                rules_path=_resolve(raw.get("rules_path")) or default_rules,
                expected_violations_min=int(raw.get("expected_violations_min", 0)),
                notes=raw.get("notes", ""),
            ))
        return cases, default_rules
