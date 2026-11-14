from __future__ import annotations

import re
from datetime import datetime, timezone

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
    latest_mission_deployment,
    review_scenario_request,
    save_mission_deployment,
    save_scenario_version,
    submit_scenario_version,
)
from .mission_planning.generators import PlanningLimits, RouteError, generate_route
from .mission_planning.schemas import AircraftPlan, MissionPlan
from .mission_planning.start import mission_start
from .mission_planning.validation import PlanIssue, PlanValidationError, issues_as_dicts, validate_plan
from .schemas import ScenarioCreateRequest, ScenarioExecutionRequest, ScenarioIntent, ScenarioReviewRequest, ScenarioUpdateRequest
from .security import AuthContext
from .simulation.engine import SimulationConfig
from .telemetry.planning import current_planning_aircraft
from .telemetry.fleet_manager import invoke_fleet_manager


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
    plan_aircraft_ids = set((content.get("mission_plan") or {}).get("selected_uav_ids") or [])
    for fault in content["uav_fault_assignments"]:
        if fault["uav_id"].startswith("px4-"):
            if fault["uav_id"] not in plan_aircraft_ids:
                raise ValueError("Fault assignments must target an aircraft in the mission plan")
            continue
        match = re.fullmatch(r"UAV-(\d{2})", fault["uav_id"])
        if not match or not 1 <= int(match.group(1)) <= config.uav_count:
            raise ValueError("Fault assignments must identify an abstract simulator UAV in the configured fleet")

    from .ai.service import redact_operator_text

    text_values = list(content["mission_objectives"]) + list(content["constraints"])
    text_values.append(content["mission_description"])
    text_values += [item["description"] for item in content["proposed_tasks"]]
    text_values += [item["rationale"] for item in content["uav_role_suggestions"]]
    text_values += [content["operating_area_notes"], content["environment_notes"]]
    mission_plan = content.get("mission_plan")
    if mission_plan is not None:
        plan = MissionPlan.model_validate(mission_plan)
        issues = validate_plan(plan, PlanningLimits.from_config(), complete=plan.workflow_stage in {"faults", "review"})
        if issues:
            raise PlanValidationError(issues)
        if plan.selected_uav_ids or plan.aircraft:
            if available_aircraft_ids is None:
                raise RuntimeError("PX4 telemetry is unavailable for mission assignment validation")
            if any(uav_id not in available_aircraft_ids for uav_id in plan.selected_uav_ids):
                raise ValueError("Selected aircraft must be connected PX4 aircraft with valid position telemetry")
        if plan.workflow_stage in {"general", "aircraft"} and content["uav_fault_assignments"]:
            raise ValueError("Configure experimental faults only after completing all individual aircraft missions")
        text_values += [plan.description, plan.notes]
    geographic_mission = content["geographic_mission"]
    if geographic_mission is not None:
        stage = geographic_mission["workflow_stage"]
        assignments = geographic_mission["uav_assignments"]
        selected_ids = geographic_mission["selected_uav_ids"]
        mission_complete = stage in {"faults", "review"}
        if selected_ids or assignments:
            if available_aircraft_ids is None:
                raise RuntimeError("PX4 telemetry is unavailable for mission assignment validation")
            if any(uav_id not in available_aircraft_ids for uav_id in selected_ids):
                raise ValueError("Selected aircraft must be connected PX4 aircraft with valid position telemetry")
            if any(assignment["uav_id"] not in available_aircraft_ids for assignment in assignments):
                raise ValueError("Mission assignments must use connected PX4 aircraft with valid position telemetry")
        if mission_complete:
            fleet_size = geographic_mission["fleet_size"]
            required_count = geographic_mission["required_uav_count"]
            activated_ids = geographic_mission["activated_uav_ids"]
            if (fleet_size is None or required_count != fleet_size or len(selected_ids) != fleet_size
                    or len(activated_ids) != fleet_size or set(activated_ids) != set(selected_ids)
                    or len(assignments) != fleet_size or {item["uav_id"] for item in assignments} != set(selected_ids)):
                raise ValueError("Complete every selected aircraft configuration before entering faults or review")
            if any(
                not assignment["confirmed"] or not assignment["role"] or not assignment["task"]
                or assignment["start_location"] is None
                or assignment["destination"] is None
                or not assignment["individual_objective"] or not assignment["destination_action"]
                or assignment["action_duration_seconds"] is None or not assignment["data_requirements"]
                or not assignment["completion_condition"] or not assignment["expected_result"]
                or assignment["return_destination"] is None or not assignment["return_completion_condition"]
                or assignment["flight_altitude_m"] is None or assignment["speed_limit_m_s"] is None
                for assignment in assignments
            ):
                raise ValueError("Confirm each aircraft after completing its individual objective, task, route, destination action, data and completion criteria, speed/altitude, and return plan")
            if geographic_mission["objective"] in {
                "area_survey", "perimeter_monitoring", "agricultural_survey", "mapping_survey",
            } and not geographic_mission["survey_zones"]:
                raise ValueError("This mission objective requires at least one mapped survey zone")
        if stage in {"general", "aircraft"} and content["uav_fault_assignments"]:
            raise ValueError("Configure experimental faults only after completing all individual aircraft missions")
        text_values += geographic_mission["constraints"] + geographic_mission["ai_recommendations"]
        text_values += [zone["name"] for zone in geographic_mission["survey_zones"]]
        text_values += [
            value
            for assignment in assignments
            for value in [assignment["role"], assignment["task"], *assignment["constraints"]]
        ]
    if any(redact_operator_text(value)[1] for value in text_values):
        if geographic_mission is None and mission_plan is None:
            raise ValueError("Geographic coordinates are unsupported by the abstract simulator")
        raise ValueError("Enter geographic locations on the map, not in free-text fields")
    return content


