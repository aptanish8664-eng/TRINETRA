from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth_routes import require_permission, require_csrf
from ..authorization import Permission, has_permission
from ..config import (
    AI_DAILY_OUTPUT_TOKEN_LIMIT,
    AI_MAX_INPUT_CHARS,
    AI_MAX_OUTPUT_TOKENS,
    AI_REQUESTS_PER_HOUR,
    AI_TIMEOUT_SECONDS,
    OPENAI_MODEL,
)
from ..persistence.database import (
    add_audit_event,
    ai_usage_remaining,
    claim_mission_proposal_approval,
    create_mission_proposal,
    create_ai_conversation,
    expire_pending_mission_proposals,
    fail_mission_proposal_approval,
    finish_ai_usage,
    finish_mission_proposal_approval,
    get_mission_proposal,
    get_ai_conversation,
    get_scenario,
    list_pending_mission_proposals,
    reject_mission_proposal,
    reserve_ai_usage,
    update_ai_conversation,
    transfer_ai_conversation_to_scenario,
)
from ..security import AuthContext, get_auth_context
from ..simulation.engine import SimulationConfig
from ..telemetry.planning import current_planning_aircraft
from .provider import (
    AIProviderFailure,
    AIProviderMalformedResponse,
    AIProviderUnavailable,
    MissionAIProvider,
    ProviderResult,
    get_ai_provider,
)
from .schemas import (
    AIUsageStatus,
    GeographicMissionPlanningRequest,
    GeographicMissionProposalContent,
    MissionApprovalRequest,
    MissionAssessmentResponse,
    MissionProposalRequest,
    MissionConversationCreateRequest,
    MissionConversationTurnRequest,
    MissionConversationScenarioTransferRequest,
    MissionConversationResponse,
    MissionRejectionRequest,
    MissionProposalResponse,
    OperatorSimulationConfig,
)
from .service import (
    ProposalValidationError,
    mission_proposal_status,
    conversation_config_conflicts,
    redact_operator_text,
    sanitize_assessment,
    validate_mission_proposal,
)


router = APIRouter()


def _conversation_context(config: OperatorSimulationConfig) -> dict[str, Any]:
    return {
        "coordinate_system": "abstract simulation units; not geographic coordinates",
        "uav_references": ["UAV-{0:02d}".format(i) for i in range(1, config.uav_count + 1)],
        "task_references": ["T-{0:02d}".format(i) for i in range(1, config.task_count + 1)],
        "verified_waypoint_references": [],
        "operator_selected_simulation_config": config.model_dump(),
    }


