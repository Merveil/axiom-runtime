from __future__ import annotations

from fastapi import FastAPI, Request, Response

from .recorder import HttpRecorder


def instrument_fastapi(app: FastAPI) -> FastAPI:
    recorder = HttpRecorder()

    @app.middleware("http")
    async def reliability_runtime_middleware(request: Request, call_next) -> Response:
        return await recorder.record(request, call_next)

    return app