def _plan_error_detail(error: PlanValidationError) -> dict:
    return {"code": "mission_plan_invalid", "message": str(error), "issues": issues_as_dicts(error.issues)}


def _plan_readiness(plan: MissionPlan) -> list[PlanIssue]:
    issues = validate_plan(plan, PlanningLimits.from_config(), complete=True)
    if plan.workflow_stage != "review":
        issues.append(PlanIssue(code="workflow", message="The A–C workflow must reach final review", field="workflow_stage"))
    return issues


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


def _mission_readiness_errors(mission) -> list[str]:
    if mission is None:
        return ["A geographic mission plan is required"]
    count = mission.required_uav_count
    selected = mission.selected_uav_ids
    assignments = mission.uav_assignments
    errors = []
    if mission.workflow_stage != "review":
        errors.append("The scenario workflow must reach final review")
    if count is None or mission.fleet_size != count or len(selected) != count:
        errors.append("Requested and selected UAV counts must match")
    if len(mission.activated_uav_ids) != count or set(mission.activated_uav_ids) != set(selected):
        errors.append("Every selected UAV must belong to the verified activated fleet")
    if len(assignments) != count or {item.uav_id for item in assignments} != set(selected):
        errors.append("Every selected UAV needs exactly one individual assignment")
    required = all(
        item.confirmed and item.role.strip() and item.individual_objective.strip() and item.task.strip()
        and item.start_location and item.destination and item.flight_altitude_m and item.speed_limit_m_s
        and item.destination_action.strip() and item.action_duration_seconds is not None
        and item.data_requirements.strip() and item.completion_condition.strip() and item.expected_result.strip()
        and item.return_destination and item.return_completion_condition.strip()
        for item in assignments
    )
    if not required:
        errors.append("Every UAV needs a confirmed objective, route, action, data/completion criteria, speed/altitude and return plan")
    if not mission.mission_description.strip() or not mission.mission_area_description.strip():
        errors.append("Mission purpose and area are required")
    if not mission.objectives:
        errors.append("At least one mission objective is required")
    if mission.objective in {"area_survey", "perimeter_monitoring", "agricultural_survey", "mapping_survey"} and not mission.survey_zones:
        errors.append("The selected area-based objective needs a mapped survey area")
    return errors


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
        available_aircraft = _planning_aircraft_ids() if payload.intent and (payload.intent.geographic_mission or payload.intent.mission_plan) else None
        intent = _validated_intent(payload.intent, config, available_aircraft)
        scenario = create_scenario(context.user.id, payload.title, config.__dict__, intent)
    except PlanValidationError as exc:
        raise HTTPException(status_code=422, detail=_plan_error_detail(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="PX4 telemetry is unavailable; geographic aircraft assignments cannot be validated.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(request, context, "scenario_created", "scenario", scenario["id"], "User created a scenario draft.")
    return scenario


# Declared before `/api/scenarios/{scenario_id}`: routes match in order, and after it this path was captured as a
# scenario id and always answered 404 "Scenario not found.".
@router.get("/api/scenarios/my-approval-requests")
def my_scenario_approval_requests(
    context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)),
) -> dict:
    return {"requests": list_my_scenario_approval_requests(context.user.id)}


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
            if payload.intent and (payload.intent.geographic_mission or payload.intent.mission_plan)
            else None
        )
        intent = _validated_intent(payload.intent, config, available_aircraft) if "intent" in payload.model_fields_set else None
        scenario = save_scenario_version(
            scenario_id, context.user.id, payload.title, config.__dict__, intent,
        )
    except PlanValidationError as exc:
        raise HTTPException(status_code=422, detail=_plan_error_detail(exc)) from exc
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
    if context.user.role != "OPERATOR":
        raise HTTPException(status_code=403, detail="Only Operator accounts may submit a scenario for General review.")
    scenario = get_scenario(scenario_id, context.user.id, include_all=False)
    if scenario is not None:
        version = next((item for item in scenario.get("versions", []) if item.get("id") == version_id), None)
        if version and version.get("intent", {}).get("mission_plan") is not None:
            try:
                plan = ScenarioIntent.model_validate(version["intent"]).mission_plan
            except Exception as exc:
                raise HTTPException(status_code=422, detail="The immutable mission plan is invalid.") from exc
            assert plan is not None
            plan_issues = _plan_readiness(plan)
            if plan_issues:
                raise HTTPException(status_code=422, detail={"code": "mission_plan_invalid", "message": "Mission validation is incomplete.", "issues": issues_as_dicts(plan_issues)})
        elif version and version.get("intent", {}).get("geographic_mission") is not None:
            try:
                mission = ScenarioIntent.model_validate(version["intent"]).geographic_mission
            except Exception as exc:
                raise HTTPException(status_code=422, detail="The immutable geographic mission is invalid.") from exc
            issues = _mission_readiness_errors(mission)
            if issues:
                raise HTTPException(status_code=422, detail={"message": "Mission validation is incomplete.", "issues": issues})
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


