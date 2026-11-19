"""Cheap independent copies of telemetry snapshots.

`copy.deepcopy` of every aircraft's data on every API call was about a third of the backend's CPU time (measured with py-spy during a 3-aircraft start with
Mission Control open). Telemetry values are plain numbers, strings, booleans, None, small dicts and lists, so a direct recursive copy is enough and several times faster.
"""
from __future__ import annotations

from typing import Any


def copy_value(value: Any) -> Any:
    """Independent copy of nested dicts and lists; every other value (numbers, strings, None, booleans) is immutable and shared."""
    if isinstance(value, dict):
        return {key: copy_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [copy_value(item) for item in value]
    return value


def copy_trail(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A flight trail is append-only (points are never changed after they are added), so a shallow copy of the list is an independent snapshot."""
    return list(points)
