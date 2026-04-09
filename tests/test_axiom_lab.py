"""
tests/test_axiom_lab.py

Full test suite for the Axiom Lab evaluation discipline.

Six test classes covering all layers of the stack:
  TestProbeCore          — json_diff, evaluate, session serialization
  TestApiDemoEndpoints   — API demo endpoint correctness in both modes
  TestApiDemoAxiomReplay — Axiom verdict engine on the API demo
  TestLlmDemo            — LLM demo endpoints and Axiom replay
  TestChaosScenarios     — Chaos app fault injection and Axiom detection
  TestRulesEngine        — Rules loading, suppression, violations
  TestCampaignRunner     — End-to-end campaign execution and reporting
"""
from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from axiom_lab.api_demo.app import create_api_demo_app
from axiom_lab.campaign import CampaignConfig, CampaignReport, run_campaign
from axiom_lab.chaos.app import create_chaos_app
from axiom_lab.llm_demo.app import create_llm_demo_app
from axiom_lab.probe import (
    DriftItem,
    ExchangeRecord,
    SessionCapture,
    SessionSummary,
    Verdict,
    VerdictReport,
    _json_diff,
    evaluate,
    replay_session,
)
from axiom_lab.rules_engine import EvaluatedReport, RuleViolation, RulesEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stable_client() -> TestClient:
    return TestClient(create_api_demo_app(drift_mode=False), raise_server_exceptions=False)


def _drifted_client(seed: int = 0) -> TestClient:
    return TestClient(
        create_api_demo_app(drift_mode=True, rng=random.Random(seed)),
        raise_server_exceptions=False,
    )


def _capture_api_stable() -> list[ExchangeRecord]:
    cap = SessionCapture(_stable_client())
    cap.get("/health")
    cap.post("/echo",  {"message": "hello", "value": 42})
    cap.post("/drift", {"context": "feature_extraction"})
    return cap.records


# ===========================================================================
# 1. Probe core — json_diff, evaluate, session serialisation
# ===========================================================================

class TestProbeCore:

    def test_json_diff_identical_returns_empty(self):
        assert _json_diff({"a": 1}, {"a": 1}) == []

    def test_json_diff_added_field(self):
        diffs = _json_diff({"a": 1}, {"a": 1, "b": 2})
        assert len(diffs) == 1
        assert diffs[0].path == "/b"
        assert diffs[0].original == "ABSENT"

    def test_json_diff_removed_field(self):
        diffs = _json_diff({"a": 1, "b": 2}, {"a": 1})
        assert len(diffs) == 1
        assert diffs[0].path == "/b"
        assert diffs[0].replayed == "MISSING"

    def test_json_diff_changed_value(self):
        diffs = _json_diff({"score": 0.92}, {"score": 0.55})
        assert len(diffs) == 1
        assert diffs[0].path == "/score"
        assert diffs[0].original == "0.92"
        assert diffs[0].replayed == "0.55"

    def test_json_diff_ignores_non_semantic_fields(self):
        diffs = _json_diff(
            {"request_id": "aaa", "id": "111", "value": 1},
            {"request_id": "bbb", "id": "222", "value": 1},
        )
        assert diffs == []

    def test_json_diff_nested_dict(self):
        diffs = _json_diff(
            {"usage": {"total_tokens": 18}},
            {"usage": {"total_tokens": 20}},
        )
        assert len(diffs) == 1
        assert diffs[0].path == "/usage/total_tokens"

    def test_json_diff_list_change(self):
        diffs = _json_diff(
            {"choices": [{"text": "A"}]},
            {"choices": [{"text": "B"}]},
        )
        assert len(diffs) == 1
        assert diffs[0].path == "/choices"

    def test_evaluate_strict_when_identical(self):
        client = _stable_client()
        record = ExchangeRecord("GET", "/health", None, 200,
                                {"status": "ok", "version": "1.0.0"})
        report = evaluate(record, client)
        assert report.verdict == Verdict.REPRODUCIBLE_STRICT

    def test_evaluate_semantic_when_only_request_id_differs(self):
        client = _stable_client()
        record = ExchangeRecord("POST", "/echo", {"msg": "hi"}, 200,
                                {"request_id": "old-uuid", "msg": "hi"})
        report = evaluate(record, client)
        assert report.verdict == Verdict.REPRODUCIBLE_SEMANTIC

    def test_evaluate_drift_when_field_changes(self):
        stable_client  = _stable_client()
        drifted_client = _drifted_client(seed=1)

        # Capture stable record for /drift
        cap = SessionCapture(stable_client)
        cap.post("/drift", {"context": "test"})
        record = cap.records[0]

        # Compare against drifted app
        report = evaluate(record, drifted_client)
        assert report.verdict == Verdict.DRIFT_DETECTED

    def test_evaluate_failed_on_500(self):
        client = _stable_client()
        record = ExchangeRecord("POST", "/boom", {}, 500, {})
        report = evaluate(record, client)
        assert report.verdict == Verdict.FAILED_TO_REPLAY

    def test_evaluate_stores_replay_body(self):
        client = _stable_client()
        record = ExchangeRecord("GET", "/health", None, 200,
                                {"status": "ok", "version": "1.0.0"})
        report = evaluate(record, client)
        assert report.replay_body == {"status": "ok", "version": "1.0.0"}

    def test_session_serialisation_roundtrip(self):
        record = ExchangeRecord(
            method="POST", uri="/echo",
            body={"key": "val"},
            expected_status=200,
            expected_body={"request_id": "x", "key": "val"},
            label="my label",
        )
        restored = ExchangeRecord.from_dict(record.to_dict())
        assert restored.method         == record.method
        assert restored.uri            == record.uri
        assert restored.body           == record.body
        assert restored.expected_status == record.expected_status
        assert restored.expected_body  == record.expected_body
        assert restored.label          == record.label

    def test_session_save_and_load(self, tmp_path):
        records = _capture_api_stable()
        fixture = str(tmp_path / "session.json")

        cap = SessionCapture(_stable_client())
        for r in records:
            cap._records.append(r)
        cap.save(fixture)

        loaded = SessionCapture.load(fixture)
        assert len(loaded) == len(records)
        for orig, rest in zip(records, loaded):
            assert orig.uri    == rest.uri
            assert orig.method == rest.method
            assert orig.body   == rest.body

    def test_session_summary_regression_rate(self):
        reports = [
            VerdictReport(Verdict.REPRODUCIBLE_SEMANTIC, 200, 200, 1.0),
            VerdictReport(Verdict.DRIFT_DETECTED,        200, 200, 1.0),
            VerdictReport(Verdict.FAILED_TO_REPLAY,      200, 500, 1.0),
            VerdictReport(Verdict.REPRODUCIBLE_STRICT,   200, 200, 1.0),
        ]
        s = SessionSummary.from_reports(reports)
        assert s.total              == 4
        assert s.drift_detected     == 1
        assert s.failed_to_replay   == 1
        assert s.regression_rate_pct == pytest.approx(50.0)