@router.post("/api/scenarios/{scenario_id}/versions/{version_id}/deploy")
def deploy_scenario(
    scenario_id: str,
    version_id: str,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.SCENARIO_CREATE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict:
    include_all = has_permission(context.user.role, Permission.MISSION_READ_ALL)
    scenario = get_scenario(scenario_id, context.user.id, include_all=include_all)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    versions = scenario.get("versions", [])
    version = next((item for item in versions if item.get("id") == version_id), None)
    if version is None:
        raise HTTPException(status_code=404, detail="Scenario version not found.")
    if not versions or versions[0].get("id") != version_id:
        raise HTTPException(status_code=409, detail={
            "code": "version_superseded", "message": "Only the latest immutable scenario version can be deployed.",
            "latest_version_id": versions[0].get("id") if versions else None,
            "latest_version_number": versions[0].get("version_number") if versions else None})
    try:
        intent = ScenarioIntent.model_validate(version.get("intent") or {})
    except Exception as exc:
        raise HTTPException(status_code=422, detail="The immutable scenario intent is invalid.") from exc
    plan = intent.mission_plan
    mission = intent.geographic_mission
    if plan is None and mission is None:
        raise HTTPException(status_code=422, detail="A complete geographic mission plan is required before deployment.")
    if plan is not None:
        plan_issues = _plan_readiness(plan)
        if plan_issues:
            raise HTTPException(status_code=422, detail={"code": "mission_plan_invalid", "message": "Mission validation is incomplete.", "issues": issues_as_dicts(plan_issues)})
        count, selected, assignments = plan.required_uav_count, plan.selected_uav_ids, plan.aircraft
    else:
        count = mission.required_uav_count
        selected = mission.selected_uav_ids
        assignments = mission.uav_assignments
        issues = _mission_readiness_errors(mission)
        if issues:
            raise HTTPException(status_code=422, detail={"message": "Mission validation is incomplete.", "issues": issues})
    process_state = invoke_fleet_manager("status", timeout_seconds=12)
    running = {int(item.get("system_id", -1)) for item in process_state.get("instances", [])
               if isinstance(item, dict) and not process_state.get("failure")}
    from .fleet_routes import _verified_fleet
    verified, _states = _verified_fleet(count)
    verified_ids = {item["aircraft_id"] for item in verified}
    required_system_ids = {int(item[len("px4-"):]) for item in selected}
    if process_state.get("failure") or not required_system_ids.issubset(running) or not set(selected).issubset(verified_ids):
        raise HTTPException(status_code=503, detail={
            "code": "fleet_not_verified",
            "message": "Deployment is blocked until every selected PX4 process, telemetry source and matching Gazebo pose are verified. Run Activate and verify.",
            "manager_error": process_state.get("failure"),
            "unverified": sorted(set(selected) - verified_ids),
            "process_missing": sorted(f"px4-{item}" for item in required_system_ids - running)})
    payload = {
        "mission_id": scenario_id, "scenario_id": scenario_id,
        "scenario_version_id": version_id, "scenario_version_hash": version.get("config_hash"),
        "scenario_version_number": version.get("version_number"),
        "mission_start": mission_start(plan),
        "scenario_title": scenario.get("title"), "role": context.user.role,
        "approval": version.get("approval"), "validation": {"passed": True, "validated_at": datetime.now(timezone.utc).isoformat()},
        "requested_uav_count": count, "activated_uav_count": len(verified),
        "assigned_uav_count": len(assignments), "fleet": verified,
        "config": version.get("config"), "intent": intent.model_dump(mode="json"),
        "execution_status": "DEPLOYED_TO_MISSION_CONTROL_NOT_UPLOADED_TO_PX4",
    }
    try:
        result = save_mission_deployment(scenario_id, version_id, context.user.id, context.user.role, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Scenario version not found.") from exc
    except ValueError as exc:
        text = str(exc)
        code = "approval_required" if "approval" in text else "version_superseded" if "latest immutable" in text else "deploy_conflict"
        raise HTTPException(status_code=409, detail={"code": code, "message": text + "."}) from exc
    _audit(request, context, "mission_deployed_to_control", "mission_deployment", result["id"],
           "Validated immutable mission handed to Mission Control; no PX4 upload or flight command was sent.",
           {"scenario_id": scenario_id, "scenario_version_id": version_id, "mission_id": scenario_id})
    return _deployment_details(result, scenario)


def _deployment_details(deployment: dict, scenario: dict | None) -> dict:
    """Facts Mission Control shows about a deployment, computed on read so older records gain them too."""
    intent = deployment.get("intent") or {}
    plan = None
    if intent.get("schema_version") == 2 and intent.get("mission_plan") is not None:
        try:
            plan = MissionPlan.model_validate(intent["mission_plan"])
        except Exception:
            plan = None
    versions = (scenario or {}).get("versions", [])
    version = next((item for item in versions if item.get("id") == deployment.get("scenario_version_id")), None)
    latest = versions[0] if versions else None
    return {
        **deployment,
        "schema_version": 2 if plan is not None else 1,
        "legacy": plan is None,
        "mission_start": mission_start(plan),
        "scenario_version_number": version.get("version_number") if version else deployment.get("scenario_version_number"),
        "latest_version_number": latest.get("version_number") if latest else None,
        "superseded": bool(latest and version and latest.get("id") != version.get("id")),
    }


@router.get("/api/mission-control/deployments/latest")
def get_latest_deployment(
    context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)),
) -> dict:
    include_all = has_permission(context.user.role, Permission.MISSION_READ_ALL)
    deployment = latest_mission_deployment(context.user.id, include_all=include_all)
    if deployment is None:
        return {"deployment": None}
    scenario = get_scenario(deployment["scenario_id"], context.user.id, include_all=include_all)
    return {"deployment": _deployment_details(deployment, scenario)}


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
    if version["intent"].get("geographic_mission") is not None or version["intent"].get("mission_plan") is not None:
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
    if version and (version["intent"].get("geographic_mission") is not None or version["intent"].get("mission_plan") is not None):
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
