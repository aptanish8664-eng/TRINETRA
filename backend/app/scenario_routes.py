from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .auth_routes import require_permission, require_csrf
from .authorization import Permission, has_permission
from .persistence.database import (
    add_audit_event,
    approved_scenario_config,
    attach_scenario_sandbox_run,
    create_scenario,
    delete_draft_scenario,
    get_scenario,
    list_my_scenario_approval_requests,
    list_scenario_approval_requests,
    list_scenarios,
    review_scenario_request,
    save_scenario_version,
    submit_scenario_version,
)
from .schemas import ScenarioCreateRequest, ScenarioExecutionRequest, ScenarioIntent, ScenarioReviewRequest, ScenarioUpdateRequest
from .security import AuthContext
from .simulation.engine import SimulationConfig
from .telemetry.planning import current_planning_aircraft


router = APIRouter()


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "request-unavailable")


def _audit(request: Request, context: AuthContext, event_type: str, target_type: str, target_id: str, reason: str, metadata: dict | None = None) -> None:
    add_audit_event(
        action=event_type,
        outcome="success",
        reason=reason,
        request_id=_request_id(request),
        actor_id=context.user.id,
        actor_name=context.user.full_name,
        target_type=target_type,
        target_id=target_id,
        metadata=metadata or {},
    )


def _simulation_config(config: dict) -> SimulationConfig:
    candidate = SimulationConfig(**config)
    candidate.validate()
    return candidate


def _validated_intent(
    intent: ScenarioIntent | None,
    config: SimulationConfig,
    available_aircraft_ids: set[str] | None = None,
) -> dict | None:
    if intent is None:
        return None
    content = intent.model_dump(mode="json")
    if len(content["proposed_tasks"]) > config.task_count:
        raise ValueError("Mission intent has more proposed tasks than the simulator task count")
    if sum(item["count"] for item in content["uav_role_suggestions"]) > config.uav_count:
        raise ValueError("Suggested UAV roles exceed the configured fleet")
    seen_task_refs: set[str] = set()
    for task in content["proposed_tasks"]:
        reference = task["task_reference"]
        if reference:
            match = re.fullmatch(r"T-(\d{2})", reference)
            if not match or not 1 <= int(match.group(1)) <= config.task_count or reference in seen_task_refs:
                raise ValueError("Mission intent contains an invalid or duplicate task reference")
            seen_task_refs.add(reference)
        uav_id = task["assigned_uav_id"]
        if uav_id:
            match = re.fullmatch(r"UAV-(\d{2})", uav_id)
            if not match or not 1 <= int(match.group(1)) <= config.uav_count:
                raise ValueError("Mission intent contains an invalid UAV reference")
    for fault in content["uav_fault_assignments"]:
        match = re.fullmatch(r"UAV-(\d{2})", fault["uav_id"])
        if not match or not 1 <= int(match.group(1)) <= config.uav_count:
            raise ValueError("Fault assignments must identify an abstract simulator UAV in the configured fleet")

    from .ai.service import redact_operator_text

    text_values = list(content["mission_objectives"]) + list(content["constraints"])
    text_values += [item["description"] for item in content["proposed_tasks"]]
    text_values += [item["rationale"] for item in content["uav_role_suggestions"]]
    text_values += [content["operating_area_notes"], content["environment_notes"]]
    geographic_mission = content["geographic_mission"]
    if geographic_mission is not None:
        if geographic_mission["start_location"] is None:
            raise ValueError("A geographic mission requires a starting location")
        if geographic_mission["objective"] != "area_survey" and geographic_mission["destination"] is None:
            raise ValueError("This geographic mission requires a destination")
        if not geographic_mission["uav_assignments"]:
            raise ValueError("A geographic mission requires at least one aircraft assignment")
        if available_aircraft_ids is None:
            raise RuntimeError("PX4 telemetry is unavailable for mission assignment validation")
        if any(assignment["uav_id"] not in available_aircraft_ids for assignment in geographic_mission["uav_assignments"]):
            raise ValueError("Mission assignments must use connected PX4 aircraft with valid position telemetry")
        text_values += geographic_mission["constraints"] + geographic_mission["ai_recommendations"]
        text_values += [zone["name"] for zone in geographic_mission["survey_zones"]]
        text_values += [assignment["task"] for assignment in geographic_mission["uav_assignments"]]
    if any(redact_operator_text(value)[1] for value in text_values):
        if geographic_mission is None:
            raise ValueError("Geographic coordinates are unsupported by the abstract simulator")
        raise ValueError("Enter geographic locations on the map, not in free-text fields")
    return content


