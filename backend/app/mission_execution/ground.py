"""Is the aircraft armed, and is it on the ground? One rule for Disarm and for relocation.

Decided from live PX4 telemetry only (GLOBAL_POSITION_INT relative altitude and ground speed, HEARTBEAT armed flag): the telemetry store
does not decode EXTENDED_SYS_STATE (landed state), so altitude and speed are the reliable inputs here. The same numbers the start
pre-check uses for "disarmed and landed". PX4 additionally refuses a normal disarm that is not safe, which is the second line.
"""
from __future__ import annotations

from typing import Any, Optional

ON_GROUND_ALT_M = 1.5
ON_GROUND_SPEED_M_S = 0.7
IN_THE_AIR = "In the air: abort (return to launch) first; it disarms itself after landing."


def ground_state(vehicle: Optional[dict[str, Any]]) -> dict[str, Any]:
    """{known, connected, armed, on_ground} from one telemetry vehicle (None / disconnected = unknown, never guessed)."""
    if not vehicle or vehicle.get("status") != "connected":
        return {"known": False, "connected": False, "armed": None, "on_ground": None}
    armed = vehicle.get("armed")
    relative = (vehicle.get("position") or {}).get("relative_altitude_m")
    speed = (vehicle.get("velocity") or {}).get("ground_speed_m_s")
    if not isinstance(relative, (int, float)):
        return {"known": False, "connected": True, "armed": armed, "on_ground": None}
    on_ground = abs(relative) < ON_GROUND_ALT_M and (not isinstance(speed, (int, float)) or speed < ON_GROUND_SPEED_M_S)
    return {"known": True, "connected": True, "armed": armed, "on_ground": bool(on_ground)}
