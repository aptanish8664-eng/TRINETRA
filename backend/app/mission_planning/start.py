"""The single definition of "start" for a schema-2 mission plan.

* Aircraft take-off point: the aircraft's `start_location`. The route generator always emits it as the first
  (take-off) waypoint, so a stored route's first waypoint is the same point; it is used only as a fallback for a
  plan that has a route but no `start_location`.
* Mission start: the take-off point of the primary aircraft (the first primary in plan order; if no aircraft is
  marked primary, the first aircraft that has a take-off point).

Users of this module: the "Max distance from start" validation (per aircraft, from that aircraft's take-off
point), the simulation home used when PX4 SITL/Gazebo is started or moved, and the Mission Control / Scenario Lab
"aircraft is far from the mission" notices. Software-only: nothing here commands a vehicle.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from .geometry import distance_m
from .schemas import AircraftPlan, GeoPoint, MissionPlan

# An aircraft further than this from the mission start gets the "Move simulation to mission start" notice.
RELOCATION_NOTICE_M = 1_000.0
# An aircraft further than this from the operating area gets the stronger "far from the mission area" message.
FAR_FROM_AREA_M = 50_000.0
# After a restart the reported position must be this close to the requested home to count as applied.
HOME_TOLERANCE_M = 50.0
# The plan stores no terrain elevation. The home altitude (AMSL) is therefore a fixed, documented value: the
# elevation of the stock PX4 `default` Gazebo world, so only latitude and longitude move.
DEFAULT_HOME_ALTITUDE_M = 488.0
HOME_ALTITUDE_RANGE_M = (-500.0, 9000.0)


def takeoff_point(aircraft: AircraftPlan) -> Optional[GeoPoint]:
    """Where one aircraft takes off: its start location (equal to the first generated waypoint)."""
    if aircraft.start_location is not None:
        return aircraft.start_location
    if aircraft.route is not None and aircraft.route.waypoints:
        first = aircraft.route.waypoints[0]
        return GeoPoint(latitude=first.latitude, longitude=first.longitude)
    return None


def mission_start(plan: Optional[MissionPlan]) -> Optional[dict[str, Any]]:
    """Mission start as plain data, or None when no aircraft has a take-off point yet."""
    if plan is None:
        return None
    ordered = [item for item in plan.aircraft if item.role == "primary"] + [item for item in plan.aircraft if item.role != "primary"]
    for aircraft in ordered:
        point = takeoff_point(aircraft)
        if point is not None:
            return {"latitude": point.latitude, "longitude": point.longitude, "altitude_m": DEFAULT_HOME_ALTITUDE_M,
                    "uav_id": aircraft.uav_id, "role": aircraft.role,
                    "definition": "Take-off point of the primary aircraft (its start location, the first route waypoint)."}
    return None


def validate_home(latitude: float, longitude: float, altitude_m: float) -> tuple[float, float, float]:
    """Range-check a simulation home. Only finite numbers inside fixed ranges are ever passed to the WSL manager."""
    values = (latitude, longitude, altitude_m)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        raise ValueError("simulation home must be three finite numbers")
    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        raise ValueError("simulation home latitude/longitude is out of range")
    if not HOME_ALTITUDE_RANGE_M[0] <= altitude_m <= HOME_ALTITUDE_RANGE_M[1]:
        raise ValueError("simulation home altitude is out of range")
    return round(float(latitude), 7), round(float(longitude), 7), round(float(altitude_m), 2)


def distance_between_m(a: dict[str, Any] | GeoPoint, b: dict[str, Any] | GeoPoint) -> float:
    """Great-circle distance, valid at any range (the planner's local ENU distance is only for a few kilometres)."""
    def pair(value) -> tuple[float, float]:
        if isinstance(value, dict):
            return float(value["latitude"]), float(value["longitude"])
        return value.latitude, value.longitude
    (lat1, lon1), (lat2, lon2) = pair(a), pair(b)
    if abs(lat1 - lat2) < 0.05 and abs(lon1 - lon2) < 0.05:
        return distance_m((lat1, lon1), (lat2, lon2))
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(h)))
