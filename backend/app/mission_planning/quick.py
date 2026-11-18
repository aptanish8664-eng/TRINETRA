"""Quick mission: one place that decides every value a user would otherwise type, and the deterministic parser behind the assistant.

A quick mission is a mission type, one point ("Where"), an optional size and a name. This module turns that into an ordinary schema-2
MissionPlan (the same object the manual form edits), generates and validates the routes with the same generators and validator as
everything else, derives the three mission limits from the generated route, and reports warnings. The frontend keeps no copy of any
rule: it asks `GET /api/mission-plans/limits` (the `quick` block) and `POST /api/mission-plans/quick`.

Software-only. Nothing here saves, deploys or sends anything to PX4: it only computes a plan.

Derivation rules (the documentation in docs/AI_MISSION_ASSISTANT.md repeats these numbers; tests/test_quick_mission.py pins them):

* area centre = the "Where" point (for delivery and waypoint route: the destination, with the area centred on it);
* start = the "Where" point for area missions (or the aircraft's position when "Use the aircraft's position" is the Where);
  for relief delivery and waypoint route the start is the aircraft's position and the Where point is the destination;
* datum = area centre; first leg heading 0 (north); return to start and land; loiter 0 s;
* speed 8 m/s (QUICK_SPEED_MPS; the threat-detector baseline is validated only at 8 m/s), altitude 50 m (QUICK_ALTITUDE_M);
* track / line spacing = 0.6 x altitude, rounded to 5 m, at least 10 m (a swath of about 0.6 x height); it is widened in 25% steps
  only when the pattern would otherwise exceed the waypoint limit, and a warning says so;
* spiral search: the spiral covers 2/3 of the area radius on each side of the datum (so it fits inside the circle), i.e. the
  generator's search_radius_m (the longest leg, the full side of the square) = 2 x 2/3 x area radius;
* relief delivery: drop altitude min(10 m, altitude), hover 10 s, payload "relief supplies" (simulated, logged only);
* perimeter patrol: one lap of the area boundary; survey: parallel tracks over the circle;
* any number of aircraft (the only ceiling is the id format, px4-1 ... px4-999); planning never depends on which aircraft are running;
* roles: UAV-1 primary, every other aircraft decoy, until the user chooses otherwise (any aircraft may be primary, decoy or standby;
  at least one primary); priority 1 for the first primary, 2 for the next aircraft, 3 for the rest; all reassignable;
* limits (auto, overridable): max distance from start = farthest route point x 1.3 + 50 m, rounded up to 50 m;
  max altitude = max(1.5 x altitude, altitude + 30 m) capped at the configured maximum; max duration = 1.5 x estimated
  flight time in whole minutes (at least 1). A value the user types is enforced exactly and is never replaced.
"""
from __future__ import annotations

import math
import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .. import config
from .generators import PlanningLimits
from .geometry import distance_m, from_enu, geodesic_m, to_enu
from .schemas import PLAN_MAX_AIRCRAFT, AircraftPlan, AutoLimits, GeoPoint, MissionConstraints, MissionPlan, OperatingArea, TaskParameters
from .start import takeoff_point
from .validation import PlanIssue, plan_warnings, point_in_area, start_to_area_distance_m, validate_aircraft, validate_plan

# --- constants (everything numeric the rules use) ---------------------------------------------------------------------------

DEFAULT_SIZE_M = 300.0
MIN_SIZE_M, MAX_SIZE_M = 50.0, 5000.0
SPACING_PER_ALTITUDE = 0.6
SPACING_STEP_M, MIN_SPACING_M = 5.0, 10.0
SPIRAL_EXTENT_FRACTION = 2.0 / 3.0
DROP_ALTITUDE_M, DROP_HOVER_S = 10.0, 10.0
DISTANCE_FACTOR, DISTANCE_MARGIN_M, DISTANCE_ROUND_M = 1.3, 50.0, 50.0
ALTITUDE_FACTOR, ALTITUDE_MARGIN_M = 1.5, 30.0
DURATION_FACTOR = 1.5
DECOY_OFFSET_M = 25.0              # lateral offset of the first decoy's parallel path; each further decoy adds this much
DECOY_MIN_SEPARATION_M = 5.0       # two decoy lanes on the same side are never closer than this
DEFAULT_EXTRA_ROLE = "decoy"       # the role of every aircraft after the first until the user chooses one
TYPO_GUARD_OVER = 50               # above this many aircraft the page asks "Did you mean N?" once (a typo guard, not a limit)
START_GRID_ROWS = 10               # default starts: 4 m apart going north, a new column 4 m to the east after this many aircraft
NAME_MAX = 40
FAR_START_OFFSET_M = 200.0         # a far aircraft: the mission starts this far south of the first place, where "Restart at mission start" can bring it
START_OFFSET_M = 4.0               # each extra aircraft starts this far (north) from the previous one, so no two share a spawn point
MAX_ROUTE_POINTS = 48              # a decoy's waypoint route is decimated to this many points (the plan allows 50)
GRAVITY_MPS2 = 9.81
FAR_AIRCRAFT_M = 50_000.0          # the aircraft is this far from the chosen place: a note points to the relocation action
POINT_LIMIT = 20

QUICK_TYPES: dict[str, dict[str, Any]] = {
    "area_survey": {
        "label": "Area survey", "objectives": ["area_survey"], "task": "coverage_survey", "where": "centre", "start": "where",
        "summary": "Parallel tracks over a circle around the point.", "size_label": "Area radius (m)",
        "keywords": ["survey", "scan", "map the area", "mapping", "coverage", "lawnmower", "cover the area", "photograph the area"],
    },
    "search_spiral": {
        "label": "Search spiral", "objectives": ["search_rescue"], "task": "expanding_square_search", "where": "centre", "start": "where",
        "summary": "An expanding square spiral from the point, inside the circle.", "size_label": "Area radius (m)",
        "keywords": ["search", "spiral", "expanding square", "look for", "find"],
    },
    "relief_delivery": {
        "label": "Relief delivery", "objectives": ["relief_supply_delivery"], "task": "payload_dropoff", "where": "destination", "start": "aircraft",
        "summary": "Fly from the aircraft to the point and drop simulated supplies there.", "size_label": "Area radius around the drop point (m)",
        "keywords": ["deliver", "delivery", "supplies", "relief", "drop", "payload", "medical kit"],
    },
    "perimeter_patrol": {
        "label": "Perimeter patrol", "objectives": ["perimeter_monitoring"], "task": "perimeter_patrol", "where": "centre", "start": "where",
        "summary": "One lap along the boundary of a circle around the point.", "size_label": "Area radius (m)",
        "keywords": ["patrol", "perimeter", "boundary", "border", "fence"],
    },
    "waypoint_route": {
        "label": "Waypoint route", "objectives": ["custom_mission"], "task": "waypoint_route", "where": "points", "start": "aircraft",
        "summary": "Fly through the points you click, in order, and come back.", "size_label": "",
        "keywords": ["fly to", "go to", "waypoint", "route", "point to point", "point-to-point", "leg"],
    },
    "waypoints_then_drop": {
        "label": "Waypoints then drop", "objectives": ["relief_supply_delivery"], "task": "payload_dropoff", "where": "points", "start": "aircraft",
        "summary": "Fly through the points you click, make the simulated drop at the last one (or the one you mark), then return and land.", "size_label": "",
        "keywords": ["then drop", "drop after", "waypoints then", "via", "through the points", "then deliver"],
    },
}


def quick_defaults(limits: Optional[PlanningLimits] = None) -> dict[str, Any]:
    limits = limits or PlanningLimits.from_config()
    speed = min(max(float(config.QUICK_SPEED_MPS), limits.min_speed_mps), limits.max_speed_mps)
    altitude = min(max(float(config.QUICK_ALTITUDE_M), limits.min_altitude_m), limits.max_altitude_m)
    return {"speed_mps": speed, "altitude_m": altitude, "size_m": DEFAULT_SIZE_M, "drone_count": 1}


