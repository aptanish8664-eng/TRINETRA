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
import re
from typing import Any, Optional

from .geometry import distance_m
from .schemas import AircraftPlan, GeoPoint, MissionPlan

# An aircraft further than this from the mission start gets the "Restart at mission start" notice.
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
            home = {"latitude": point.latitude, "longitude": point.longitude, "altitude_m": DEFAULT_HOME_ALTITUDE_M,
                    "uav_id": aircraft.uav_id, "role": aircraft.role,
                    "definition": "Take-off point of the primary aircraft (its start location, the first route waypoint)."}
            spawned = spawn_poses(plan, home)
            home["spawn"], home["spawn_warnings"] = spawned["poses"], spawned["warnings"]
            return home
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


# --- where each simulated aircraft is spawned -------------------------------------------------------------------------------------------
# PX4 SITL has ONE home (the world origin) for the whole simulation: the mission start, i.e. the primary's take-off point. Each aircraft is then placed in Gazebo at an east/north
# offset from that origin. Before this, every aircraft was put on a fixed line (0 m east, 3 m north per instance) next to the primary, whatever start point the plan gave it;
# the primary was exactly at the origin, which is why its Gazebo pose read 0.00, 0.00. Now every aircraft, including standby ones, is spawned at its OWN planned start point.
MIN_START_SEPARATION_M = 3.0        # two aircraft are never spawned closer than this (the X500 is about 0.5 m across; rotor wash and take-off drift need room)
FALLBACK_SPACING_M = 3.0            # an aircraft with no start point and no route is placed on a line north of the primary, this far apart
MAX_SPAWN_OFFSET_M = 1_500_000.0    # the long-range ground plane is +-1,050 km wide


def aircraft_instance(uav_id: str) -> Optional[int]:
    """px4-N is PX4 instance N-1 (MAV_SYS_ID N, Gazebo model x500_{N-1})."""
    match = re.fullmatch(r"px4-([1-9][0-9]{0,2})", uav_id or "")
    return int(match.group(1)) - 1 if match else None


def _lat_lon(point: Any) -> tuple[float, float]:
    """(latitude, longitude) of a GeoPoint or of the plain dict it serialises to."""
    if isinstance(point, dict):
        return float(point["latitude"]), float(point["longitude"])
    return float(point.latitude), float(point.longitude)


def _separate(candidate: tuple[float, float], placed: list[tuple[float, float]]) -> tuple[float, float]:
    """The nearest position (searching outward in rings, 8 directions per ring) that is at least MIN_START_SEPARATION_M from every placed aircraft."""
    def free(point):
        return all(math.hypot(point[0] - other[0], point[1] - other[1]) >= MIN_START_SEPARATION_M - 1e-6 for other in placed)
    if free(candidate):
        return candidate
    for ring in range(1, 40):
        for step in range(8):
            angle = math.pi / 4 * step
            point = (candidate[0] + ring * MIN_START_SEPARATION_M * math.cos(angle), candidate[1] + ring * MIN_START_SEPARATION_M * math.sin(angle))
            if free(point):
                return point
    return (candidate[0] + 40 * MIN_START_SEPARATION_M, candidate[1])


def spawn_poses(plan: Optional[MissionPlan], home: dict[str, Any]) -> dict[str, Any]:
    """Per-aircraft spawn offsets from `home` (east, north in metres) plus plain-language warnings about starts that were too close and were moved apart."""
    from .geometry import to_enu

    if plan is None:
        return {"poses": [], "warnings": []}
    origin = (float(home["latitude"]), float(home["longitude"]))
    ordered = sorted((item for item in plan.aircraft if aircraft_instance(item.uav_id) is not None), key=lambda item: aircraft_instance(item.uav_id))
    placed: list[tuple[float, float]] = []
    poses: list[dict[str, Any]] = []
    warnings: list[str] = []
    fallback_rank = 0
    for aircraft in ordered:
        point = takeoff_point(aircraft)
        if point is not None:
            east, north, _up = to_enu(origin, *_lat_lon(point))
            source = "start_location" if aircraft.start_location is not None else "first_route_waypoint"
        else:
            fallback_rank += 1
            east, north, source = 0.0, FALLBACK_SPACING_M * fallback_rank, "fallback_line"
        east, north = max(-MAX_SPAWN_OFFSET_M, min(MAX_SPAWN_OFFSET_M, east)), max(-MAX_SPAWN_OFFSET_M, min(MAX_SPAWN_OFFSET_M, north))
        wanted = (east, north)
        final = _separate(wanted, placed)
        adjusted = final != wanted
        if adjusted:
            nearest = min((math.hypot(wanted[0] - other[0], wanted[1] - other[1]), index) for index, other in enumerate(placed))
            neighbour = poses[nearest[1]]["uav_id"]
            warnings.append(f"{aircraft.uav_id} starts only {nearest[0]:.1f} m from {neighbour}; in the simulation it is placed {math.hypot(final[0] - wanted[0], final[1] - wanted[1]):.1f} m away "
                            f"so the aircraft are at least {MIN_START_SEPARATION_M:g} m apart.")
        placed.append(final)
        poses.append({"instance": aircraft_instance(aircraft.uav_id), "uav_id": aircraft.uav_id, "east_m": round(final[0], 2), "north_m": round(final[1], 2), "source": source,
                      "adjusted": adjusted, "wanted_east_m": round(wanted[0], 2), "wanted_north_m": round(wanted[1], 2)})
    return {"poses": poses, "warnings": warnings}


def start_separation_warnings(plan: MissionPlan) -> dict[str, str]:
    """uav_id -> warning, for every aircraft whose planned start is closer than the minimum to an earlier aircraft's start (shown in the plan before anything runs)."""
    found: dict[str, str] = {}
    points = [(item, takeoff_point(item)) for item in plan.aircraft]
    for index, (aircraft, point) in enumerate(points):
        if point is None:
            continue
        for other, other_point in points[:index]:
            if other_point is None:
                continue
            gap = distance_m(_lat_lon(point), _lat_lon(other_point))
            if gap < MIN_START_SEPARATION_M:
                found[aircraft.uav_id] = (f"The start is {gap:.1f} m from {other.uav_id}'s start. Aircraft must start at least {MIN_START_SEPARATION_M:g} m apart, so in the simulation "
                                          f"{aircraft.uav_id} is placed {MIN_START_SEPARATION_M:g} m or more away from it. Move the start point if you want a specific position.")
                break
    return found