async def _process_conversation_turn(
    conversation_id: str, message: str, request: Request, context: AuthContext,
    provider: MissionAIProvider,
) -> MissionConversationResponse:
    record = get_ai_conversation(conversation_id, context.user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="Mission conversation not found or expired.")
    if not provider.available:
        raise HTTPException(status_code=503, detail="AI is unavailable. Manual planning remains available.")
    messages = record["messages"] if record else []
    user_turns = sum(item["role"] == "user" for item in messages)
    if user_turns >= 8:
        raise HTTPException(status_code=409, detail="Conversation turn limit reached. Start a new planning conversation.")
    clean_message, contains_coordinates = redact_operator_text(message)
    if contains_coordinates:
        raise HTTPException(status_code=422, detail="Geographic coordinate text is not supported by this abstract simulator.")
    config = OperatorSimulationConfig.model_validate(record["operator_config"])
    prior_chars = sum(len(item["content"]) for item in messages)
    if prior_chars + len(clean_message) > AI_MAX_INPUT_CHARS:
        raise HTTPException(status_code=413, detail="Conversation exceeds the configured AI input limit. Start a new conversation.")
    messages = messages + [{"role": "user", "content": clean_message}]
    usage_id = _reserve_usage(context, "mission_conversation", prior_chars + len(clean_message))
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(
            provider.continue_mission_conversation(messages, _conversation_context(config)),
            timeout=AI_TIMEOUT_SECONDS + 1,
        )
        if not isinstance(result, ProviderResult):
            raise AIProviderMalformedResponse("provider result type invalid")
        proposal, issues = validate_mission_proposal(result.parsed, config)
        conflicts = conversation_config_conflicts(messages, config)
        if conflicts:
            proposal = proposal.model_copy(update={
                "required_information": list(dict.fromkeys(conflicts + proposal.required_information))[:12],
                "clarification_questions": list(dict.fromkeys(conflicts + proposal.clarification_questions))[:12],
            })
        ready = mission_proposal_status(proposal) == "pending_review"
        assistant_message = (
            "The structured proposal is ready for your review. It remains advisory and has not been saved or executed."
            if ready else "To continue, please clarify: " + " ".join(proposal.clarification_questions)
        )
        if len(assistant_message) > 2000:
            assistant_message = assistant_message[:1997] + "..."
        messages = messages + [{"role": "assistant", "content": assistant_message}]
        proposal_id = None
        if ready:
            proposal_id = create_mission_proposal(
                context.user.id, "pending_review", proposal.model_dump(mode="json"),
                config.model_dump(mode="json"), issues,
            )
        if not update_ai_conversation(
            conversation_id, context.user.id, messages,
            "ready_for_review" if ready else "collecting",
            proposal.model_dump(mode="json"), proposal_id,
        ):
            raise HTTPException(status_code=409, detail="Conversation changed concurrently; reload it before continuing.")
        finish_ai_usage(usage_id, result.output_tokens, True)
        _record_ai_event(request, context, "ai_mission_conversation", "success", "proposal_ready" if ready else "clarification_requested", {
            "model": OPENAI_MODEL, "response_id": result.response_id,
            "input_chars": prior_chars + len(clean_message), "output_tokens": result.output_tokens,
            "duration_ms": int((time.monotonic() - started) * 1000), "validation_issues": issues,
        }, proposal_id, "mission_proposal" if proposal_id else "ai_conversation")
        updated = get_ai_conversation(conversation_id, context.user.id)
        if updated is None:
            raise HTTPException(status_code=500, detail="Conversation state could not be reloaded.")
        return MissionConversationResponse(
            conversation_id=conversation_id, status=updated["status"],
            messages=updated["messages"], proposal=proposal,
            proposal_id=proposal_id, operator_config=config, expires_at=updated["expires_at"],
            scenario_id=updated["scenario_id"],
        )
    except HTTPException:
        finish_ai_usage(usage_id, 0, False)
        raise
    except (asyncio.TimeoutError, AIProviderUnavailable, AIProviderFailure, AIProviderMalformedResponse, ProposalValidationError, ValueError) as exc:
        finish_ai_usage(usage_id, 0, False)
        reason = "provider_timeout" if isinstance(exc, asyncio.TimeoutError) else "invalid_or_unavailable_provider_response"
        _record_ai_event(request, context, "ai_mission_conversation", "failed", reason, {
            "model": OPENAI_MODEL, "input_chars": prior_chars + len(clean_message),
            "duration_ms": int((time.monotonic() - started) * 1000),
        }, target_type="ai_conversation")
        status = 503 if isinstance(exc, (asyncio.TimeoutError, AIProviderUnavailable, AIProviderFailure)) else 502
        raise HTTPException(status_code=status, detail="AI conversation failed safely. Retry or continue with manual planning.") from exc


def _record_ai_event(request: Request, context: AuthContext, action: str, outcome: str,
                     reason: str, metadata: dict[str, Any] | None = None,
                     target_id: str | None = None, target_type: str | None = None) -> None:
    add_audit_event(
        action=action, outcome=outcome, reason=reason,
        request_id=getattr(request.state, "request_id", "request-unavailable"),
        actor_id=context.user.id, actor_name=context.user.full_name,
        target_type=target_type or ("mission_proposal" if target_id else "ai_request"), target_id=target_id,
        metadata=metadata or {},
    )


def _expire_pending_proposals(request: Request) -> None:
    request_id = getattr(request.state, "request_id", "request-unavailable")
    for proposal_id, requester_id in expire_pending_mission_proposals():
        add_audit_event(
            action="ai_proposal_expired", outcome="expired",
            reason="AI mission proposal approval expired after seven days.",
            request_id=request_id, target_type="mission_proposal", target_id=proposal_id,
            metadata={"requester_id": requester_id},
        )


def _reserve_usage(context: AuthContext, endpoint: str, input_chars: int) -> str:
    try:
        return reserve_ai_usage(
            context.user.id, endpoint, OPENAI_MODEL, input_chars,
            AI_REQUESTS_PER_HOUR, AI_MAX_OUTPUT_TOKENS, AI_DAILY_OUTPUT_TOKEN_LIMIT,
        )
    except ValueError as exc:
        if str(exc) == "ai_rate_limit":
            raise HTTPException(status_code=429, detail="AI request limit reached. Try again later.") from exc
        raise HTTPException(status_code=429, detail="AI daily usage limit reached.") from exc


