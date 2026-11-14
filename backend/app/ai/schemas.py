from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class StructuredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposedTask(StructuredOutput):
    description: str = Field(min_length=1, max_length=300)
    task_reference: Optional[str] = Field(max_length=16)
    assigned_uav_id: Optional[str] = Field(max_length=16)
    waypoint_reference: Optional[str] = Field(max_length=80)
    priority: int = Field(ge=1, le=3)


class UAVRoleSuggestion(StructuredOutput):
    role: Literal["general", "search", "monitor", "relay", "reserve"]
    count: int = Field(ge=0, le=12)
    rationale: str = Field(min_length=1, max_length=240)


class MissionProposalContent(StructuredOutput):
    mission_objectives: list[str] = Field(min_length=1, max_length=8)
    required_information: list[str] = Field(max_length=12)
    proposed_tasks: list[ProposedTask] = Field(max_length=40)
    uav_role_suggestions: list[UAVRoleSuggestion] = Field(max_length=12)
    waypoint_references: list[str] = Field(max_length=20)
    assumptions: list[str] = Field(max_length=12)
    constraints: list[str] = Field(max_length=12)
    assessment_explanations: list[str] = Field(min_length=1, max_length=8)
    clarification_questions: list[str] = Field(max_length=12)


class AssessmentFinding(StructuredOutput):
    explanation: str = Field(min_length=1, max_length=500)
    evidence_references: list[str] = Field(max_length=30)


class MissionAssessmentContent(StructuredOutput):
    summary: str = Field(min_length=1, max_length=800)
    findings: list[AssessmentFinding] = Field(max_length=12)
    suggested_responses: list[Literal["continue_monitoring", "pause_simulation", "stop_simulation"]] = Field(max_length=3)


class OperatorSimulationConfig(StructuredOutput):
    """The operator-selected simulator settings; AI does not select these values."""
    uav_count: int = Field(ge=2, le=12)
    task_count: int = Field(ge=1, le=40)
    seed: int
    duration: int = Field(ge=1, le=3600)
    spoofing_type: Literal["none", "sudden", "gradual", "intermittent", "jump", "freeze", "noise", "degradation", "velocity_inconsistency"]
    target_uav: str = Field(min_length=1, max_length=16)
    spoof_start: int = Field(ge=0)
    spoof_duration: int = Field(ge=1)
    spoof_magnitude: float = Field(ge=0)
    response_policy: Literal["adaptive", "monitor", "baseline"]
    detector_threshold: Optional[float] = Field(gt=0)


class MissionProposalRequest(StructuredOutput):
    description: str = Field(min_length=1, max_length=20000)
    operator_config: OperatorSimulationConfig


class MissionConversationCreateRequest(StructuredOutput):
    message: str = Field(min_length=1, max_length=2000)
    operator_config: OperatorSimulationConfig


class MissionConversationTurnRequest(StructuredOutput):
    message: str = Field(min_length=1, max_length=2000)


class MissionConversationScenarioTransferRequest(StructuredOutput):
    scenario_id: Optional[str] = Field(default=None, min_length=1, max_length=64)


class GeographicZoneLabel(StructuredOutput):
    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=60)


class GeographicMissionPlanningRequest(StructuredOutput):
    objective: Literal[
        "area_survey", "search_rescue", "perimeter_monitoring", "inspection", "disaster_assessment",
        "agricultural_survey", "mapping_survey", "target_tracking", "environmental_monitoring",
        "custom_mission", "search_observation", "communication_relay",
    ]
    description: str = Field(min_length=1, max_length=2000)
    zones: list[GeographicZoneLabel] = Field(max_length=20)
    aircraft_id: Optional[str] = Field(default=None, min_length=5, max_length=16, pattern=r"^px4-[1-9][0-9]{0,2}$")
    objective_suggestions_only: bool = False


class GeographicObjectiveSuggestion(StructuredOutput):
    objective: Literal[
        "area_survey", "search_rescue", "perimeter_monitoring", "inspection", "disaster_assessment",
        "agricultural_survey", "mapping_survey", "target_tracking", "environmental_monitoring",
        "custom_mission",
    ]
    rationale: str = Field(min_length=1, max_length=240)


class GeographicAssignmentSuggestion(StructuredOutput):
    uav_id: str = Field(min_length=5, max_length=16)
    role: Literal["area_survey", "perimeter_monitoring", "inspection", "search_observation", "communication_relay"]
    task: str = Field(min_length=1, max_length=300)
    zone_id: Optional[str] = Field(max_length=40)


