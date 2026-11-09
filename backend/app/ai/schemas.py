from __future__ import annotations

from typing import Literal, Optional

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
    objective: Literal["area_survey", "perimeter_monitoring", "inspection", "search_observation", "communication_relay"]
    description: str = Field(min_length=1, max_length=2000)
    zones: list[GeographicZoneLabel] = Field(max_length=20)


class GeographicAssignmentSuggestion(StructuredOutput):
    uav_id: str = Field(min_length=5, max_length=16)
    role: Literal["area_survey", "perimeter_monitoring", "inspection", "search_observation", "communication_relay"]
    task: str = Field(min_length=1, max_length=300)
    zone_id: Optional[str] = Field(max_length=40)


class GeographicMissionProposalContent(StructuredOutput):
    summary: str = Field(min_length=1, max_length=500)
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
