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


class ScenarioIntent(BaseModel):
    mission_objectives: list[str] = Field(default_factory=list, max_length=8)
    proposed_tasks: list[ScenarioTaskIntent] = Field(default_factory=list, max_length=40)
    uav_role_suggestions: list[ScenarioRoleIntent] = Field(default_factory=list, max_length=12)
    operating_area_notes: str = Field(default="", max_length=1000)
    environment_notes: str = Field(default="", max_length=1000)
    constraints: list[str] = Field(default_factory=list, max_length=12)

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