def _validated_config(config: OperatorSimulationConfig) -> SimulationConfig:
    candidate = SimulationConfig(**config.model_dump())
    try:
        candidate.validate()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Operator-selected simulator configuration is invalid.") from exc
    return candidate


@router.get("/api/ai/status", response_model=AIUsageStatus)
def ai_status(
    context: AuthContext = Depends(get_auth_context),
    provider: MissionAIProvider = Depends(get_ai_provider),
) -> AIUsageStatus:
    remaining_requests, remaining_tokens = ai_usage_remaining(
        context.user.id, AI_REQUESTS_PER_HOUR, AI_DAILY_OUTPUT_TOKEN_LIMIT,
    )
    return AIUsageStatus(
        available=provider.available,
        provider="openai",
        model=OPENAI_MODEL,
        reason=None if provider.available else "OPENAI_API_KEY is not configured on the backend.",
        remaining_requests=remaining_requests,
        remaining_output_tokens=remaining_tokens,
    )


@router.post("/api/ai/proposals", response_model=MissionProposalResponse)
async def create_ai_proposal(
    payload: MissionProposalRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.AI_PROPOSE)),
    _csrf: AuthContext = Depends(require_csrf),
    provider: MissionAIProvider = Depends(get_ai_provider),
) -> MissionProposalResponse:
    if not provider.available:
        _record_ai_event(request, context, "ai_mission_proposal", "unavailable", "openai_key_not_configured",
                         {"description_chars": len(payload.description), "model": OPENAI_MODEL})
        raise HTTPException(status_code=503, detail="AI is unavailable: configure the backend OpenAI API key. Manual planning remains available.")
    if len(payload.description) > AI_MAX_INPUT_CHARS:
        _record_ai_event(request, context, "ai_mission_proposal", "denied", "ai_input_limit_exceeded",
                         {"description_chars": len(payload.description), "model": OPENAI_MODEL})
        raise HTTPException(status_code=413, detail="Mission description exceeds the configured AI input limit.")
    try:
        simulation_config = _validated_config(payload.operator_config)
    except HTTPException:
        _record_ai_event(request, context, "ai_mission_proposal", "rejected", "operator_config_failed_validation",
                         {"description_chars": len(payload.description), "model": OPENAI_MODEL})
        raise
    redacted_description, contains_coordinates = redact_operator_text(payload.description)
    if contains_coordinates:
        _record_ai_event(request, context, "ai_mission_proposal", "rejected", "operator_coordinate_text_not_sent",
                         {"description_chars": len(payload.description), "model": OPENAI_MODEL})
        raise HTTPException(status_code=422, detail="Geographic coordinate text was not sent to AI. This simulator accepts abstract task markers only; use manual scenario planning or provide a non-geographic description.")

    try:
        usage_id = _reserve_usage(context, "mission_proposal", len(redacted_description))
    except HTTPException as exc:
        reason = "ai_request_limit_exceeded" if exc.status_code == 429 and "request limit" in str(exc.detail).lower() else "ai_usage_limit_exceeded"
        _record_ai_event(request, context, "ai_mission_proposal", "denied", reason,
                         {"description_chars": len(redacted_description), "model": OPENAI_MODEL})
        raise
    started = time.monotonic()
    try:
        context_data = {
            "coordinate_system": "abstract simulation units; not geographic coordinates",
            "uav_references": ["UAV-{0:02d}".format(i) for i in range(1, simulation_config.uav_count + 1)],
            "task_references": ["T-{0:02d}".format(i) for i in range(1, simulation_config.task_count + 1)],
            "maximum_suggested_tasks_per_uav": max(2, (simulation_config.task_count + simulation_config.uav_count - 1) // simulation_config.uav_count),
            "verified_waypoint_references": [],
            "operator_selected_simulation_config": payload.operator_config.model_dump(),
        }
        result = await asyncio.wait_for(
            provider.propose_mission(redacted_description, context_data),
            timeout=AI_TIMEOUT_SECONDS + 1,
        )
        if not isinstance(result, ProviderResult):
            raise AIProviderMalformedResponse("provider result type invalid")
        content, issues = validate_mission_proposal(result.parsed, payload.operator_config)
        status = mission_proposal_status(content)
        proposal_id = create_mission_proposal(
            context.user.id, status, content.model_dump(mode="json"),
            payload.operator_config.model_dump(mode="json"), issues,
        )
        stored_proposal = get_mission_proposal(proposal_id, context.user.id)
        finish_ai_usage(usage_id, result.output_tokens, True)
        _record_ai_event(request, context, "ai_mission_proposal", "success", status, {
            "provider": "openai", "model": OPENAI_MODEL, "response_id": result.response_id,
            "input_chars": len(redacted_description), "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "validation_issues": issues,
        }, proposal_id, "mission_proposal")
        return MissionProposalResponse(
            proposal_id=proposal_id, status=status, ready_for_review=status == "pending_review",
            validation_issues=issues, proposal=content, operator_config=payload.operator_config,
            run_id=None,
            expires_at=stored_proposal["expires_at"] if stored_proposal else None,
        )
    except (asyncio.TimeoutError, AIProviderUnavailable, AIProviderFailure, AIProviderMalformedResponse, ProposalValidationError, ValueError) as exc:
        finish_ai_usage(usage_id, 0, False)
        reason = "provider_timeout" if isinstance(exc, asyncio.TimeoutError) else "invalid_or_unavailable_provider_response"
        _record_ai_event(request, context, "ai_mission_proposal", "failed", reason, {
            "provider": "openai", "model": OPENAI_MODEL,
            "input_chars": len(redacted_description),
            "duration_ms": int((time.monotonic() - started) * 1000),
        })
        status = 503 if isinstance(exc, (asyncio.TimeoutError, AIProviderUnavailable, AIProviderFailure)) else 502
        message = "AI service is unavailable. Manual planning remains available." if status == 503 else "AI proposal failed deterministic validation. Review the request or continue with manual planning."
        raise HTTPException(status_code=status, detail=message) from exc


@router.post("/api/ai/geographic-mission-plan", response_model=GeographicMissionProposalContent)
async def create_geographic_mission_plan(
    payload: GeographicMissionPlanningRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.AI_PROPOSE)),
    _csrf: AuthContext = Depends(require_csrf),
    provider: MissionAIProvider = Depends(get_ai_provider),
) -> GeographicMissionProposalContent:
    aircraft = current_planning_aircraft()
    if aircraft is None:
        raise HTTPException(status_code=503, detail="PX4 telemetry is unavailable. Continue with manual planning.")
    if not aircraft:
        raise HTTPException(status_code=409, detail="No connected PX4 aircraft with valid position telemetry are available.")
    if not provider.available:
        _record_ai_event(
            request, context, "ai_geographic_mission_plan", "unavailable",
            "openai_key_not_configured", {"model": OPENAI_MODEL},
        )
        raise HTTPException(status_code=503, detail="AI is unavailable: configure the backend OpenAI API key. Manual planning remains available.")
    if len(payload.description) > AI_MAX_INPUT_CHARS:
        raise HTTPException(status_code=413, detail="Mission description exceeds the configured AI input limit.")
    if len({zone.id for zone in payload.zones}) != len(payload.zones):
        raise HTTPException(status_code=422, detail="Zone identifiers must be unique.")
    zone_ids = {zone.id for zone in payload.zones}
    if any(redact_operator_text(zone.name)[1] for zone in payload.zones):
        raise HTTPException(status_code=422, detail="Enter geographic locations on the map, not in zone names.")
    description, contains_coordinates = redact_operator_text(payload.description)
    if contains_coordinates:
        raise HTTPException(status_code=422, detail="Coordinates are selected on the map and are not sent to AI.")
    if not description.strip():
        raise HTTPException(status_code=422, detail="Describe the mission without including geographic coordinates.")

    available_ids = {item["id"] for item in aircraft}
    mission_context = {
        "objective": payload.objective,
        "zones": [zone.model_dump(mode="json") for zone in payload.zones],
        "available_aircraft_ids": sorted(available_ids),
        "coordinate_reference": "WGS84; exact coordinate values are omitted",
        "route_validation": "not_supported",
    }
    usage_id = _reserve_usage(context, "geographic_mission_plan", len(description))
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(
            provider.propose_geographic_mission(description, mission_context),
            timeout=AI_TIMEOUT_SECONDS + 1,
        )
        if not isinstance(result, ProviderResult) or not isinstance(result.parsed, GeographicMissionProposalContent):
            raise AIProviderMalformedResponse("geographic provider result type invalid")
        suggested_ids = [item.uav_id for item in result.parsed.assignments]
        if len(set(suggested_ids)) != len(suggested_ids):
            raise AIProviderMalformedResponse("duplicate aircraft assignment")
        if any(item.uav_id not in available_ids for item in result.parsed.assignments):
            raise AIProviderMalformedResponse("unknown aircraft assignment")
        if any(item.zone_id is not None and item.zone_id not in zone_ids for item in result.parsed.assignments):
            raise AIProviderMalformedResponse("unknown survey zone")
        generated_text = [result.parsed.summary]
        generated_text += result.parsed.missing_information + result.parsed.constraints + result.parsed.warnings
        generated_text += [item.task for item in result.parsed.assignments]
        if any(redact_operator_text(value)[1] for value in generated_text):
            raise AIProviderMalformedResponse("geographic coordinates in advisory text")
        finish_ai_usage(usage_id, result.output_tokens, True)
        _record_ai_event(request, context, "ai_geographic_mission_plan", "success", "advisory_plan_returned", {
            "provider": "openai", "model": OPENAI_MODEL, "response_id": result.response_id,
            "input_chars": len(description), "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "assignment_count": len(suggested_ids),
        }, target_type="geographic_mission_plan")
        return result.parsed
    except HTTPException:
        finish_ai_usage(usage_id, 0, False)
        raise
    except (asyncio.TimeoutError, AIProviderUnavailable, AIProviderFailure, AIProviderMalformedResponse, ValueError) as exc:
        finish_ai_usage(usage_id, 0, False)
        reason = "provider_timeout" if isinstance(exc, asyncio.TimeoutError) else "invalid_or_unavailable_provider_response"
        _record_ai_event(request, context, "ai_geographic_mission_plan", "failed", reason, {
            "provider": "openai", "model": OPENAI_MODEL,
            "input_chars": len(description),
            "duration_ms": int((time.monotonic() - started) * 1000),
        }, target_type="geographic_mission_plan")
        status = 503 if isinstance(exc, (asyncio.TimeoutError, AIProviderUnavailable, AIProviderFailure)) else 502
        message = "AI service is unavailable. Manual planning remains available." if status == 503 else "AI suggestions failed validation. Review the mission manually."
        raise HTTPException(status_code=status, detail=message) from exc


