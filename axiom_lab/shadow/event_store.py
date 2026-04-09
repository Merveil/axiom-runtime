"""
Axiom Shadow — Event store.

Thread-safe SQLite store for shadow-captured HTTP exchanges.
Uses a single shared connection (check_same_thread=False) with a write
lock, so the same store instance can be shared safely between the ASGI
middleware thread and any background reader / CLI.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from axiom_lab.probe import ExchangeRecord

# ---------------------------------------------------------------------------
# Optional Rust acceleration (axiom_core extension module)
# ---------------------------------------------------------------------------
try:
    from axiom_core import RustEventStore as _RustStore
    _RUST_AVAILABLE = True
except ImportError:  # pragma: no cover
    _RustStore = None  # type: ignore[assignment,misc]
    _RUST_AVAILABLE = False

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS shadow_events (
    id                  TEXT    PRIMARY KEY,
    timestamp           REAL    NOT NULL,
    app_name            TEXT    NOT NULL DEFAULT '',
    method              TEXT    NOT NULL,
    uri                 TEXT    NOT NULL,
    request_body        TEXT,
    response_status     INTEGER NOT NULL,
    response_body       TEXT    NOT NULL DEFAULT '{}',
    capture_overhead_ms REAL    NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS shadow_ignored (
    path   TEXT    PRIMARY KEY,
    count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS shadow_verdicts (
    event_id    TEXT  PRIMARY KEY,
    verdict     TEXT  NOT NULL,
    replayed_at REAL  NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shadow_ts ON shadow_events(timestamp);
"""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ShadowEvent:
    """One captured HTTP exchange."""
    id:                  str
    timestamp:           float
    app_name:            str
    method:              str
    uri:                 str
    request_body:        dict[str, Any] | None
    response_status:     int
    response_body:       dict[str, Any]
    capture_overhead_ms: float = 0.0

    def to_record(self) -> ExchangeRecord:
        """Convert to an ExchangeRecord suitable for `evaluate()`."""
        return ExchangeRecord(
            method=self.method,
            uri=self.uri,
            body=self.request_body,
            expected_status=self.response_status,
            expected_body=self.response_body,
            label=self.id,
        )


