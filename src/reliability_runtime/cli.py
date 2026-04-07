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


@app.command()
def list_events() -> None:
    """List recorded events."""
    storage = EventStorage()
    events = storage.list_events()
    if not events:
        typer.echo("No recorded events found.")
        raise typer.Exit(code=0)

    for event in events:
        typer.echo(
            f"{event.event_id} | {event.request.method} {event.request.path} | "
            f"status={event.response.status_code} | {event.response.duration_ms:.2f}ms"
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
        replayer = EventReplayer(base_url=base_url)

        try:
            response = await replayer.replay_http_event(event)
        except Exception as exc:
            typer.echo("Replay verdict: FAILED_TO_REPLAY")
            typer.echo(f"Replay error: {type(exc).__name__}: {exc}")
            raise typer.Exit(code=1)

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
                    original_json,
                    replay_json,
                    rules=invariant_rules,
                )

                schema_drifts = detect_schema_drift(original_json, replay_json)
            except Exception:
                pass

        typer.echo(f"Replay mode: {mode.value}")
        typer.echo(f"Replay status: {replay_status}")
        typer.echo(f"Original status: {original_status}")
        typer.echo(f"Body comparison mode: {body_compare_mode}")

        if schema_drifts:
            typer.echo("\n⚠️ Schema drift detected:")
            for drift in schema_drifts[:10]:
                typer.echo(f"- {drift}")

        if event.exception:
            typer.echo("\n🔥 Original execution had an exception")
            typer.echo(f"Type: {event.exception.type}")
            typer.echo(f"Message: {event.exception.message}")

        if mode == ReplayMode.strict:
            if status_match and body_match:
                typer.echo("\nReplay verdict: REPRODUCIBLE_STRICT")
                typer.echo("✅ Exact response match: YES")
            else:
                typer.echo("\nReplay verdict: DRIFT_DETECTED")
                typer.echo("❌ Strict mismatch detected")

                if not status_match:
                    typer.echo(f"Status mismatch: {original_status} -> {replay_status}")

                if not body_match:
                    typer.echo("\n--- Original body ---")
                    typer.echo(original_body)

                    typer.echo("\n--- Replay body ---")
                    typer.echo(replay_body)

            return

        # semantic mode
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
            if status_match and body_match:
                typer.echo("\nReplay verdict: REPRODUCIBLE_STRICT")
                typer.echo("✅ Exact response match: YES")
            else:
                typer.echo("\nReplay verdict: REPRODUCIBLE_SEMANTIC")
                typer.echo("✅ Significant behavior reproduced")

                if not body_match:
                    typer.echo("\nℹ️ Representation drift detected but behavior is considered equivalent")
                    typer.echo("\n--- Original body ---")
                    typer.echo(original_body)
                    typer.echo("\n--- Replay body ---")
                    typer.echo(replay_body)

                if not invariant_match and violations:
                    typer.echo("\n⚠️ Invariant violations:")
                    for v in violations[:5]:
                        typer.echo(f"- {v}")
        else:
            typer.echo("\nReplay verdict: DRIFT_DETECTED")
            typer.echo("❌ Semantic mismatch detected")

            if not status_match:
                typer.echo(f"Status mismatch: {original_status} -> {replay_status}")

            typer.echo("\n--- Original body ---")
            typer.echo(original_body)
            typer.echo("\n--- Replay body ---")
            typer.echo(replay_body)

            if violations:
                typer.echo("\n⚠️ Invariant violations:")
                for v in violations[:5]:
                    typer.echo(f"- {v}")

    asyncio.run(_run())