@router.post("/api/ai/conversations", response_model=MissionConversationResponse, status_code=201)
async def create_ai_conversation_route(
    payload: MissionConversationCreateRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.AI_PROPOSE)),
    _csrf: AuthContext = Depends(require_csrf),
    provider: MissionAIProvider = Depends(get_ai_provider),
) -> MissionConversationResponse:
    try:
        _validated_config(payload.operator_config)
    except HTTPException:
        raise
    if len(payload.message) > AI_MAX_INPUT_CHARS:
        raise HTTPException(status_code=413, detail="Message exceeds the configured AI input limit.")
    if not provider.available:
        raise HTTPException(status_code=503, detail="AI is unavailable. Manual planning remains available.")
    clean, has_coordinates = redact_operator_text(payload.message)
    if has_coordinates:
        raise HTTPException(status_code=422, detail="Geographic coordinate text is not supported by this abstract simulator.")
    conversation_id = create_ai_conversation(
        context.user.id, payload.operator_config.model_dump(mode="json"),
    )
    return await _process_conversation_turn(
        conversation_id, clean, request, context, provider,
    )


@router.post("/api/ai/conversations/{conversation_id}/turn", response_model=MissionConversationResponse)
async def continue_ai_conversation_route(
    conversation_id: str,
    payload: MissionConversationTurnRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.AI_PROPOSE)),
    _csrf: AuthContext = Depends(require_csrf),
    provider: MissionAIProvider = Depends(get_ai_provider),
) -> MissionConversationResponse:
    return await _process_conversation_turn(
        conversation_id, payload.message, request, context, provider,
    )