# ===========================================================================
# 2. API demo endpoints — raw HTTP correctness
# ===========================================================================

class TestApiDemoEndpoints:

    def test_health_returns_200(self):
        c = _stable_client()
        assert c.get("/health").status_code == 200

    def test_health_has_status_ok(self):
        body = _stable_client().get("/health").json()
        assert body["status"] == "ok"

    def test_health_has_version(self):
        body = _stable_client().get("/health").json()
        assert "version" in body

    def test_echo_mirrors_string_field(self):
        body = _stable_client().post("/echo", json={"msg": "hello"}).json()
        assert body["msg"] == "hello"

    def test_echo_mirrors_numeric_field(self):
        body = _stable_client().post("/echo", json={"n": 99}).json()
        assert body["n"] == 99

    def test_echo_adds_request_id(self):
        body = _stable_client().post("/echo", json={}).json()
        assert "request_id" in body

    def test_drift_stable_score_is_fixed(self):
        body = _stable_client().post("/drift", json={}).json()
        assert body["score"] == 0.92

    def test_drift_stable_tag_is_stable(self):
        body = _stable_client().post("/drift", json={}).json()
        assert body["tag"] == "stable"

    def test_drift_stable_processed_true(self):
        body = _stable_client().post("/drift", json={}).json()
        assert body["processed"] is True

    def test_drift_drifted_score_varies(self):
        scores = set()
        for seed in range(10):
            c    = _drifted_client(seed=seed)
            body = c.post("/drift", json={}).json()
            scores.add(body["score"])
        assert len(scores) > 1, "Drifted score should vary across seeds"

    def test_drift_drifted_tag_varies(self):
        tags = set()
        for seed in range(10):
            c    = _drifted_client(seed=seed)
            body = c.post("/drift", json={}).json()
            tags.add(body["tag"])
        assert len(tags) > 1, "Drifted tag should vary across seeds"

    def test_boom_returns_500(self):
        assert _stable_client().post("/boom", json={}).status_code == 500


# ===========================================================================
# 3. Axiom replay on the API demo
# ===========================================================================

