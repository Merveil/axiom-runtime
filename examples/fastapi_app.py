from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from reliability_runtime import instrument_fastapi

app = FastAPI(title="Reliability Runtime Example")
instrument_fastapi(app)


class EchoPayload(BaseModel):
    name: str
    value: int


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/echo")
async def echo(payload: EchoPayload) -> dict[str, object]:
    return {
        "message": f"hello {payload.name}",
        "double": payload.value * 2,
    }


@app.get("/sometimes-fails")
async def sometimes_fails(flag: int = 0) -> dict[str, object]:
    if flag == 1:
        raise ValueError("Simulated failure")
    return {"ok": True, "flag": flag}