@router.get("/api/ai/conversations/{conversation_id}", response_model=MissionConversationResponse)
def read_ai_conversation_route(
    conversation_id: str,
    context: AuthContext = Depends(require_permission(Permission.AI_PROPOSE)),
) -> MissionConversationResponse:
    record = get_ai_conversation(conversation_id, context.user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="Mission conversation not found or expired.")
    return MissionConversationResponse(
        conversation_id=record["id"], status=record["status"],
        messages=record["messages"], proposal=record["proposal"],
        proposal_id=record["proposal_id"],
        scenario_id=record["scenario_id"],
        operator_config=record["operator_config"], expires_at=record["expires_at"],
    )


@router.post("/api/ai/conversations/{conversation_id}/scenario")
def transfer_ai_conversation_route(
    conversation_id: str,
    request: Request,
    payload: Optional[MissionConversationScenarioTransferRequest] = None,
    context: AuthContext = Depends(require_permission(Permission.SCENARIO_CREATE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    record = get_ai_conversation(conversation_id, context.user.id)
    if record is None:
        raise HTTPException(status_code=404, detail="Mission conversation not found or expired.")
    if record["status"] != "ready_for_review" or record["proposal"] is None:
        raise HTTPException(status_code=409, detail="Complete the conversation and review its proposal before saving a scenario.")
    try:
        target_scenario_id = payload.scenario_id if payload else None
        if target_scenario_id:
            target_scenario = get_scenario(target_scenario_id, context.user.id)
            if target_scenario is None:
                raise HTTPException(status_code=404, detail="Scenario not found.")
            latest = target_scenario["versions"][0]
            config = SimulationConfig(**latest["config"])
            from ..schemas import ScenarioIntent

            existing_intent = ScenarioIntent.model_validate(latest["intent"])
            proposal = record["proposal"]
            intent_payload = ScenarioIntent(
                **{
                    **existing_intent.model_dump(),
                    "mission_objectives": proposal["mission_objectives"],
                    "proposed_tasks": [
                        {
                            "description": item["description"],
                            "task_reference": item["task_reference"],
                            "assigned_uav_id": item["assigned_uav_id"],
                            "priority": item["priority"],
                        }
                        for item in proposal["proposed_tasks"]
                    ],
                    "uav_role_suggestions": proposal["uav_role_suggestions"],
                    "constraints": list(dict.fromkeys([
                        *existing_intent.constraints, *proposal["constraints"],
                    ]))[:12],
                }
            )
        else:
            config = SimulationConfig(**record["operator_config"])
            from ..schemas import ScenarioIntent

            proposal = record["proposal"]
            intent_payload = ScenarioIntent(
                mission_objectives=proposal["mission_objectives"],
                proposed_tasks=proposal["proposed_tasks"],
                uav_role_suggestions=proposal["uav_role_suggestions"],
                operating_area_notes="",
                environment_notes="",
                constraints=proposal["constraints"],
            )
        config.validate()
        from ..scenario_routes import _validated_intent

        available_aircraft = current_planning_aircraft()
        available_ids = (
            {item["id"] for item in available_aircraft}
            if available_aircraft is not None and intent_payload.geographic_mission is not None
            else None
        )
        intent = _validated_intent(intent_payload, config, available_ids)
        scenario = transfer_ai_conversation_to_scenario(
            conversation_id, context.user.id, intent, target_scenario_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="PX4 telemetry is unavailable; geographic aircraft assignments cannot be validated.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Conversation configuration failed scenario validation.") from exc
    if scenario is None:
        raise HTTPException(status_code=404, detail="Mission conversation not found or expired.")
    _record_ai_event(request, context, "ai_conversation_transferred_to_scenario", "success", "operator_saved_validated_scenario_draft", {
        "scenario_id": scenario["id"], "conversation_id": conversation_id,
        "scenario_version_id": scenario["versions"][0]["id"],
    }, scenario["id"], "scenario")
    return scenario


@router.get("/api/ai/proposals/pending")
def pending_ai_proposals(
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.AI_REVIEW)),
) -> dict:
    _expire_pending_proposals(request)
    return {"proposals": list_pending_mission_proposals()}


@router.get("/api/ai/proposals/{proposal_id}", response_model=MissionProposalResponse)
def read_ai_proposal(
    proposal_id: str,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)),
) -> MissionProposalResponse:
    _expire_pending_proposals(request)
    include_all = has_permission(context.user.role, Permission.MISSION_READ_ALL)
    record = get_mission_proposal(proposal_id, context.user.id, include_all=include_all)
    if record is None:
        raise HTTPException(status_code=404, detail="Mission proposal not found")
    return MissionProposalResponse(
        proposal_id=record["id"], status=record["status"],
        ready_for_review=record["status"] == "pending_review",
        validation_issues=record["validation_issues"],
        proposal=record["proposal"], operator_config=record["operator_config"],
        run_id=record["run_id"], decided_by=record["decided_by"],
        decision_reason=record["decision_reason"],
    )