class TestApiDemoAxiomReplay:

    def _session(self):
        return _capture_api_stable()

    def test_stable_to_stable_health_strict(self):
        records = self._session()
        health  = next(r for r in records if r.uri == "/health")
        report  = evaluate(health, _stable_client())
        assert report.verdict == Verdict.REPRODUCIBLE_STRICT

    def test_stable_to_stable_echo_semantic(self):
        records = self._session()
        echo    = next(r for r in records if "/echo" in r.uri)
        report  = evaluate(echo, _stable_client())
        assert report.verdict == Verdict.REPRODUCIBLE_SEMANTIC

    def test_stable_to_stable_drift_semantic(self):
        records = self._session()
        drift   = next(r for r in records if "/drift" in r.uri)
        report  = evaluate(drift, _stable_client())
        assert report.verdict in (Verdict.REPRODUCIBLE_STRICT, Verdict.REPRODUCIBLE_SEMANTIC)

    def test_stable_to_drifted_health_still_strict(self):
        records = self._session()
        health  = next(r for r in records if r.uri == "/health")
        report  = evaluate(health, _drifted_client())
        assert report.verdict == Verdict.REPRODUCIBLE_STRICT

    def test_stable_to_drifted_echo_still_semantic(self):
        records = self._session()
        echo    = next(r for r in records if "/echo" in r.uri)
        report  = evaluate(echo, _drifted_client())
        assert report.verdict == Verdict.REPRODUCIBLE_SEMANTIC

    def test_stable_to_drifted_drift_detected(self):
        records = self._session()
        d_rec   = next(r for r in records if "/drift" in r.uri)
        # Use a seed known to change score/tag from stable values
        report  = evaluate(d_rec, _drifted_client(seed=1))
        assert report.verdict == Verdict.DRIFT_DETECTED

    def test_drift_path_is_score_or_tag(self):
        records = self._session()
        d_rec   = next(r for r in records if "/drift" in r.uri)
        report  = evaluate(d_rec, _drifted_client(seed=1))
        paths = {d.path for d in report.drift}
        assert paths & {"/score", "/tag"}, f"Expected /score or /tag in drift paths, got {paths}"

    def test_boom_produces_failed_to_replay(self):
        record = ExchangeRecord("POST", "/boom", {}, 200, {})
        report = evaluate(record, _stable_client())
        assert report.verdict == Verdict.FAILED_TO_REPLAY

    def test_full_session_regression_with_drift(self):
        records = _capture_api_stable()
        reports = replay_session(records, _drifted_client(seed=1))
        summary = SessionSummary.from_reports(reports)
        assert summary.regression_rate_pct > 0.0


# ===========================================================================
# 4. LLM demo — endpoints and Axiom replay
# ===========================================================================

class TestLlmDemo:

    def _stable(self):
        return TestClient(create_llm_demo_app(drift_mode=False),
                          raise_server_exceptions=False)

    def _drifted(self, seed=0):
        return TestClient(
            create_llm_demo_app(drift_mode=True, rng=random.Random(seed)),
            raise_server_exceptions=False,
        )

    def test_stable_completions_200(self):
        assert self._stable().post("/v1/completions", json={}).status_code == 200

    def test_stable_completions_has_choices(self):
        body = self._stable().post("/v1/completions", json={}).json()
        assert "choices" in body

    def test_stable_completions_choices_has_text(self):
        body    = self._stable().post("/v1/completions", json={}).json()
        choices = body["choices"]
        assert len(choices) > 0
        assert "text" in choices[0]

    def test_stable_completions_has_usage(self):
        body = self._stable().post("/v1/completions", json={}).json()
        assert "usage" in body

    def test_stable_models_200(self):
        assert self._stable().get("/v1/models").status_code == 200

    def test_stable_to_stable_reproducible(self):
        cap     = SessionCapture(self._stable())
        cap.post("/v1/completions", {"prompt": "q"})
        records = cap.records
        report  = evaluate(records[0], self._stable())
        assert report.verdict in (Verdict.REPRODUCIBLE_STRICT, Verdict.REPRODUCIBLE_SEMANTIC)

    def test_drifted_completions_vary_across_seeds(self):
        texts = set()
        for seed in range(20):
            c    = self._drifted(seed=seed)
            body = c.post("/v1/completions", json={"prompt": "q"}).json()
            # Could be missing choices; just collect what we can
            choices = body.get("choices", [])
            if choices:
                texts.add(choices[0].get("text", ""))
        assert len(texts) > 1, "Drifted LLM should return varied text across seeds"

    def test_stable_to_drifted_drift_detected(self):
        cap  = SessionCapture(self._stable())
        cap.post("/v1/completions", {"prompt": "q"})
        record = cap.records[0]

        # seed=3 → roll < 0.35 (VARIABLE_CONTENT) or other drift mode
        # drive until we find a seed that changes the output
        for seed in range(50):
            report = evaluate(record, self._drifted(seed=seed))
            if report.verdict == Verdict.DRIFT_DETECTED:
                return  # found one
        pytest.fail("Expected at least one DRIFT_DETECTED across 50 seeds")

    def test_missing_choices_is_drift(self):
        """Force MISSING_FIELD mode by using a seeded RNG that hits 0.35–0.55."""
        cap  = SessionCapture(self._stable())
        cap.post("/v1/completions", {"prompt": "q"})
        record = cap.records[0]

        # Find a seed that produces a missing-choices response
        for seed in range(100):
            rng  = random.Random(seed)
            roll = rng.random()
            if 0.35 <= roll < 0.55:
                c      = self._drifted(seed=seed)
                report = evaluate(record, c)
                assert report.verdict == Verdict.DRIFT_DETECTED
                assert any("choices" in d.path or "error" in d.path for d in report.drift)
                return
        pytest.skip("No suitable seed found in range(100) — extend range or adjust bounds")

    def test_id_field_ignored_in_stable_replay(self):
        """'id' is in _NON_SEMANTIC_FIELDS; stable→stable should not drift on it."""
        cap  = SessionCapture(self._stable())
        cap.post("/v1/completions", {"prompt": "q"})
        record = cap.records[0]
        report = evaluate(record, self._stable())
        drift_paths = {d.path for d in report.drift}
        assert "/id" not in drift_paths


