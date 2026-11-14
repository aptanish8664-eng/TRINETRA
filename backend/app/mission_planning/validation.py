"""Authoritative validation of a schema-2 mission plan. Returns structured issues the UI and assistant can show."""
from __future__ import annotations

import math
from typing import Optional

from .generators import PlanningLimits, RouteError, area_polygon_enu, generate_route
from .geometry import distance_m, point_in_polygon, to_enu
from .schemas import AircraftPlan, GeneratedRoute, GeoPoint, MissionPlan, OperatingArea, StrictModel
from .start import takeoff_point

# Parameters that must be present (non-empty) per task type; everything else has a safe default.
REQUIRED_PARAMETERS: dict[str, tuple[str, ...]] = {
    "expanding_square_search": ("datum", "track_spacing_m", "search_radius_m"),
    "sector_search": ("datum", "sector_radius_m"),
    "coverage_survey": ("line_spacing_m",),
    "perimeter_patrol": (),
    "point_inspection": ("points",),
    "waypoint_route": ("points",),
    "loiter_hold": (),
    "payload_dropoff": ("drop_point", "drop_altitude_m", "hover_duration_s", "payload_category"),
}
AREA_BASED_TASKS = {"coverage_survey"}
TRACKING_ONLY_OBJECTIVES = {"target_tracking", "custom_mission"}
DELIVERY_OBJECTIVES = {"relief_supply_delivery", "search_rescue", "disaster_assessment"}


class PlanIssue(StrictModel):
    code: str
    message: str
    uav_id: Optional[str] = None
    field: Optional[str] = None


def _present(value) -> bool:
    return value is not None and value != [] and value != ""


def _area_centre_and_extent(area: OperatingArea) -> tuple[tuple[float, float], list[tuple[float, float]]]:
    if area.kind == "circle":
        assert area.centre is not None
        return (area.centre.latitude, area.centre.longitude), []
    vertices = [(v.latitude, v.longitude) for v in area.vertices]
    return vertices[0], vertices


def point_in_area(area: OperatingArea, point: GeoPoint) -> bool:
    if area.kind == "circle":
        assert area.centre is not None and area.radius_m is not None
        return distance_m((area.centre.latitude, area.centre.longitude), (point.latitude, point.longitude)) <= area.radius_m
    origin = (area.vertices[0].latitude, area.vertices[0].longitude)
    polygon = [to_enu(origin, v.latitude, v.longitude)[:2] for v in area.vertices]
    return point_in_polygon(to_enu(origin, point.latitude, point.longitude)[:2], polygon)


def start_to_area_distance_m(area: OperatingArea, start: GeoPoint) -> float:
    s = (start.latitude, start.longitude)
    if area.kind == "circle":
        assert area.centre is not None and area.radius_m is not None
        return max(0.0, distance_m((area.centre.latitude, area.centre.longitude), s) - area.radius_m)
    if point_in_area(area, start):
        return 0.0
    return min(distance_m(s, (v.latitude, v.longitude)) for v in area.vertices)


def effective_max_altitude(plan: MissionPlan, limits: PlanningLimits) -> float:
    cap = plan.constraints.max_altitude_m
    return min(limits.max_altitude_m, cap) if cap is not None else limits.max_altitude_m


def reference_points(aircraft: AircraftPlan) -> list[tuple[str, GeoPoint]]:
    p = aircraft.parameters
    points: list[tuple[str, GeoPoint]] = []
    for name in ("datum", "hold_point", "drop_point"):
        value = getattr(p, name)
        if value is not None:
            points.append((name, value))
    points += [("points", item) for item in p.points] + [("path", item) for item in p.path]
    return points