@router.post("/api/ai/proposals/{proposal_id}/approve")
async def approve_ai_proposal(
    proposal_id: str,
    payload: MissionApprovalRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.AI_REVIEW)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    _expire_pending_proposals(request)
    record = get_mission_proposal(proposal_id, context.user.id, include_all=True)
    if record is None:
        raise HTTPException(status_code=404, detail="Mission proposal not found")
    if not record["validation_issues"] == [] or record["status"] != "pending_review":
        raise HTTPException(status_code=409, detail="Proposal requires clarification or is no longer awaiting review.")
    if record["operator_id"] == context.user.id:
        _record_ai_event(request, context, "ai_proposal_approval", "denied", "requester_cannot_approve_own_proposal", {
            "requester_id": record["operator_id"],
        }, proposal_id, "mission_proposal")
        raise HTTPException(status_code=403, detail="A different authorized operator must approve this proposal.")
    try:
        config = SimulationConfig(**record["operator_config"])
        config.validate()
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail="Stored operator configuration failed deterministic validation.") from exc
    if not claim_mission_proposal_approval(proposal_id, record["operator_id"]):
        raise HTTPException(status_code=409, detail="Proposal is no longer awaiting operator approval.")
    try:
        # The operator configuration is the sole simulator input. AI text/roles/assignments do not configure the engine.
        from ..main import _new_run

        managed = _new_run(config, record["operator_id"])
        finish_mission_proposal_approval(
            proposal_id, record["operator_id"], context.user.id, managed.simulation_id,
        )
    except Exception as exc:
        # Fail closed: a partially persisted run must never become an approvable duplicate.
        fail_mission_proposal_approval(proposal_id, record["operator_id"])
        _record_ai_event(request, context, "ai_proposal_approval", "failed", "ready_run_creation_failed", {
            "model": OPENAI_MODEL,
        }, proposal_id, "mission_proposal")
        raise HTTPException(status_code=503, detail="Approval could not complete run creation. Review mission history before submitting a new proposal.") from exc
    _record_ai_event(request, context, "ai_proposal_approved", "success", "operator_approved_ready_run_created", {
        "operator_config": record["operator_config"], "run_id": managed.simulation_id,
        "requester_id": record["operator_id"], "approver_id": context.user.id,
        "approval_required_start": True,
    }, proposal_id)
    return {"proposal_id": proposal_id, "status": "approved", "run_id": managed.simulation_id,
            "state": managed.simulation.view(), "started": False}


