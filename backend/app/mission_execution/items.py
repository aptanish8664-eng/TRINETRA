"""Turn one aircraft's stored, generated route into the fixed mission items the WSL executor uploads.

Mapping (frame MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, altitudes relative to home):
  takeoff               -> MAV_CMD_NAV_TAKEOFF (first item; PX4 requires take-off to be the first waypoint item)
  waypoint / hover      -> MAV_CMD_NAV_WAYPOINT with the hold time from `hover_s`
  payload_release_point -> MAV_CMD_NAV_WAYPOINT with the hold time, flagged `simulated_event: payload_released`.
                           It is a logged software event when the aircraft reaches the item. No actuator, servo or
                           release command is ever generated.
  land                  -> MAV_CMD_NAV_LAND
  speed                 -> MAV_CMD_DO_CHANGE_SPEED (ground speed) after the take-off and whenever the route speed changes

Only numbers leave this module; the executor range-checks every one of them again before sending anything.
"""
from __future__ import annotations

import math
from typing import Any

from ..mission_planning.schemas import AircraftPlan
from ..telemetry.fleet_manager import FLEET_CAPACITY

MAV_CMD_NAV_WAYPOINT = 16
MAV_CMD_NAV_LAND = 21
MAV_CMD_NAV_TAKEOFF = 22
MAV_CMD_DO_CHANGE_SPEED = 178
MAX_ITEMS = 500


class MissionItemsError(ValueError):
    """The stored route cannot be turned into a flyable SITL mission."""


def instance_for_aircraft(uav_id: str) -> int:
    """px4-N is PX4 SITL instance N-1 (MAVLink system id N, Gazebo model x500_(N-1))."""
    if not uav_id.startswith("px4-") or not uav_id[4:].isdigit() or not 1 <= int(uav_id[4:]) <= FLEET_CAPACITY:
        raise MissionItemsError(f"{uav_id} is not a managed SITL aircraft identity")
    return int(uav_id[4:]) - 1


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MissionItemsError(f"{name} is not a finite number")
    return float(value)


def build_mission(aircraft: AircraftPlan, execution_id: str, *, in_air: bool = False) -> dict[str, Any]:
    """The payload for `wsl_mission_executor.py start` (validated again there)."""
    route = aircraft.route
    if route is None or len(route.waypoints) < 2:
        raise MissionItemsError("the aircraft has no generated route")
    waypoints = list(route.waypoints)
    if waypoints[0].action != "takeoff":
        raise MissionItemsError("the route does not start with a take-off")
    if waypoints[-1].action != "land":
        raise MissionItemsError("the route does not end with a landing")
    instance = instance_for_aircraft(aircraft.uav_id)
    items: list[dict[str, Any]] = []
    speed: float | None = None
    for index, waypoint in enumerate(waypoints):
        point = {"latitude": round(_finite(waypoint.latitude, "latitude"), 7), "longitude": round(_finite(waypoint.longitude, "longitude"), 7),
                 "altitude_m": round(_finite(waypoint.altitude_m, "altitude"), 2), "route_seq": int(waypoint.seq)}
        wanted_speed = round(_finite(waypoint.speed_mps, "speed"), 2)
        if index > 0 and wanted_speed != speed and waypoint.action != "land":
            # A take-off must be the first waypoint item, so the first speed change follows it.
            items.append({"command": MAV_CMD_DO_CHANGE_SPEED, "speed_mps": wanted_speed})
            speed = wanted_speed
        if waypoint.action == "takeoff":
            if index != 0:
                raise MissionItemsError("a take-off is only allowed as the first waypoint")
            items.append({"command": MAV_CMD_NAV_TAKEOFF, **point})
        elif waypoint.action == "land":
            if index != len(waypoints) - 1:
                raise MissionItemsError("a landing is only allowed as the last waypoint")
            items.append({"command": MAV_CMD_NAV_LAND, **{**point, "altitude_m": 0.0}})
        elif waypoint.action in ("waypoint", "hover", "payload_release_point"):
            item = {"command": MAV_CMD_NAV_WAYPOINT, **point, "hold_s": round(_finite(waypoint.hover_s, "hover"), 1)}
            if waypoint.action == "payload_release_point":
                item["simulated_event"] = "payload_released"
            items.append(item)
        else:
            raise MissionItemsError(f"route action {waypoint.action!r} cannot be flown")
    if len(items) > MAX_ITEMS:
        raise MissionItemsError(f"the route needs {len(items)} mission items; the limit is {MAX_ITEMS}")
    mission = {"execution_id": execution_id, "instance": instance, "system_id": instance + 1, "items": items, "route_total": len(waypoints)}
    if in_air:
        mission["in_air"] = True        # the executor may begin on an armed, flying aircraft (hand-over, resume from the air)
    return mission