def validate_aircraft(aircraft: AircraftPlan, plan: MissionPlan, limits: PlanningLimits, *,
                      require_route: bool) -> tuple[list[PlanIssue], Optional[GeneratedRoute]]:
    issues: list[PlanIssue] = []
    uav = aircraft.uav_id

    def add(code: str, message: str, field: str | None = None) -> None:
        issues.append(PlanIssue(code=code, message=message, uav_id=uav, field=field))

    if aircraft.role is None:
        add("missing_field", "Choose a role (primary, decoy or standby).", "role")
    if aircraft.task_type is None:
        add("missing_field", "Choose a task type.", "task_type")
    if aircraft.start_location is None:
        add("missing_field", "Select the aircraft start location on the map.", "start_location")
    if aircraft.altitude_m is None:
        add("missing_field", "Set the cruise altitude (m).", "altitude_m")
    if aircraft.speed_mps is None:
        add("missing_field", "Set the speed (m/s).", "speed_mps")
    if plan.operating_area is None and aircraft.task_type in AREA_BASED_TASKS:
        add("missing_field", "Select the operating area on the map first.", "operating_area")
    if any(item.code == "missing_field" for item in issues):
        return issues, None

    kind = aircraft.task_type
    assert kind is not None and aircraft.start_location is not None
    # "Start" for every distance check is the shared take-off point definition (mission_planning/start.py).
    takeoff = takeoff_point(aircraft)
    assert takeoff is not None
    start = (takeoff.latitude, takeoff.longitude)
    max_alt = effective_max_altitude(plan, limits)
    assert aircraft.altitude_m is not None and aircraft.speed_mps is not None
    if aircraft.altitude_m > max_alt:
        add("altitude_over_limit", f"Altitude {aircraft.altitude_m:g} m exceeds the maximum {max_alt:g} m.", "altitude_m")
    if aircraft.altitude_m < limits.min_altitude_m:
        add("altitude_under_limit", f"Altitude must be at least {limits.min_altitude_m:g} m.", "altitude_m")
    if not limits.min_speed_mps <= aircraft.speed_mps <= limits.max_speed_mps:
        add("speed_out_of_range", f"Speed must be between {limits.min_speed_mps:g} and {limits.max_speed_mps:g} m/s.", "speed_mps")

    missing = [name for name in REQUIRED_PARAMETERS[kind] if not _present(getattr(aircraft.parameters, name))]
    if kind == "loiter_hold" and aircraft.loiter_duration_s <= 0:
        missing.append("loiter_duration_s")
    for name in missing:
        add("missing_parameter", f"{name} is required for {kind}.", name)

    objectives = set(plan.objectives)
    if objectives and objectives <= TRACKING_ONLY_OBJECTIVES and kind != "waypoint_route":
        add("task_not_allowed", "Target Tracking and Custom Mission only allow a waypoint route; no tracking capability exists.", "task_type")
    if kind == "payload_dropoff" and not objectives & DELIVERY_OBJECTIVES:
        add("task_not_allowed", "payload_dropoff needs the Relief Supply Delivery, Search and Rescue or Disaster Assessment objective.", "task_type")

    p = aircraft.parameters
    if kind == "payload_dropoff":
        if p.drop_altitude_m is not None and not limits.min_altitude_m <= p.drop_altitude_m <= max_alt:
            add("drop_altitude_out_of_range", f"drop_altitude_m must be between {limits.min_altitude_m:g} and {max_alt:g} m.", "drop_altitude_m")
        if p.hover_duration_s is not None and not limits.min_hover_s <= p.hover_duration_s <= limits.max_hover_s:
            add("hover_out_of_range", f"hover_duration_s must be between {limits.min_hover_s:g} and {limits.max_hover_s:g} s.", "hover_duration_s")
        if p.drop_point is not None and plan.operating_area is not None and not point_in_area(plan.operating_area, p.drop_point):
            add("drop_outside_area", "The drop point must be inside the operating area.", "drop_point")
        if p.drop_point is not None and plan.constraints.max_distance_from_start_m is not None:
            dist = distance_m(start, (p.drop_point.latitude, p.drop_point.longitude))
            if dist > plan.constraints.max_distance_from_start_m:
                add("drop_too_far", f"The drop point is {dist:.0f} m from the start; the limit is {plan.constraints.max_distance_from_start_m:g} m.", "drop_point")
    elif plan.operating_area is not None:
        for name, point in reference_points(aircraft):
            if not point_in_area(plan.operating_area, point):
                add("point_outside_area", f"A {name} point lies outside the operating area.", name)
                break

    if plan.operating_area is not None:
        gap = start_to_area_distance_m(plan.operating_area, aircraft.start_location)
        if gap > limits.max_start_to_area_m:
            add("start_too_far_from_area", f"The start is {gap / 1000:.1f} km from the operating area; the limit is {limits.max_start_to_area_m / 1000:g} km for SITL.", "start_location")

    if issues:
        return issues, None
    try:
        route = generate_route(aircraft, plan, limits)
    except RouteError as error:
        add(error.code, error.message, error.field)
        return issues, None

    limit = plan.constraints.max_distance_from_start_m
    if limit is not None:
        worst = max(distance_m(start, (w.latitude, w.longitude)) for w in route.waypoints)
        if worst > limit:
            add("route_too_far", f"A waypoint is {worst:.0f} m from the start; max_distance_from_start_m is {limit:g}.", "max_distance_from_start_m")
    peak = max(w.altitude_m for w in route.waypoints)
    if peak > max_alt:
        add("altitude_over_limit", f"A waypoint altitude {peak:g} m exceeds the maximum {max_alt:g} m.", "altitude_m")
    if len(route.waypoints) > limits.max_waypoints:
        add("waypoint_cap", f"The route has {len(route.waypoints)} waypoints; the limit is {limits.max_waypoints}.")
    # Endurance of the simulated X500 applies to every plan, with or without max_duration_minutes.
    advice = "Reduce the area, widen track spacing, or raise speed."
    if route.duration_s > limits.max_flight_s:
        add("endurance_time_exceeded", f"Route takes about {route.duration_s / 60:.0f} min; the aircraft limit is {limits.max_flight_s / 60:g} min. {advice}", "duration_s")
    if route.length_m > limits.max_flight_distance_m:
        add("endurance_distance_exceeded", f"Route is about {route.length_m / 1000:.1f} km long; the aircraft limit is {limits.max_flight_distance_m / 1000:g} km. {advice}", "length_m")
    if plan.max_duration_minutes is not None and plan.max_duration_minutes * 60 < limits.max_flight_s and route.duration_s > plan.max_duration_minutes * 60:
        add("duration_over_limit", f"The route takes about {route.duration_s / 60:.1f} min; max_duration_minutes is {plan.max_duration_minutes}.", "max_duration_minutes")
    if aircraft.route is not None and aircraft.route.model_dump() != route.model_dump():
        add("route_mismatch", "The stored route does not match the plan parameters; regenerate it.", "route")
    if require_route and aircraft.route is None:
        add("route_missing", "Generate and preview the route before confirming this aircraft.", "route")
    return issues, route