RULE_TEXT = [
    "Area centre = the point you choose (for delivery and waypoint route: the destination).",
    "Start = the point you choose for area missions, or the aircraft's position; for delivery and waypoint route the start is the aircraft's position.",
    "Datum = area centre. First leg heading 0 (north). Return to start and land. Loiter 0 s.",
    "Speed default 8 m/s: the threat-detector baseline is validated only at that speed (configurable). Altitude default 50 m (configurable).",
    "Track / line spacing = 0.6 x altitude, rounded to 5 m, at least 10 m; widened only when the pattern would exceed the waypoint limit.",
    "Spiral search: the spiral covers 2/3 of the area radius on each side of the datum, so it fits inside the circle.",
    "Relief delivery: drop altitude 10 m (or the cruise altitude if lower), hover 10 s; the release is a simulated, logged event only.",
    "Any number of aircraft can be planned; how many can be simulated on this machine is a separate setting that never blocks planning or saving.",
    "Roles: UAV-1 is the primary and every other aircraft is a decoy until you choose otherwise. You choose every role (primary, decoy or standby); at least one aircraft must be the primary. Priority: 1 for the first primary, 2 for the next aircraft, 3 for the rest.",
    "Several aircraft: a primary flies the mission route; a decoy flies a parallel path beside it (the first 25 m to the side that stays inside the area, the second 50 m, and so on; "
    "when that distance does not fit, the next free lane is used, never closer than 5 m to another decoy); a standby stays on the ground at its start and is never armed or flown. "
    "Each extra aircraft starts 4 m further north, and a new column 4 m to the east begins after every 10 aircraft, so no two share a spawn point. Every route is generated and validated like any other.",
    "Per aircraft you can override the altitude, the speed and the start offset (metres north of the mission start). No two aircraft may fly the identical path or start at the same point.",
    "Waypoint types (waypoint route, waypoints then drop): the area is a circle around the centre of the points, just large enough to hold them (50 m steps, at least 100 m); the drop is at the last waypoint unless you mark another.",
    "Speed when you do not choose one: the highest the route can safely fly = min(speed ceiling, sqrt(lateral acceleration x tightest corner radius)). "
    "Lateral acceleration = min(profile acceleration, g x tan(profile tilt)); a corner radius is at most half the shorter leg divided by tan(half the turn angle) and never less than the waypoint acceptance radius. "
    "Above 8 m/s the threat monitor is NOT validated: a badge says so and one click restores 8 m/s.",
    "Limits are derived from the generated route and shown as auto: max distance = farthest route point x 1.3 + 50 m (rounded up to 50 m); "
    "max altitude = max(1.5 x altitude, altitude + 30 m) up to the configured maximum; max duration = 1.5 x estimated flight time.",
    "A limit you type is enforced exactly and is never replaced by the auto value.",
]


def rules_payload(limits: Optional[PlanningLimits] = None) -> dict[str, Any]:
    """Everything the frontend needs to show the Quick mission panel; the frontend keeps no second copy of these."""
    return {
        "types": [{"id": key, "label": spec["label"], "summary": spec["summary"], "size_label": spec["size_label"], "task_type": spec["task"],
                   "objectives": spec["objectives"], "where": spec["where"], "start": spec["start"]} for key, spec in QUICK_TYPES.items()],
        "defaults": quick_defaults(limits), "size_range_m": [MIN_SIZE_M, MAX_SIZE_M],
        # Planning has no product limit: `plan_max_aircraft` is only the id format (px4-1 ... px4-999). `simulation_capacity` is how many aircraft this
        # machine can RUN; it limits activating and starting, never planning or saving.
        "plan_max_aircraft": PLAN_MAX_AIRCRAFT, "max_drones": PLAN_MAX_AIRCRAFT, "simulation_capacity": int(config.SIM_MAX_AIRCRAFT),
        "default_extra_role": DEFAULT_EXTRA_ROLE, "extra_roles": [DEFAULT_EXTRA_ROLE] * 4, "roles": ["primary", "decoy", "standby"], "typo_guard_over": TYPO_GUARD_OVER, "name_max": NAME_MAX,
        "start_offset_m": START_OFFSET_M, "start_grid_rows": START_GRID_ROWS,
        "rules": RULE_TEXT, "speed_reason": RULE_TEXT[3], "research_speed_mps": quick_defaults(limits)["speed_mps"],
        "far_aircraft_m": FAR_AIRCRAFT_M, "point_limit": POINT_LIMIT, "decoy_offset_m": DECOY_OFFSET_M,
        "constants": {"spacing_per_altitude": SPACING_PER_ALTITUDE, "spiral_extent_fraction": SPIRAL_EXTENT_FRACTION, "distance_factor": DISTANCE_FACTOR,
                      "distance_margin_m": DISTANCE_MARGIN_M, "distance_round_m": DISTANCE_ROUND_M, "altitude_factor": ALTITUDE_FACTOR,
                      "altitude_margin_m": ALTITUDE_MARGIN_M, "duration_factor": DURATION_FACTOR},
    }


# --- derivations -------------------------------------------------------------------------------------------------------------

def track_spacing_m(altitude_m: float) -> float:
    return max(MIN_SPACING_M, round(SPACING_PER_ALTITUDE * altitude_m / SPACING_STEP_M) * SPACING_STEP_M)


def spiral_search_radius_m(area_radius_m: float) -> float:
    """The generator's longest leg (the side of the square): twice the half-extent that must fit inside the circle."""
    return 2.0 * SPIRAL_EXTENT_FRACTION * area_radius_m


def derive_limits(worst_distance_m: float, altitude_m: float, estimated_s: float, max_altitude_cap_m: float) -> dict[str, float]:
    distance = math.ceil((DISTANCE_FACTOR * worst_distance_m + DISTANCE_MARGIN_M) / DISTANCE_ROUND_M) * DISTANCE_ROUND_M
    altitude = min(float(max_altitude_cap_m), max(ALTITUDE_FACTOR * altitude_m, altitude_m + ALTITUDE_MARGIN_M))
    minutes = max(1, int(round(DURATION_FACTOR * estimated_s / 60.0)))
    return {"max_distance_from_start_m": float(distance), "max_altitude_m": float(math.ceil(altitude)) if math.ceil(altitude) <= max_altitude_cap_m else float(max_altitude_cap_m),
            "max_duration_minutes": minutes}


def route_worst_distance_m(aircraft: AircraftPlan) -> float:
    start = takeoff_point(aircraft)
    if start is None or aircraft.route is None:
        return 0.0
    return max((distance_m((start.latitude, start.longitude), (w.latitude, w.longitude)) for w in aircraft.route.waypoints), default=0.0)


def apply_auto_limits(plan: MissionPlan, limits: PlanningLimits) -> tuple[MissionPlan, dict[str, float]]:
    """Derives the limits flagged `auto` from the routes already on the aircraft and writes them into the plan. A typed value is kept."""
    routed = [item for item in plan.aircraft if item.route is not None]
    if not routed:
        return plan, {}
    derived = derive_limits(max(route_worst_distance_m(item) for item in routed), max(float(item.altitude_m or 0) for item in routed),
                            max(item.route.duration_s for item in routed if item.route), limits.max_altitude_m)
    constraints = plan.constraints.model_copy()
    update: dict[str, Any] = {}
    if plan.auto_limits.max_distance_from_start_m:
        constraints = constraints.model_copy(update={"max_distance_from_start_m": derived["max_distance_from_start_m"]})
    if plan.auto_limits.max_altitude_m:
        constraints = constraints.model_copy(update={"max_altitude_m": derived["max_altitude_m"]})
    if plan.auto_limits.max_duration_minutes:
        update["max_duration_minutes"] = derived["max_duration_minutes"]
    return plan.model_copy(update={"constraints": constraints, **update}), derived


# --- building a plan ---------------------------------------------------------------------------------------------------------

class QuickAircraft(BaseModel):
    """One row of the Quick Mission aircraft box. `None` always means "use the default", so auto stays auto when the mission is reopened."""
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, max_length=NAME_MAX)
    role: Optional[str] = Field(default=None, pattern=r"^(primary|decoy|standby)$")
    altitude_m: Optional[float] = Field(default=None, gt=0, le=10000, allow_inf_nan=False)
    speed_mps: Optional[float] = Field(default=None, gt=0, le=1000, allow_inf_nan=False)
    start_offset_m: Optional[float] = Field(default=None, ge=-100000, le=100000, allow_inf_nan=False)     # metres north of the mission start


class QuickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mission_type: str = Field(max_length=32)
    where: Optional[GeoPoint] = None
    points: list[GeoPoint] = Field(default_factory=list, max_length=POINT_LIMIT)       # waypoint types: the clicked points in order
    drop_index: Optional[int] = Field(default=None, ge=0, lt=POINT_LIMIT)               # waypoints then drop: which point is the drop (default last)
    where_source: str = Field(default="map", pattern=r"^(map|place|aircraft)$")
    size_m: Optional[float] = Field(default=None, gt=0, le=1_000_000, allow_inf_nan=False)
    drone_count: int = Field(default=1, ge=1)                                             # any whole number; the id format is checked in build_quick_plan
    # Optional: ids of running aircraft to plan with. Missing ids are filled with px4-1, px4-2, ... so planning never needs a running fleet.
    aircraft_ids: list[str] = Field(default_factory=list, max_length=PLAN_MAX_AIRCRAFT)
    roles: list[str] = Field(default_factory=list, max_length=PLAN_MAX_AIRCRAFT)        # older clients: roles of the 2nd, 3rd ... aircraft (decoy | standby)
    aircraft: list[QuickAircraft] = Field(default_factory=list, max_length=PLAN_MAX_AIRCRAFT)   # one row per aircraft, in order; a row wins over `roles`
    aircraft_position: Optional[GeoPoint] = None
    aircraft_positions: list[GeoPoint] = Field(default_factory=list, max_length=PLAN_MAX_AIRCRAFT)
    speed_mps: Optional[float] = Field(default=None, gt=0, le=1000, allow_inf_nan=False)
    altitude_m: Optional[float] = Field(default=None, gt=0, le=10000, allow_inf_nan=False)
    place_name: Optional[str] = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def check_ids(self) -> "QuickRequest":
        if any(not re.fullmatch(r"px4-[1-9][0-9]{0,2}", item) for item in self.aircraft_ids) or len(set(self.aircraft_ids)) != len(self.aircraft_ids):
            raise ValueError("aircraft_ids must be unique PX4 aircraft ids such as px4-1")
        if any(role not in ("decoy", "standby") for role in self.roles):
            raise ValueError("roles must be decoy or standby (use the aircraft rows to choose a primary)")
        if self.where is None and not self.points:
            raise ValueError("Choose a place: click the map, search, or use the aircraft's position.")
        return self


