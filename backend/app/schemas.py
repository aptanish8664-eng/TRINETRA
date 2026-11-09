from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class SimulationRequest(BaseModel):
    uav_count: int = Field(default=4, ge=2, le=12)
    task_count: int = Field(default=10, ge=1, le=40)
    seed: int = 21
    duration: int = Field(default=180, ge=1, le=3600)
    spoofing_type: str = "sudden"
    target_uav: str = "UAV-02"
    spoof_start: int = Field(default=15, ge=0)
    spoof_duration: int = Field(default=35, ge=1)
    spoof_magnitude: float = Field(default=3.0, ge=0)
    response_policy: str = "adaptive"
    detector_threshold: Optional[float] = Field(default=None, gt=0)


class ExperimentRequest(SimulationRequest):
    duration: int = Field(default=180, ge=1, le=3600)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=128)


class RegistrationRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    email: Optional[str] = Field(default=None, min_length=3, max_length=254)
    password: SecretStr = Field(min_length=12, max_length=128)
    requested_role: Literal["GENERAL", "OPERATOR"]
    justification: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        normalized = value.strip().casefold()
        local_part = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
        domain = r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
        if len(normalized) > 254 or not re.fullmatch(rf"{local_part}@{domain}", normalized):
            raise ValueError("Enter a valid email address")
        return normalized

    @field_validator("full_name", "username")
    @classmethod
    def trim_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("This field cannot be blank")
        return normalized


class RegistrationDecisionRequest(BaseModel):
    decision: Literal["approve", "reject", "hold", "resume"]
    role: Optional[Literal["GENERAL", "OPERATOR"]] = None
    rejection_message: Optional[str] = Field(default=None, max_length=1000)


class UserRoleRequest(BaseModel):
    role: Literal["GENERAL", "OPERATOR"]


class ScenarioCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    config: SimulationRequest
    intent: Optional["ScenarioIntent"] = None

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("Scenario title cannot be blank")
        return title


class ScenarioUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    config: SimulationRequest
    intent: Optional["ScenarioIntent"] = None

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("Scenario title cannot be blank")
        return title


