from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import Enum

import typer

from .replayer import EventReplayer
from .schema_drift import detect_schema_drift
from .schemas import RuntimeEvent
from .storage import EventStorage

app = typer.Typer(help="Reliability Runtime CLI")


class ReplayMode(str, Enum):
    strict = "strict"
    semantic = "semantic"


def compare_response_bodies(original_body: str, replay_body: str) -> tuple[bool, str]:
    """
    Compare two response bodies.
    Returns:
      - match: whether they are equivalent
      - mode: 'json' if compared structurally, 'text' if compared as raw text
    """
    original_body = original_body or ""
    replay_body = replay_body or ""

    try:
        original_json = json.loads(original_body)
        replay_json = json.loads(replay_body)
        return original_json == replay_json, "json"
    except Exception:
        return original_body == replay_body, "text"


@dataclass
class ReplayResult:
    verdict: str
    event_id: str
    method: str
    path: str
    original_status: int
    replay_status: int
    body_match: bool
    body_compare_mode: str
    schema_drifts: list[str]
    invariant_match: bool
    violations: list[str]
    original_body: str
    replay_body: str
    had_exception: bool
    error: str | None = None


async def _evaluate_event(
    event: RuntimeEvent,
    base_url: str,
    mode: ReplayMode,
    invariant_rules: dict[str, object],
) -> ReplayResult:
    replayer = EventReplayer(base_url=base_url)
    try:
        response = await replayer.replay_http_event(event)
    except Exception as exc:
        return ReplayResult(
            verdict="FAILED_TO_REPLAY",
            event_id=event.event_id,
            method=event.request.method,
            path=event.request.path,
            original_status=event.response.status_code,
            replay_status=0,
            body_match=False,
            body_compare_mode="text",
            schema_drifts=[],
            invariant_match=False,
            violations=[],
            original_body=event.response.body_text or "",
            replay_body="",
            had_exception=event.exception is not None,
            error=f"{type(exc).__name__}: {exc}",
        )

    original_status = event.response.status_code
    replay_status = response.status_code
    original_body = event.response.body_text or ""
    replay_body = response.text or ""
    original_had_exception = event.exception is not None
    replay_looks_like_error = replay_status >= 500

    status_match = replay_status == original_status
    body_match, body_compare_mode = compare_response_bodies(original_body, replay_body)

    schema_drifts: list[str] = []
    invariant_match = True
    violations: list[str] = []

    if body_compare_mode == "json":
        try:
            from reliability_runtime.invariants import compare_json_with_invariants

            original_json = json.loads(original_body)
            replay_json = json.loads(replay_body)
            invariant_match, violations = compare_json_with_invariants(
                original_json, replay_json, rules=invariant_rules,
            )
            schema_drifts = detect_schema_drift(original_json, replay_json)
        except Exception:
            pass

    if mode == ReplayMode.strict:
        verdict = "REPRODUCIBLE_STRICT" if (status_match and body_match) else "DRIFT_DETECTED"
    else:
        semantic_match = False
        if status_match and body_match and not schema_drifts:
            semantic_match = True
        elif status_match and invariant_match and not schema_drifts:
            semantic_match = True
        elif original_had_exception and replay_looks_like_error and status_match:
            semantic_match = True
        elif not original_had_exception and status_match and not schema_drifts:
            semantic_match = True

        if semantic_match:
            verdict = "REPRODUCIBLE_STRICT" if (status_match and body_match) else "REPRODUCIBLE_SEMANTIC"
        else:
            verdict = "DRIFT_DETECTED"

    return ReplayResult(
        verdict=verdict,
        event_id=event.event_id,
        method=event.request.method,
        path=event.request.path,
        original_status=original_status,
        replay_status=replay_status,
        body_match=body_match,
        body_compare_mode=body_compare_mode,
        schema_drifts=schema_drifts,
        invariant_match=invariant_match,
        violations=violations,
        original_body=original_body,
        replay_body=replay_body,
        had_exception=original_had_exception,
    )