class QuickError(ValueError):
    def __init__(self, issues: list[PlanIssue]):
        super().__init__(issues[0].message if issues else "The quick mission could not be built.")
        self.issues = issues


def _issue(code: str, message: str, field: Optional[str] = None) -> PlanIssue:
    return PlanIssue(code=code, message=message, field=field)


def suggested_name(mission_type: str, place: Optional[str], where: GeoPoint) -> str:
    label = QUICK_TYPES[mission_type]["label"]
    spot = (place or "").strip() or f"{where.latitude:.4f}, {where.longitude:.4f}"
    return f"{label} · {spot}"[:120]


def _shift(point: GeoPoint, east_m: float, north_m: float) -> GeoPoint:
    latitude, longitude = from_enu((point.latitude, point.longitude), east_m, north_m)
    return GeoPoint(latitude=round(latitude, 7) + 0.0, longitude=round(longitude, 7) + 0.0)      # + 0.0: never a negative zero (a browser sends -0.0 back as 0)


def _centroid(points: list[GeoPoint]) -> GeoPoint:
    return GeoPoint(latitude=sum(p.latitude for p in points) / len(points), longitude=sum(p.longitude for p in points) / len(points))


def _round_up(value: float, step: float) -> float:
    return math.ceil(value / step) * step


def _aircraft_parameters(kind: str, where: GeoPoint, size_m: float, altitude_m: float, spacing_m: float,
                         points: Optional[list[GeoPoint]] = None, drop_index: Optional[int] = None) -> TaskParameters:
    if kind == "coverage_survey":
        return TaskParameters(line_spacing_m=spacing_m, heading_deg=0.0)
    if kind == "expanding_square_search":
        return TaskParameters(datum=where, track_spacing_m=spacing_m, search_radius_m=spiral_search_radius_m(size_m), heading_deg=0.0)
    if kind == "perimeter_patrol":
        return TaskParameters(laps=1)
    if kind == "payload_dropoff":
        if points:
            index = len(points) - 1 if drop_index is None else min(drop_index, len(points) - 1)
            return TaskParameters(points=points, drop_index=index, drop_point=points[index], drop_altitude_m=min(DROP_ALTITUDE_M, altitude_m),
                                  hover_duration_s=DROP_HOVER_S, payload_category="relief_supplies")
        return TaskParameters(drop_point=where, drop_altitude_m=min(DROP_ALTITUDE_M, altitude_m), hover_duration_s=DROP_HOVER_S, payload_category="relief_supplies")
    return TaskParameters(points=list(points or [where]))        # waypoint_route


# --- the speed rule ----------------------------------------------------------------------------------------------------------

def safe_speed(waypoints: list[Any], limits: PlanningLimits) -> dict[str, Any]:
    """The highest speed the route can safely fly: min(ceiling, sqrt(lateral acceleration x tightest corner radius)).

    Lateral acceleration = min(profile acceleration, g x tan(profile tilt)). At each interior vertex with turn angle theta between legs L1 and L2 the
    corner arc tangent to both legs has radius (min(L1, L2) / 2) / tan(theta / 2); PX4 switches legs inside the acceptance radius, so a corner is never
    taken tighter than that radius. A turn-around (theta = 180 degrees) therefore uses the acceptance radius. The ceiling is the configured maximum speed.
    """
    accel = min(float(config.LONG_RANGE_ACCEL_MPS2), GRAVITY_MPS2 * math.tan(math.radians(float(config.LONG_RANGE_TILT_DEG))))
    accept = float(config.LONG_RANGE_ACCEPTANCE_M)
    ceiling = float(limits.max_speed_mps)
    origin = (waypoints[0].latitude, waypoints[0].longitude)
    pts: list[tuple[float, float]] = []
    for item in waypoints:
        east, north, _ = to_enu(origin, item.latitude, item.longitude)
        if not pts or math.hypot(east - pts[-1][0], north - pts[-1][1]) > 1.0:
            pts.append((east, north))
    worst: Optional[tuple[float, float]] = None
    for i in range(1, len(pts) - 1):
        ax, ay = pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]
        bx, by = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
        l1, l2 = math.hypot(ax, ay), math.hypot(bx, by)
        theta = math.acos(max(-1.0, min(1.0, (ax * bx + ay * by) / (l1 * l2))))
        if theta < math.radians(1.0):
            continue
        radius = (min(l1, l2) / 2.0) / math.tan(theta / 2.0) if theta < math.pi - 1e-3 else 0.0
        radius = max(radius, accept)
        speed = math.sqrt(accel * radius)
        if worst is None or speed < worst[0]:
            worst = (speed, radius)
    corner = worst[0] if worst else math.inf
    value = max(float(limits.min_speed_mps), math.floor(min(ceiling, corner) * 2.0) / 2.0)
    return {"speed_mps": value, "ceiling_mps": ceiling, "corner_limit_mps": None if worst is None else round(corner, 1),
            "tightest_corner_radius_m": None if worst is None else round(worst[1], 1), "lateral_accel_mps2": round(accel, 2),
            "acceptance_radius_m": accept, "limited_by": "corner" if corner < ceiling else "ceiling"}


# --- multi-aircraft derivation ---------------------------------------------------------------------------------------------

DECOY_LAYER_STEP_M = 5.0           # decoys beyond the lanes one altitude layer holds fly this much higher per extra layer
MAX_LANES_PER_SIDE = 400


def _flown_enu(route: Any, start: GeoPoint) -> tuple[tuple[float, float], list[tuple[float, float]]]:
    """The primary's flown waypoints (without take-off and landing) in a local east/north frame around `start`."""
    origin = (start.latitude, start.longitude)
    pts: list[tuple[float, float]] = []
    for item in route.waypoints[1:-1]:
        east, north, _ = to_enu(origin, item.latitude, item.longitude)
        if math.hypot(east, north) < 0.5 or (pts and math.hypot(east - pts[-1][0], north - pts[-1][1]) < 1.0):
            continue
        pts.append((east, north))
    return origin, pts


def _shifted_path(origin: tuple[float, float], pts: list[tuple[float, float]], side: float, distance: float) -> list[GeoPoint]:
    """The flown waypoints shifted sideways by `distance` (left of the travel direction for side +1, right for -1)."""
    out = []
    for i, (east, north) in enumerate(pts):
        nxt, prv = pts[min(i + 1, len(pts) - 1)], pts[max(i - 1, 0)]
        dx, dy = (nxt[0] - prv[0], nxt[1] - prv[1]) if len(pts) > 1 else (east, north)
        length = math.hypot(dx, dy) or 1.0
        latitude, longitude = from_enu(origin, east - dy / length * distance * side, north + dx / length * distance * side)
        out.append(GeoPoint(latitude=round(latitude, 7) + 0.0, longitude=round(longitude, 7) + 0.0))
    return out


def _decimate(points: list[GeoPoint]) -> list[GeoPoint]:
    if len(points) <= MAX_ROUTE_POINTS:
        return points
    step = (len(points) - 1) / (MAX_ROUTE_POINTS - 1)
    return [points[round(index * step)] for index in range(MAX_ROUTE_POINTS)]


def decoy_lanes(route: Any, start: GeoPoint, area: OperatingArea) -> list[dict[str, Any]]:
    """Every parallel lane the area has room for, in the order decoys take them.

    The side that keeps more of the path inside the area comes first: 25 m, 50 m, 75 m ... on that side until a lane no longer has any point inside
    the area, then the same lanes on the other side. When not even a 25 m lane fits, one narrower lane (12.5 m, then 10 m) is tried. Decoys beyond the
    number of lanes reuse the lanes on a higher altitude layer (+5 m per layer), so no two decoys ever fly the identical path.
    """
    origin, pts = _flown_enu(route, start)
    if not pts:
        return []

    def inside_of(side: float, distance: float) -> list[GeoPoint]:
        return [point for point in _shifted_path(origin, pts, side, distance) if point_in_area(area, point)]

    inner = max((1.0, -1.0), key=lambda side: len(inside_of(side, DECOY_OFFSET_M)))
    lanes: list[dict[str, Any]] = []
    for side in (inner, -inner):
        for magnitude in range(1, MAX_LANES_PER_SIDE + 1):
            distance = DECOY_OFFSET_M * magnitude
            inside = inside_of(side, distance)
            if not inside:
                break
            lanes.append({"side": side, "distance_m": distance, "points": _decimate(inside)})
    if not lanes:
        for distance in (DECOY_OFFSET_M / 2.0, 10.0):
            inside = inside_of(inner, distance)
            if inside:
                lanes.append({"side": inner, "distance_m": distance, "points": _decimate(inside)})
                break
    return lanes


# --- the plain-language explanation ------------------------------------------------------------------------------------------