def _fault_execution_issue(config: dict, intent: dict) -> str | None:
    assignments = intent.get("uav_fault_assignments", [])
    if not assignments:
        return None
    active = [item for item in assignments if item["fault_type"] != "none"]
    if len(active) > 1:
        return (
            "This version records multiple per-UAV fault profiles, but the deterministic simulator "
            "supports only one active GPS fault at a time."
        )
    if not active:
        if config["spoofing_type"] != "none":
            return "The per-UAV fault assignments do not match the simulator configuration."
        return None
    fault = active[0]
    if (
        fault["uav_id"] != config["target_uav"]
        or fault["fault_type"] != config["spoofing_type"]
        or fault["spoof_start"] != config["spoof_start"]
        or fault["spoof_duration"] != config["spoof_duration"]
        or fault["spoof_magnitude"] != config["spoof_magnitude"]
    ):
        return "The per-UAV fault assignment does not match the simulator configuration."
    return None


def _planning_aircraft_ids() -> set[str] | None:
    aircraft = current_planning_aircraft()
    return None if aircraft is None else {item["id"] for item in aircraft}


@router.get("/api/scenarios")
def scenarios(context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    include_all = has_permission(context.user.role, Permission.MISSION_READ_ALL)
    return {"scenarios": list_scenarios(context.user.id, include_all=include_all)}


@router.post("/api/scenarios", status_code=201)
def create_scenario_route(
    payload: ScenarioCreateRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.SCENARIO_CREATE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    try:
        config = _simulation_config(payload.config.model_dump())
        available_aircraft = _planning_aircraft_ids() if payload.intent and payload.intent.geographic_mission else None
        intent = _validated_intent(payload.intent, config, available_aircraft)
        scenario = create_scenario(context.user.id, payload.title, config.__dict__, intent)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="PX4 telemetry is unavailable; geographic aircraft assignments cannot be validated.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(request, context, "scenario_created", "scenario", scenario["id"], "User created a scenario draft.")
    return scenario


@router.get("/api/scenarios/{scenario_id}")
def read_scenario(
    scenario_id: str,
    context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)),
) -> dict:
    include_all = has_permission(context.user.role, Permission.MISSION_READ_ALL)
    scenario = get_scenario(scenario_id, context.user.id, include_all=include_all)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    return scenario