# ===========================================================================
# 5. Chaos scenarios
# ===========================================================================

class TestChaosScenarios:

    def _stable_chaos(self, slow_ms=10):
        return TestClient(
            create_chaos_app(chaos_enabled=False, slow_ms=slow_ms),
            raise_server_exceptions=False,
        )

    def _chaotic(self, *, flaky_error_rate=1.0, slow_ms=10, rng=None):
        return TestClient(
            create_chaos_app(
                chaos_enabled=True,
                slow_ms=slow_ms,
                flaky_error_rate=flaky_error_rate,
                rng=rng or random.Random(0),
            ),
            raise_server_exceptions=False,
        )

    def _capture_stable(self):
        cap = SessionCapture(self._stable_chaos())
        cap.get("/slow")
        cap.post("/flaky", {})
        cap.get("/empty")
        cap.get("/malformed")
        cap.get("/down")
        return cap.records

    def test_stable_all_endpoints_200(self):
        client = self._stable_chaos()
        for uri in ["/slow", "/empty", "/malformed", "/down"]:
            assert client.get(uri).status_code == 200, f"{uri} should be 200 in stable mode"

    def test_stable_flaky_200(self):
        assert self._stable_chaos().post("/flaky", json={}).status_code == 200

    def test_chaos_flaky_produces_failed_to_replay(self):
        records = self._capture_stable()
        flaky   = next(r for r in records if "/flaky" in r.uri)
        # error_rate=1.0 → always fails
        report  = evaluate(flaky, self._chaotic(flaky_error_rate=1.0))
        assert report.verdict == Verdict.FAILED_TO_REPLAY

    def test_chaos_down_produces_failed_to_replay(self):
        records = self._capture_stable()
        down    = next(r for r in records if "/down" in r.uri)
        report  = evaluate(down, self._chaotic())
        assert report.verdict == Verdict.FAILED_TO_REPLAY

    def test_chaos_empty_produces_drift(self):
        records = self._capture_stable()
        empty   = next(r for r in records if "/empty" in r.uri)
        report  = evaluate(empty, self._chaotic())
        assert report.verdict == Verdict.DRIFT_DETECTED

    def test_chaos_malformed_produces_drift(self):
        records = self._capture_stable()
        mal     = next(r for r in records if "/malformed" in r.uri)
        report  = evaluate(mal, self._chaotic())
        assert report.verdict == Verdict.DRIFT_DETECTED

    def test_chaos_slow_still_responds_200(self):
        # In chaos mode /slow still returns 200, just slower
        assert self._chaotic(slow_ms=10).get("/slow").status_code == 200

    def test_chaos_regression_rate_nonzero(self):
        records = self._capture_stable()
        reports = replay_session(records, self._chaotic(flaky_error_rate=1.0))
        summary = SessionSummary.from_reports(reports)
        assert summary.regression_rate_pct > 0.0


# ===========================================================================
# 6. Rules engine
# ===========================================================================

