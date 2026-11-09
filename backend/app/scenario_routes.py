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


def _validated_intent(intent: ScenarioIntent | None, config: SimulationConfig) -> dict | None:
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

    from .ai.service import redact_operator_text

    text_values = list(content["mission_objectives"]) + list(content["constraints"])
    text_values += [item["description"] for item in content["proposed_tasks"]]
    text_values += [item["rationale"] for item in content["uav_role_suggestions"]]
    text_values += [content["operating_area_notes"], content["environment_notes"]]
    if any(redact_operator_text(value)[1] for value in text_values):
        raise ValueError("Geographic coordinates are unsupported by the abstract simulator")
    return content


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
        intent = _validated_intent(payload.intent, config)
        scenario = create_scenario(context.user.id, payload.title, config.__dict__, intent)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Scenario configuration is invalid.") from exc
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
        intent = _validated_intent(payload.intent, config) if "intent" in payload.model_fields_set else None
        scenario = save_scenario_version(
            scenario_id, context.user.id, payload.title, config.__dict__, intent,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Scenario configuration is invalid.") from exc
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