class ScenarioTaskIntent(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    task_reference: Optional[str] = Field(default=None, max_length=16)
    assigned_uav_id: Optional[str] = Field(default=None, max_length=16)
    priority: int = Field(default=1, ge=1, le=3)

    @field_validator("description")
    @classmethod
    def trim_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Task description cannot be blank")
        return value


class ScenarioRoleIntent(BaseModel):
    role: Literal["general", "search", "monitor", "relay", "reserve"]
    count: int = Field(ge=0, le=12)
    rationale: str = Field(default="", max_length=240)


class GeographicPoint(BaseModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class GeographicZone(BaseModel):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=60)
    vertices: list[GeographicPoint] = Field(min_length=3, max_length=100)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Survey zone names cannot be blank")
        return normalized

    @field_validator("vertices")
    @classmethod
    def validate_polygon(cls, vertices: list[GeographicPoint]) -> list[GeographicPoint]:
        points = [(point.longitude, point.latitude) for point in vertices]
        if any(abs(points[(index + 1) % len(points)][0] - point[0]) > 180 for index, point in enumerate(points)):
            raise ValueError("Survey zones that cross the antimeridian are not supported")
        if len(set(points)) < 3 or len(set(points)) != len(points):
            raise ValueError("Survey zone vertices must be distinct")
        for first in range(len(points)):
            a, b = points[first], points[(first + 1) % len(points)]
            for second in range(first + 1, len(points)):
                if second in {first, (first + 1) % len(points)} or first == (second + 1) % len(points):
                    continue
                c, d = points[second], points[(second + 1) % len(points)]
                if _segments_intersect(a, b, c, d):
                    raise ValueError("Survey zone boundaries cannot self-intersect")
        area = sum(
            points[index][0] * points[(index + 1) % len(points)][1]
            - points[(index + 1) % len(points)][0] * points[index][1]
            for index in range(len(points))
        )
        if abs(area) < 1e-10:
            raise ValueError("Survey zone must enclose a non-zero area")
        return vertices


def _segments_intersect(a: tuple[float, float], b: tuple[float, float],
                        c: tuple[float, float], d: tuple[float, float]) -> bool:
    def orientation(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def on_segment(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> bool:
        return min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and min(p[1], r[1]) <= q[1] <= max(p[1], r[1])

    first = orientation(a, b, c)
    second = orientation(a, b, d)
    third = orientation(c, d, a)
    fourth = orientation(c, d, b)
    if first * second < 0 and third * fourth < 0:
        return True
    return (
        (first == 0 and on_segment(a, c, b))
        or (second == 0 and on_segment(a, d, b))
        or (third == 0 and on_segment(c, a, d))
        or (fourth == 0 and on_segment(c, b, d))
    )


class GeographicUavAssignment(BaseModel):
    uav_id: str = Field(min_length=5, max_length=16, pattern=r"^px4-[1-9][0-9]{0,2}$")
    role: Literal["area_survey", "perimeter_monitoring", "inspection", "search_observation", "communication_relay"]
    task: str = Field(min_length=1, max_length=300)
    zone_id: Optional[str] = Field(default=None, max_length=40)
    zone_ids: list[str] = Field(default_factory=list, max_length=20)
    start_location: Optional[GeographicPoint] = None
    destination: Optional[GeographicPoint] = None
    waypoints: list[GeographicPoint] = Field(default_factory=list, max_length=20)

    @field_validator("task")
    @classmethod
    def trim_task(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Aircraft task cannot be blank")
        return normalized


class GeographicMissionIntent(BaseModel):
    objective: Literal["area_survey", "perimeter_monitoring", "inspection", "search_observation", "communication_relay"]
    start_location: Optional[GeographicPoint] = None
    destination: Optional[GeographicPoint] = None
    survey_zones: list[GeographicZone] = Field(default_factory=list, max_length=20)
    uav_assignments: list[GeographicUavAssignment] = Field(default_factory=list, max_length=20)
    constraints: list[str] = Field(default_factory=list, max_length=12)
    ai_recommendations: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_assignments(self) -> "GeographicMissionIntent":
        ids = [assignment.uav_id for assignment in self.uav_assignments]
        if len(set(ids)) != len(ids):
            raise ValueError("Each PX4 aircraft may have only one mission assignment")
        zone_ids = {zone.id for zone in self.survey_zones}
        if len(zone_ids) != len(self.survey_zones):
            raise ValueError("Survey zone IDs must be unique")
        if any(
            assignment.zone_id is not None and assignment.zone_id not in zone_ids
            or any(zone_id not in zone_ids for zone_id in assignment.zone_ids)
            or len(set(assignment.zone_ids)) != len(assignment.zone_ids)
            or assignment.zone_id is not None and assignment.zone_id in assignment.zone_ids
            for assignment in self.uav_assignments
        ):
            raise ValueError("Aircraft assignments must reference an existing survey zone")
        if self.objective in {"area_survey", "perimeter_monitoring"} and not self.survey_zones:
            raise ValueError("This mission objective requires at least one survey zone")
        return self


class ScenarioUavFaultIntent(BaseModel):
    uav_id: str = Field(min_length=6, max_length=16, pattern=r"^UAV-(0[1-9]|1[0-2])$")
    fault_type: Literal[
        "none", "sudden", "gradual", "intermittent", "jump", "freeze",
        "noise", "degradation", "velocity_inconsistency",
    ]
    spoof_start: int = Field(default=0, ge=0)
    spoof_duration: int = Field(default=1, ge=1)
    spoof_magnitude: float = Field(default=0.0, ge=0, allow_inf_nan=False)


class ScenarioIntent(BaseModel):
    mission_objectives: list[str] = Field(default_factory=list, max_length=8)
    proposed_tasks: list[ScenarioTaskIntent] = Field(default_factory=list, max_length=40)
    uav_role_suggestions: list[ScenarioRoleIntent] = Field(default_factory=list, max_length=12)
    operating_area_notes: str = Field(default="", max_length=1000)
    environment_notes: str = Field(default="", max_length=1000)
    constraints: list[str] = Field(default_factory=list, max_length=12)
    geographic_mission: Optional[GeographicMissionIntent] = None
    uav_fault_assignments: list[ScenarioUavFaultIntent] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_fault_assignments(self) -> "ScenarioIntent":
        ids = [assignment.uav_id for assignment in self.uav_fault_assignments]
        if len(set(ids)) != len(ids):
            raise ValueError("Each abstract simulator UAV may have only one fault assignment")
        return self

    @field_validator("mission_objectives", "constraints")
    @classmethod
    def clean_text_list(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("Mission intent entries cannot be blank")
        return cleaned


ScenarioCreateRequest.model_rebuild()
ScenarioUpdateRequest.model_rebuild()


class ScenarioReviewRequest(BaseModel):
    decision: Literal["approve", "reject", "request_changes"]
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def trim_reason(cls, value: str) -> str:
        reason = value.strip()
        if not reason:
            raise ValueError("A review reason is required")
        return reason


class ScenarioExecutionRequest(BaseModel):
    version_id: str = Field(min_length=1, max_length=64)


class TaskReassignmentRequest(BaseModel):
    target_uav_id: str = Field(min_length=1, max_length=16)


class CommandDecisionCreateRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=32)
    command: Literal["stop", "reset", "isolate_uav", "reassign_task"]
    uav_id: Optional[str] = Field(default=None, min_length=1, max_length=16)
    task_id: Optional[str] = Field(default=None, min_length=1, max_length=16)
    target_uav_id: Optional[str] = Field(default=None, min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_command_arguments(self) -> "CommandDecisionCreateRequest":
        if self.command == "isolate_uav":
            valid = self.uav_id is not None and self.task_id is None and self.target_uav_id is None
        elif self.command == "reassign_task":
            valid = self.uav_id is None and self.task_id is not None and self.target_uav_id is not None
        else:
            valid = self.uav_id is None and self.task_id is None and self.target_uav_id is None
        if not valid:
            raise ValueError("Command arguments do not match the requested command")
        return self


class CommandDecisionReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reason: Optional[str] = Field(default=None, min_length=1, max_length=500)