class GeographicMissionProposalContent(StructuredOutput):
    summary: str = Field(min_length=1, max_length=500)
    objective_suggestions: list[GeographicObjectiveSuggestion] = Field(default_factory=list, max_length=5)
    assignments: list[GeographicAssignmentSuggestion] = Field(max_length=20)
    missing_information: list[str] = Field(max_length=12)
    constraints: list[str] = Field(max_length=12)
    warnings: list[str] = Field(max_length=12)


class ConversationMessage(StructuredOutput):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class MissionConversationResponse(StructuredOutput):
    conversation_id: str
    status: Literal["collecting", "ready_for_review"]
    messages: list[ConversationMessage]
    proposal: Optional[MissionProposalContent]
    proposal_id: Optional[str]
    scenario_id: Optional[str] = None
    operator_config: OperatorSimulationConfig
    expires_at: str


class MissionApprovalRequest(StructuredOutput):
    confirmation: Literal["approve"]


class MissionRejectionRequest(StructuredOutput):
    reason: str = Field(min_length=1, max_length=500)


class MissionProposalResponse(StructuredOutput):
    proposal_id: str
    status: Literal["needs_clarification", "pending_review", "approved", "approving", "approval_failed", "rejected", "expired"]
    ready_for_review: bool
    validation_issues: list[str]
    proposal: MissionProposalContent
    operator_config: OperatorSimulationConfig
    run_id: Optional[str]
    decided_by: Optional[str] = None
    decision_reason: Optional[str] = None
    expires_at: Optional[str] = None


class AIUsageStatus(StructuredOutput):
    available: bool
    provider: Literal["openai"]
    model: str
    reason: Optional[str]
    remaining_requests: Optional[int]
    remaining_output_tokens: Optional[int]


class MissionAssessmentResponse(StructuredOutput):
    run_id: str
    tick: int
    simulation_status: str
    validated_evidence: dict
    assessment: MissionAssessmentContent


class ScenarioAssistantTurn(StructuredOutput):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=600)


class ScenarioAssistantAircraft(StructuredOutput):
    index: int = Field(ge=1, le=5)
    uav_id: str = Field(max_length=16)
    confirmed: bool
    role: Optional[str] = Field(default=None, max_length=20)
    priority: int = Field(default=2, ge=1, le=3)
    reassignable: bool = True
    task_type: Optional[str] = Field(default=None, max_length=40)
    altitude_m: Optional[float] = None
    speed_mps: Optional[float] = None
    loiter_duration_s: float = 0.0
    return_mode: str = Field(default="return_to_start", max_length=30)
    start_selected: bool = False
    route_waypoints: int = Field(default=0, ge=0, le=2000)
    parameters: dict[str, Union[float, str]] = Field(default_factory=dict, max_length=12)
    missing: list[str] = Field(default_factory=list, max_length=16)


class ScenarioAssistantSnapshot(StructuredOutput):
    """Compact, coordinate-free view of the Scenario Lab form. It is untrusted data, never instructions."""
    section: Literal["A", "B", "C"]  # A mission, B individual UAV configuration, C review and validation
    title: str = Field(default="", max_length=120)
    objectives: list[str] = Field(default_factory=list, max_length=11)
    description: str = Field(default="", max_length=500)
    area_kind: Optional[Literal["circle", "polygon"]] = None
    max_duration_minutes: Optional[int] = None
    max_distance_from_start_m: Optional[float] = None
    max_altitude_m: Optional[float] = None
    required_uav_count: Optional[int] = None
    activated_uav_count: int = Field(default=0, ge=0, le=20)
    fleet_verified: bool = False
    active_uav_index: int = Field(default=1, ge=1, le=5)
    validation_items: list[str] = Field(default_factory=list, max_length=14)
    deploy_blockers: list[str] = Field(default_factory=list, max_length=16)
    aircraft: list[ScenarioAssistantAircraft] = Field(default_factory=list, max_length=5)


class ScenarioAssistantRequest(StructuredOutput):
    draft_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    message: str = Field(min_length=1, max_length=500)
    history: list[ScenarioAssistantTurn] = Field(default_factory=list, max_length=12)
    snapshot: ScenarioAssistantSnapshot


class ScenarioAssistantContent(BaseModel):
    """Provider output. Actions are validated one by one by the backend so a bad one is dropped, not fatal."""
    model_config = ConfigDict(extra="forbid")
    reply: str = Field(min_length=1, max_length=1500)
    actions: list[dict] = Field(max_length=5)


class ScenarioAssistantResponse(StructuredOutput):
    reply: str
    actions: list[dict]
    source: Literal["ai"] = "ai"
    dropped_actions: int = 0
