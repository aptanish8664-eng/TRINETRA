"""Schema-version 2 mission plan: every field here is read by the validator, the route generator,
Mission Control hand-off, or human review. Fields nothing consumes are deliberately absent.

Software-only: a plan describes a route for PX4 SITL / the simulator. It never commands a vehicle.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 2

TaskType = Literal[
    "expanding_square_search", "sector_search", "coverage_survey", "perimeter_patrol",
    "point_inspection", "waypoint_route", "loiter_hold", "payload_dropoff",
]
TASK_TYPES: tuple[str, ...] = (
    "expanding_square_search", "sector_search", "coverage_survey", "perimeter_patrol",
    "point_inspection", "waypoint_route", "loiter_hold", "payload_dropoff",
)
AircraftRole = Literal["primary", "decoy", "standby"]
ReturnMode = Literal["return_to_start", "land_at_destination"]
PayloadCategory = Literal["relief_supplies", "medical_kit", "flotation_device"]
PAYLOAD_CATEGORIES: tuple[str, ...] = ("relief_supplies", "medical_kit", "flotation_device")
Objective = Literal[
    "area_survey", "search_rescue", "perimeter_monitoring", "inspection", "disaster_assessment",
    "agricultural_survey", "mapping_survey", "target_tracking", "environmental_monitoring",
    "custom_mission", "relief_supply_delivery",
]
# Objectives only *suggest* a task type; the operator always picks the task type explicitly.
OBJECTIVE_DEFAULT_TASK: dict[str, str] = {
    "search_rescue": "expanding_square_search",
    "area_survey": "coverage_survey", "disaster_assessment": "coverage_survey",
    "agricultural_survey": "coverage_survey", "mapping_survey": "coverage_survey",
    "perimeter_monitoring": "perimeter_patrol",
    "inspection": "point_inspection", "environmental_monitoring": "point_inspection",
    "target_tracking": "waypoint_route", "custom_mission": "waypoint_route",
    "relief_supply_delivery": "payload_dropoff",
}
# Task types an objective may additionally select per aircraft (besides its default).
OBJECTIVE_EXTRA_TASKS: dict[str, tuple[str, ...]] = {
    "search_rescue": ("payload_dropoff",), "disaster_assessment": ("payload_dropoff",),
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeoPoint(StrictModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class OperatingArea(StrictModel):
    """Structured operating area chosen on the map: a circle or a simple polygon."""
    kind: Literal["circle", "polygon"]
    centre: Optional[GeoPoint] = None
    radius_m: Optional[float] = Field(default=None, gt=0, le=50000, allow_inf_nan=False)
    vertices: list[GeoPoint] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_shape(self) -> "OperatingArea":
        if self.kind == "circle":
            if self.centre is None or self.radius_m is None or self.vertices:
                raise ValueError("A circular operating area needs a centre and radius_m only")
            return self
        if self.centre is not None or self.radius_m is not None:
            raise ValueError("A polygon operating area uses vertices only")
        validate_simple_polygon(self.vertices)
        return self


def validate_simple_polygon(vertices: list[GeoPoint]) -> None:
    if len(vertices) < 3:
        raise ValueError("A polygon needs at least three vertices")
    points = [(point.longitude, point.latitude) for point in vertices]
    if len(set(points)) != len(points):
        raise ValueError("Polygon vertices must be distinct")
    if any(abs(points[(index + 1) % len(points)][0] - point[0]) > 180 for index, point in enumerate(points)):
        raise ValueError("Polygons that cross the antimeridian are not supported")
    from ..schemas import _segments_intersect  # runtime import: app.schemas imports this module

    count = len(points)
    for first in range(count):
        for second in range(first + 1, count):
            if second == first + 1 or (first == 0 and second == count - 1):
                continue
            if _segments_intersect(points[first], points[(first + 1) % count], points[second], points[(second + 1) % count]):
                raise ValueError("Polygon boundaries cannot self-intersect")
    area = sum(points[i][0] * points[(i + 1) % count][1] - points[(i + 1) % count][0] * points[i][1] for i in range(count))
    if abs(area) < 1e-10:
        raise ValueError("A polygon must enclose a non-zero area")


class MissionConstraints(StrictModel):
    """Numeric limits the validator enforces on every generated waypoint."""
    max_distance_from_start_m: Optional[float] = Field(default=None, gt=0, le=100000, allow_inf_nan=False)
    max_altitude_m: Optional[float] = Field(default=None, gt=0, le=500, allow_inf_nan=False)


class TaskParameters(StrictModel):
    """Union of per-type parameters. Which ones are required depends on `task_type` (see validation.REQUIRED)."""
    # search patterns
    datum: Optional[GeoPoint] = None
    track_spacing_m: Optional[float] = Field(default=None, ge=2, le=1000, allow_inf_nan=False)
    search_radius_m: Optional[float] = Field(default=None, gt=0, le=20000, allow_inf_nan=False)
    heading_deg: float = Field(default=0.0, ge=0, lt=360, allow_inf_nan=False)
    sector_radius_m: Optional[float] = Field(default=None, gt=0, le=20000, allow_inf_nan=False)
    sector_angle_deg: float = Field(default=120.0, ge=30, le=180, allow_inf_nan=False)
    sector_count: int = Field(default=3, ge=1, le=6)
    # coverage survey
    line_spacing_m: Optional[float] = Field(default=None, ge=2, le=1000, allow_inf_nan=False)
    # perimeter patrol (path overrides the operating-area boundary)
    path: list[GeoPoint] = Field(default_factory=list, max_length=100)
    laps: int = Field(default=1, ge=1, le=10)
    # point inspection / custom route / hold
    points: list[GeoPoint] = Field(default_factory=list, max_length=50)
    orbit_radius_m: float = Field(default=0.0, ge=0, le=500, allow_inf_nan=False)
    orbit_points: int = Field(default=8, ge=4, le=16)
    hover_s: float = Field(default=10.0, ge=0, le=3600, allow_inf_nan=False)
    hold_point: Optional[GeoPoint] = None
    # simulated relief supply delivery (a logged software event only)
    drop_point: Optional[GeoPoint] = None
    drop_altitude_m: Optional[float] = Field(default=None, gt=0, le=500, allow_inf_nan=False)
    hover_duration_s: Optional[float] = Field(default=None, ge=0, le=3600, allow_inf_nan=False)
    payload_category: Optional[PayloadCategory] = None


class RouteWaypoint(StrictModel):
    seq: int = Field(ge=0)
    latitude: float
    longitude: float
    altitude_m: float
    speed_mps: float
    hover_s: float = 0.0
    action: Literal["takeoff", "waypoint", "hover", "payload_release_point", "land"] = "waypoint"


class RouteEvent(StrictModel):
    type: Literal["payload_released"]
    aircraft_id: str
    waypoint_seq: int
    planned_time_s: float
    payload_category: PayloadCategory
    simulated: Literal[True] = True


class GeneratedRoute(StrictModel):
    generator: str
    generator_version: int
    waypoints: list[RouteWaypoint] = Field(max_length=2000)
    events: list[RouteEvent] = Field(default_factory=list, max_length=20)
    length_m: float
    duration_s: float
    assumptions: str


class AircraftPlan(StrictModel):
    uav_id: str = Field(min_length=5, max_length=16, pattern=r"^px4-[1-9][0-9]{0,2}$")
    role: Optional[AircraftRole] = None
    priority: int = Field(default=2, ge=1, le=3)
    reassignable: bool = True
    task_type: Optional[TaskType] = None
    parameters: TaskParameters = Field(default_factory=TaskParameters)
    start_location: Optional[GeoPoint] = None
    altitude_m: Optional[float] = Field(default=None, gt=0, le=500, allow_inf_nan=False)
    speed_mps: Optional[float] = Field(default=None, gt=0, le=50, allow_inf_nan=False)
    loiter_duration_s: float = Field(default=0.0, ge=0, le=3600, allow_inf_nan=False)
    return_mode: ReturnMode = "return_to_start"
    route: Optional[GeneratedRoute] = None
    confirmed: bool = False


class MissionPlan(StrictModel):
    objectives: list[Objective] = Field(default_factory=list, max_length=11)
    description: str = Field(default="", max_length=500)
    operating_area: Optional[OperatingArea] = None
    required_uav_count: Optional[int] = Field(default=None, ge=1, le=5)
    max_duration_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    constraints: MissionConstraints = Field(default_factory=MissionConstraints)
    notes: str = Field(default="", max_length=500)
    allow_multiple_primary: bool = False
    selected_uav_ids: list[str] = Field(default_factory=list, max_length=5)
    activated_uav_ids: list[str] = Field(default_factory=list, max_length=5)
    aircraft: list[AircraftPlan] = Field(default_factory=list, max_length=5)
    workflow_stage: Literal["general", "aircraft", "faults", "review"] = "general"
    current_uav_index: int = Field(default=0, ge=0, le=4)

    @field_validator("description", "notes")
    @classmethod
    def trim(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_structure(self) -> "MissionPlan":
        ids = [item.uav_id for item in self.aircraft]
        if len(set(ids)) != len(ids):
            raise ValueError("Each PX4 aircraft may have only one assignment")
        if len(set(self.objectives)) != len(self.objectives):
            raise ValueError("Mission objectives must be unique")
        pattern = re.compile(r"^px4-[1-9][0-9]{0,2}$")
        if any(not pattern.fullmatch(item) for item in self.selected_uav_ids + self.activated_uav_ids):
            raise ValueError("Aircraft IDs must be valid PX4 telemetry identities")
        if len(set(self.selected_uav_ids)) != len(self.selected_uav_ids):
            raise ValueError("Selected aircraft IDs must be unique")
        if any(item not in self.selected_uav_ids for item in ids):
            raise ValueError("Aircraft assignments must belong to the selected fleet")
        return self