@router.put("/api/scenarios/{scenario_id}")
def update_scenario(
    scenario_id: str,
    payload: ScenarioUpdateRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.SCENARIO_CREATE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    try:
        config = _simulation_config(payload.config.model_dump())
        available_aircraft = (
            _planning_aircraft_ids()
            if payload.intent and payload.intent.geographic_mission
            else None
        )
        intent = _validated_intent(payload.intent, config, available_aircraft) if "intent" in payload.model_fields_set else None
        scenario = save_scenario_version(
            scenario_id, context.user.id, payload.title, config.__dict__, intent,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="PX4 telemetry is unavailable; geographic aircraft assignments cannot be validated.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    latest = scenario["versions"][0]
    _audit(
        request,
        context,
        "scenario_version_saved",
        "scenario_version",
        latest["id"],
        "User saved a scenario version.",
        {"scenario_id": scenario_id, "version_number": latest["version_number"]},
    )
    return scenario


@router.delete("/api/scenarios/{scenario_id}", status_code=204, response_class=Response)
def delete_scenario_route(
    scenario_id: str,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.SCENARIO_CREATE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> Response:
    try:
        deleted = delete_draft_scenario(scenario_id, context.user.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    _audit(request, context, "scenario_deleted", "scenario", scenario_id, "User deleted an unsubmitted draft.")
    return Response(status_code=204)


@router.post("/api/scenarios/{scenario_id}/versions/{version_id}/submit")
def submit_scenario(
    scenario_id: str,
    version_id: str,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.SCENARIO_CREATE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    try:
        result = submit_scenario_version(scenario_id, version_id, context.user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(
        request,
        context,
        "scenario_submitted",
        "scenario_version",
        version_id,
        "User submitted an immutable scenario version for General review.",
        {"scenario_id": scenario_id, "approval_request_id": result["id"]},
    )
    return result


@router.get("/api/scenarios/my-approval-requests")
def my_scenario_approval_requests(
    context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)),
) -> dict:
    return {"requests": list_my_scenario_approval_requests(context.user.id)}


@router.get("/api/general/scenario-approvals")
def pending_scenario_approvals(
    context: AuthContext = Depends(require_permission(Permission.SCENARIO_REVIEW)),
) -> dict:
    return {"requests": list_scenario_approval_requests(pending_only=True)}


@router.post("/api/general/scenario-approvals/{request_id}/decision")
def decide_scenario(
    request_id: str,
    payload: ScenarioReviewRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.SCENARIO_REVIEW)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    try:
        result = review_scenario_request(
            request_id, context.user.id, payload.decision, payload.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(
        request,
        context,
        f"scenario_{payload.decision}",
        "scenario_approval_request",
        request_id,
        payload.reason,
        {"scenario_version_id": result["scenario_version_id"]},
    )
    return result


@router.post("/api/scenarios/{scenario_id}/versions/{version_id}/sandbox")
def run_scenario_sandbox(
    scenario_id: str,
    version_id: str,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.SCENARIO_CREATE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    scenario = get_scenario(scenario_id, context.user.id)
    version = next(
        (item for item in scenario["versions"] if item["id"] == version_id),
        None,
    ) if scenario else None
    if version is None:
        raise HTTPException(status_code=404, detail="Scenario version not found.")
    if version["intent"].get("geographic_mission") is not None:
        raise HTTPException(status_code=409, detail="Geographic mission drafts are not executable in the abstract simulator.")
    fault_issue = _fault_execution_issue(version["config"], version["intent"])
    if fault_issue:
        raise HTTPException(status_code=409, detail=fault_issue)
    try:
        config = _simulation_config(version["config"])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Stored scenario configuration is invalid.") from exc
    from .main import _new_run

    managed = _new_run(config, context.user.id)
    attach_scenario_sandbox_run(scenario_id, version_id, context.user.id, managed.simulation_id)
    _audit(
        request, context, "scenario_sandbox_run_created", "scenario_version", version_id,
        "User created an unrestricted sandbox run for a saved scenario version.",
        {"run_id": managed.simulation_id},
    )
    return {"run_id": managed.simulation_id, "state": managed.simulation.view()}


@router.post("/api/scenarios/{scenario_id}/execute")
async def execute_approved_scenario(
    scenario_id: str,
    payload: ScenarioExecutionRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.SCENARIO_EXECUTE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    scenario = get_scenario(scenario_id, context.user.id)
    version = next(
        (item for item in scenario["versions"] if item["id"] == payload.version_id),
        None,
    ) if scenario else None
    if version and version["intent"].get("geographic_mission") is not None:
        raise HTTPException(status_code=409, detail="Geographic missions require a future validated route-execution workflow.")
    if version:
        fault_issue = _fault_execution_issue(version["config"], version["intent"])
        if fault_issue:
            raise HTTPException(status_code=409, detail=fault_issue)
    config_json = approved_scenario_config(scenario_id, payload.version_id)
    if config_json is None:
        add_audit_event(
            action="scenario_execution_denied",
            outcome="denied",
            reason="Scenario version is unapproved, superseded, or failed its integrity check.",
            request_id=_request_id(request),
            actor_id=context.user.id,
            actor_name=context.user.full_name,
            target_type="scenario_version",
            target_id=payload.version_id,
        )
        raise HTTPException(status_code=403, detail="Only the current approved scenario version can be executed.")
    try:
        config = _simulation_config(config_json)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Approved scenario configuration is invalid.") from exc
    from .main import _new_run

    managed = _new_run(config, context.user.id)
    _audit(
        request, context, "approved_scenario_executed", "scenario_version", payload.version_id,
        "User executed a scenario version after backend approval and integrity verification.",
        {"scenario_id": scenario_id, "run_id": managed.simulation_id},
    )
    return {"run_id": managed.simulation_id, "state": managed.simulation.view()}
