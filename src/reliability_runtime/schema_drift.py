from __future__ import annotations

from typing import Any, cast


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def detect_schema_drift(
    original: Any,
    replay: Any,
    path: str = "",
) -> list[str]:
    drifts: list[str] = []

    current_path = path or "<root>"

    # missing / added
    if original is None and replay is not None:
        drifts.append(f"{current_path}: field missing in original / added in replay")
        return drifts

    if original is not None and replay is None:
        drifts.append(f"{current_path}: field missing in replay")
        return drifts

    # type mismatch
    original_type = type_name(original)
    replay_type = type_name(replay)

    if original_type != replay_type:
        drifts.append(f"{current_path}: type changed ({original_type} -> {replay_type})")
        return drifts

    # dict recursion
    if isinstance(original, dict) and isinstance(replay, dict):
        da = cast(dict[str, Any], original)
        db = cast(dict[str, Any], replay)

        original_keys = set(da.keys())
        replay_keys = set(db.keys())

        missing_in_replay = original_keys - replay_keys
        added_in_replay = replay_keys - original_keys
        common_keys = original_keys & replay_keys

        for key in sorted(missing_in_replay):
            child_path = f"{path}.{key}" if path else key
            drifts.append(f"{child_path}: field missing in replay")

        for key in sorted(added_in_replay):
            child_path = f"{path}.{key}" if path else key
            drifts.append(f"{child_path}: field added in replay")

        for key in sorted(common_keys):
            child_path = f"{path}.{key}" if path else key
            drifts.extend(detect_schema_drift(da[key], db[key], child_path))

        return drifts

    # list recursion
    if isinstance(original, list) and isinstance(replay, list):
        la = cast(list[Any], original)
        lb = cast(list[Any], replay)

        if len(la) != len(lb):
            drifts.append(f"{current_path}: list length changed ({len(la)} -> {len(lb)})")

        for i, (orig_item, replay_item) in enumerate(zip(la, lb)):
            child_path = f"{path}[{i}]" if path else f"[{i}]"
            drifts.extend(detect_schema_drift(orig_item, replay_item, child_path))

        return drifts

    # scalar same type => no schema drift
    return drifts
