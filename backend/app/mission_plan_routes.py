"""Route preview for schema-2 mission plans. Read-only computation: nothing is saved, executed or sent to PX4."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth_routes import require_csrf, require_permission
from .authorization import Permission
from .mission_planning.generators import PlanningLimits
from .mission_planning.schemas import MissionPlan, OBJECTIVE_DEFAULT_TASK, OBJECTIVE_EXTRA_TASKS, PAYLOAD_CATEGORIES, TASK_TYPES
from .mission_planning.validation import estimate_summary, issues_as_dicts, validate_aircraft

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
    }


@router.post("/api/mission-plans/preview-route")
def preview_route(
    payload: RoutePreviewRequest,
    _context=Depends(require_permission(Permission.SCENARIO_CREATE)),
    _csrf=Depends(require_csrf),
) -> dict:
    aircraft = next((item for item in payload.plan.aircraft if item.uav_id == payload.uav_id), None)
    if aircraft is None:
        raise HTTPException(status_code=404, detail={"code": "aircraft_not_found", "message": "That aircraft is not in the plan."})
    issues, route = validate_aircraft(aircraft.model_copy(update={"route": None}), payload.plan,
                                      PlanningLimits.from_config(), require_route=False)
    if issues or route is None:
        raise HTTPException(status_code=422, detail={
            "code": "route_invalid", "message": issues[0].message if issues else "The route could not be generated.",
            "issues": issues_as_dicts(issues),
        })
    return {"route": route.model_dump(mode="json"), "summary": estimate_summary(route, PlanningLimits.from_config())}