def _make_event(
    method:          str,
    uri:             str,
    request_body:    dict[str, Any] | None,
    response_status: int,
    response_body:   dict[str, Any],
    app_name:        str = "",
    capture_overhead_ms: float = 0.0,
) -> ShadowEvent:
    """Factory used by the middleware — generates a UUID automatically."""
    return ShadowEvent(
        id=uuid.uuid4().hex,
        timestamp=time.time(),
        app_name=app_name,
        method=method,
        uri=uri,
        request_body=request_body,
        response_status=response_status,
        response_body=response_body,
        capture_overhead_ms=capture_overhead_ms,
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class ShadowEventStore:
    """Thread-safe SQLite store for shadow-captured events.

    Args:
        path:       Database file path or ``":memory:"`` for an ephemeral store.
        max_events: Soft cap — when exceeded, the oldest 10 % are pruned.
    """

    def __init__(
        self,
        path: str | Path = ":memory:",
        max_events: int = 10_000,
    ) -> None:
        self._path      = str(path)
        self._max       = max_events

        if _RUST_AVAILABLE:
            # Rust store: zero-GIL SQLite via rusqlite; no Python lock needed.
            self._rust: "_RustStore | None" = _RustStore(self._path, max_events)
        else:
            self._rust = None

        # Python fallback store (also used when Rust is not available)
        self._lock      = threading.Lock()
        self._conn      = sqlite3.connect(
            self._path, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_DDL)
            self._conn.commit()

    # ── Writes ────────────────────────────────────────────────────────────────

    def add_event(self, event: ShadowEvent) -> None:
        if self._rust is not None:
            self._rust.add_event(
                event.id,
                event.timestamp,
                event.app_name,
                event.method,
                event.uri,
                json.dumps(event.request_body) if event.request_body is not None else None,
                event.response_status,
                json.dumps(event.response_body),
                event.capture_overhead_ms,
            )
            return
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO shadow_events
                    (id, timestamp, app_name, method, uri,
                     request_body, response_status, response_body,
                     capture_overhead_ms)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.id,
                    event.timestamp,
                    event.app_name,
                    event.method,
                    event.uri,
                    json.dumps(event.request_body)
                    if event.request_body is not None else None,
                    event.response_status,
                    json.dumps(event.response_body),
                    event.capture_overhead_ms,
                ),
            )
            self._conn.commit()
            # Prune oldest entries when over cap
            if self._max:
                n = self._conn.execute(
                    "SELECT COUNT(*) FROM shadow_events"
                ).fetchone()[0]
                if n > self._max:
                    prune = max(1, self._max // 10)
                    self._conn.execute(
                        "DELETE FROM shadow_events WHERE id IN "
                        "(SELECT id FROM shadow_events "
                        "ORDER BY timestamp ASC LIMIT ?)",
                        (prune,),
                    )
                    self._conn.commit()

    def record_ignored(self, path: str) -> None:
        """Increment the ignored-request counter for *path* (policy rejections only)."""
        if self._rust is not None:
            self._rust.record_ignored(path)
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO shadow_ignored(path, count) VALUES(?,1) "
                "ON CONFLICT(path) DO UPDATE SET count = count + 1",
                (path,),
            )
            self._conn.commit()

    def record_verdict(self, event_id: str, verdict: str) -> None:
        """Persist a replay verdict so ``store_inspection`` can surface drift info."""
        if self._rust is not None:
            self._rust.record_verdict(event_id, verdict)
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO shadow_verdicts(event_id, verdict, replayed_at) "
                "VALUES(?,?,?)",
                (event_id, verdict, time.time()),
            )
            self._conn.commit()

    def clear(self) -> None:
        if self._rust is not None:
            self._rust.clear()
            return
        with self._lock:
            self._conn.execute("DELETE FROM shadow_events")
            self._conn.execute("DELETE FROM shadow_ignored")
            self._conn.execute("DELETE FROM shadow_verdicts")
            self._conn.commit()

    # ── Reads ─────────────────────────────────────────────────────────────────

    def count(self) -> int:
        if self._rust is not None:
            return self._rust.count()
        return self._conn.execute(
            "SELECT COUNT(*) FROM shadow_events"
        ).fetchone()[0]

    def get_events(
        self,
        limit:      int           = 100,
        since:      float | None  = None,
        method:     str | None    = None,
        uri_prefix: str | None    = None,
    ) -> list[ShadowEvent]:
        """Return captured events, newest first, with optional filters."""
        if self._rust is not None:
            raw = self._rust.get_events(limit, since, method, uri_prefix)
            return [
                ShadowEvent(
                    id=r[0],
                    timestamp=r[1],
                    app_name=r[2],
                    method=r[3],
                    uri=r[4],
                    request_body=json.loads(r[5]) if r[5] else None,
                    response_status=r[6],
                    response_body=json.loads(r[7]),
                    capture_overhead_ms=r[8],
                )
                for r in raw
            ]
        q    = "SELECT * FROM shadow_events WHERE 1=1"
        args: list = []
        if since is not None:
            q += " AND timestamp >= ?"
            args.append(since)
        if method:
            q += " AND method = ?"
            args.append(method.upper())
        if uri_prefix:
            q += " AND uri LIKE ?"
            args.append(uri_prefix + "%")
        q += " ORDER BY timestamp DESC LIMIT ?"
        args.append(limit)
        rows = self._conn.execute(q, args).fetchall()
        return [_row_to_event(r) for r in rows]

    def avg_capture_overhead_ms(self) -> float:
        """Average overhead added per captured event (µs-level for SQLite)."""
        if self._rust is not None:
            return self._rust.avg_capture_overhead_ms()
        row = self._conn.execute(
            "SELECT AVG(capture_overhead_ms) FROM shadow_events"
        ).fetchone()[0]
        return row or 0.0

    def get_ignored_total(self) -> int:
        """Total number of requests skipped by capture policy."""
        if self._rust is not None:
            return self._rust.get_ignored_total()
        row = self._conn.execute(
            "SELECT COALESCE(SUM(count), 0) FROM shadow_ignored"
        ).fetchone()[0]
        return int(row)

    def get_ignored_summary(self) -> dict[str, int]:
        """Per-path ignored counts, ordered by count descending."""
        if self._rust is not None:
            return dict(self._rust.get_ignored_summary())
        rows = self._conn.execute(
            "SELECT path, count FROM shadow_ignored ORDER BY count DESC"
        ).fetchall()
        return {r["path"]: r["count"] for r in rows}

    def get_verdict_summary(self) -> dict[str, int]:
        """Count of each verdict string from previous replay runs."""
        if self._rust is not None:
            return dict(self._rust.get_verdict_summary())
        rows = self._conn.execute(
            "SELECT verdict, COUNT(*) AS n FROM shadow_verdicts GROUP BY verdict"
        ).fetchall()
        return {r["verdict"]: r["n"] for r in rows}

    def get_drift_routes(self, limit: int = 5) -> list[tuple[str, int]]:
        """Top routes by drift count from previous replay runs."""
        if self._rust is not None:
            return self._rust.get_drift_routes(limit)
        rows = self._conn.execute(
            """
            SELECT e.uri, COUNT(*) AS drift_count
            FROM shadow_verdicts v
            JOIN shadow_events e ON e.id = v.event_id
            WHERE v.verdict = 'DRIFT_DETECTED'
            GROUP BY e.uri
            ORDER BY drift_count DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def get_top_routes(self, limit: int = 5) -> list[tuple[str, int]]:
        """Top routes by captured-event volume."""
        if self._rust is not None:
            return self._rust.get_top_routes(limit)
        rows = self._conn.execute(
            "SELECT uri, COUNT(*) AS n FROM shadow_events "
            "GROUP BY uri ORDER BY n DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def as_records(self, limit: int = 100) -> list[ExchangeRecord]:
        """Convenience: return events as replay-ready ExchangeRecords."""
        return [e.to_record() for e in self.get_events(limit=limit)]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_event(row: sqlite3.Row) -> ShadowEvent:
    return ShadowEvent(
        id=row["id"],
        timestamp=row["timestamp"],
        app_name=row["app_name"],
        method=row["method"],
        uri=row["uri"],
        request_body=(
            json.loads(row["request_body"])
            if row["request_body"] else None
        ),
        response_status=row["response_status"],
        response_body=json.loads(row["response_body"]),
        capture_overhead_ms=row["capture_overhead_ms"],
    )
