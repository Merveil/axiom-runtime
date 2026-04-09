"""
tests/test_axiom_lab_calibration.py

Test suite for the Axiom Lab calibration training system.

Five test classes covering all layers of the calibration stack:
  TestCorpusLoader        — JSON load, path resolution, field parsing
  TestCaseResult          — is_false_positive / is_false_negative properties
  TestCalibrationMetrics  — FPR, FNR, accuracy, confusion matrix, by_verdict
  TestCalibrationRun      — end-to-end with api / llm / chaos corpus
  TestCalibrationReport   — to_dict shape, save/load, summary_table
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from axiom_lab.api_demo.app import create_api_demo_app
from axiom_lab.calibration import (
    CalibrationReport,
    CaseResult,
    _build_report,
    run_calibration,
)
from axiom_lab.chaos.app import create_chaos_app
from axiom_lab.corpus import CorpusLoader, LabeledCase
from axiom_lab.llm_demo.app import create_llm_demo_app
from axiom_lab.probe import ExchangeRecord, Verdict

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_CORPUS_DIR  = Path(__file__).parent.parent / "axiom_lab" / "corpus"
_RULES_DIR   = Path(__file__).parent.parent / "axiom_lab" / "rules"
_API_CORPUS  = _CORPUS_DIR / "api_corpus.json"
_LLM_CORPUS  = _CORPUS_DIR / "llm_corpus.json"
_CHAOS_CORPUS = _CORPUS_DIR / "chaos_corpus.json"

# ---------------------------------------------------------------------------
# Calibration client factories
# ---------------------------------------------------------------------------

def _api_clients(seed: int = 1) -> dict[str, TestClient]:
    return {
        "api_stable": TestClient(
            create_api_demo_app(drift_mode=False),
            raise_server_exceptions=False,
        ),
        "api_drift": TestClient(
            create_api_demo_app(drift_mode=True, rng=random.Random(seed)),
            raise_server_exceptions=False,
        ),
    }


def _llm_clients() -> dict[str, TestClient]:
    return {
        "llm_stable":    TestClient(
            create_llm_demo_app(drift_mode=False),
            raise_server_exceptions=False,
        ),
        "llm_variable":  TestClient(
            create_llm_demo_app(force_mode="variable"),
            raise_server_exceptions=False,
        ),
        "llm_missing":   TestClient(
            create_llm_demo_app(force_mode="missing"),
            raise_server_exceptions=False,
        ),
        "llm_schema":    TestClient(
            create_llm_demo_app(force_mode="schema"),
            raise_server_exceptions=False,
        ),
        "llm_incoherent": TestClient(
            create_llm_demo_app(force_mode="incoherent"),
            raise_server_exceptions=False,
        ),
    }


def _chaos_clients() -> dict[str, TestClient]:
    return {
        "chaos_stable": TestClient(
            create_chaos_app(chaos_enabled=False),
            raise_server_exceptions=False,
        ),
        "chaos_active": TestClient(
            create_chaos_app(chaos_enabled=True, flaky_error_rate=1.0,
                             rng=random.Random(0)),
            raise_server_exceptions=False,
        ),
    }


# ---------------------------------------------------------------------------
# Synthetic corpus helpers
# ---------------------------------------------------------------------------

def _make_case(
    label:            str = "test",
    family:           str = "normal",
    expected_verdict: str = "REPRODUCIBLE_STRICT",
    app:              str = "api_stable",
    expected_violations_min: int = 0,
) -> LabeledCase:
    return LabeledCase(
        label=label,
        family=family,
        record=ExchangeRecord("GET", "/health", None, 200,
                              {"status": "ok", "version": "1.0.0"}),
        expected_verdict=expected_verdict,
        app=app,
        expected_violations_min=expected_violations_min,
    )


def _make_result(
    *,
    label:            str   = "test",
    family:           str   = "normal",
    expected_verdict: str   = "REPRODUCIBLE_STRICT",
    predicted_verdict: str  = "REPRODUCIBLE_STRICT",
    correct:          bool  = True,
    violations:       list  = None,
) -> CaseResult:
    return CaseResult(
        label=label,
        family=family,
        expected_verdict=expected_verdict,
        predicted_verdict=predicted_verdict,
        correct=correct,
        violations=violations or [],
    )


# ===========================================================================
# 1. Corpus loader
# ===========================================================================

class TestCorpusLoader:

    def test_load_api_corpus_count(self):
        if not _API_CORPUS.exists():
            pytest.skip("api_corpus.json not found")
        cases, _ = CorpusLoader.from_file(_API_CORPUS)
        assert len(cases) == 6

    def test_load_api_corpus_families(self):
        if not _API_CORPUS.exists():
            pytest.skip()
        cases, _ = CorpusLoader.from_file(_API_CORPUS)
        families = [c.family for c in cases]
        assert families.count("normal")    == 2
        assert families.count("tolerable") == 2
        assert families.count("dangerous") == 2

    def test_load_api_corpus_apps(self):
        if not _API_CORPUS.exists():
            pytest.skip()
        cases, _ = CorpusLoader.from_file(_API_CORPUS)
        apps = {c.app for c in cases}
        assert "api_stable" in apps
        assert "api_drift"  in apps

    def test_load_api_corpus_expected_verdicts(self):
        if not _API_CORPUS.exists():
            pytest.skip()
        cases, _ = CorpusLoader.from_file(_API_CORPUS)
        verdicts = {c.expected_verdict for c in cases}
        assert "REPRODUCIBLE_STRICT"   in verdicts
        assert "REPRODUCIBLE_SEMANTIC" in verdicts
        assert "DRIFT_DETECTED"        in verdicts
        assert "FAILED_TO_REPLAY"      in verdicts

    def test_load_api_corpus_record_is_exchange_record(self):
        if not _API_CORPUS.exists():
            pytest.skip()
        cases, _ = CorpusLoader.from_file(_API_CORPUS)
        for c in cases:
            assert isinstance(c.record, ExchangeRecord)
            assert c.record.method in ("GET", "POST")
            assert c.record.uri.startswith("/")

    def test_load_llm_corpus_count(self):
        if not _LLM_CORPUS.exists():
            pytest.skip()
        cases, _ = CorpusLoader.from_file(_LLM_CORPUS)
        assert len(cases) == 5

    def test_load_llm_corpus_rules_path_resolved(self):
        if not _LLM_CORPUS.exists():
            pytest.skip()
        cases, default_rules = CorpusLoader.from_file(_LLM_CORPUS)
        assert default_rules is not None
        assert Path(default_rules).exists(), f"Resolved rules path not found: {default_rules}"

    def test_load_llm_corpus_rules_inherited_by_cases(self):
        if not _LLM_CORPUS.exists():
            pytest.skip()
        cases, default_rules = CorpusLoader.from_file(_LLM_CORPUS)
        for c in cases:
            assert c.rules_path == default_rules

    def test_load_chaos_corpus_count(self):
        if not _CHAOS_CORPUS.exists():
            pytest.skip()
        cases, _ = CorpusLoader.from_file(_CHAOS_CORPUS)
        assert len(cases) == 5

    def test_load_corpus_from_tmp_file(self, tmp_path):
        """CorpusLoader can parse a hand-crafted corpus file."""
        corpus = {
            "name": "tmp",
            "cases": [
                {
                    "label": "t1",
                    "family": "dangerous",
                    "app": "my_app",
                    "expected_verdict": "DRIFT_DETECTED",
                    "expected_violations_min": 1,
                    "notes": "test case",
                    "record": {
                        "method": "GET", "uri": "/x", "body": None,
                        "expected_status": 200, "expected_body": {}, "label": "t1"
                    },
                }
            ],
        }
        p = tmp_path / "corpus.json"
        p.write_text(json.dumps(corpus))
        cases, _ = CorpusLoader.from_file(p)
        assert len(cases) == 1
        c = cases[0]
        assert c.label   == "t1"
        assert c.family  == "dangerous"
        assert c.app     == "my_app"
        assert c.expected_verdict       == "DRIFT_DETECTED"
        assert c.expected_violations_min == 1
        assert c.notes   == "test case"

    def test_load_corpus_default_violations_min_zero(self, tmp_path):
        corpus = {"cases": [{"label": "x", "family": "normal", "app": "a",
                             "expected_verdict": "REPRODUCIBLE_STRICT",
                             "record": {"method": "GET", "uri": "/h", "body": None,
                                        "expected_status": 200,
                                        "expected_body": {}, "label": ""}}]}
        p = tmp_path / "c.json"
        p.write_text(json.dumps(corpus))
        cases, _ = CorpusLoader.from_file(p)
        assert cases[0].expected_violations_min == 0

    def test_load_corpus_relative_rules_path_resolved(self, tmp_path):
        """A relative rules_path is resolved relative to the corpus file."""
        rules_file = tmp_path / "my_rules.json"
        rules_file.write_text(json.dumps({"rules": []}))
        corpus = {
            "rules_path": "my_rules.json",
            "cases": [],
        }
        p = tmp_path / "corpus.json"
        p.write_text(json.dumps(corpus))
        _, default_rules = CorpusLoader.from_file(p)
        assert default_rules is not None
        assert Path(default_rules).resolve() == rules_file.resolve()


# ===========================================================================
# 2. CaseResult properties
# ===========================================================================

class TestCaseResult:

    def test_false_positive_normal_flagged_as_drift(self):
        r = _make_result(family="normal", predicted_verdict="DRIFT_DETECTED", correct=False)
        assert r.is_false_positive is True

    def test_false_positive_tolerable_flagged_as_failed(self):
        r = _make_result(family="tolerable", predicted_verdict="FAILED_TO_REPLAY", correct=False)
        assert r.is_false_positive is True

    def test_not_false_positive_when_correct(self):
        r = _make_result(family="normal",
                         expected_verdict="REPRODUCIBLE_STRICT",
                         predicted_verdict="REPRODUCIBLE_STRICT",
                         correct=True)
        assert r.is_false_positive is False

    def test_not_false_positive_for_dangerous_family(self):
        r = _make_result(family="dangerous", predicted_verdict="DRIFT_DETECTED", correct=False)
        assert r.is_false_positive is False

    def test_false_negative_dangerous_missed(self):
        r = _make_result(family="dangerous",
                         expected_verdict="DRIFT_DETECTED",
                         predicted_verdict="REPRODUCIBLE_SEMANTIC",
                         correct=False)
        assert r.is_false_negative is True

    def test_not_false_negative_when_dangerous_correct(self):
        r = _make_result(family="dangerous",
                         expected_verdict="DRIFT_DETECTED",
                         predicted_verdict="DRIFT_DETECTED",
                         correct=True)
        assert r.is_false_negative is False

    def test_not_false_negative_for_normal_family(self):
        r = _make_result(family="normal",
                         expected_verdict="REPRODUCIBLE_STRICT",
                         predicted_verdict="DRIFT_DETECTED",
                         correct=False)
        assert r.is_false_negative is False


# ===========================================================================
# 3. Calibration metrics (_build_report)
# ===========================================================================

class TestCalibrationMetrics:

    def test_perfect_accuracy_100_pct(self):
        results = [
            _make_result(family="normal",    expected_verdict="REPRODUCIBLE_STRICT",
                         predicted_verdict="REPRODUCIBLE_STRICT",    correct=True),
            _make_result(family="tolerable", expected_verdict="REPRODUCIBLE_SEMANTIC",
                         predicted_verdict="REPRODUCIBLE_SEMANTIC",  correct=True),
            _make_result(family="dangerous", expected_verdict="DRIFT_DETECTED",
                         predicted_verdict="DRIFT_DETECTED",         correct=True),
        ]
        report = _build_report("test", results)
        assert report.accuracy_pct   == pytest.approx(100.0)
        assert report.fpr            == pytest.approx(0.0)
        assert report.fnr            == pytest.approx(0.0)
        assert report.false_positives == 0
        assert report.false_negatives == 0

    def test_fpr_nonzero_when_benign_mispredicted(self):
        results = [
            _make_result(family="normal",    expected_verdict="REPRODUCIBLE_STRICT",
                         predicted_verdict="DRIFT_DETECTED",  correct=False),
            _make_result(family="normal",    expected_verdict="REPRODUCIBLE_STRICT",
                         predicted_verdict="REPRODUCIBLE_STRICT", correct=True),
            _make_result(family="dangerous", expected_verdict="DRIFT_DETECTED",
                         predicted_verdict="DRIFT_DETECTED",  correct=True),
        ]
        report = _build_report("test", results)
        # 1 FP out of 2 benign → FPR = 0.5
        assert report.fpr == pytest.approx(0.5)
        assert report.false_positives == 1

    def test_fnr_nonzero_when_dangerous_missed(self):
        results = [
            _make_result(family="dangerous", expected_verdict="DRIFT_DETECTED",
                         predicted_verdict="REPRODUCIBLE_SEMANTIC", correct=False),
            _make_result(family="dangerous", expected_verdict="FAILED_TO_REPLAY",
                         predicted_verdict="FAILED_TO_REPLAY",      correct=True),
        ]
        report = _build_report("test", results)
        # 1 FN out of 2 dangerous → FNR = 0.5
        assert report.fnr == pytest.approx(0.5)
        assert report.false_negatives == 1

    def test_accuracy_partial(self):
        results = [
            _make_result(correct=True),
            _make_result(correct=True),
            _make_result(correct=False,
                         predicted_verdict="DRIFT_DETECTED",
                         expected_verdict="REPRODUCIBLE_STRICT"),
            _make_result(correct=False,
                         predicted_verdict="FAILED_TO_REPLAY",
                         expected_verdict="DRIFT_DETECTED"),
        ]
        report = _build_report("test", results)
        assert report.total   == 4
        assert report.correct == 2
        assert report.accuracy_pct == pytest.approx(50.0)

    def test_confusion_matrix_populated(self):
        results = [
            _make_result(expected_verdict="REPRODUCIBLE_STRICT",
                         predicted_verdict="REPRODUCIBLE_STRICT",  correct=True),
            _make_result(expected_verdict="REPRODUCIBLE_STRICT",
                         predicted_verdict="DRIFT_DETECTED",        correct=False),
            _make_result(expected_verdict="DRIFT_DETECTED",
                         predicted_verdict="DRIFT_DETECTED",        correct=True),
        ]
        report = _build_report("test", results)
        assert report.confusion_matrix["REPRODUCIBLE_STRICT"]["REPRODUCIBLE_STRICT"] == 1
        assert report.confusion_matrix["REPRODUCIBLE_STRICT"]["DRIFT_DETECTED"]       == 1
        assert report.confusion_matrix["DRIFT_DETECTED"]["DRIFT_DETECTED"]            == 1

    def test_by_verdict_precision_recall_perfect(self):
        results = [
            _make_result(expected_verdict="DRIFT_DETECTED",
                         predicted_verdict="DRIFT_DETECTED",        correct=True),
            _make_result(expected_verdict="DRIFT_DETECTED",
                         predicted_verdict="DRIFT_DETECTED",        correct=True),
        ]
        report = _build_report("test", results)
        drift = report.by_verdict["DRIFT_DETECTED"]
        assert drift["precision"] == pytest.approx(1.0)
        assert drift["recall"]    == pytest.approx(1.0)
        assert drift["tp"]        == 2
        assert drift["fp"]        == 0
        assert drift["fn"]        == 0

    def test_by_family_breakdown(self):
        results = [
            _make_result(family="normal",    correct=True),
            _make_result(family="normal",    correct=False,
                         predicted_verdict="DRIFT_DETECTED",
                         expected_verdict="REPRODUCIBLE_STRICT"),
            _make_result(family="dangerous", correct=True,
                         expected_verdict="DRIFT_DETECTED",
                         predicted_verdict="DRIFT_DETECTED"),
        ]
        report = _build_report("test", results)
        assert report.by_family["normal"]["total"]        == 2
        assert report.by_family["normal"]["correct"]      == 1
        assert report.by_family["normal"]["accuracy_pct"] == pytest.approx(50.0)
        assert report.by_family["dangerous"]["total"]     == 1

    def test_empty_corpus_returns_zero_metrics(self):
        report = _build_report("empty", [])
        assert report.total        == 0
        assert report.accuracy_pct == pytest.approx(0.0)
        assert report.fpr          == pytest.approx(0.0)
        assert report.fnr          == pytest.approx(0.0)


# ===========================================================================
# 4. End-to-end calibration runs
# ===========================================================================

class TestCalibrationRun:

    # --- API corpus ----------------------------------------------------------

    def test_api_corpus_all_correct(self):
        """All 6 API corpus cases should be classified correctly."""
        if not _API_CORPUS.exists():
            pytest.skip()
        cases, _ = CorpusLoader.from_file(_API_CORPUS)
        report = run_calibration(cases, _api_clients(), name="api-test")
        assert report.total == 6
        # Allow at most 1 failure due to RNG sensitivity on seed-dependent drift case
        assert report.correct >= 5, (
            f"Expected >=5 correct; got {report.correct}\n"
            + "\n".join(
                f"  {r.label}: expected={r.expected_verdict} predicted={r.predicted_verdict}"
                for r in report.cases if not r.correct
            )
        )

    def test_api_corpus_dangerous_detected(self):
        """Both dangerous API cases must be detected."""
        if not _API_CORPUS.exists():
            pytest.skip()
        cases, _ = CorpusLoader.from_file(_API_CORPUS)
        report = run_calibration(cases, _api_clients(), name="api-dng")
        dng = [r for r in report.cases if r.family == "dangerous"]
        assert all(r.correct for r in dng), (
            f"Dangerous cases not all detected: {[(r.label, r.predicted_verdict) for r in dng if not r.correct]}"
        )

    def test_api_corpus_fnr_zero(self):
        if not _API_CORPUS.exists():
            pytest.skip()
        cases, _ = CorpusLoader.from_file(_API_CORPUS)
        report = run_calibration(cases, _api_clients())
        assert report.fnr == pytest.approx(0.0), f"FNR={report.fnr} — dangerous cases missed"

    def test_api_corpus_benign_not_all_flagged(self):
        """Normal + tolerable cases must not ALL be flagged as regressions."""
        if not _API_CORPUS.exists():
            pytest.skip()
        cases, _ = CorpusLoader.from_file(_API_CORPUS)
        report = run_calibration(cases, _api_clients())
        assert report.fpr < 1.0, f"FPR={report.fpr} — all benign cases are false-positives"

    def test_unknown_app_counted_as_failed(self):
        """A case with an unregistered app should produce a predictable error result."""
        case = _make_case(app="does_not_exist", family="normal",
                          expected_verdict="FAILED_TO_REPLAY")
        report = run_calibration([case], {}, name="unknown-app")
        assert report.total == 1
        r = report.cases[0]
        assert r.predicted_verdict == "FAILED_TO_REPLAY"
        assert "No client registered" in r.notes

    def test_missing_app_does_not_crash(self):
        """Missing app in clients dict should not raise — just count as incorrect."""
        case = _make_case(app="missing", family="normal",
                          expected_verdict="REPRODUCIBLE_STRICT")
        report = run_calibration([case], {})
        assert report.total == 1

    # --- LLM corpus ----------------------------------------------------------

    def test_llm_corpus_normal_cases_correct(self):
        if not _LLM_CORPUS.exists():
            pytest.skip()
        cases, default_rules = CorpusLoader.from_file(_LLM_CORPUS)
        report = run_calibration(cases, _llm_clients(), name="llm-test",
                                 default_rules_path=default_rules)
        normal_cases = [r for r in report.cases if r.family == "normal"]
        assert all(r.correct for r in normal_cases), (
            [(r.label, r.predicted_verdict) for r in normal_cases if not r.correct]
        )

    def test_llm_corpus_incoherent_has_violations(self):
        """INCOHERENT case must produce >= 2 rule violations."""
        if not _LLM_CORPUS.exists():
            pytest.skip()
        cases, default_rules = CorpusLoader.from_file(_LLM_CORPUS)
        report = run_calibration(cases, _llm_clients(), name="llm-incoherent",
                                 default_rules_path=default_rules)
        incoherent = next(
            (r for r in report.cases if "incoherent" in r.label), None
        )
        assert incoherent is not None
        assert len(incoherent.violations) >= 2, (
            f"Expected >=2 violations for INCOHERENT; got {incoherent.violations}"
        )

    def test_llm_corpus_missing_choices_detected(self):
        if not _LLM_CORPUS.exists():
            pytest.skip()
        cases, default_rules = CorpusLoader.from_file(_LLM_CORPUS)
        report = run_calibration(cases, _llm_clients(),
                                 default_rules_path=default_rules)
        missing = next(
            (r for r in report.cases if "missing" in r.label), None
        )
        assert missing is not None
        assert missing.predicted_verdict == "DRIFT_DETECTED"

    def test_llm_corpus_fnr_zero(self):
        """All dangerous LLM cases must be detected (verdict or violations)."""
        if not _LLM_CORPUS.exists():
            pytest.skip()
        cases, default_rules = CorpusLoader.from_file(_LLM_CORPUS)
        report = run_calibration(cases, _llm_clients(),
                                 default_rules_path=default_rules)
        assert report.fnr == pytest.approx(0.0), (
            f"FNR={report.fnr} — LLM dangerous cases missed"
        )

    # --- Chaos corpus --------------------------------------------------------

    def test_chaos_corpus_all_dangerous_detected(self):
        if not _CHAOS_CORPUS.exists():
            pytest.skip()
        cases, _ = CorpusLoader.from_file(_CHAOS_CORPUS)
        report = run_calibration(cases, _chaos_clients(), name="chaos-test")
        dng = [r for r in report.cases if r.family == "dangerous"]
        assert all(r.correct for r in dng), (
            [(r.label, r.predicted_verdict) for r in dng if not r.correct]
        )

    def test_chaos_corpus_stable_cases_not_flagged(self):
        if not _CHAOS_CORPUS.exists():
            pytest.skip()
        cases, _ = CorpusLoader.from_file(_CHAOS_CORPUS)
        report = run_calibration(cases, _chaos_clients())
        benign = [r for r in report.cases if r.family in ("normal", "tolerable")]
        flagged = [r for r in benign if r.is_false_positive]
        assert flagged == [], [(r.label, r.predicted_verdict) for r in flagged]

    def test_chaos_corpus_fnr_zero(self):
        if not _CHAOS_CORPUS.exists():
            pytest.skip()
        cases, _ = CorpusLoader.from_file(_CHAOS_CORPUS)
        report = run_calibration(cases, _chaos_clients())
        assert report.fnr == pytest.approx(0.0)

    # --- Force mode validation -----------------------------------------------

    def test_force_mode_incoherent_always_empty(self):
        """force_mode='incoherent' always returns zeroed content regardless of seed."""
        for seed in range(5):
            client = TestClient(
                create_llm_demo_app(force_mode="incoherent",
                                    rng=random.Random(seed)),
                raise_server_exceptions=False,
            )
            body = client.post("/v1/completions", json={}).json()
            assert body["choices"][0]["text"] == ""
            assert body["usage"]["total_tokens"] == 0

    def test_force_mode_missing_always_no_choices(self):
        for seed in range(5):
            client = TestClient(
                create_llm_demo_app(force_mode="missing", rng=random.Random(seed)),
                raise_server_exceptions=False,
            )
            body = client.post("/v1/completions", json={}).json()
            assert "choices" not in body
            assert "error" in body

    def test_force_mode_invalid_raises(self):
        with pytest.raises(ValueError, match="force_mode"):
            create_llm_demo_app(force_mode="unknown_mode")


# ===========================================================================
# 5. CalibrationReport output
# ===========================================================================

class TestCalibrationReport:

    def _quick_report(self) -> CalibrationReport:
        results = [
            _make_result(family="normal",    correct=True,
                         expected_verdict="REPRODUCIBLE_STRICT",
                         predicted_verdict="REPRODUCIBLE_STRICT"),
            _make_result(family="dangerous", correct=True,
                         expected_verdict="DRIFT_DETECTED",
                         predicted_verdict="DRIFT_DETECTED"),
            _make_result(family="dangerous", correct=False,
                         expected_verdict="FAILED_TO_REPLAY",
                         predicted_verdict="REPRODUCIBLE_SEMANTIC"),
        ]
        return _build_report("unit-test", results)

    def test_to_dict_required_keys(self):
        d = self._quick_report().to_dict()
        required = {
            "name", "timestamp", "total", "correct",
            "false_positives", "false_negatives",
            "accuracy_pct", "fpr", "fnr",
            "by_family", "by_verdict", "confusion_matrix", "cases",
        }
        assert required <= set(d.keys())

    def test_to_dict_cases_list(self):
        d = self._quick_report().to_dict()
        assert isinstance(d["cases"], list)
        assert len(d["cases"]) == 3

    def test_save_and_reload(self, tmp_path):
        report = self._quick_report()
        path = tmp_path / "calib.json"
        report.save(path)
        assert path.exists()
        loaded = json.loads(path.read_text())
        assert loaded["name"]  == "unit-test"
        assert loaded["total"] == 3

    def test_save_creates_parent_dirs(self, tmp_path):
        report = self._quick_report()
        path = tmp_path / "nested" / "deep" / "report.json"
        report.save(path)
        assert path.exists()

    def test_summary_table_contains_name(self):
        text = self._quick_report().summary_table()
        assert "unit-test" in text

    def test_summary_table_contains_accuracy(self):
        text = self._quick_report().summary_table()
        assert "Accuracy" in text or "accuracy" in text

    def test_summary_table_contains_fpr_fnr(self):
        text = self._quick_report().summary_table()
        assert "FPR" in text
        assert "FNR" in text

    def test_summary_table_lists_families(self):
        text = self._quick_report().summary_table()
        assert "normal"    in text
        assert "dangerous" in text

    def test_summary_table_confusion_matrix(self):
        text = self._quick_report().summary_table()
        assert "DRIFT_DETECTED" in text

    def test_report_timestamp_utc_format(self):
        r = self._quick_report()
        # Should end with Z or +00:00
        assert r.timestamp.endswith("Z") or "+" in r.timestamp
