"""
Axiom Lab — Generic probe.

Domain-agnostic session capture, HTTP replay, and verdict engine.
No domain-specific verdict escalation; consumer code applies rules on top
via rules_engine.py.

Verdict hierarchy (ascending severity):
  REPRODUCIBLE_STRICT   — byte-identical status and body
  REPRODUCIBLE_SEMANTIC — only non-semantic fields differ (request_id, etc.)
  DRIFT_DETECTED        — meaningful body difference or status change
  FAILED_TO_REPLAY      — 5xx or connection error
"""
from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Optional Rust acceleration (axiom_core extension module)
# ---------------------------------------------------------------------------
try:
    import axiom_core as _rust
    _RUST_AVAILABLE = True
except ImportError:  # pragma: no cover
    _rust = None  # type: ignore[assignment]
    _RUST_AVAILABLE = False


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

class Verdict(Enum):
    REPRODUCIBLE_STRICT   = "REPRODUCIBLE_STRICT"
    REPRODUCIBLE_SEMANTIC = "REPRODUCIBLE_SEMANTIC"
    DRIFT_DETECTED        = "DRIFT_DETECTED"
    FAILED_TO_REPLAY      = "FAILED_TO_REPLAY"


# Fields that differ per-request but carry no semantic meaning.
# _json_diff skips these; any remaining differences drive the verdict.
_NON_SEMANTIC_FIELDS = frozenset({
    "request_id", "id", "trace_id", "created_at", "timestamp",
    "system_fingerprint", "x_request_id",
})


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class DriftItem:
    path:     str
    original: str
    replayed: str
    reason:   str = ""