@router.post("/api/ai/proposals/{proposal_id}/reject")
def reject_ai_proposal(
    proposal_id: str,
    payload: MissionRejectionRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.AI_REVIEW)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    _expire_pending_proposals(request)
    record = get_mission_proposal(proposal_id, context.user.id, include_all=True)
    if record is None:
        raise HTTPException(status_code=404, detail="Mission proposal not found")
    if record["operator_id"] == context.user.id:
        _record_ai_event(request, context, "ai_proposal_rejection", "denied", "requester_cannot_reject_own_proposal", {
            "requester_id": record["operator_id"],
        }, proposal_id, "mission_proposal")
        raise HTTPException(status_code=403, detail="A different authorized operator must review this proposal.")
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="A rejection reason is required.")
    if not reject_mission_proposal(proposal_id, record["operator_id"], context.user.id, reason):
        raise HTTPException(status_code=409, detail="Proposal is no longer awaiting review.")
    _record_ai_event(request, context, "ai_proposal_rejected", "success", "independent_reviewer_rejected_proposal", {
        "requester_id": record["operator_id"], "reviewer_id": context.user.id,
        "reason": reason,
    }, proposal_id, "mission_proposal")
    return {"proposal_id": proposal_id, "status": "rejected", "reason": reason}


def _telemetry_evidence(state: dict, context: AuthContext) -> tuple[dict, set[str], set[str]]:
    aircraft = []
    allowed_evidence_ids: set[str] = set()
    for uav_id, uav in state["uavs"].items():
        allowed_evidence_ids.add(uav_id)
        if uav["task_id"]:
            allowed_evidence_ids.add(uav["task_id"])
        aircraft.append({
            "uav_id": uav_id,
            "reported_gps": uav["gps"],
            "assigned_task_id": uav["task_id"],
            "status": uav["status"],
            "threat_level": uav["threat_level"],
            "anomaly_score": uav["anomaly_score"],
            "trajectory_residual": uav["residual"],
        })
    events = []
    for event in state["events"][-40:]:
        allowed_evidence_ids.add(event["id"])
        events.append({key: event[key] for key in ("id", "tick", "type", "uav_id", "task_id", "severity") if key in event})
    allowed_responses = {"continue_monitoring"}
    if state["status"] == "running":
        allowed_responses.update({"pause_simulation", "stop_simulation"})
    evidence = {
        "coordinate_system": state["coordinate_system"],
        "tick": state["tick"],
        "simulation_status": state["status"],
        "validated_progress": state["progress"],
        "reported_telemetry": aircraft,
        "recent_event_metadata": events,
        "permitted_response_suggestions": sorted(allowed_responses),
    }
    return evidence, allowed_evidence_ids, allowed_responses


