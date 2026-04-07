from __future__ import annotations

import httpx

from .schemas import RuntimeEvent


class EventReplayer:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def replay_http_event(self, event: RuntimeEvent) -> httpx.Response:
        if event.event_type != "http_request":
            raise ValueError(f"Unsupported event type: {event.event_type}")

        url = f"{self.base_url}{event.request.path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=event.request.method,
                url=url,
                params=event.request.query_params,
                headers=event.request.headers,
                content=(event.request.body_text or "").encode("utf-8"),
            )
        return response