def _hlen(a: Any, b: Any) -> float:
    return geodesic_m((a.latitude, a.longitude), (b.latitude, b.longitude))


def _km_min(length_m: float, seconds: float) -> str:
    distance = f"{length_m:.0f} m" if length_m < 1000 else f"{length_m / 1000:.1f} km"
    return f"{distance}, about {seconds / 60:.0f} min" if seconds >= 90 else f"{distance}, about {seconds:.0f} s"


def describe_route(key: str, aircraft: AircraftPlan, route: Any, size_m: float, spacing: Optional[float], role: str = "primary") -> list[str]:
    """Ordered plain-language steps built from the ACTUAL generated route (real distances, times and counts)."""
    speed = float(aircraft.speed_mps or 1.0)
    waypoints = list(route.waypoints)
    start = waypoints[0]
    alt = float(aircraft.altitude_m or 0)
    if aircraft.task_type == "ground_standby":
        return ["Stay on the ground at its start. It is not armed or flown."]
    body = waypoints[1:-1]
    while len(body) > 1 and _hlen(body[-1], start) < 0.5 and body[-1].action == "waypoint":
        body = body[:-1]
    if not body:
        return [f"Take off to {alt:g} m and land."]
    transit = _hlen(start, body[0])
    pattern_len = sum(_hlen(body[i], body[i + 1]) for i in range(len(body) - 1))
    hovers = sum(item.hover_s for item in body)
    back = _hlen(body[-1], start)
    steps = [f"Take off and climb to {alt:g} m."]
    t_pattern = pattern_len / speed + hovers

    def go(where_to: str) -> list[str]:
        return [f"Fly {_km_min(transit, transit / speed)} to {where_to}."] if transit >= 5 else []
    if role == "decoy":
        key = "decoy"
    if key == "area_survey":
        steps += go("the survey circle") + [f"Fly about {max(1, len(body) // 2)} parallel tracks{f' {spacing:g} m apart' if spacing else ''} over the {size_m:g} m circle ({_km_min(pattern_len, t_pattern)})."]
    elif key == "search_spiral":
        steps += go("the datum (the centre of the search)") + [f"Fly an expanding square spiral{f', {spacing:g} m between tracks' if spacing else ''}, covering about {spiral_search_radius_m(size_m) / 2:.0f} m on each side of the datum ({_km_min(pattern_len, t_pattern)})."]
    elif key == "perimeter_patrol":
        steps += go("the circle edge") + [f"Fly one lap of the {size_m:g} m circle ({_km_min(pattern_len, t_pattern)})."]
    elif key == "relief_delivery":
        drop = next((item for item in body if item.action == "payload_release_point"), body[-1])
        steps += go("the drop point") + [f"Descend to {drop.altitude_m:g} m and hover {drop.hover_s:g} s. The supply drop is simulated: an event is logged and nothing is released."]
    elif key == "decoy":
        steps.append(f"Fly a parallel path beside the primary route through {len(body)} waypoint{'s' if len(body) != 1 else ''} ({_km_min(transit + pattern_len, (transit + pattern_len) / speed)}). Simulated decoy.")
    else:   # waypoint_route and waypoints_then_drop
        flown = [item for item in body if item.action != "payload_release_point"]
        steps.append(f"Fly through {len(flown)} waypoint{'s' if len(flown) != 1 else ''} in order ({_km_min(transit + pattern_len, (transit + pattern_len) / speed + hovers)}).")
        drop_at = next((i for i, item in enumerate(body) if item.action == "payload_release_point"), None)
        if drop_at is not None:
            order = sum(1 for item in body[:drop_at] if item.action != "payload_release_point")
            steps.append(f"At waypoint {order} descend to {body[drop_at].altitude_m:g} m and hover {body[drop_at].hover_s:g} s. The drop is simulated: an event is logged, nothing is released.")
    steps.append(f"Return {_km_min(back, back / speed)} to the start and land." if aircraft.return_mode == "return_to_start" else "Land at the last point.")
    steps.append(f"Total {route.length_m / 1000:.2f} km, about {route.duration_s / 60:.1f} min, {len(waypoints)} waypoints.")
    return steps


def aircraft_line(name: str, role: str, route: Any, speed: float, altitude: float, lane: Optional[dict[str, Any]] = None) -> str:
    """One plain sentence saying exactly what this aircraft will do (built from its real generated route)."""
    if role == "standby":
        return f"{name} (standby) stays on the ground at its start. It is never armed or flown."
    amount = _km_min(route.length_m, route.duration_s)
    if role == "decoy":
        layer = f", {lane['layer'] * DECOY_LAYER_STEP_M:g} m higher than the first layer" if lane and lane.get("layer") else ""
        beside = f"about {lane['distance_m']:g} m beside the primary route{layer}" if lane else "beside the primary route"
        return f"{name} (decoy) flies a parallel path {beside}: {amount} at {speed:g} m/s and {altitude:g} m."
    return f"{name} (primary) flies the mission route: {amount} at {speed:g} m/s and {altitude:g} m."


def plan_fingerprint(plan: dict) -> str:
    """A hash of the fields Quick Mission controls: the area and, per aircraft, role, task, parameters, start, altitude, speed and return mode.

    Limits, notes, confirmations and the generated routes are not part of it (the routes follow from these fields). A quick source is kept on a saved
    version only while this fingerprint equals the one stored with it, i.e. while the quick-controlled fields are unchanged.
    """
    import hashlib
    import json

    def plain(value: Any) -> Any:
        # A negative zero and a zero are the same number, but JSON writes them differently and a browser turns -0.0 into 0.
        if isinstance(value, float):
            return value + 0.0
        if isinstance(value, dict):
            return {key: plain(item) for key, item in value.items()}
        if isinstance(value, list):
            return [plain(item) for item in value]
        return value

    plan = plain(plan)
    area = plan.get("operating_area") or {}
    body = {"area": {"kind": area.get("kind"), "centre": area.get("centre"), "radius_m": area.get("radius_m"), "vertices": area.get("vertices")},
            "aircraft": [{key: item.get(key) for key in ("uav_id", "role", "task_type", "parameters", "start_location", "altitude_m", "speed_mps", "return_mode")}
                         for item in plan.get("aircraft", [])]}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _aircraft_ids(request: QuickRequest, count: int) -> list[str]:
    """The ids of the aircraft the plan uses: the running aircraft the client named first, then the smallest unused px4-N. Planning never needs a running fleet."""
    ids = list(request.aircraft_ids[:count])
    used = set(ids)
    number = 1
    while len(ids) < count:
        candidate = f"px4-{number}"
        if candidate not in used:
            ids.append(candidate)
            used.add(candidate)
        number += 1
    return ids


def _group_warnings(items: list[PlanIssue]) -> list[PlanIssue]:
    """With many aircraft the same warning would repeat once per aircraft: show it once with a count."""
    out: list[PlanIssue] = []
    by_code: dict[str, list[PlanIssue]] = {}
    for item in items:
        by_code.setdefault(item.code, []).append(item)
    for code, group in by_code.items():
        if len(group) <= 3:
            out += group
        else:
            first = group[0]
            out.append(PlanIssue(code=code, message=f"{first.message} The same applies to {len(group) - 1} more aircraft.", uav_id=first.uav_id, field=first.field))
    return out


