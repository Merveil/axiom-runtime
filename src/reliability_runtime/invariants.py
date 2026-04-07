from __future__ import annotations

from typing import Any, cast


def compare_json_with_invariants(
    original: Any,
    replay: Any,
    rules: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """
    Compare two JSON objects with simple invariants.

    Rules are keyed by field name (last path segment). Supported per-field options:
      - ignore: bool  — skip this field entirely
      - tolerance: float  — acceptable numeric delta

    Returns:
        (match, violations)
    """
    rules = rules or {}
    violations: list[str] = []

    def _compare(path: str, a: Any, b: Any) -> None:
        key = path.split(".")[-1] if path else ""

        rule: dict[str, Any] = rules.get(key, {})

        # Ignore field
        if rule.get("ignore"):
            return

        # Tolerance (numeric)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            tol = rule.get("tolerance")
            if tol is not None:
                if abs(a - b) > tol:
                    violations.append(f"{path}: {a} vs {b} > tol {tol}")
                return

        # Recursive dict
        if isinstance(a, dict) and isinstance(b, dict):
            da, db = cast(dict[str, Any], a), cast(dict[str, Any], b)
            keys = set(da.keys()) | set(db.keys())
            for k in keys:
                _compare(f"{path}.{k}" if path else k, da.get(k), db.get(k))
            return

        # List (element-wise)
        if isinstance(a, list) and isinstance(b, list):
            la, lb = cast(list[Any], a), cast(list[Any], b)
            for i, (x, y) in enumerate(zip(la, lb)):
                _compare(f"{path}[{i}]", x, y)
            if len(la) != len(lb):
                violations.append(f"{path}: list length {len(la)} vs {len(lb)}")
            return

        # Default strict
        if a != b:
            violations.append(f"{path}: {a!r} != {b!r}")

    _compare("", original, replay)

    return len(violations) == 0, violations
