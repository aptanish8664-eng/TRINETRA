"""Pure route generators. All pattern maths is done in local ENU metres around the aircraft's start position
(see geometry.FLAT_EARTH_ASSUMPTION) and converted to WGS84 at the end. Nothing here talks to PX4 or Gazebo.

`payload_dropoff` produces a *simulated, logged* `payload_released` event only; no actuator command exists.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .. import config
from .geometry import FLAT_EARTH_ASSUMPTION, from_enu, geodesic_m, to_enu
from .schemas import (
    AircraftPlan, GeneratedRoute, GeoPoint, MissionPlan, OperatingArea, RouteEvent, RouteWaypoint,
)

GENERATOR_VERSION = 1
GEODESIC_ASSUMPTION = (
    "Waypoints are the points you picked, in WGS84; leg lengths are geodesic distances on the WGS84 ellipsoid, so they are right at "
    "hundreds of kilometres. Duration assumes the planned cruise speed on every leg with no wind or acceleration time."
)
Point = tuple[float, float]


@dataclass(frozen=True)
class PlanningLimits:
    max_waypoints: int
    max_start_to_area_m: float
    min_speed_mps: float
    max_speed_mps: float
    min_altitude_m: float
    max_altitude_m: float
    min_hover_s: float
    max_hover_s: float
    # Simulated X500 endurance (config TRINETRA_MP_MAX_FLIGHT_MINUTES / _DISTANCE_M); defaults match the config defaults.
    max_flight_s: float = 18000.0
    max_flight_distance_m: float = 1_000_000.0
    # Area generators use a flat local plane; beyond this range they refuse rather than draw a wrong route.
    max_planar_extent_m: float = 20_000.0
    # Point-to-point routes reaching further than this from the start use geodesic lengths and the points' own coordinates.
    geodesic_above_m: float = 20_000.0

    @classmethod
    def from_config(cls) -> "PlanningLimits":
        return cls(
            max_waypoints=config.MP_MAX_WAYPOINTS, max_start_to_area_m=float(config.MP_MAX_START_TO_AREA_M),
            min_speed_mps=float(config.MP_MIN_SPEED_MPS), max_speed_mps=float(config.MP_MAX_SPEED_MPS),
            min_altitude_m=float(config.MP_MIN_ALTITUDE_M), max_altitude_m=float(config.MP_MAX_ALTITUDE_M),
            min_hover_s=float(config.MP_MIN_HOVER_S), max_hover_s=float(config.MP_MAX_HOVER_S),
            max_flight_s=float(config.MP_MAX_FLIGHT_MINUTES) * 60.0, max_flight_distance_m=float(config.MP_MAX_FLIGHT_DISTANCE_M),
            max_planar_extent_m=float(config.MP_MAX_PLANAR_EXTENT_M), geodesic_above_m=float(config.MP_GEODESIC_ABOVE_M),
        )


class RouteError(ValueError):
    def __init__(self, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.code, self.message, self.field = code, message, field


def _unit(heading_deg: float) -> Point:
    angle = math.radians(heading_deg)
    return math.sin(angle), math.cos(angle)  # (east, north) for a bearing clockwise from north


def _guard(points: list, limit: int) -> None:
    if len(points) > limit:
        raise RouteError("waypoint_cap", f"The pattern needs more than the allowed {limit} waypoints; widen the spacing or shrink the area.")


# --- pure pattern generators (ENU metres) -------------------------------------------------------

def expanding_square(datum: Point, spacing_m: float, radius_m: float, heading_deg: float, max_points: int) -> list[Point]:
    """Square spiral from the datum: legs s, s, 2s, 2s, 3s, 3s, ... turning 90 degrees clockwise."""
    pairs = int(radius_m // spacing_m)
    if pairs < 1:
        raise RouteError("pattern_too_small", "search_radius_m must be at least one track spacing.", "search_radius_m")
    if 2 * pairs + 1 > max_points:
        raise RouteError("waypoint_cap", f"The expanding square needs {2 * pairs + 1} waypoints, above the allowed {max_points}.")
    points = [datum]
    east, north = datum
    for leg in range(2 * pairs):
        length = (leg // 2 + 1) * spacing_m
        d_east, d_north = _unit(heading_deg + 90 * leg)
        east, north = east + length * d_east, north + length * d_north
        points.append((east, north))
    return points


def sector_search(datum: Point, radius_m: float, angle_deg: float, count: int, heading_deg: float) -> list[Point]:
    """Pass i sweeps the sector [h + i*a, h + (i+1)*a]: datum -> arc start -> arc end -> datum. a=120, count=3 covers 360."""
    points = [datum]
    for index in range(count):
        for bearing in (heading_deg + index * angle_deg, heading_deg + (index + 1) * angle_deg):
            d_east, d_north = _unit(bearing)
            points.append((datum[0] + radius_m * d_east, datum[1] + radius_m * d_north))
        points.append(datum)
    return points


def lawnmower(polygon: list[Point], spacing_m: float, heading_deg: float, max_points: int) -> list[Point]:
    """Parallel tracks (boustrophedon) over a simple polygon; tracks run along `heading_deg`.

    Non-convex polygons may yield several segments per line; they are visited in line order, alternating direction.
    """
    s, c = _unit(heading_deg)
    along = [(e * s + n * c, e * c - n * s) for e, n in polygon]  # (u along track, v across tracks)
    v_min, v_max = min(v for _, v in along), max(v for _, v in along)
    if v_max - v_min <= 0:
        raise RouteError("area_too_small", "The survey area has no extent across the track direction.")
    lines = []
    v = v_min + spacing_m / 2
    while v < v_max:
        lines.append(v)
        v += spacing_m
    if not lines:
        lines = [(v_min + v_max) / 2]
    if 2 * len(lines) > max_points * 4:
        raise RouteError("waypoint_cap", "The survey would need far more tracks than the allowed waypoint count.", "line_spacing_m")
    points: list[Point] = []
    forward = True
    for line in lines:
        crossings = []
        for index, (u1, v1) in enumerate(along):
            u2, v2 = along[(index + 1) % len(along)]
            if (v1 <= line < v2) or (v2 <= line < v1):
                crossings.append(u1 + (line - v1) * (u2 - u1) / (v2 - v1))
        crossings.sort()
        for pair in range(0, len(crossings) - 1, 2):
            a, b = crossings[pair], crossings[pair + 1]
            ends = [(a, line), (b, line)] if forward else [(b, line), (a, line)]
            points.extend((u * s + w * c, u * c - w * s) for u, w in ends)
            forward = not forward
        _guard(points, max_points)
    if not points:
        raise RouteError("area_too_small", "No survey track fits inside the area; reduce line_spacing_m.", "line_spacing_m")
    return points


def perimeter(path: list[Point], laps: int, closed: bool) -> list[Point]:
    """Closed boundary laps repeat the loop; an open polyline is flown out and back on alternate laps."""
    points: list[Point] = []
    for lap in range(laps):
        if closed:
            points.extend(path + [path[0]])
        else:
            points.extend(path if lap % 2 == 0 else list(reversed(path)))
    return [point for index, point in enumerate(points) if index == 0 or point != points[index - 1]]


def orbit(centre: Point, radius_m: float, count: int, first_bearing_deg: float = 0.0) -> list[Point]:
    points = []
    for index in range(count):
        d_east, d_north = _unit(first_bearing_deg + 360.0 * index / count)
        points.append((centre[0] + radius_m * d_east, centre[1] + radius_m * d_north))
    return points


def area_polygon_enu(area: OperatingArea, origin: tuple[float, float]) -> list[Point]:
    if area.kind == "polygon":
        return [to_enu(origin, v.latitude, v.longitude)[:2] for v in area.vertices]
    assert area.centre is not None and area.radius_m is not None
    centre = to_enu(origin, area.centre.latitude, area.centre.longitude)[:2]
    return orbit(centre, area.radius_m, 48)


# --- route assembly -----------------------------------------------------------------------------

@dataclass
class _Step:
    east: float
    north: float
    altitude: float
    hover: float = 0.0
    action: str = "waypoint"
    # Set in geodesic mode: the step's own WGS84 position (east/north are then unused).
    lat: Optional[float] = None
    lon: Optional[float] = None


def _enu(origin: tuple[float, float], point: GeoPoint) -> Point:
    return to_enu(origin, point.latitude, point.longitude)[:2]


def _require(value, name: str):
    if value is None or (isinstance(value, list) and not value):
        raise RouteError("missing_parameter", f"{name} is required for this task type.", name)
    return value


PLANAR_TASKS = {"expanding_square_search", "sector_search", "coverage_survey", "perimeter_patrol"}
POINT_TASKS = {"waypoint_route", "payload_dropoff", "point_inspection", "loiter_hold"}
LONG_LEG_ADVICE = "Use a waypoint route for long legs."


def _planar_guard(extent_m: float, limits: PlanningLimits, what: str) -> None:
    if extent_m > limits.max_planar_extent_m:
        raise RouteError("planar_range_exceeded", (
            f"{what} reaches {extent_m / 1000:.1f} km from the start, beyond the {limits.max_planar_extent_m / 1000:g} km a flat local plane is "
            f"valid for, so this route would be wrong. {LONG_LEG_ADVICE}"), "operating_area")


def _user_points(aircraft: AircraftPlan) -> list[GeoPoint]:
    p = aircraft.parameters
    if aircraft.task_type == "waypoint_route":
        return list(p.points)
    if aircraft.task_type == "point_inspection":
        return list(p.points)
    if aircraft.task_type == "payload_dropoff":
        return list(p.points) if p.points else ([p.drop_point] if p.drop_point else [])
    if aircraft.task_type == "loiter_hold":
        return [p.hold_point] if p.hold_point else []
    return []


def geodesic_reach_m(aircraft: AircraftPlan, origin: tuple[float, float]) -> float:
    """Farthest user-picked point from the start, as a geodesic distance (0 for tasks without point inputs)."""
    return max((geodesic_m(origin, (point.latitude, point.longitude)) for point in _user_points(aircraft)), default=0.0)


def build_geodesic_pattern(aircraft: AircraftPlan, origin: tuple[float, float], cruise_alt: float, cap: int) -> list[_Step]:
    """Point-to-point tasks flown with the points' own WGS84 coordinates (no flat-plane conversion)."""
    p = aircraft.parameters
    kind = aircraft.task_type

    def at(point: GeoPoint, altitude: float = cruise_alt, hover: float = 0.0, action: str = "waypoint") -> _Step:
        return _Step(0.0, 0.0, altitude, hover, action, round(point.latitude, 7), round(point.longitude, 7))

    if kind == "waypoint_route":
        steps = [at(point) for point in _require(p.points, "points")]
    elif kind == "point_inspection":
        steps = []
        for point in _require(p.points, "points"):
            if p.orbit_radius_m > 0:
                for index, (east, north) in enumerate(orbit((0.0, 0.0), p.orbit_radius_m, p.orbit_points)):
                    lat, lon = from_enu((point.latitude, point.longitude), east, north)   # a small ring: local plane about its own centre
                    steps.append(_Step(0.0, 0.0, cruise_alt, p.hover_s if index == 0 else 0.0, "hover" if index == 0 and p.hover_s else "waypoint", round(lat, 7), round(lon, 7)))
            else:
                steps.append(at(point, hover=p.hover_s, action="hover" if p.hover_s else "waypoint"))
    elif kind == "loiter_hold":
        if aircraft.loiter_duration_s <= 0:
            raise RouteError("missing_parameter", "loiter_duration_s must be greater than zero for a loiter hold.", "loiter_duration_s")
        steps = [at(p.hold_point, hover=aircraft.loiter_duration_s, action="hover")]
    else:  # payload_dropoff
        drop = _require(p.drop_point, "drop_point")
        hover = _require(p.hover_duration_s, "hover_duration_s")
        _require(p.drop_altitude_m, "drop_altitude_m")
        _require(p.payload_category, "payload_category")
        if p.points:
            index = p.drop_index if p.drop_index is not None else len(p.points) - 1
            steps = []
            for position, point in enumerate(p.points):
                steps.append(at(point))
                if position == index:
                    steps.append(at(point, altitude=p.drop_altitude_m, hover=hover, action="payload_release_point"))
        else:
            steps = [at(drop), at(drop, altitude=p.drop_altitude_m, hover=hover, action="payload_release_point")]
    _guard(steps, cap)
    return steps


