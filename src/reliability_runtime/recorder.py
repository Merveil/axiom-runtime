from __future__ import annotations

import time
import traceback
from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.responses import Response as StarletteResponse

from .redaction import redact_headers, redact_query_params, redact_text_body
from .schemas import ExceptionData, HttpRequestData, HttpResponseData, RuntimeEvent
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
            query_params=redact_query_params(dict(request.query_params)),
            headers=redact_headers({k: v for k, v in request.headers.items()}),
            body_text=redact_text_body(
                request_body_bytes.decode("utf-8", errors="replace") or None
            ),
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

            event = RuntimeEvent(
                event_type="http_request",
                request=request_data,
                response=HttpResponseData(
                    status_code=response.status_code,
                    headers={k: v for k, v in response.headers.items()},
                    body_text=redact_text_body(
                        response_body.decode("utf-8", errors="replace") or None
                    ),
                    duration_ms=duration_ms,
                ),
                metadata={
                    "client": request.client.host if request.client else None,
                    "had_exception": False,
                },
            )
            self.storage.append_event(event)

            safe_headers = {
                k: v
                for k, v in response.headers.items()
                if k.lower() not in {"content-length", "transfer-encoding"}
            }

            return StarletteResponse(
                content=response_body,
                status_code=response.status_code,
                headers=safe_headers,
                media_type=response.media_type,
            )

        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            tb_text = traceback.format_exc()

            event = RuntimeEvent(
                event_type="http_request",
                request=request_data,
                response=HttpResponseData(
                    status_code=500,
                    headers={},
                    body_text=redact_text_body(f"{type(exc).__name__}: {exc}"),
                    duration_ms=duration_ms,
                ),
                exception=ExceptionData(
                    type=type(exc).__name__,
                    message=str(exc),
                    traceback_text=tb_text,
                ),
                metadata={
                    "client": request.client.host if request.client else None,
                    "had_exception": True,
                },
            )
            self.storage.append_event(event)
            raise

    async def _collect_response_body(self, response: Response) -> bytes:
        body = b""

        body_iterator = getattr(response, "body_iterator", None)
        if body_iterator is not None:
            async for chunk in body_iterator:
                if isinstance(chunk, bytes):
                    body += chunk
                else:
                    body += str(chunk).encode("utf-8", errors="replace")
            return body

        raw_body = getattr(response, "body", None)
        if raw_body is None:
            return b""

        if isinstance(raw_body, bytes):
            return raw_body

        return str(raw_body).encode("utf-8", errors="replace")
