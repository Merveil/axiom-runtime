from __future__ import annotations

import json
from typing import Any, cast


REDACTED = "[REDACTED]"

SENSITIVE_HEADER_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
}

SENSITIVE_FIELD_KEYS = {
    "password",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "api_key",
    "card_number",
    "credit_card",
    "cvv",
}


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in SENSITIVE_HEADER_KEYS:
            redacted[key] = REDACTED
        else:
            redacted[key] = value
    return redacted


def redact_query_params(params: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in params.items():
        if key.lower() in SENSITIVE_FIELD_KEYS:
            redacted[key] = REDACTED
        else:
            redacted[key] = value
    return redacted


def redact_json_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        d = cast(dict[str, Any], obj)
        result: dict[str, Any] = {}
        for str_key, v in d.items():
            result[str_key] = REDACTED if str_key.lower() in SENSITIVE_FIELD_KEYS else redact_json_obj(v)
        return result
    if isinstance(obj, list):
        return [redact_json_obj(item) for item in obj]  # type: ignore[misc]
    return obj


def redact_text_body(text: str | None) -> str | None:
    if not text:
        return text

    try:
        parsed = json.loads(text)
        redacted = redact_json_obj(parsed)
        return json.dumps(redacted, separators=(",", ":"))
    except Exception:
        return text
