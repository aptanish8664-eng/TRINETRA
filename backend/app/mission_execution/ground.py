"""Is the aircraft armed, and is it on the ground? One rule for Land here, the armed-on-ground notice, relocation and the Threat Monitor.

Decided from PX4's EXTENDED_SYS_STATE landed state when it is fresh (received at 1-2 Hz; the executor asks for 2 Hz on the onboard link).
Relative altitude is unreliable far from home in this simulation (a flat Gazebo world versus curved GPS altitude, about 70 m off at 30 km), so
altitude and ground speed are only a FALLBACK when the landed state is missing or stale, and the result says so (`source`).
"""
from __future__ import annotations

from typing import Any, Optional

ON_GROUND_ALT_M = 1.5
ON_GROUND_SPEED_M_S = 0.7
LANDED_STATE_FRESH_S = 5.0
IN_THE_AIR = "In the air: use Land here (lands at the current position and disarms) or Abort (return to launch)."


def ground_state(vehicle: Optional[dict[str, Any]]) -> dict[str, Any]:
    """{known, connected, armed, on_ground, landed_state, source} from one telemetry vehicle (None / disconnected = unknown, never guessed)."""
    if not vehicle or vehicle.get("status") != "connected":
        return {"known": False, "connected": False, "armed": None, "on_ground": None, "landed_state": None, "source": None}
    armed = vehicle.get("armed")
    extended = vehicle.get("landed_state") or {}
    age = extended.get("age_s")
    if isinstance(extended.get("value"), int) and isinstance(age, (int, float)) and age <= LANDED_STATE_FRESH_S and extended["value"] != 0:
        return {"known": True, "connected": True, "armed": armed, "on_ground": extended["value"] == 1, "landed_state": extended.get("name"), "source": "px4_landed_state"}
    relative = (vehicle.get("position") or {}).get("relative_altitude_m")
    speed = (vehicle.get("velocity") or {}).get("ground_speed_m_s")
    if not isinstance(relative, (int, float)):
        return {"known": False, "connected": True, "armed": armed, "on_ground": None, "landed_state": None, "source": None}
    on_ground = abs(relative) < ON_GROUND_ALT_M and (not isinstance(speed, (int, float)) or speed < ON_GROUND_SPEED_M_S)
    return {"known": True, "connected": True, "armed": armed, "on_ground": bool(on_ground), "landed_state": None, "source": "altitude_speed_fallback"}
