from __future__ import annotations

from typing import Any


def current_planning_aircraft() -> list[dict[str, Any]] | None:
    """Return aircraft with current, valid PX4 positions, or None if telemetry is disabled."""
    from ..main import px4_telemetry_store, px4_udp_transport

    if px4_udp_transport is None:
        return None
    snapshot = px4_telemetry_store.snapshot()
    return [
        vehicle for vehicle in snapshot["vehicles"]
        if vehicle["status"] == "connected"
        and vehicle["position"] is not None
        and vehicle["position"]["valid"] is True
    ]