def build_pattern(aircraft: AircraftPlan, plan: MissionPlan, origin: tuple[float, float], limits: PlanningLimits,
                  cruise_alt: float) -> list[_Step]:
    p = aircraft.parameters
    kind = aircraft.task_type
    cap = limits.max_waypoints
    if kind in PLANAR_TASKS:  # refuse an area the flat plane cannot represent before generating anything
        if kind in ("expanding_square_search", "sector_search") and p.datum is not None:
            reach = geodesic_m(origin, (p.datum.latitude, p.datum.longitude)) + (p.search_radius_m or p.sector_radius_m or 0.0)
            _planar_guard(reach, limits, "The search pattern")
        area = plan.operating_area
        if kind in ("coverage_survey", "perimeter_patrol") and area is not None and not (kind == "perimeter_patrol" and p.path):
            if area.kind == "circle" and area.centre is not None and area.radius_m is not None:
                reach = geodesic_m(origin, (area.centre.latitude, area.centre.longitude)) + area.radius_m
            else:
                reach = max((geodesic_m(origin, (v.latitude, v.longitude)) for v in area.vertices), default=0.0)
            _planar_guard(reach, limits, "The operating area")
        if kind == "perimeter_patrol" and p.path:
            _planar_guard(max(geodesic_m(origin, (v.latitude, v.longitude)) for v in p.path), limits, "The patrol path")

    def steps(points: list[Point], altitude: float = cruise_alt) -> list[_Step]:
        _guard(points, cap)
        return [_Step(e, n, altitude) for e, n in points]

    if kind == "expanding_square_search":
        datum = _enu(origin, _require(p.datum, "datum"))
        return steps(expanding_square(datum, _require(p.track_spacing_m, "track_spacing_m"),
                                      _require(p.search_radius_m, "search_radius_m"), p.heading_deg, cap))
    if kind == "sector_search":
        datum = _enu(origin, _require(p.datum, "datum"))
        return steps(sector_search(datum, _require(p.sector_radius_m, "sector_radius_m"), p.sector_angle_deg,
                                   p.sector_count, p.heading_deg))
    if kind == "coverage_survey":
        area = _require(plan.operating_area, "operating_area")
        return steps(lawnmower(area_polygon_enu(area, origin), _require(p.line_spacing_m, "line_spacing_m"), p.heading_deg, cap))
    if kind == "perimeter_patrol":
        if p.path:
            if len(p.path) < 2:
                raise RouteError("missing_parameter", "A perimeter path needs at least two points.", "path")
            return steps(perimeter([_enu(origin, v) for v in p.path], p.laps, closed=False))
        area = _require(plan.operating_area, "operating_area")
        return steps(perimeter(area_polygon_enu(area, origin), p.laps, closed=True))
    if kind == "point_inspection":
        result: list[_Step] = []
        for point in _require(p.points, "points"):
            centre = _enu(origin, point)
            if p.orbit_radius_m > 0:
                ring = orbit(centre, p.orbit_radius_m, p.orbit_points)
                result += [_Step(e, n, cruise_alt, p.hover_s if index == 0 else 0.0, "hover" if index == 0 and p.hover_s else "waypoint")
                           for index, (e, n) in enumerate(ring)]
            else:
                result.append(_Step(centre[0], centre[1], cruise_alt, p.hover_s, "hover" if p.hover_s else "waypoint"))
            _guard(result, cap)
        return result
    if kind == "waypoint_route":
        return steps([_enu(origin, v) for v in _require(p.points, "points")])
    if kind == "loiter_hold":
        if aircraft.loiter_duration_s <= 0:
            raise RouteError("missing_parameter", "loiter_duration_s must be greater than zero for a loiter hold.", "loiter_duration_s")
        centre = _enu(origin, p.hold_point) if p.hold_point else (0.0, 0.0)
        return [_Step(centre[0], centre[1], cruise_alt, aircraft.loiter_duration_s, "hover")]
    if kind == "payload_dropoff":
        drop = _enu(origin, _require(p.drop_point, "drop_point"))
        drop_alt = _require(p.drop_altitude_m, "drop_altitude_m")
        hover = _require(p.hover_duration_s, "hover_duration_s")
        _require(p.payload_category, "payload_category")
        if p.points:
            index = p.drop_index if p.drop_index is not None else len(p.points) - 1
            out: list[_Step] = []
            for position, point in enumerate(p.points):
                east, north = _enu(origin, point)
                out.append(_Step(east, north, cruise_alt))
                if position == index:
                    out.append(_Step(east, north, drop_alt, hover, "payload_release_point"))
            _guard(out, cap)
            return out
        return [_Step(drop[0], drop[1], cruise_alt),
                _Step(drop[0], drop[1], drop_alt, hover, "payload_release_point")]
    raise RouteError("missing_parameter", "Choose a task type before generating a route.", "task_type")