@router.post("/api/ai/assessments/{run_id}", response_model=MissionAssessmentResponse)
async def assess_mission(
    run_id: str,
    request: Request,
    context: AuthContext = Depends(get_auth_context),
    _csrf: AuthContext = Depends(require_csrf),
    provider: MissionAIProvider = Depends(get_ai_provider),
) -> MissionAssessmentResponse:
    from ..main import _require_run_owner

    managed = _require_run_owner(run_id, context)
    state = managed.simulation.view()
    if not provider.available:
        _record_ai_event(request, context, "ai_mission_assessment", "unavailable", "openai_key_not_configured",
                         {"run_id": run_id, "model": OPENAI_MODEL})
        raise HTTPException(status_code=503, detail="AI is unavailable: configure the backend OpenAI API key. Manual assessment remains available.")
    evidence, allowed_evidence_ids, allowed_responses = _telemetry_evidence(state, context)
    evidence_chars = len(str(evidence))
    if evidence_chars > AI_MAX_INPUT_CHARS:
        # The simulator currently bounds this payload tightly; fail closed if that contract changes.
        _record_ai_event(request, context, "ai_mission_assessment", "denied", "ai_input_limit_exceeded",
                         {"run_id": run_id, "input_chars": evidence_chars, "model": OPENAI_MODEL}, run_id, "run")
        raise HTTPException(status_code=413, detail="Telemetry evidence exceeds the configured AI input limit.")
    try:
        usage_id = _reserve_usage(context, "mission_assessment", evidence_chars)
    except HTTPException as exc:
        reason = "ai_request_limit_exceeded" if exc.status_code == 429 and "request limit" in str(exc.detail).lower() else "ai_usage_limit_exceeded"
        _record_ai_event(request, context, "ai_mission_assessment", "denied", reason,
                         {"run_id": run_id, "model": OPENAI_MODEL})
        raise
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(provider.assess_mission(evidence), timeout=AI_TIMEOUT_SECONDS + 1)
        if not isinstance(result, ProviderResult):
            raise AIProviderMalformedResponse("provider result type invalid")
        assessment = sanitize_assessment(result.parsed, allowed_evidence_ids, allowed_responses)
        finish_ai_usage(usage_id, result.output_tokens, True)
        _record_ai_event(request, context, "ai_mission_assessment", "success", "telemetry_assessment_explained", {
            "provider": "openai", "model": OPENAI_MODEL, "response_id": result.response_id,
            "input_chars": evidence_chars, "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "run_id": run_id, "tick": state["tick"],
        }, run_id, "run")
        return MissionAssessmentResponse(
            run_id=run_id, tick=state["tick"], simulation_status=state["status"],
            validated_evidence=evidence, assessment=assessment,
        )
    except (asyncio.TimeoutError, AIProviderUnavailable, AIProviderFailure, AIProviderMalformedResponse, ProposalValidationError) as exc:
        finish_ai_usage(usage_id, 0, False)
        reason = "provider_timeout" if isinstance(exc, asyncio.TimeoutError) else "invalid_or_unavailable_provider_response"
        _record_ai_event(request, context, "ai_mission_assessment", "failed", reason, {
            "model": OPENAI_MODEL, "run_id": run_id,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }, run_id, "run")
        status = 503 if isinstance(exc, (asyncio.TimeoutError, AIProviderUnavailable, AIProviderFailure)) else 502
        message = "AI service is unavailable. Use the deterministic telemetry and event views." if status == 503 else "AI assessment failed evidence validation. Use the deterministic telemetry and event views."
        raise HTTPException(status_code=status, detail=message) from exc
