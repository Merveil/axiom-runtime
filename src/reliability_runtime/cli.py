from __future__ import annotations

import asyncio
import json
from enum import Enum

import typer

from .replayer import EventReplayer
from .schema_drift import detect_schema_drift
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


def load_rules(rules_path: str) -> dict[str, object]:
    with open(rules_path, encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def classify_replay_result(
    event: object,
    replay_status: int,
    replay_body: str,
    mode: ReplayMode,
    rules: dict[str, object] | None = None,
) -> dict[str, object]:
    from .schemas import RuntimeEvent
    assert isinstance(event, RuntimeEvent)

    original_status = event.response.status_code
    original_body = event.response.body_text or ""
    original_had_exception = event.exception is not None
    replay_looks_like_error = replay_status >= 500

    status_match = replay_status == original_status
    body_match, body_compare_mode = compare_response_bodies(original_body, replay_body)

    invariant_match = True
    violations: list[str] = []
    schema_drifts: list[str] = []

    if body_compare_mode == "json":
        try:
            from reliability_runtime.invariants import compare_json_with_invariants

            original_json = json.loads(original_body)
            replay_json = json.loads(replay_body)
            invariant_match, violations = compare_json_with_invariants(
                original_json, replay_json, rules=rules or {},
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

    return {
        "verdict": verdict,
        "original_status": original_status,
        "replay_status": replay_status,
        "original_body": original_body,
        "replay_body": replay_body,
        "status_match": status_match,
        "body_match": body_match,
        "body_compare_mode": body_compare_mode,
        "invariant_match": invariant_match,
        "violations": violations,
        "schema_drifts": schema_drifts,
        "original_had_exception": original_had_exception,
    }


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
    rules_path: str | None = typer.Option(None, "--rules", help="Path to a JSON file with invariant rules."),
) -> None:
    """Replay a recorded HTTP event and classify reproducibility."""
    storage = EventStorage()
    event = storage.get_event(event_id)

    if not event:
        typer.echo(f"Event not found: {event_id}")
        raise typer.Exit(code=1)

    rules = load_rules(rules_path) if rules_path else {}

    async def _run() -> None:
        replayer = EventReplayer(base_url=base_url)
        try:
            response = await replayer.replay_http_event(event)
        except Exception as exc:
            typer.echo("Replay verdict: FAILED_TO_REPLAY")
            typer.echo(f"Replay error: {type(exc).__name__}: {exc}")
            raise typer.Exit(code=1)

        result = classify_replay_result(
            event=event,
            replay_status=response.status_code,
            replay_body=response.text or "",
            mode=mode,
            rules=rules,
        )

        typer.echo(f"Replay mode: {mode.value}")
        typer.echo(f"Replay status: {result['replay_status']}")
        typer.echo(f"Original status: {result['original_status']}")
        typer.echo(f"Body comparison mode: {result['body_compare_mode']}")

        if event.exception:
            typer.echo("\n🔥 Original execution had an exception")
            typer.echo(f"Type: {event.exception.type}")
            typer.echo(f"Message: {event.exception.message}")

        schema_drifts: list[str] = result["schema_drifts"]  # type: ignore[assignment]
        violations: list[str] = result["violations"]  # type: ignore[assignment]

        if schema_drifts:
            typer.echo("\n⚠️ Schema drift detected:")
            for drift in schema_drifts[:10]:
                typer.echo(f"- {drift}")

        if not result["invariant_match"] and violations:
            typer.echo("\n⚠️ Invariant violations:")
            for v in violations[:10]:
                typer.echo(f"- {v}")

        typer.echo(f"\nReplay verdict: {result['verdict']}")

        if result["verdict"] == "REPRODUCIBLE_STRICT":
            typer.echo("✅ Exact response match: YES")
        elif result["verdict"] == "REPRODUCIBLE_SEMANTIC":
            typer.echo("✅ Significant behavior reproduced")
        else:
            typer.echo("❌ Mismatch detected")
            if not result["status_match"]:
                typer.echo(f"Status mismatch: {result['original_status']} -> {result['replay_status']}")
            if not result["body_match"]:
                typer.echo("\n--- Original body ---")
                typer.echo(result["original_body"])
                typer.echo("\n--- Replay body ---")
                typer.echo(result["replay_body"])

    asyncio.run(_run())


@app.command("check-regressions")
def check_regressions(
    base_url: str = "http://127.0.0.1:8000",
    mode: ReplayMode = ReplayMode.semantic,
    rules_path: str | None = typer.Option(None, "--rules", help="Path to invariant rules JSON file."),
    route: str | None = typer.Option(None, "--route", help="Filter by request path (substring match)."),
    method: str | None = typer.Option(None, "--method", help="Filter by HTTP method."),
    status: int | None = typer.Option(None, "--status", help="Filter by response status code."),
    limit: int = typer.Option(20, "--limit", help="Maximum number of events to check."),
) -> None:
    """Replay recorded events and summarize regressions."""
    storage = EventStorage()
    events = storage.list_events()

    if route:
        events = [e for e in events if route in e.request.path]
    if method:
        events = [e for e in events if e.request.method.upper() == method.upper()]
    if status is not None:
        events = [e for e in events if e.response.status_code == status]
    if limit > 0:
        events = events[-limit:]

    if not events:
        typer.echo("No matching events found.")
        raise typer.Exit(code=0)

    rules = load_rules(rules_path) if rules_path else {}

    summary = {
        "REPRODUCIBLE_STRICT": 0,
        "REPRODUCIBLE_SEMANTIC": 0,
        "DRIFT_DETECTED": 0,
        "FAILED_TO_REPLAY": 0,
    }
    detailed: list[tuple[object, dict[str, object]]] = []

    async def _run_all() -> None:
        replayer = EventReplayer(base_url=base_url)
        for event in events:
            try:
                response = await replayer.replay_http_event(event)
                result = classify_replay_result(
                    event=event,
                    replay_status=response.status_code,
                    replay_body=response.text or "",
                    mode=mode,
                    rules=rules,
                )
            except Exception as exc:
                result = {
                    "verdict": "FAILED_TO_REPLAY",
                    "error": f"{type(exc).__name__}: {exc}",
                    "schema_drifts": [],
                    "violations": [],
                    "status_match": False,
                    "body_match": False,
                }
            verdict: str = result["verdict"]  # type: ignore[assignment]
            summary[verdict] = summary.get(verdict, 0) + 1
            detailed.append((event, result))

    asyncio.run(_run_all())

    typer.echo(f"Checked: {len(events)} events")
    typer.echo(f"Reproducible strict:   {summary['REPRODUCIBLE_STRICT']}")
    typer.echo(f"Reproducible semantic: {summary['REPRODUCIBLE_SEMANTIC']}")
    typer.echo(f"Drift detected:        {summary['DRIFT_DETECTED']}")
    typer.echo(f"Failed to replay:      {summary['FAILED_TO_REPLAY']}")

    interesting = [
        (ev, res)
        for ev, res in detailed
        if res["verdict"] != "REPRODUCIBLE_STRICT"
    ]

    if interesting:
        typer.echo("\nInteresting results:")
        for ev, res in interesting[:20]:
            from .schemas import RuntimeEvent
            assert isinstance(ev, RuntimeEvent)
            exception_flag = " 🔥" if ev.exception else ""
            typer.echo(
                f"- {ev.event_id} | {ev.request.method} {ev.request.path}{exception_flag} "
                f"| verdict={res['verdict']}"
            )
            if res["verdict"] == "FAILED_TO_REPLAY":
                typer.echo(f"  error: {res['error']}")
                continue
            schema_drifts: list[str] = res["schema_drifts"]  # type: ignore[assignment]
            violations: list[str] = res["violations"]  # type: ignore[assignment]
            for drift in schema_drifts[:3]:
                typer.echo(f"  schema: {drift}")
            for violation in violations[:3]:
                typer.echo(f"  invariant: {violation}")
            if not res["body_match"] and res.get("original_body") and res.get("replay_body"):
                typer.echo(f"  Expected: {str(res['original_body'])[:100]}")
                typer.echo(f"  Got:      {str(res['replay_body'])[:100]}")

    regressions = summary["DRIFT_DETECTED"] + summary["FAILED_TO_REPLAY"]
    raise typer.Exit(code=1 if regressions else 0)