def generate_route(aircraft: AircraftPlan, plan: MissionPlan, limits: PlanningLimits | None = None) -> GeneratedRoute:
    """Deterministic: the same inputs always give an identical route, so the backend can verify a stored one."""
    limits = limits or PlanningLimits.from_config()
    if aircraft.start_location is None:
        raise RouteError("missing_parameter", "Select the aircraft start location on the map.", "start_location")
    if aircraft.altitude_m is None or aircraft.speed_mps is None:
        raise RouteError("missing_parameter", "altitude_m and speed_mps are required.", "altitude_m")
    origin = (aircraft.start_location.latitude, aircraft.start_location.longitude)
    cruise, speed = float(aircraft.altitude_m), float(aircraft.speed_mps)
    if aircraft.task_type == "ground_standby":
        # A standby aircraft stays where it is: a two-point record (never armed or flown), so every aircraft of a plan has a stored route.
        waypoints = [RouteWaypoint(seq=0, latitude=round(origin[0], 7), longitude=round(origin[1], 7), altitude_m=0.0, speed_mps=speed, hover_s=0.0, action="takeoff"),
                     RouteWaypoint(seq=1, latitude=round(origin[0], 7), longitude=round(origin[1], 7), altitude_m=0.0, speed_mps=speed, hover_s=0.0, action="land")]
        return GeneratedRoute(generator="ground_standby", generator_version=GENERATOR_VERSION, waypoints=waypoints, events=[], length_m=0.0, duration_s=0.0,
                              assumptions="This aircraft stays on the ground at its start. It is not armed or flown.")
    # Long point-to-point routes: use each point's own WGS84 coordinates and geodesic lengths. Short routes keep the
    # local-plane method (identical to earlier versions, so stored routes still verify).
    geodesic = aircraft.task_type in POINT_TASKS and geodesic_reach_m(aircraft, origin) > limits.geodesic_above_m
    pattern = build_geodesic_pattern(aircraft, origin, cruise, limits.max_waypoints) if geodesic else build_pattern(aircraft, plan, origin, limits, cruise)
    if not pattern:
        raise RouteError("empty_route", "The task produced no waypoints.")
    if not geodesic and aircraft.task_type in PLANAR_TASKS:
        _planar_guard(max(math.hypot(step.east, step.north) for step in pattern), limits, "The pattern")
    last = pattern[-1]
    if aircraft.loiter_duration_s > 0 and aircraft.task_type not in {"loiter_hold"}:
        last.hover += aircraft.loiter_duration_s
        last.action = last.action if last.action == "payload_release_point" else "hover"
    home = _Step(0.0, 0.0, cruise, 0.0, "takeoff", origin[0], origin[1]) if geodesic else _Step(0.0, 0.0, cruise, 0.0, "takeoff")
    steps = [home] + pattern
    if aircraft.return_mode == "return_to_start":
        away = (geodesic_m(origin, (last.lat, last.lon)) > 0.5) if geodesic else math.hypot(last.east, last.north) > 0.5
        if away or last.altitude != cruise:
            steps.append(_Step(0.0, 0.0, cruise, lat=origin[0] if geodesic else None, lon=origin[1] if geodesic else None))
        steps.append(_Step(0.0, 0.0, 0.0, 0.0, "land", origin[0] if geodesic else None, origin[1] if geodesic else None))
    else:
        steps.append(_Step(last.east, last.north, 0.0, 0.0, "land", last.lat, last.lon))
    _guard(steps, limits.max_waypoints)

    waypoints: list[RouteWaypoint] = []
    events: list[RouteEvent] = []
    length = elapsed = 0.0
    prev = (0.0, 0.0, 0.0)
    prev_ll = origin
    for index, step in enumerate(steps):
        if geodesic:
            lat, lon = step.lat, step.lon
            distance = math.hypot(geodesic_m(prev_ll, (lat, lon)), step.altitude - prev[2])
            prev_ll = (lat, lon)
        else:
            distance = math.dist(prev, (step.east, step.north, step.altitude))
            lat, lon = from_enu(origin, step.east, step.north)
        length += distance
        elapsed += distance / speed
        arrival = elapsed
        elapsed += step.hover
        waypoints.append(RouteWaypoint(
            seq=index, latitude=round(lat, 7), longitude=round(lon, 7), altitude_m=round(step.altitude, 3),
            speed_mps=speed, hover_s=round(step.hover, 3), action=step.action,  # type: ignore[arg-type]
        ))
        if step.action == "payload_release_point" and aircraft.parameters.payload_category:
            events.append(RouteEvent(
                type="payload_released", aircraft_id=aircraft.uav_id, waypoint_seq=index,
                planned_time_s=round(arrival + float(aircraft.parameters.hover_duration_s or 0.0), 1),
                payload_category=aircraft.parameters.payload_category,
            ))
        prev = (step.east, step.north, step.altitude)
    return GeneratedRoute(
        generator=aircraft.task_type or "none", generator_version=GENERATOR_VERSION, waypoints=waypoints, events=events,
        length_m=round(length, 1), duration_s=round(elapsed, 1), assumptions=GEODESIC_ASSUMPTION if geodesic else FLAT_EARTH_ASSUMPTION,
    )