def validate_plan(plan: MissionPlan, limits: PlanningLimits, *, complete: bool) -> list[PlanIssue]:
    """`complete=False` checks drafts (only already generated routes are verified); `complete=True` is used for review/deploy."""
    issues: list[PlanIssue] = []
    for aircraft in plan.aircraft:
        if complete or aircraft.route is not None or aircraft.confirmed:
            found, _ = validate_aircraft(aircraft, plan, limits, require_route=complete or aircraft.confirmed)
            issues += found
    previous_confirmed = True
    for aircraft in plan.aircraft:
        if aircraft.confirmed and not previous_confirmed:
            issues.append(PlanIssue(code="sequence", uav_id=aircraft.uav_id, field="confirmed",
                                    message="Aircraft must be confirmed in order; confirm the previous aircraft first."))
        previous_confirmed = aircraft.confirmed
    if not complete:
        return issues

    def add(code: str, message: str, field: str | None = None) -> None:
        issues.append(PlanIssue(code=code, message=message, field=field))

    count = plan.required_uav_count
    if count is None or len(plan.selected_uav_ids) != count or len(plan.activated_uav_ids) != count or len(plan.aircraft) != count:
        add("fleet_mismatch", "Requested, activated, selected and assigned UAV counts must match.", "required_uav_count")
    elif set(plan.activated_uav_ids) != set(plan.selected_uav_ids) or {a.uav_id for a in plan.aircraft} != set(plan.selected_uav_ids):
        add("fleet_mismatch", "Every selected UAV needs exactly one assignment and a verified activation.", "selected_uav_ids")
    if not plan.objectives:
        add("missing_field", "Select at least one mission objective.", "objectives")
    if plan.operating_area is None:
        add("missing_field", "Select the operating area on the map.", "operating_area")
    if plan.constraints.max_distance_from_start_m is None:
        add("missing_field", "Set max_distance_from_start_m.", "max_distance_from_start_m")
    if plan.constraints.max_altitude_m is None:
        add("missing_field", "Set max_altitude_m.", "max_altitude_m")
    if any(not a.confirmed for a in plan.aircraft):
        add("not_confirmed", "Every aircraft must be confirmed.", "confirmed")
    primaries = sum(1 for a in plan.aircraft if a.role == "primary")
    if plan.allow_multiple_primary:
        if primaries < 1:
            add("primary_count", "At least one aircraft must be the primary.", "role")
    elif primaries != 1:
        add("primary_count", "Exactly one aircraft must be the primary (enable multiple primaries explicitly to change this).", "role")
    return issues


def issues_as_dicts(issues: list[PlanIssue]) -> list[dict]:
    return [item.model_dump() for item in issues]


def estimate_summary(route: GeneratedRoute, limits: PlanningLimits | None = None) -> dict:
    summary = {"length_m": route.length_m, "duration_s": route.duration_s, "waypoints": len(route.waypoints),
               "length_km": round(route.length_m / 1000, 3) if math.isfinite(route.length_m) else None}
    if limits is not None:  # the same limits the validation above enforced, so the preview can show them next to the estimate
        summary.update(limit_duration_s=limits.max_flight_s, limit_length_m=limits.max_flight_distance_m)
    return summary


class PlanValidationError(ValueError):
    """Raised when a mission plan fails validation; `issues` is the structured list."""

    def __init__(self, issues: list[PlanIssue]):
        super().__init__("; ".join(item.message for item in issues[:3]) or "Mission plan validation failed")
        self.issues = issues