@app.command()
def list_events(
    route: str | None = typer.Option(None, "--route", help="Filter by request path (substring match)."),
    method: str | None = typer.Option(None, "--method", help="Filter by HTTP method (e.g. GET, POST)."),
    status: int | None = typer.Option(None, "--status", help="Filter by response status code."),
    limit: int | None = typer.Option(None, "--limit", help="Maximum number of events to display."),
) -> None:
    """List recorded events, with optional filters."""
    storage = EventStorage()
    events = storage.list_events()

    if route:
        events = [e for e in events if route in e.request.path]
    if method:
        events = [e for e in events if e.request.method.upper() == method.upper()]
    if status:
        events = [e for e in events if e.response.status_code == status]
    if limit is not None:
        events = events[-limit:]

    if not events:
        typer.echo("No recorded events found.")
        raise typer.Exit(code=0)

    for event in events:
        exception_flag = " 🔥" if event.exception else ""
        typer.echo(
            f"{event.event_id} | {event.request.method} {event.request.path} | "
            f"status={event.response.status_code} | {event.response.duration_ms:.2f}ms{exception_flag}"
        )


@app.command()
def show(event_id: str) -> None:
    """Show a single recorded event as JSON and highlight exception info."""
    storage = EventStorage()
    event = storage.get_event(event_id)
    if not event:
        typer.echo(f"Event not found: {event_id}")
        raise typer.Exit(code=1)

    typer.echo(json.dumps(event.model_dump(mode="json"), indent=2))

    if event.exception:
        typer.echo("\n🔥 Exception detected")
        typer.echo(f"Type: {event.exception.type}")
        typer.echo(f"Message: {event.exception.message}")


@app.command()
def clear() -> None:
    """Delete all recorded events."""
    storage = EventStorage()
    storage.clear()
    typer.echo("All events cleared.")


@app.command()
def replay(
    event_id: str,
    base_url: str = "http://127.0.0.1:8000",
    mode: ReplayMode = ReplayMode.strict,
    rules: str | None = typer.Option(None, "--rules", help="Path to a JSON file with invariant rules."),
) -> None:
    """Replay a recorded HTTP event and classify reproducibility."""
    storage = EventStorage()
    event = storage.get_event(event_id)

    if not event:
        typer.echo(f"Event not found: {event_id}")
        raise typer.Exit(code=1)

    invariant_rules: dict[str, object] = {}
    if rules:
        import pathlib
        rules_path = pathlib.Path(rules)
        if not rules_path.exists():
            typer.echo(f"Rules file not found: {rules}")
            raise typer.Exit(code=1)
        try:
            invariant_rules = json.loads(rules_path.read_text(encoding="utf-8"))
        except Exception as exc:
            typer.echo(f"Failed to parse rules file: {exc}")
            raise typer.Exit(code=1)

    async def _run() -> None:
        result = await _evaluate_event(event, base_url, mode, invariant_rules)

        if result.verdict == "FAILED_TO_REPLAY":
            typer.echo("Replay verdict: FAILED_TO_REPLAY")
            typer.echo(f"Replay error: {result.error}")
            raise typer.Exit(code=1)

        status_match = result.replay_status == result.original_status

        typer.echo(f"Replay mode: {mode.value}")
        typer.echo(f"Replay status: {result.replay_status}")
        typer.echo(f"Original status: {result.original_status}")
        typer.echo(f"Body comparison mode: {result.body_compare_mode}")

        if result.schema_drifts:
            typer.echo("\n⚠️ Schema drift detected:")
            for drift in result.schema_drifts[:10]:
                typer.echo(f"- {drift}")

        if event.exception:
            typer.echo("\n🔥 Original execution had an exception")
            typer.echo(f"Type: {event.exception.type}")
            typer.echo(f"Message: {event.exception.message}")

        if result.verdict == "REPRODUCIBLE_STRICT":
            typer.echo("\nReplay verdict: REPRODUCIBLE_STRICT")
            typer.echo("✅ Exact response match: YES")
        elif result.verdict == "REPRODUCIBLE_SEMANTIC":
            typer.echo("\nReplay verdict: REPRODUCIBLE_SEMANTIC")
            typer.echo("✅ Significant behavior reproduced")
            if not result.body_match:
                typer.echo("\nℹ️ Representation drift detected but behavior is considered equivalent")
                typer.echo("\n--- Original body ---")
                typer.echo(result.original_body)
                typer.echo("\n--- Replay body ---")
                typer.echo(result.replay_body)
            if not result.invariant_match and result.violations:
                typer.echo("\n⚠️ Invariant violations:")
                for v in result.violations[:5]:
                    typer.echo(f"- {v}")
        else:
            typer.echo("\nReplay verdict: DRIFT_DETECTED")
            typer.echo("❌ Mismatch detected")
            if not status_match:
                typer.echo(f"Status mismatch: {result.original_status} -> {result.replay_status}")
            if not result.body_match:
                typer.echo("\n--- Original body ---")
                typer.echo(result.original_body)
                typer.echo("\n--- Replay body ---")
                typer.echo(result.replay_body)
            if result.violations:
                typer.echo("\n⚠️ Invariant violations:")
                for v in result.violations[:5]:
                    typer.echo(f"- {v}")

    asyncio.run(_run())