@dataclass
class ExchangeRecord:
    method:          str
    uri:             str
    body:            dict[str, Any] | None
    expected_status: int
    expected_body:   dict[str, Any]
    label:           str = ""

    def to_dict(self) -> dict:
        return {
            "method":          self.method,
            "uri":             self.uri,
            "body":            self.body,
            "expected_status": self.expected_status,
            "expected_body":   self.expected_body,
            "label":           self.label,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExchangeRecord":
        return cls(
            method=d["method"],
            uri=d["uri"],
            body=d.get("body"),
            expected_status=d["expected_status"],
            expected_body=d["expected_body"],
            label=d.get("label", ""),
        )


@dataclass
class VerdictReport:
    verdict:           Verdict
    original_status:   int
    replay_status:     int
    replay_latency_ms: float
    drift:             list[DriftItem] = field(default_factory=list)
    replay_body:       dict[str, Any]  = field(default_factory=dict)
    summary:           str = ""


# ---------------------------------------------------------------------------
# JSON diff
# ---------------------------------------------------------------------------

def _json_diff(
    original: dict[str, Any],
    replayed: dict[str, Any],
    *,
    path_prefix: str = "",
    ignore: frozenset[str] = _NON_SEMANTIC_FIELDS,
) -> list[DriftItem]:
    """Recursive deep diff over two dicts; returns changed paths.

    Lists and scalar values are compared by equality (not element-wise for
    lists).  Non-semantic fields are silently skipped.

    When the axiom_core Rust extension is available and the caller uses the
    default arguments, the diff is computed in Rust (~10× faster for large
    payloads) and the results are converted back to Python DriftItem objects.
    """
    # Fast path: delegate to Rust for the common default call
    if _RUST_AVAILABLE and path_prefix == "" and ignore is _NON_SEMANTIC_FIELDS:
        rust_items = _rust.json_diff(original, replayed)
        return [
            DriftItem(
                path=item.path,
                original=item.original,
                replayed=item.replayed,
                reason=item.reason,
            )
            for item in rust_items
        ]

    # Pure-Python fallback (also used for nested recursive calls)
    diffs: list[DriftItem] = []
    all_keys = set(original) | set(replayed)
    for key in sorted(all_keys):
        if key in ignore:
            continue
        path = f"{path_prefix}/{key}"
        if key not in replayed:
            diffs.append(DriftItem(path, str(original[key]), "MISSING", "field removed"))
        elif key not in original:
            diffs.append(DriftItem(path, "ABSENT", str(replayed[key]), "field added"))
        else:
            ov, rv = original[key], replayed[key]
            if isinstance(ov, dict) and isinstance(rv, dict):
                diffs.extend(_json_diff(ov, rv, path_prefix=path, ignore=ignore))
            elif ov != rv:
                diffs.append(DriftItem(path, str(ov), str(rv), "value changed"))
    return diffs


# ---------------------------------------------------------------------------
# Session capture
# ---------------------------------------------------------------------------

class SessionCapture:
    def __init__(self, client) -> None:
        self._client  = client
        self._records: list[ExchangeRecord] = []

    def post(self, uri: str, body: dict, *, label: str = "") -> dict[str, Any]:
        r         = self._client.post(uri, json=body)
        resp_body = r.json() if r.content else {}
        self._records.append(ExchangeRecord(
            method="POST", uri=uri, body=body,
            expected_status=r.status_code, expected_body=resp_body,
            label=label or f"POST {uri}",
        ))
        return resp_body

    def get(self, uri: str, *, label: str = "") -> dict[str, Any]:
        r         = self._client.get(uri)
        resp_body = r.json() if r.content else {}
        self._records.append(ExchangeRecord(
            method="GET", uri=uri, body=None,
            expected_status=r.status_code, expected_body=resp_body,
            label=label or f"GET {uri}",
        ))
        return resp_body

    @property
    def records(self) -> list[ExchangeRecord]:
        return list(self._records)

    def save(self, path: str | Path) -> None:
        """Persist session to a JSON fixture file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            json.dump([r.to_dict() for r in self._records], f, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> list[ExchangeRecord]:
        """Load a session from a JSON fixture file."""
        with open(path) as f:
            data = json.load(f)
        return [ExchangeRecord.from_dict(d) for d in data]


# ---------------------------------------------------------------------------
# Verdict engine
# ---------------------------------------------------------------------------

def evaluate(record: ExchangeRecord, replay_client) -> VerdictReport:
    t0 = time.perf_counter()
    try:
        if record.method == "POST":
            r = replay_client.post(record.uri, json=record.body)
        else:
            r = replay_client.get(record.uri)
        latency_ms = (time.perf_counter() - t0) * 1000.0
    except Exception:
        return VerdictReport(
            verdict=Verdict.FAILED_TO_REPLAY,
            original_status=record.expected_status,
            replay_status=0,
            replay_latency_ms=0.0,
            summary=f"Connection error: {traceback.format_exc(limit=1).strip()}",
        )

    replay_status = r.status_code
    replay_body: dict[str, Any] = {}
    try:
        if r.content:
            replay_body = r.json()
    except Exception:
        pass

    if replay_status >= 500:
        return VerdictReport(
            verdict=Verdict.FAILED_TO_REPLAY,
            original_status=record.expected_status,
            replay_status=replay_status,
            replay_latency_ms=latency_ms,
            replay_body=replay_body,
            summary=f"Replay returned {replay_status}",
        )

    if replay_status != record.expected_status:
        reason = f"HTTP status changed: {record.expected_status} → {replay_status}"
        return VerdictReport(
            verdict=Verdict.DRIFT_DETECTED,
            original_status=record.expected_status,
            replay_status=replay_status,
            replay_latency_ms=latency_ms,
            drift=[DriftItem("/http_status", str(record.expected_status),
                             str(replay_status), reason)],
            replay_body=replay_body,
            summary=reason,
        )

    drifts = _json_diff(record.expected_body, replay_body)
    if drifts:
        field_list = ", ".join(d.path for d in drifts)
        return VerdictReport(
            verdict=Verdict.DRIFT_DETECTED,
            original_status=record.expected_status,
            replay_status=replay_status,
            replay_latency_ms=latency_ms,
            drift=drifts,
            replay_body=replay_body,
            summary=f"Drift on: {field_list}",
        )

    # No meaningful diff — STRICT if byte-identical, SEMANTIC otherwise
    if record.expected_body == replay_body:
        return VerdictReport(
            verdict=Verdict.REPRODUCIBLE_STRICT,
            original_status=record.expected_status,
            replay_status=replay_status,
            replay_latency_ms=latency_ms,
            replay_body=replay_body,
        )

    return VerdictReport(
        verdict=Verdict.REPRODUCIBLE_SEMANTIC,
        original_status=record.expected_status,
        replay_status=replay_status,
        replay_latency_ms=latency_ms,
        replay_body=replay_body,
    )


def replay_session(
    records: list[ExchangeRecord],
    replay_client,
) -> list[VerdictReport]:
    return [evaluate(record, replay_client) for record in records]


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

@dataclass
class SessionSummary:
    total:                 int = 0
    reproducible_strict:   int = 0
    reproducible_semantic: int = 0
    drift_detected:        int = 0
    failed_to_replay:      int = 0

    @property
    def regression_rate_pct(self) -> float:
        if self.total == 0:
            return 0.0
        bad = self.drift_detected + self.failed_to_replay
        return bad / self.total * 100.0

    @classmethod
    def from_reports(cls, reports: list[VerdictReport]) -> "SessionSummary":
        s = cls(total=len(reports))
        for r in reports:
            if r.verdict == Verdict.REPRODUCIBLE_STRICT:
                s.reproducible_strict += 1
            elif r.verdict == Verdict.REPRODUCIBLE_SEMANTIC:
                s.reproducible_semantic += 1
            elif r.verdict == Verdict.DRIFT_DETECTED:
                s.drift_detected += 1
            elif r.verdict == Verdict.FAILED_TO_REPLAY:
                s.failed_to_replay += 1
        return s
