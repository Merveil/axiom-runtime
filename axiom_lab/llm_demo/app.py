"""
Axiom Lab — LLM Demo

Simulates an LLM-style completion endpoint with four behavioral drift modes
that represent common real-world LLM API regressions:

  VARIABLE_CONTENT  — same prompt → different wording each call
  MISSING_FIELD     — 'choices' absent; partial/error response returned
  SCHEMA_CHANGE     — 'text' field renamed to 'content' inside choices
  INCOHERENT        — all fields present but empty / zero values

Stable mode always returns the canonical structured response so Axiom
can capture a reliable golden session.

Factory:
    create_llm_demo_app(drift_mode=False)                → stable
    create_llm_demo_app(drift_mode=True, rng=rng)        → random drift mode
    create_llm_demo_app(force_mode="incoherent")         → fixed drift mode
"""
from __future__ import annotations

import random
import uuid
from typing import Any

from fastapi import FastAPI

_STABLE_TEXT = "The answer is forty-two."

_VARIABLE_TEXTS = [
    "The answer is forty-two.",
    "Forty-two is the answer.",
    "42 — the answer to life, the universe, and everything.",
    "The response is: forty-two (42).",
]

_STABLE_USAGE = {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}

_FORCE_MODES = frozenset({"variable", "missing", "schema", "incoherent"})


def _cmpl_id() -> str:
    return f"cmpl-{uuid.uuid4().hex[:8]}"


def create_llm_demo_app(
    *,
    drift_mode: bool = False,
    rng: random.Random | None = None,
    force_mode: str | None = None,
) -> FastAPI:
    """
    Args:
        drift_mode:  Enable random drift (roll determines mode).
        rng:         Seeded RNG for reproducible drift.
        force_mode:  Override roll with a fixed mode: one of (variable, missing,
                     schema, incoherent).  Takes precedence over drift_mode.
    """
    if force_mode is not None and force_mode not in _FORCE_MODES:
        raise ValueError(f"force_mode must be one of {sorted(_FORCE_MODES)} or None")

    app  = FastAPI(title="Axiom Lab — LLM Demo")
    _rng = rng or random.Random(0)

    @app.post("/v1/completions")
    def completions(body: dict[str, Any] = {}):
        # Resolve which mode to use
        if force_mode is not None:
            _mode = force_mode
        elif not drift_mode:
            _mode = "stable"
        else:
            roll = _rng.random()
            if roll < 0.35:    _mode = "variable"
            elif roll < 0.55:  _mode = "missing"
            elif roll < 0.75:  _mode = "schema"
            else:              _mode = "incoherent"

        if _mode == "stable":
            return {
                "id":      _cmpl_id(),
                "object":  "text_completion",
                "choices": [{"text": _STABLE_TEXT, "index": 0, "finish_reason": "stop"}],
                "usage":   _STABLE_USAGE,
            }

        elif _mode == "variable":
            # VARIABLE_CONTENT — different wording
            return {
                "id":      _cmpl_id(),
                "object":  "text_completion",
                "choices": [{"text": _rng.choice(_VARIABLE_TEXTS), "index": 0,
                             "finish_reason": "stop"}],
                "usage":   _STABLE_USAGE,
            }

        elif _mode == "missing":
            # MISSING_FIELD — 'choices' absent
            return {
                "id":     _cmpl_id(),
                "object": "text_completion",
                "error":  "upstream model timeout — partial response",
            }

        elif _mode == "schema":
            # SCHEMA_CHANGE — 'text' renamed to 'content'
            return {
                "id":      _cmpl_id(),
                "object":  "text_completion",
                "choices": [{"content": _STABLE_TEXT, "index": 0}],
            }

        else:
            # INCOHERENT — all correct fields but empty/zeroed values
            return {
                "id":      _cmpl_id(),
                "object":  "text_completion",
                "choices": [{"text": "", "index": 0, "finish_reason": ""}],
                "usage":   {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

    @app.get("/v1/models")
    def models():
        return {"data": [{"id": "lab-model-v1", "object": "model"}]}

    return app