class TestRulesEngine:

    def _report_with_drift(self, drifts: list[DriftItem], replay_body: dict | None = None) -> VerdictReport:
        return VerdictReport(
            verdict=Verdict.DRIFT_DETECTED,
            original_status=200,
            replay_status=200,
            replay_latency_ms=1.0,
            drift=drifts,
            replay_body=replay_body or {},
        )

    def _clean_report(self, replay_body: dict | None = None) -> VerdictReport:
        return VerdictReport(
            verdict=Verdict.REPRODUCIBLE_SEMANTIC,
            original_status=200,
            replay_status=200,
            replay_latency_ms=1.0,
            replay_body=replay_body or {"status": "ok"},
        )

    def test_ignore_field_suppresses_drift(self):
        engine = RulesEngine([{"id": "R1", "type": "ignore_field", "field": "request_id"}])
        report = self._report_with_drift([DriftItem("/request_id", "old", "new")])
        ev = engine.evaluate(report)
        assert ev.surviving_drift == []
        assert ev.effective_verdict == Verdict.REPRODUCIBLE_SEMANTIC

    def test_ignore_field_does_not_suppress_other_path(self):
        engine = RulesEngine([{"type": "ignore_field", "field": "request_id"}])
        report = self._report_with_drift([DriftItem("/score", "0.9", "0.5")])
        ev = engine.evaluate(report)
        assert len(ev.surviving_drift) == 1

    def test_numeric_tolerance_suppresses_small_change(self):
        engine = RulesEngine([{"type": "numeric_tolerance", "field": "score", "tolerance": 0.05}])
        report = self._report_with_drift([DriftItem("/score", "0.92", "0.94")])
        ev = engine.evaluate(report)
        assert ev.surviving_drift == []
        assert ev.effective_verdict == Verdict.REPRODUCIBLE_SEMANTIC

    def test_numeric_tolerance_does_not_suppress_large_change(self):
        engine = RulesEngine([{"type": "numeric_tolerance", "field": "score", "tolerance": 0.05}])
        report = self._report_with_drift([DriftItem("/score", "0.92", "0.50")])
        ev = engine.evaluate(report)
        assert len(ev.surviving_drift) == 1
        assert ev.effective_verdict == Verdict.DRIFT_DETECTED

    def test_required_field_violation_when_missing(self):
        engine = RulesEngine([{"id": "R1", "description": "needs status", "type": "required_field", "field": "status"}])
        report = self._clean_report(replay_body={"other": "value"})
        ev = engine.evaluate(report)
        assert len(ev.violations) == 1
        assert ev.violations[0].path == "/status"

    def test_required_field_no_violation_when_present(self):
        engine = RulesEngine([{"id": "R1", "type": "required_field", "field": "status"}])
        report = self._clean_report(replay_body={"status": "ok"})
        ev = engine.evaluate(report)
        assert ev.violations == []

    def test_prohibited_field_violation_when_present(self):
        engine = RulesEngine([{"id": "R1", "description": "no debug", "type": "prohibited_field", "field": "debug_token"}])
        report = self._clean_report(replay_body={"status": "ok", "debug_token": "secret"})
        ev = engine.evaluate(report)
        assert len(ev.violations) == 1
        assert "debug_token" in ev.violations[0].path

    def test_prohibited_field_no_violation_when_absent(self):
        engine = RulesEngine([{"type": "prohibited_field", "field": "debug_token"}])
        report = self._clean_report(replay_body={"status": "ok"})
        ev = engine.evaluate(report)
        assert ev.violations == []

    def test_multiple_rules_all_applied(self):
        engine = RulesEngine([
            {"type": "ignore_field",      "field": "request_id"},
            {"type": "numeric_tolerance", "field": "score", "tolerance": 0.01},
            {"type": "required_field",    "field": "processed"},
        ])
        drifts = [
            DriftItem("/request_id", "old", "new"),
            DriftItem("/score", "0.92", "0.921"),   # within tolerance
            DriftItem("/tag",   "stable", "alpha"),  # not suppressed
        ]
        report = self._report_with_drift(drifts, replay_body={"score": 0.921, "tag": "alpha"})
        ev = engine.evaluate(report)
        assert len(ev.surviving_drift) == 1
        assert ev.surviving_drift[0].path == "/tag"
        assert len(ev.violations) == 1     # 'processed' missing

    def test_failed_to_replay_unchanged_by_rules(self):
        engine = RulesEngine([{"type": "ignore_field", "field": "status"}])
        report = VerdictReport(
            verdict=Verdict.FAILED_TO_REPLAY,
            original_status=200, replay_status=500, replay_latency_ms=1.0,
        )
        ev = engine.evaluate(report)
        assert ev.effective_verdict == Verdict.FAILED_TO_REPLAY

    def test_from_file_loads_rules(self, tmp_path):
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps({
            "name": "test",
            "rules": [
                {"id": "X1", "type": "ignore_field", "field": "ts"},
                {"id": "X2", "type": "required_field", "field": "result"},
            ]
        }))
        engine = RulesEngine.from_file(rules_file)
        assert len(engine._rules) == 2

    # ------------------------------------------------------------------
    # V1.10 — content-level semantic rules
    # ------------------------------------------------------------------

    def test_contains_keyword_passes_when_present(self):
        engine = RulesEngine([{"id": "C1", "type": "contains_keyword",
                               "field": "choices.0.text", "keyword": "answer"}])
        body = {"choices": [{"text": "The answer is 42."}]}
        report = self._clean_report(replay_body=body)
        ev = engine.evaluate(report)
        assert ev.violations == []

    def test_contains_keyword_violation_when_absent(self):
        engine = RulesEngine([{"id": "C1", "type": "contains_keyword",
                               "field": "choices.0.text", "keyword": "answer"}])
        body = {"choices": [{"text": ""}]}
        report = self._clean_report(replay_body=body)
        ev = engine.evaluate(report)
        assert len(ev.violations) == 1
        assert "choices/0/text" in ev.violations[0].path

    def test_contains_keyword_case_insensitive_default(self):
        engine = RulesEngine([{"id": "C1", "type": "contains_keyword",
                               "field": "text", "keyword": "ANSWER"}])
        report = self._clean_report(replay_body={"text": "the answer is here"})
        ev = engine.evaluate(report)
        assert ev.violations == []

    def test_contains_keyword_case_sensitive_mismatch(self):
        engine = RulesEngine([{"id": "C1", "type": "contains_keyword",
                               "field": "text", "keyword": "ANSWER",
                               "case_sensitive": True}])
        report = self._clean_report(replay_body={"text": "the answer is here"})
        ev = engine.evaluate(report)
        assert len(ev.violations) == 1

    def test_not_contains_keyword_passes_when_absent(self):
        engine = RulesEngine([{"id": "C2", "type": "not_contains_keyword",
                               "field": "text", "keyword": "error"}])
        report = self._clean_report(replay_body={"text": "all good"})
        ev = engine.evaluate(report)
        assert ev.violations == []

    def test_not_contains_keyword_violation_when_present(self):
        engine = RulesEngine([{"id": "C2", "type": "not_contains_keyword",
                               "field": "text", "keyword": "error"}])
        report = self._clean_report(replay_body={"text": "error occurred upstream"})
        ev = engine.evaluate(report)
        assert len(ev.violations) == 1

    def test_value_in_range_passes(self):
        engine = RulesEngine([{"id": "R7", "type": "value_in_range",
                               "field": "usage.total_tokens", "min": 1, "max": 100000}])
        report = self._clean_report(replay_body={"usage": {"total_tokens": 18}})
        ev = engine.evaluate(report)
        assert ev.violations == []

    def test_value_in_range_violation_when_zero(self):
        engine = RulesEngine([{"id": "R7", "type": "value_in_range",
                               "field": "usage.total_tokens", "min": 1, "max": 100000}])
        report = self._clean_report(replay_body={"usage": {"total_tokens": 0}})
        ev = engine.evaluate(report)
        assert len(ev.violations) == 1
        assert "total_tokens" in ev.violations[0].detail

    def test_value_in_set_passes(self):
        engine = RulesEngine([{"id": "R8", "type": "value_in_set",
                               "field": "choices.0.finish_reason",
                               "allowed": ["stop", "length"]}])
        body = {"choices": [{"finish_reason": "stop"}]}
        report = self._clean_report(replay_body=body)
        ev = engine.evaluate(report)
        assert ev.violations == []

    def test_value_in_set_violation_when_invalid(self):
        engine = RulesEngine([{"id": "R8", "type": "value_in_set",
                               "field": "choices.0.finish_reason",
                               "allowed": ["stop", "length"]}])
        body = {"choices": [{"finish_reason": ""}]}
        report = self._clean_report(replay_body=body)
        ev = engine.evaluate(report)
        assert len(ev.violations) == 1

    def test_field_consistency_inactive_when_condition_not_met(self):
        engine = RulesEngine([{"id": "FC1", "type": "field_consistency",
                               "condition_field": "risk_level", "condition_value": "high",
                               "target_field": "confidence", "constraint": "value_in_range",
                               "min": 0.7, "max": 1.0}])
        # risk_level = "low" — rule should not fire
        report = self._clean_report(replay_body={"risk_level": "low", "confidence": 0.3})
        ev = engine.evaluate(report)
        assert ev.violations == []

    def test_field_consistency_violation_when_out_of_range(self):
        engine = RulesEngine([{"id": "FC1", "type": "field_consistency",
                               "condition_field": "risk_level", "condition_value": "high",
                               "target_field": "confidence", "constraint": "value_in_range",
                               "min": 0.7, "max": 1.0}])
        # risk_level = "high" but confidence is low — violation
        report = self._clean_report(replay_body={"risk_level": "high", "confidence": 0.3})
        ev = engine.evaluate(report)
        assert len(ev.violations) == 1
        assert "confidence" in ev.violations[0].detail

    def test_incoherent_llm_response_caught_by_content_rules(self):
        """INCOHERENT mode: all fields present but empty/zeroed — caught by L005+L006+L007."""
        incoherent_body = {
            "id": "cmpl-abc",
            "object": "text_completion",
            "choices": [{"text": "", "index": 0, "finish_reason": ""}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        rules_path = Path(__file__).parent.parent / "axiom_lab" / "rules" / "llm_demo.json"
        if not rules_path.exists():
            pytest.skip("llm_demo.json rules file not found")
        engine = RulesEngine.from_file(rules_path)
        report = VerdictReport(
            verdict=Verdict.REPRODUCIBLE_SEMANTIC,   # probe sees semantic; rules catch the rest
            original_status=200,
            replay_status=200,
            replay_latency_ms=1.0,
            replay_body=incoherent_body,
        )
        ev = engine.evaluate(report)
        assert len(ev.violations) >= 2, (
            f"Expected at least 2 violations for INCOHERENT body; got {ev.violations}"
        )


# ===========================================================================
# 7. Campaign runner
# ===========================================================================

class TestCampaignRunner:

    def _write_fixture(self, records: list, tmp_path: Path) -> Path:
        cap = SessionCapture(_stable_client())
        for r in records:
            cap._records.append(r)
        p = str(tmp_path / "fixture.json")
        cap.save(p)
        return Path(p)

    def test_stable_campaign_zero_drift(self, tmp_path):
        records = _capture_api_stable()
        fixture = self._write_fixture(records, tmp_path)
        config  = CampaignConfig(name="stable", fixture_path=fixture)
        report  = run_campaign(config, _stable_client())
        assert report.drift   == 0
        assert report.failed  == 0

    def test_drifted_campaign_has_drift(self, tmp_path):
        records = _capture_api_stable()
        fixture = self._write_fixture(records, tmp_path)
        config  = CampaignConfig(name="drifted", fixture_path=fixture)
        report  = run_campaign(config, _drifted_client(seed=1))
        assert report.drift > 0

    def test_campaign_total_count(self, tmp_path):
        records = _capture_api_stable()
        fixture = self._write_fixture(records, tmp_path)
        config  = CampaignConfig(name="count-test", fixture_path=fixture)
        report  = run_campaign(config, _stable_client())
        assert report.total == len(records)

    def test_campaign_regression_rate_zero_on_stable(self, tmp_path):
        records = _capture_api_stable()
        fixture = self._write_fixture(records, tmp_path)
        config  = CampaignConfig(name="rr", fixture_path=fixture)
        report  = run_campaign(config, _stable_client())
        assert report.regression_rate_pct == pytest.approx(0.0)

    def test_campaign_routes_with_issues(self, tmp_path):
        records = _capture_api_stable()
        fixture = self._write_fixture(records, tmp_path)
        config  = CampaignConfig(name="routes", fixture_path=fixture)
        report  = run_campaign(config, _drifted_client(seed=1))
        if report.drift > 0:
            assert "/drift" in report.routes_with_issues

    def test_campaign_details_length_matches_total(self, tmp_path):
        records = _capture_api_stable()
        fixture = self._write_fixture(records, tmp_path)
        config  = CampaignConfig(name="details", fixture_path=fixture)
        report  = run_campaign(config, _stable_client())
        assert len(report.details) == report.total

    def test_campaign_to_dict_shape(self, tmp_path):
        records = _capture_api_stable()
        fixture = self._write_fixture(records, tmp_path)
        config  = CampaignConfig(name="shape", fixture_path=fixture)
        report  = run_campaign(config, _stable_client())
        d = report.to_dict()
        assert set(d.keys()) >= {
            "name", "timestamp", "total", "strict", "semantic",
            "drift", "failed", "rule_violations", "regression_rate_pct",
            "routes_with_issues",
        }

    def test_campaign_save_to_file(self, tmp_path):
        records = _capture_api_stable()
        fixture = self._write_fixture(records, tmp_path)
        config  = CampaignConfig(name="save", fixture_path=fixture)
        report  = run_campaign(config, _stable_client())
        out     = tmp_path / "report.json"
        report.save(out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["name"] == "save"

    def test_campaign_with_rules_file(self, tmp_path):
        """Rules file from axiom_lab/rules/api_demo.json is loadable and applied."""
        rules_path = Path(__file__).parent.parent / "axiom_lab" / "rules" / "api_demo.json"
        if not rules_path.exists():
            pytest.skip("api_demo.json rules file not found")
        records = _capture_api_stable()
        fixture = self._write_fixture(records, tmp_path)
        config  = CampaignConfig(name="rules", fixture_path=fixture, rules_path=rules_path)
        report  = run_campaign(config, _stable_client())
        assert report.total == len(records)

    def test_campaign_from_bundled_fixture(self):
        """The bundled api_demo_stable.json fixture loads and replays cleanly."""
        fixture_path = Path(__file__).parent.parent / "axiom_lab" / "fixtures" / "api_demo_stable.json"
        if not fixture_path.exists():
            pytest.skip("api_demo_stable.json not found")
        config = CampaignConfig(name="bundled", fixture_path=fixture_path)
        report = run_campaign(config, _stable_client())
        assert report.total == 3
        assert report.failed == 0
        # Health strict, echo+drift semantic; all within normal range
        assert report.regression_rate_pct == pytest.approx(0.0)

    # ------------------------------------------------------------------
    # V1.11 — breakdown tables
    # ------------------------------------------------------------------

    def test_by_verdict_populated(self, tmp_path):
        records = _capture_api_stable()
        fixture = self._write_fixture(records, tmp_path)
        config  = CampaignConfig(name="by-verdict", fixture_path=fixture)
        report  = run_campaign(config, _stable_client())
        assert isinstance(report.by_verdict, dict)
        total_from_verdicts = sum(report.by_verdict.values())
        assert total_from_verdicts == report.total

    def test_by_route_populated(self, tmp_path):
        records = _capture_api_stable()
        fixture = self._write_fixture(records, tmp_path)
        config  = CampaignConfig(name="by-route", fixture_path=fixture)
        report  = run_campaign(config, _stable_client())
        assert isinstance(report.by_route, dict)
        # Every URI in records must appear
        for r in records:
            assert r.uri in report.by_route, f"{r.uri} missing from by_route"

    def test_by_route_totals_consistent(self, tmp_path):
        records = _capture_api_stable()
        fixture = self._write_fixture(records, tmp_path)
        config  = CampaignConfig(name="by-route-totals", fixture_path=fixture)
        report  = run_campaign(config, _stable_client())
        total_from_routes  = sum(v["total"] for v in report.by_route.values())
        assert total_from_routes == report.total

    def test_by_rule_class_populated_with_violations(self, tmp_path):
        """A rules file with violations should populate by_rule_class."""
        rules = [
            {"id": "R1", "type": "required_field", "field": "missing_field"},
        ]
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps({"name": "t", "rules": rules}))

        records = _capture_api_stable()
        fixture = self._write_fixture(records, tmp_path)
        config  = CampaignConfig(name="rule-class", fixture_path=fixture, rules_path=rules_path)
        report  = run_campaign(config, _stable_client())
        # "R1" → class "R" should have violations (missing_field on every record)
        assert "R" in report.by_rule_class
        assert report.by_rule_class["R"] > 0

    def test_to_dict_includes_breakdowns(self, tmp_path):
        records = _capture_api_stable()
        fixture = self._write_fixture(records, tmp_path)
        config  = CampaignConfig(name="dict-shape", fixture_path=fixture)
        report  = run_campaign(config, _stable_client())
        d = report.to_dict()
        assert "by_verdict"    in d
        assert "by_route"      in d
        assert "by_rule_class" in d

    def test_details_include_rule_violations_when_present(self, tmp_path):
        """When a rule fires, its details should appear in the per-exchange detail entry."""
        rules = [{"id": "P1", "type": "required_field", "field": "nonexistent"}]
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps({"name": "t", "rules": rules}))

        records = _capture_api_stable()
        fixture = self._write_fixture(records, tmp_path)
        config  = CampaignConfig(name="detail-viols", fixture_path=fixture, rules_path=rules_path)
        report  = run_campaign(config, _stable_client())
        # At least one detail entry should have rule_violations
        entries_with_viols = [d for d in report.details if "rule_violations" in d]
        assert len(entries_with_viols) > 0
