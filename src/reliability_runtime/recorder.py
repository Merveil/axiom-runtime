from __future__ import annotations

import time
from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.responses import Response as StarletteResponse
from starlette.responses import StreamingResponse

from .schemas import HttpRequestData, HttpResponseData, RuntimeEvent
from .storage import EventStorage


class HttpRecorder:
    def __init__(self, storage: EventStorage | None = None) -> None:
        self.storage = storage or EventStorage()

    async def record(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.perf_counter()

        request_body_bytes = await request.body()
        request_data = HttpRequestData(
            method=request.method,
            path=request.url.path,
            query_params=dict(request.query_params),
            headers={k: v for k, v in request.headers.items()},
            body_text=request_body_bytes.decode("utf-8", errors="replace") or None,
        )

        async def receive() -> dict:
            return {
                "type": "http.request",
                "body": request_body_bytes,
                "more_body": False,
            }

        replayable_request = Request(request.scope, receive)

        try:
            response = await call_next(replayable_request)
            response_body = await self._collect_response_body(response)
            duration_ms = (time.perf_counter() - started) * 1000.0

            response_data = HttpResponseData(
                status_code=response.status_code,
                headers={k: v for k, v in response.headers.items()},
                body_text=response_body.decode("utf-8", errors="replace") or None,
                duration_ms=duration_ms,
            )

            event = RuntimeEvent(
                event_type="http_request",
                request=request_data,
                response=response_data,
                metadata={
                    "client": request.client.host if request.client else None,
                    "had_exception": False,
                },
            )
            self.storage.append_event(event)

            safe_headers = {
                k: v
                for k, v in response.headers.items()
                if k.lower() != "content-length"
            }

            rebuilt = StarletteResponse(
                content=response_body,
                status_code=response.status_code,
                headers=safe_headers,
                media_type=response.media_type,
            )
            return rebuilt

        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0

            response_data = HttpResponseData(
                status_code=500,
                headers={},
                body_text=f"Internal recorder-captured exception: {type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )

            event = RuntimeEvent(
                event_type="http_request",
                request=request_data,
                response=response_data,
                metadata={
                    "client": request.client.host if request.client else None,
                    "had_exception": True,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                },
            )
            self.storage.append_event(event)
            raise

    async def _collect_response_body(self, response: Response) -> bytes:
        if isinstance(response, StreamingResponse):
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
            return body

        body = getattr(response, "body", None)
        if body is None:
            return b""
        if isinstance(body, bytes):
            return body
        return str(body).encode("utf-8", errors="replace")