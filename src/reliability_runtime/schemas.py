from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


EventType = Literal["http_request"]


class HttpRequestData(BaseModel):
    method: str
    path: str
    query_params: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body_text: str | None = None


class HttpResponseData(BaseModel):
    status_code: int
    headers: dict[str, str] = Field(default_factory=dict)
    body_text: str | None = None
    duration_ms: float


class ExceptionData(BaseModel):
    type: str
    message: str
    traceback_text: str | None = None


class RuntimeEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: EventType
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    request: HttpRequestData
    response: HttpResponseData
    exception: ExceptionData | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    