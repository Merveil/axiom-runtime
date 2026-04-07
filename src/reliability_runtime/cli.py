from __future__ import annotations

import asyncio
import json

import typer

from .replayer import EventReplayer
from .storage import EventStorage

app = typer.Typer(help="Reliability Runtime CLI")


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
    """Show a single recorded event as JSON."""
    storage = EventStorage()
    event = storage.get_event(event_id)
    if not event:
        typer.echo(f"Event not found: {event_id}")
        raise typer.Exit(code=1)

    typer.echo(json.dumps(event.model_dump(mode="json"), indent=2))


@app.command()
def clear() -> None:
    """Delete all recorded events."""
    storage = EventStorage()
    storage.clear()
    typer.echo("All events cleared.")


@app.command()
def replay(event_id: str, base_url: str = "http://127.0.0.1:8000") -> None:
    """Replay a recorded HTTP event against a running app."""
    storage = EventStorage()
    event = storage.get_event(event_id)
    if not event:
        typer.echo(f"Event not found: {event_id}")
        raise typer.Exit(code=1)

    async def _run() -> None:
        replayer = EventReplayer(base_url=base_url)
        response = await replayer.replay_http_event(event)
        typer.echo(f"Replay status: {response.status_code}")
        typer.echo(response.text)

    asyncio.run(_run())