@app.command()
def check_regressions(
    base_url: str = "http://127.0.0.1:8000",
    mode: ReplayMode = ReplayMode.semantic,
    rules: str | None = typer.Option(None, "--rules", help="Path to invariant rules JSON file."),
    route: str | None = typer.Option(None, "--route", help="Filter by request path (substring match)."),
    method: str | None = typer.Option(None, "--method", help="Filter by HTTP method."),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="Stop on first regression."),
) -> None:
    """Replay all recorded events and report regressions."""
    storage = EventStorage()
    events = storage.list_events()

    if route:
        events = [e for e in events if route in e.request.path]
    if method:
        events = [e for e in events if e.request.method.upper() == method.upper()]

    if not events:
        typer.echo("No recorded events found.")
        raise typer.Exit(code=0)

    invariant_rules: dict[str, object] = {}
    if rules:
        import pathlib
        rules_path = pathlib.Path(rules)
        if not rules_path.exists():
            typer.echo(f"Rules file not found: {rules}")
            raise typer.Exit(code=1)
        try:
            invariant_rules = json.loads(rules_path.read_text(encoding="utf-8"))
        except Exception as exc:
            typer.echo(f"Failed to parse rules file: {exc}")
            raise typer.Exit(code=1)

    async def _run_all() -> list[ReplayResult]:
        results: list[ReplayResult] = []
        for event in events:
            result = await _evaluate_event(event, base_url, mode, invariant_rules)
            results.append(result)
            if fail_fast and result.verdict not in ("REPRODUCIBLE_STRICT", "REPRODUCIBLE_SEMANTIC"):
                break
        return results

    typer.echo(f"Replaying {len(events)} event(s) against {base_url} [{mode.value} mode]...\n")
    results = asyncio.run(_run_all())

    passed = [r for r in results if r.verdict in ("REPRODUCIBLE_STRICT", "REPRODUCIBLE_SEMANTIC")]
    regressions = [r for r in results if r.verdict not in ("REPRODUCIBLE_STRICT", "REPRODUCIBLE_SEMANTIC")]

    if regressions:
        s = "s" if len(regressions) > 1 else ""
        typer.echo(f"⚠️  Regression detected ({len(regressions)} event{s}):\n")
        for i, r in enumerate(regressions, 1):
            status_info = (
                str(r.original_status)
                if r.replay_status == r.original_status
                else f"{r.original_status} → {r.replay_status}"
            )
            exception_flag = " 🔥" if r.had_exception else ""
            typer.echo(f"  [{i}] {r.method} {r.path}{exception_flag}  (id: {r.event_id[:8]})")
            typer.echo(f"      Verdict : {r.verdict}")
            typer.echo(f"      Status  : {status_info}")
            if r.error:
                typer.echo(f"      Error   : {r.error}")
            if r.schema_drifts:
                typer.echo("      Schema drift:")
                for d in r.schema_drifts[:5]:
                    typer.echo(f"        - {d}")
            if r.violations:
                typer.echo("      Invariant violations:")
                for v in r.violations[:3]:
                    typer.echo(f"        - {v}")
            if not r.body_match and r.original_body and r.replay_body:
                typer.echo(f"      Expected: {r.original_body[:120]}")
                typer.echo(f"      Got:      {r.replay_body[:120]}")
            typer.echo("")

    typer.echo("─" * 52)
    if not regressions:
        typer.echo(f"✅  All {len(passed)} event(s) reproduced successfully.")
        raise typer.Exit(code=0)
    else:
        typer.echo(f"Results: {len(passed)} passed, {len(regressions)} regression(s)")
        raise typer.Exit(code=1)