def build_quick_plan(request: QuickRequest, limits: Optional[PlanningLimits] = None) -> dict[str, Any]:
    """Returns {plan, summary, warnings, derived, suggested_name}; raises QuickError with structured issues."""
    limits = limits or PlanningLimits.from_config()
    spec = QUICK_TYPES.get(request.mission_type)
    if spec is None:
        raise QuickError([_issue("unknown_mission_type", f"Unknown mission type {request.mission_type!r}.", "mission_type")])
    count = request.drone_count
    if count > PLAN_MAX_AIRCRAFT:
        raise QuickError([_issue("too_many_aircraft", f"Aircraft ids go up to px4-{PLAN_MAX_AIRCRAFT}, so one plan holds at most {PLAN_MAX_AIRCRAFT} aircraft.", "drone_count")])
    defaults = quick_defaults(limits)
    multi_point = spec["where"] == "points"
    points = list(request.points) if multi_point else []
    if multi_point and not points and request.where is not None:
        points = [request.where]
    where = (request.where if request.where is not None else points[0]) if not multi_point else _centroid(points)
    positions = list(request.aircraft_positions) or ([request.aircraft_position] if request.aircraft_position else [])
    size = float(request.size_m if request.size_m is not None else defaults["size_m"])
    typed_speed = request.speed_mps is not None
    altitude = float(request.altitude_m if request.altitude_m is not None else defaults["altitude_m"])
    ids = _aircraft_ids(request, count)
    rows = list(request.aircraft[:count])
    while len(rows) < count:
        rows.append(QuickAircraft())
    names = [(row.name or "").strip() or f"UAV-{index + 1}" for index, row in enumerate(rows)]
    roles: list[str] = []
    for index, row in enumerate(rows):
        legacy = request.roles[index - 1] if index >= 1 and index - 1 < len(request.roles) else None
        roles.append(row.role or legacy or ("primary" if index == 0 else DEFAULT_EXTRA_ROLE))
    problems: list[PlanIssue] = []
    if spec["where"] == "centre" and not MIN_SIZE_M <= size <= MAX_SIZE_M:
        problems.append(_issue("size_out_of_range", f"The size must be between {MIN_SIZE_M:g} and {MAX_SIZE_M:g} m (a quick mission); use a waypoint route for longer legs.", "size_m"))
    if typed_speed and not limits.min_speed_mps <= float(request.speed_mps) <= limits.max_speed_mps:
        problems.append(_issue("speed_out_of_range", f"Speed must be between {limits.min_speed_mps:g} and {limits.max_speed_mps:g} m/s.", "speed_mps"))
    if not limits.min_altitude_m <= altitude <= limits.max_altitude_m:
        problems.append(_issue("altitude_out_of_range", f"Altitude must be between {limits.min_altitude_m:g} and {limits.max_altitude_m:g} m.", "altitude_m"))
    for index, row in enumerate(rows):
        where_field = f"aircraft.{index}"
        if row.altitude_m is not None and not limits.min_altitude_m <= row.altitude_m <= limits.max_altitude_m:
            problems.append(PlanIssue(code="aircraft_altitude_out_of_range", uav_id=ids[index], field=f"{where_field}.altitude_m",
                                      message=f"{names[index]}: altitude must be between {limits.min_altitude_m:g} and {limits.max_altitude_m:g} m."))
        if row.speed_mps is not None and not limits.min_speed_mps <= row.speed_mps <= limits.max_speed_mps:
            problems.append(PlanIssue(code="aircraft_speed_out_of_range", uav_id=ids[index], field=f"{where_field}.speed_mps",
                                      message=f"{names[index]}: speed must be between {limits.min_speed_mps:g} and {limits.max_speed_mps:g} m/s."))
    seen_names: dict[str, int] = {}
    for index, label in enumerate(names):
        key = label.lower()
        if key in seen_names:
            problems.append(PlanIssue(code="duplicate_name", uav_id=ids[index], field=f"aircraft.{index}.name",
                                      message=f"{names[seen_names[key]]} and aircraft {index + 1} have the same name ({label}). Give each aircraft its own name."))
        else:
            seen_names[key] = index
    if "primary" not in roles:
        problems.append(_issue("primary_count", "At least one aircraft must be the primary. Choose a primary in the aircraft list.", "aircraft.0.role"))
    if spec["start"] == "aircraft" and not positions:
        problems.append(_issue("aircraft_position_missing", f"{spec['label']} starts from the aircraft: no aircraft position is known. Connect and verify an aircraft first.", "aircraft_position"))
    if spec["where"] != "centre" and request.where_source == "aircraft":
        problems.append(_issue("destination_is_aircraft", f"{spec['label']} needs a destination: click a point on the map or search a place.", "where"))
    if request.mission_type == "waypoints_then_drop" and request.drop_index is not None and request.drop_index >= len(points):
        problems.append(_issue("drop_index_out_of_range", "The marked drop waypoint is not one of the clicked points.", "drop_index"))
    if problems:
        raise QuickError(problems)

    if multi_point:
        radius = max(100.0, _round_up(max(geodesic_m((where.latitude, where.longitude), (p.latitude, p.longitude)) for p in points) + 50.0, 50.0))
    else:
        radius = size
    area = OperatingArea(kind="circle", centre=where, radius_m=radius)
    kind = spec["task"]
    spacing = track_spacing_m(altitude)
    warnings: list[PlanIssue] = []

    target = points[0] if points else where
    far_m = geodesic_m((positions[0].latitude, positions[0].longitude), (target.latitude, target.longitude)) if positions else 0.0
    if spec["start"] == "aircraft" and far_m > FAR_AIRCRAFT_M:
        # The simulated aircraft is far away: the plan starts near the place instead, so the existing relocation action has a mission start to move it to.
        positions = [_shift(target, 0.0, -FAR_START_OFFSET_M)]

    def grid(index: int) -> tuple[float, float]:
        """Default offset of aircraft `index` from the first aircraft: 4 m north each, a new column 4 m east after every START_GRID_ROWS aircraft."""
        return START_OFFSET_M * (index // START_GRID_ROWS), START_OFFSET_M * (index % START_GRID_ROWS)

    def start_for(index: int) -> GeoPoint:
        east, north = grid(index)
        typed = rows[index].start_offset_m
        if spec["start"] == "where":
            return _shift(where, east, typed if typed is not None else north) if (east or (typed if typed is not None else north)) else where
        if index < len(positions):                      # a real aircraft position is used as is, unless the user typed an offset
            return positions[index] if typed is None else _shift(positions[index], 0.0, typed)
        return _shift(positions[0], east, typed if typed is not None else north)

    def altitude_of(index: int) -> float:
        return float(rows[index].altitude_m) if rows[index].altitude_m is not None else altitude

    def aircraft_item(index: int, role: str, current_spacing: float, speed: float, flight_altitude: Optional[float] = None) -> AircraftPlan:
        height = altitude_of(index) if flight_altitude is None else flight_altitude
        return AircraftPlan(
            uav_id=ids[index], callsign=(rows[index].name or "").strip() or None, role=role, priority=1 if role == "primary" and index == roles.index("primary") else max(2, min(index + 1, 3)),
            reassignable=True, task_type=kind, parameters=_aircraft_parameters(kind, where, size, height, current_spacing, points or None, request.drop_index),
            start_location=start_for(index), altitude_m=height, speed_mps=speed, loiter_duration_s=0.0, return_mode="return_to_start")

    primaries = sum(1 for role in roles if role == "primary")

    def assemble(aircraft: list[AircraftPlan]) -> MissionPlan:
        return MissionPlan(
            objectives=list(spec["objectives"]), operating_area=area, required_uav_count=len(ids), selected_uav_ids=list(ids), activated_uav_ids=list(ids),
            aircraft=aircraft, workflow_stage="aircraft", current_uav_index=0, allow_multiple_primary=primaries > 1,
            auto_limits=AutoLimits(max_distance_from_start_m=True, max_altitude_m=True, max_duration_minutes=True))

    def route_of(plan: MissionPlan, item: AircraftPlan):
        found, route = validate_aircraft(item.model_copy(update={"route": None}), plan, limits, require_route=False)
        return [entry for entry in found if entry.code != "route_too_far"], route

    lead = roles.index("primary")
    # 1. the lead primary's route at the research speed (the geometry does not depend on speed), widening the spacing only if the waypoint cap needs it
    attempt, primary = spacing, None
    for _ in range(40):
        primary = aircraft_item(lead, "primary", attempt, float(defaults["speed_mps"]))
        issues, route = route_of(assemble([primary]), primary)
        if issues and all(entry.code == "waypoint_cap" for entry in issues) and kind in ("coverage_survey", "expanding_square_search") and attempt < 1000:
            attempt = min(1000.0, math.ceil(attempt * 1.25 / SPACING_STEP_M) * SPACING_STEP_M)
            continue
        if issues:
            raise QuickError(issues)
        break
    assert primary is not None and route is not None
    if attempt != spacing:
        warnings.append(_issue("spacing_widened", f"The track spacing was widened from {spacing:g} m to {attempt:g} m to stay within the {limits.max_waypoints}-waypoint limit.", "line_spacing_m"))
    # 2. the speed: the one the user typed, or the highest the route can safely fly
    info = safe_speed(route.waypoints, limits)
    speed = float(request.speed_mps) if typed_speed else float(info["speed_mps"])

    def speed_of(index: int) -> float:
        return float(rows[index].speed_mps) if rows[index].speed_mps is not None else speed

    primary = aircraft_item(lead, "primary", attempt, speed_of(lead))
    lead_plan = assemble([primary])
    issues, lead_route = route_of(lead_plan, primary)
    if issues:
        raise QuickError(issues)
    lead_start = start_for(lead)
    lanes = decoy_lanes(lead_route, lead_start, area) if "decoy" in roles else []
    if "decoy" in roles and not lanes:
        decoy_at = roles.index("decoy")
        raise QuickError([PlanIssue(code="decoy_path_unavailable", uav_id=ids[decoy_at], field=f"aircraft.{decoy_at}.role",
                                    message=f"{names[decoy_at]} (decoy): no parallel path fits inside the area. Enlarge the area or choose standby.")])
    # 3. every aircraft, in order
    aircraft: list[AircraftPlan] = []
    lane_info: dict[str, dict[str, Any]] = {}
    decoy_ordinal = 0
    for index in range(count):
        role = roles[index]
        if index == lead:
            aircraft.append(primary.model_copy(update={"route": lead_route}))
            continue
        if role == "primary":
            item = aircraft_item(index, "primary", attempt, speed_of(index))
        elif role == "standby":
            item = AircraftPlan(uav_id=ids[index], callsign=(rows[index].name or "").strip() or None, role="standby", priority=max(2, min(index + 1, 3)), reassignable=True, task_type="ground_standby",
                                parameters=TaskParameters(), start_location=start_for(index), altitude_m=altitude_of(index), speed_mps=speed_of(index), loiter_duration_s=0.0, return_mode="land_at_destination")
        else:
            layer, lane_index = divmod(decoy_ordinal, len(lanes))
            decoy_ordinal += 1
            lane = {**lanes[lane_index], "layer": layer}
            height = altitude_of(index) if rows[index].altitude_m is not None else altitude + DECOY_LAYER_STEP_M * layer
            lane_info[ids[index]] = lane
            item = AircraftPlan(uav_id=ids[index], callsign=(rows[index].name or "").strip() or None, role="decoy", priority=max(2, min(index + 1, 3)), reassignable=True, task_type="waypoint_route",
                                parameters=TaskParameters(points=lane["points"]), start_location=start_for(index), altitude_m=height, speed_mps=speed_of(index), loiter_duration_s=0.0, return_mode="return_to_start")
        found, other = route_of(assemble(aircraft + [item]), item)
        if found:
            raise QuickError([PlanIssue(code=entry.code, message=f"{names[index]} ({role}): {entry.message}", field=entry.field, uav_id=ids[index]) for entry in found])
        aircraft.append(item.model_copy(update={"route": other}))
    # 4. no two aircraft may start at the same point or fly the identical path
    shared: list[PlanIssue] = []
    starts: dict[tuple[float, float], int] = {}
    flown: dict[tuple, int] = {}
    for index, item in enumerate(aircraft):
        begin = item.start_location
        if begin is not None:
            key = (round(begin.latitude, 5), round(begin.longitude, 5))                  # about a metre
            if key in starts:
                shared.append(PlanIssue(code="start_conflict", uav_id=item.uav_id, field=f"aircraft.{index}.start_offset_m",
                                        message=f"{names[starts[key]]} and {names[index]} start at the same point. Give one of them a different start offset."))
            else:
                starts[key] = index
        if item.route is not None and item.role != "standby":
            # The flown pattern: take-off, landing and the return to this aircraft's own start do not count (two aircraft starting 4 m apart fly the same pattern).
            path = tuple((round(w.latitude, 6), round(w.longitude, 6), round(w.altitude_m, 1)) for w in item.route.waypoints[1:-1]
                         if begin is None or distance_m((begin.latitude, begin.longitude), (w.latitude, w.longitude)) > 1.5)
            if path in flown:
                shared.append(PlanIssue(code="identical_path", uav_id=item.uav_id, field=f"aircraft.{index}.altitude_m",
                                        message=f"{names[flown[path]]} and {names[index]} would fly the identical path. Give one of them a different altitude, make it a decoy or a standby."))
            else:
                flown[path] = index
    if shared:
        raise QuickError(shared)
    # 5. derive and apply the limits from all routes, then validate the finished plan; every aircraft is confirmed (its route came from a documented rule)
    plan, derived = apply_auto_limits(assemble(aircraft), limits)
    plan = plan.model_copy(update={"aircraft": [item.model_copy(update={"confirmed": True}) for item in plan.aircraft], "workflow_stage": "review"})
    final = validate_plan(plan, limits, complete=True)
    if final:
        raise QuickError(final)
    collected: list[PlanIssue] = []
    for item in plan.aircraft:
        collected += plan_warnings(item, plan, limits, item.route)
    warnings += _group_warnings(collected)
    first = plan.aircraft[lead]
    route = first.route
    assert route is not None
    if far_m > FAR_AIRCRAFT_M:
        warnings.append(_issue("aircraft_far", f"The aircraft is {far_m / 1000:.0f} km from this place. The mission starts near the place; use \"Restart at mission start\" (Mission Control, or Scenario Lab after saving) to bring the simulation here.", "where"))
    capacity = int(config.SIM_MAX_AIRCRAFT)
    if count > capacity:
        warnings.append(_issue("exceeds_simulation_capacity", capacity_message(count, capacity), "drone_count"))
    research = float(defaults["speed_mps"])
    speed_info = {**info, "speed_mps": speed, "mode": "typed" if typed_speed else "derived", "derived_speed_mps": info["speed_mps"], "research_speed_mps": research,
                  "above_validated": speed > research + 1e-9}
    if speed_info["above_validated"]:
        warnings.append(_issue("speed_above_validated", f"{speed:g} m/s: the threat monitor is validated only at {research:g} m/s.", "speed_mps"))
    explanation = []
    rows_out = []
    for index, item in enumerate(plan.aircraft):
        if item.route is None:
            continue
        lane = lane_info.get(item.uav_id)
        line = aircraft_line(names[index], item.role or "primary", item.route, float(item.speed_mps or 0), float(item.altitude_m or 0), lane)
        explanation.append({"uav_id": item.uav_id, "name": names[index], "role": item.role, "task_type": item.task_type, "line": line,
                            "steps": describe_route(request.mission_type, item, item.route, radius, attempt, item.role or "primary")})
        east, north = grid(index)
        rows_out.append({"uav_id": item.uav_id, "name": names[index], "role": item.role, "task_type": item.task_type, "length_m": item.route.length_m, "duration_s": item.route.duration_s,
                         "waypoints": len(item.route.waypoints), "altitude_m": item.altitude_m, "speed_mps": item.speed_mps, "line": line,
                         "start_offset_m": rows[index].start_offset_m if rows[index].start_offset_m is not None else north, "layer": (lane or {}).get("layer", 0)})
    summary = {
        "mission_type": request.mission_type, "label": spec["label"], "area": {"kind": "circle", "radius_m": radius, "centre": where.model_dump()},
        "start": first.start_location.model_dump() if first.start_location else None, "drones": len(plan.aircraft), "aircraft_ids": ids,
        "roles": [item.role for item in plan.aircraft], "speed_mps": speed, "altitude_m": altitude, "estimated_s": route.duration_s, "length_m": route.length_m,
        "waypoints": len(route.waypoints), "track_spacing_m": attempt if kind in ("coverage_survey", "expanding_square_search") else None,
        "limits": {"max_distance_from_start_m": plan.constraints.max_distance_from_start_m, "max_altitude_m": plan.constraints.max_altitude_m, "max_duration_minutes": plan.max_duration_minutes},
        "confirmed": True, "speed_info": speed_info, "explanation": explanation, "simulation_capacity": capacity,
        "points": [p.model_dump() for p in points], "drop_index": (len(points) - 1 if request.drop_index is None else request.drop_index) if request.mission_type == "waypoints_then_drop" else None,
        "aircraft": rows_out, "decoy_lanes": len(lanes),
    }
    plan_json = plan.model_dump(mode="json")
    summary["fingerprint"] = plan_fingerprint(plan_json)
    return {"plan": plan_json, "summary": summary, "warnings": [item.model_dump() for item in warnings], "derived": derived,
            "suggested_name": suggested_name(request.mission_type, request.place_name, where)}


def capacity_options(count: int, capacity: int) -> list[dict[str, Any]]:
    """The three things a user can do when a mission needs more aircraft than this machine runs (shown as buttons next to the sentence)."""
    return [{"id": "start_first", "label": f"Start the first {capacity} aircraft", "aircraft": capacity},
            {"id": "activate_fewer", "label": "Activate fewer aircraft", "aircraft": None},
            {"id": "plan_only", "label": "Deploy as a plan only", "aircraft": 0}]


def capacity_message(count: int, capacity: int) -> str:
    """The one sentence every place uses when a mission needs more aircraft than this machine runs reliably."""
    return (f"This mission has {count} aircraft, this machine runs up to {capacity} reliably. "
            f"You can start the first {capacity} aircraft, activate fewer, or still deploy as a plan only.")


# --- the assistant's deterministic parser ------------------------------------------------------------------------------------

ONES = {"zero": 0, "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
        "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
NUMBER_WORD = r"(?:" + "|".join(sorted(list(ONES) + list(TENS) + ["hundred", "thousand", "and", "half"], key=len, reverse=True)) + r")"
NUMBER = rf"(?:\d+(?:[.,]\d+)?|{NUMBER_WORD}(?:[\s-]+{NUMBER_WORD})*)"
UNIT_M = r"(?:km|kilomet(?:er|re)s?|k\b|m\b|met(?:er|re)s?)"


def words_to_number(text: str) -> Optional[float]:
    text = text.lower().replace(",", ".").strip()
    try:
        return float(text)
    except ValueError:
        pass
    total, current, seen = 0.0, 0.0, False
    for token in re.split(r"[\s-]+", text):
        if token in ("and", ""):
            continue
        if token == "half":
            current += 0.5
        elif token in ONES:
            current += ONES[token]
        elif token in TENS:
            current += TENS[token]
        elif token == "hundred":
            current = (current or 1) * 100
        elif token == "thousand":
            total += (current or 1) * 1000
            current = 0.0
        else:
            return None
        seen = True
    return total + current if seen else None


def _to_metres(value: float, unit: str) -> float:
    return value * 1000.0 if unit.lower().startswith(("k", "kilo")) else value


class ParseState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mission_type: Optional[str] = Field(default=None, max_length=32)
    size_m: Optional[float] = Field(default=None, gt=0, le=1e9, allow_inf_nan=False)
    drone_count: Optional[int] = Field(default=None, ge=1, le=100000)
    decoys: Optional[int] = Field(default=None, ge=0, le=100000)                        # "2 decoys": that many of the other aircraft are decoys
    standbys: Optional[int] = Field(default=None, ge=0, le=100000)                      # "3 standby": that many of the other aircraft stay on the ground
    role_changes: dict[str, str] = Field(default_factory=dict, max_length=1000)         # "make UAV-7 standby": aircraft number -> role
    drone_confirmed: Optional[int] = Field(default=None, ge=1, le=100000)               # the count the user confirmed after the typo guard
    speed_mps: Optional[float] = Field(default=None, gt=0, le=1e6, allow_inf_nan=False)
    altitude_m: Optional[float] = Field(default=None, gt=0, le=1e6, allow_inf_nan=False)
    where_source: Optional[str] = Field(default=None, pattern=r"^(map|place|aircraft)$")
    place_query: Optional[str] = Field(default=None, max_length=80)
    asked: Optional[str] = Field(default=None, max_length=24)          # the one question currently open


class ParseContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available_aircraft: int = Field(default=1, ge=0, le=100000)
    has_aircraft_position: bool = True


class ParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: Optional[str] = Field(default=None, max_length=500)
    choice: Optional[str] = Field(default=None, max_length=60)
    state: ParseState = Field(default_factory=ParseState)
    context: ParseContext = Field(default_factory=ParseContext)


CLICK_WORDS = r"(?:the\s+(?:point|spot|place|location)\s+i\s+click|where\s+i\s+click|point\s+i\s+click|(?:this|that)\s+(?:area|point|spot|place|location)|on\s+the\s+map|i(?:'ll| will)\s+click|i\s+click)"
AIRCRAFT_WORDS = r"(?:\bhere\b|my\s+position|(?:the\s+)?(?:aircraft|drone|uav)(?:'s|s)?\s+(?:current\s+)?(?:position|location)|where\s+the\s+(?:aircraft|drone|uav)\s+is|current\s+position|(?:the\s+)?current\s+location|(?:at|from)\s+the\s+(?:aircraft|drone))"
UNSUPPORTED = [
    (r"\b(follow|track|chase|pursue)\b.*\b(car|vehicle|person|target|man|boat|truck)\b", "waypoint_route", "Following or tracking a moving target is not supported (no tracking capability exists)."),
    (r"\b(photograph|photo|film|video|record|take pictures?)\b", "area_survey", "Camera tasks are not simulated; the closest supported type is an area survey of the place."),
    (r"\b(inspect|inspection|orbit|circle around|hover over)\b", "waypoint_route", "Point inspection is not a quick mission type; a waypoint route can fly to the point."),
    (r"\b(land|take ?off|launch|arm|disarm|return home|return to launch|rtl)\b", None, "I only fill the mission draft; arming, taking off, landing and returning are done in Mission Control."),
    (r"\b(attack|spoof|jam|inject|bomb|strike|weapon)\b", None, "Attacks are designed in the Attack Lab, never in a mission."),
    (r"\b(moon|mars|ocean|across the sea|another country)\b", None, "That destination is outside what the simulator can plan."),
    (r"\b(spray|spraying|crop dust)\b", None, "Spraying is not supported."),
    (r"\b(multiple|several|different)\s+(?:missions|tasks)\b|\band then\b|\bafterwards\b", None, "One quick mission is one task; fill them one at a time."),
]
FORBIDDEN_ACTIONS = r"\b(save|deploy|start|begin|run|execute|abort|pause|resume|send|upload|submit|approve)\b"
QUESTION_START = r"^\s*(what|how|why|who|when|where is|which|explain|tell me|is |are |can |does |do |should |could |would |show me)"


def _detect_types(lowered: str) -> list[str]:
    found = []
    for key, spec in QUICK_TYPES.items():
        if any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in spec["keywords"]):
            found.append(key)
    # "search" inside "search and rescue"/"search area" is a spiral, but "survey ... search" mixes: a survey word wins only when no search word is present
    if "relief_delivery" in found and "search_spiral" in found and not re.search(r"\bsearch\b", lowered):
        found.remove("search_spiral")
    if "waypoint_route" in found and len(found) > 1 and not re.search(r"\b(fly to|go to|waypoint|point to point|point-to-point)\b", lowered):
        found.remove("waypoint_route")
    return found


def _extract(text: str) -> dict[str, Any]:
    lowered = text.lower()
    out: dict[str, Any] = {}
    taken: list[tuple[int, int]] = []

    def claim(match: "re.Match[str]") -> None:
        taken.append(match.span())

    speed = re.search(rf"(?:at|with|speed(?:\s+of)?|going|flying|flies)?\s*({NUMBER})\s*(m/s|mps|metres?\s+per\s+second|meters?\s+per\s+second|km/h|kph|kmh|kilomet(?:er|re)s?\s+per\s+hour)", lowered)
    if speed:
        value = words_to_number(speed.group(1))
        if value is not None:
            out["speed_mps"] = value / 3.6 if re.match(r"km|kilo", speed.group(2)) else value
            claim(speed)
    altitude = (re.search(rf"\b(?:altitude|height|elevation)\s+(?:of\s+)?({NUMBER})\s*({UNIT_M})?", lowered)
                or re.search(rf"\b({NUMBER})\s*({UNIT_M})\s+(?:high|up|altitude|above)", lowered)
                or re.search(rf"\bat\s+({NUMBER})\s*({UNIT_M})\s+(?:altitude|height)", lowered)
                or re.search(rf"\bfly(?:ing)?\s+at\s+({NUMBER})\s*({UNIT_M})\b(?!\s*/)", lowered))
    if altitude:
        value = words_to_number(altitude.group(1))
        if value is not None:
            out["altitude_m"] = _to_metres(value, altitude.group(2) or "m")
            claim(altitude)
    # roles named in the sentence: "make UAV-7 standby", "2 decoys", "three standby" (the user's own choice of roles; nothing is chosen for them)
    changes: dict[str, str] = {}
    for match in re.finditer(r"\b(?:uav|aircraft|drone)[\s-]*(\d{1,3})\s*(?:to|as|into|=|:)?\s*(?:be\s+)?(?:a\s+|an\s+|the\s+)?(primary|decoy|stand-?by)\b", lowered):
        changes[str(int(match.group(1)))] = "standby" if match.group(2).startswith("stand") else match.group(2)
        claim(match)
    if changes:
        out["role_changes"] = changes
    for match in re.finditer(rf"\b({NUMBER})\s+(decoys?|stand-?bys?)\b(?:\s+(?:drones?|uavs?|aircraft))?", lowered):
        if any(match.start() < end and match.end() > start for start, end in taken):
            continue
        number = words_to_number(match.group(1))
        if number is not None and float(number).is_integer() and number >= 0:
            out["decoys" if match.group(2).startswith("decoy") else "standbys"] = int(number)
            claim(match)
    drones = None
    for found in re.finditer(rf"\b({NUMBER})\s+(?:\w+\s+)?(?:drones?|uavs?|aircraft|quadcopters?|vehicles?|copters?)\b", lowered):
        if not any(found.start() < end and found.end() > start for start, end in taken):
            drones = found
            break
    drones = drones or re.search(r"\b(both|a single|single|a pair of)\s+(?:drones?|uavs?|aircraft)\b", lowered)
    if drones:
        token = drones.group(1)
        value = {"both": 2, "a single": 1, "single": 1, "a pair of": 2}.get(token) or words_to_number(token)
        if value is not None and float(value).is_integer():
            out["drone_count"] = int(value)
            claim(drones)
    size = None
    if re.search(r"\bhalf\s+an?\s+kilomet(?:er|re)\b", lowered):
        size = 500.0
    elif re.search(r"\b(?:a|one)\s+kilomet(?:er|re)\b", lowered):
        size = 1000.0
    for match in ([] if size is not None else re.finditer(rf"({NUMBER})\s*({UNIT_M})(?!\s*/|\s*per)", lowered)):
        if any(match.start() >= start and match.end() <= end for start, end in taken):
            continue
        before = lowered[max(0, match.start() - 14):match.start()]
        if re.search(r"(altitude|height)\s+(of\s+)?$", before):
            continue
        value = words_to_number(match.group(1))
        if value is None:
            continue
        size = _to_metres(value, match.group(2))
        break
    if size is not None:
        out["size_m"] = size
    if re.search(CLICK_WORDS, lowered):
        out["where_source"] = "map"
    elif re.search(AIRCRAFT_WORDS, lowered):
        out["where_source"] = "aircraft"
    else:
        name = r"((?:[A-Z][\w'’.-]+)(?:\s+(?:[A-Z][\w'’.-]+|of|de|la))*)"
        place = (re.search(rf"\b(?:in|near|around|at|over|to|of)\s+(?:the\s+)?{name}", text)
                 or re.search(rf"\b(?:[Ss]urvey|[Ss]earch|[Pp]atrol|[Mm]ap|[Cc]over|[Ss]can)\s+(?:the\s+)?{name}", text))
        if place and not re.fullmatch(r"(?i)(uav|drone|aircraft|m|km|here)", place.group(1)):
            out["where_source"], out["place_query"] = "place", place.group(1).strip()[:80]
    return out


def _question(question_id: str, text: str, choices: list[tuple[str, str]]) -> dict[str, Any]:
    return {"id": question_id, "text": text, "choices": [{"id": ident, "label": label} for ident, label in choices]}


def parse_mission_text(request: ParseRequest, limits: Optional[PlanningLimits] = None) -> dict[str, Any]:
    """Pure, offline and stateless: the client sends the state it got last time. Returns the merged state, what was understood, and at most one question."""
    limits = limits or PlanningLimits.from_config()
    defaults = quick_defaults(limits)
    state = request.state.model_copy()
    notes: list[str] = []
    text = (request.text or "").strip()
    lowered = text.lower()

    def result(**extra: Any) -> dict[str, Any]:
        base = {"intent": "mission", "state": state.model_dump(), "understood": understood(), "question": None, "complete": False, "unsupported": None, "notes": notes, "refused": None}
        base.update(extra)
        return base

    def understood() -> list[str]:
        items = []
        if state.mission_type:
            items.append(QUICK_TYPES[state.mission_type]["label"])
        if state.size_m:
            items.append(f"radius {state.size_m:g} m")
        if state.drone_count:
            items.append(f"{state.drone_count} drone{'s' if state.drone_count != 1 else ''}")
        if state.decoys is not None:
            items.append(f"{state.decoys} decoy{'s' if state.decoys != 1 else ''}")
        if state.standbys is not None:
            items.append(f"{state.standbys} standby")
        items += [f"UAV-{number} as {role}" for number, role in sorted(state.role_changes.items(), key=lambda pair: int(pair[0]))]
        if state.speed_mps:
            items.append(f"{state.speed_mps:g} m/s")
        if state.altitude_m:
            items.append(f"{state.altitude_m:g} m altitude")
        if state.where_source == "aircraft":
            items.append("at the aircraft's position")
        elif state.where_source == "map":
            items.append("at the point you click")
        elif state.where_source == "place":
            items.append(f"near {state.place_query}")
        return items

    # a button press is an answer, never free text
    if request.choice:
        kind, _, value = request.choice.partition(":")
        if kind == "type" and value in QUICK_TYPES:
            state.mission_type = value
        elif kind == "where" and value in ("map", "aircraft"):
            state.where_source, state.place_query = value, None
        elif kind == "search" and value:
            state.where_source, state.place_query = "place", value[:80]
        elif kind == "speed" and value in ("max", "default"):
            state.speed_mps = limits.max_speed_mps if value == "max" else defaults["speed_mps"]
        elif kind == "altitude" and value in ("max", "default"):
            state.altitude_m = limits.max_altitude_m if value == "max" else defaults["altitude_m"]
        elif kind == "size" and value in ("max", "default"):
            state.size_m = MAX_SIZE_M if value == "max" else DEFAULT_SIZE_M
        elif kind == "drones" and (value in ("max", "default") or value.isdigit()):
            if value.isdigit():
                state.drone_count = max(1, min(int(value), PLAN_MAX_AIRCRAFT))
                state.drone_confirmed = state.drone_count               # the typo guard asks once; a button answer settles it
            else:
                state.drone_count = PLAN_MAX_AIRCRAFT if value == "max" else 1
        elif kind == "closest" and value in QUICK_TYPES:
            state.mission_type = value
        else:
            notes.append("That option is not available any more.")
        state.asked = None
    elif text:
        if re.search(FORBIDDEN_ACTIONS, lowered) and re.search(r"\b(it|mission|plan|draft|scenario|this|them|flight)\b", lowered) and not _detect_types(lowered) and not state.mission_type:
            return result(intent="refused", refused="I only fill in the mission draft. Saving, deploying and starting a flight are your own clicks: I never do them.")
        if re.match(QUESTION_START, lowered) or (lowered.endswith("?") and not re.search(r"\b(want|need|please|let's|lets)\b", lowered)):
            return result(intent="other")
        for pattern, closest, reason in UNSUPPORTED:
            if re.search(pattern, lowered):
                if closest is None:
                    return result(intent="unsupported", unsupported={"reason": reason, "closest": None})
                if not _detect_types(lowered) or closest not in _detect_types(lowered):
                    return result(intent="unsupported", unsupported={"reason": reason, "closest": closest},
                                  question=_question("closest", f"{reason} Use {QUICK_TYPES[closest]['label']} instead?", [(f"closest:{closest}", f"Use {QUICK_TYPES[closest]['label']}"), ("cancel", "Never mind")]))
        types = _detect_types(lowered)
        fields = _extract(text)
        if not types and not state.mission_type and not fields.get("where_source") and "size_m" not in fields:
            return result(intent="other")
        if len(types) > 1 and not state.mission_type:
            options = [(f"type:{key}", QUICK_TYPES[key]["label"]) for key in types]
            return result(question=_question("type", "Which of these do you mean?", options))
        if types and not state.mission_type:
            state.mission_type = types[0]
        elif types and state.asked == "type":
            state.mission_type = types[0]
        for key in ("size_m", "drone_count", "speed_mps", "altitude_m", "where_source", "place_query", "decoys", "standbys"):
            if key in fields:
                setattr(state, key, fields[key])
        if fields.get("role_changes"):
            state.role_changes = {**state.role_changes, **fields["role_changes"]}
        if "where_source" in fields and fields["where_source"] != "place":
            state.place_query = None
        state.asked = None
        if state.asked is None and not any([types, fields]) and request.state.asked:
            notes.append("I did not catch that.")
    # conflicts first: each is one question
    if state.speed_mps is not None and not limits.min_speed_mps <= state.speed_mps <= limits.max_speed_mps:
        bad = state.speed_mps
        state.speed_mps = None
        return result(question=_question("speed", f"{bad:g} m/s is outside the allowed {limits.min_speed_mps:g}-{limits.max_speed_mps:g} m/s.",
                                         [("speed:max", f"Use {limits.max_speed_mps:g} m/s"), ("speed:default", f"Use the default {defaults['speed_mps']:g} m/s")]))
    if state.altitude_m is not None and not limits.min_altitude_m <= state.altitude_m <= limits.max_altitude_m:
        bad = state.altitude_m
        state.altitude_m = None
        return result(question=_question("altitude", f"{bad:g} m altitude is outside the allowed {limits.min_altitude_m:g}-{limits.max_altitude_m:g} m.",
                                         [("altitude:max", f"Use {limits.max_altitude_m:g} m"), ("altitude:default", f"Use the default {defaults['altitude_m']:g} m")]))
    if state.size_m is not None and not MIN_SIZE_M <= state.size_m <= MAX_SIZE_M:
        bad = state.size_m
        state.size_m = None
        return result(question=_question("size", f"{bad:g} m is outside a quick mission's {MIN_SIZE_M:g}-{MAX_SIZE_M:g} m radius.",
                                         [("size:max", f"Use {MAX_SIZE_M:g} m"), ("size:default", f"Use the default {DEFAULT_SIZE_M:g} m")]))
    if state.drone_count is not None and not 1 <= state.drone_count <= PLAN_MAX_AIRCRAFT:
        bad = state.drone_count
        state.drone_count = None
        return result(question=_question("drones", f"{bad} aircraft is more than one plan can hold (aircraft ids go up to px4-{PLAN_MAX_AIRCRAFT}).",
                                         [("drones:max", f"Use {PLAN_MAX_AIRCRAFT}"), ("drones:default", "Use 1 drone")]))
    if state.drone_count is not None and state.drone_count > TYPO_GUARD_OVER and state.drone_confirmed != state.drone_count:
        # A typo guard only (not a limit): asked once per number.
        guess = max(1, state.drone_count // 10)
        return result(question=_question("drones_typo", f"{state.drone_count} aircraft is a lot. Did you mean {guess}?",
                                         [(f"drones:{guess}", f"Yes, {guess}"), (f"drones:{state.drone_count}", f"No, {state.drone_count}")]))
    # then what is missing, one at a time
    if state.mission_type is None:
        state.asked = "type"
        return result(question=_question("type", "What kind of mission? I can plan:", [(f"type:{key}", spec["label"]) for key, spec in QUICK_TYPES.items()]))
    spec = QUICK_TYPES[state.mission_type]
    if state.where_source == "aircraft" and spec["where"] != "centre":
        state.where_source = None
        notes.append(f"{spec['label']} needs a destination; the aircraft's position is where it starts.")
    if state.where_source is None or (state.where_source == "place" and not state.place_query):
        state.asked = "where"
        choices = [("where:map", "Click the place on the map")]
        if spec["where"] == "centre" and request.context.has_aircraft_position:
            choices.append(("where:aircraft", "Use the aircraft's position"))
        return result(question=_question("where", f"Where should the {spec['label'].lower()} be?" if spec["where"] == "centre" else (f"Where is the {'drop' if state.mission_type == 'relief_delivery' else 'destination'} point?" if spec["where"] == "destination" else "Click the waypoints on the map, in order."), choices))
    if state.where_source == "place":
        return result(question=_question("place", f"Search the map for “{state.place_query}” and click the exact point.",
                                         [(f"search:{state.place_query}", f"Search for “{state.place_query}”"), ("where:map", "Click the place on the map")]), place_pending=True)
    return result(complete=True)
