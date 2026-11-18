"""Route preview for schema-2 mission plans. Read-only computation: nothing is saved, executed or sent to PX4."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth_routes import require_csrf, require_permission
from .authorization import Permission
from .mission_planning.generators import PlanningLimits
from .mission_planning.schemas import MissionPlan, OBJECTIVE_DEFAULT_TASK, OBJECTIVE_EXTRA_TASKS, PAYLOAD_CATEGORIES, TASK_TYPES
from .mission_planning.quick import ParseRequest, QuickError, QuickRequest, apply_auto_limits, build_quick_plan, parse_mission_text, rules_payload
from .mission_planning.validation import estimate_summary, issues_as_dicts, plan_warnings, validate_aircraft

router = APIRouter()


class RoutePreviewRequest(BaseModel):
    plan: MissionPlan
    uav_id: str


@router.get("/api/mission-plans/limits")
def planning_limits(_context=Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    return {
        "schema_version": 2, "limits": asdict(PlanningLimits.from_config()), "task_types": list(TASK_TYPES),
        "payload_categories": list(PAYLOAD_CATEGORIES), "objective_default_task": OBJECTIVE_DEFAULT_TASK,
        "objective_extra_tasks": {key: list(value) for key, value in OBJECTIVE_EXTRA_TASKS.items()},
        # Quick mission: the types, defaults and every derivation rule (the frontend keeps no second copy).
        "quick": rules_payload(),
    }


@router.post("/api/mission-plans/preview-route")
def preview_route(
    payload: RoutePreviewRequest,
    _context=Depends(require_permission(Permission.SCENARIO_CREATE)),
    _csrf=Depends(require_csrf),
) -> dict:
    """Generates and validates one aircraft's route. Limits flagged `auto` are derived from the route first (route, then limits, then validation)."""
    aircraft = next((item for item in payload.plan.aircraft if item.uav_id == payload.uav_id), None)
    if aircraft is None:
        raise HTTPException(status_code=404, detail={"code": "aircraft_not_found", "message": "That aircraft is not in the plan."})
    limits = PlanningLimits.from_config()
    plan = payload.plan
    auto = plan.auto_limits
    derived = None
    if auto.max_distance_from_start_m or auto.max_altitude_m or auto.max_duration_minutes:
        constraints = plan.constraints.model_copy(update={
            "max_distance_from_start_m": None if auto.max_distance_from_start_m else plan.constraints.max_distance_from_start_m,
            "max_altitude_m": None if auto.max_altitude_m else plan.constraints.max_altitude_m})
        probe = plan.model_copy(update={"constraints": constraints, "max_duration_minutes": None if auto.max_duration_minutes else plan.max_duration_minutes})
        issues, route = validate_aircraft(aircraft.model_copy(update={"route": None}), probe, limits, require_route=False)
        if issues or route is None:
            raise HTTPException(status_code=422, detail={"code": "route_invalid", "message": issues[0].message if issues else "The route could not be generated.",
                                                         "issues": issues_as_dicts(issues)})
        routed = probe.model_copy(update={"aircraft": [item.model_copy(update={"route": route}) if item.uav_id == aircraft.uav_id else item for item in probe.aircraft]})
        plan, derived = apply_auto_limits(plan.model_copy(update={"aircraft": routed.aircraft}), limits)
    issues, route = validate_aircraft(aircraft.model_copy(update={"route": None}), plan, limits, require_route=False)
    if issues or route is None:
        raise HTTPException(status_code=422, detail={
            "code": "route_invalid", "message": issues[0].message if issues else "The route could not be generated.",
            "issues": issues_as_dicts(issues),
        })
    return {"route": route.model_dump(mode="json"), "summary": estimate_summary(route, limits), "warnings": issues_as_dicts(plan_warnings(aircraft, plan, limits, route)),
            "limits": ({"max_distance_from_start_m": plan.constraints.max_distance_from_start_m, "max_altitude_m": plan.constraints.max_altitude_m,
                        "max_duration_minutes": plan.max_duration_minutes, "auto": plan.auto_limits.model_dump()} if derived else None)}


@router.post("/api/mission-plans/quick")
def quick_mission(
    payload: QuickRequest,
    _context=Depends(require_permission(Permission.SCENARIO_CREATE)),
    _csrf=Depends(require_csrf),
) -> dict:
    """Builds a complete draft plan from a mission type and one point. Computation only: nothing is saved, deployed or sent to PX4."""
    try:
        return build_quick_plan(payload, PlanningLimits.from_config())
    except QuickError as error:
        raise HTTPException(status_code=422, detail={"code": "quick_mission_invalid", "message": str(error), "issues": issues_as_dicts(error.issues)}) from error


@router.post("/api/mission-plans/quick/parse")
def quick_mission_parse(
    payload: ParseRequest,
    _context=Depends(require_permission(Permission.SCENARIO_CREATE)),
    _csrf=Depends(require_csrf),
) -> dict:
    """The assistant's offline sentence parser: state in, state and at most one question out. It has no side effects."""
    return parse_mission_text(payload, PlanningLimits.from_config())
