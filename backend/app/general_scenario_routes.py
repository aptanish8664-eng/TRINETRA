"""GENERAL's saved-scenarios manager: every user's scenarios, export, and the cascade delete. Reads, plus one explicit delete."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .auth_routes import require_csrf, require_permission
from .authorization import Permission
from .persistence.database import add_audit_event, delete_scenario_cascade, list_scenarios_admin, scenario_delete_preview, scenario_export
from .security import AuthContext

router = APIRouter()


class DeleteScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_name: str = Field(min_length=1, max_length=120)
    delete_results: bool = False


@router.get("/api/general/scenarios")
def general_scenarios(owner: Optional[str] = None, status: Optional[str] = None, q: Optional[str] = None,
                      _context: AuthContext = Depends(require_permission(Permission.SCENARIO_REVIEW))) -> dict:
    rows = list_scenarios_admin()
    if owner:
        rows = [row for row in rows if owner in (row["owner_user_id"], row["owner_username"])]
    if status:
        rows = [row for row in rows if row["status"] == status]
    if q:
        needle = q.strip().casefold()
        rows = [row for row in rows if needle in row["title"].casefold()]
    return {"scenarios": rows}


@router.get("/api/general/scenarios/{scenario_id}/export")
def general_scenario_export(scenario_id: str, _context: AuthContext = Depends(require_permission(Permission.SCENARIO_REVIEW))) -> dict:
    record = scenario_export(scenario_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    return record


@router.get("/api/general/scenarios/{scenario_id}/delete-preview")
def general_delete_preview(scenario_id: str, _context: AuthContext = Depends(require_permission(Permission.SCENARIO_REVIEW))) -> dict:
    preview = scenario_delete_preview(scenario_id)
    if preview is None:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    return preview


@router.post("/api/general/scenarios/{scenario_id}/delete")
def general_delete_scenario(scenario_id: str, payload: DeleteScenarioRequest, request: Request,
                            context: AuthContext = Depends(require_permission(Permission.SCENARIO_REVIEW)),
                            _csrf: AuthContext = Depends(require_csrf)) -> dict:
    preview = scenario_delete_preview(scenario_id)
    if preview is None:
        raise HTTPException(status_code=404, detail="Scenario not found.")
    if payload.confirm_name.strip() != preview["title"]:
        raise HTTPException(status_code=422, detail={"code": "name_mismatch", "message": "Type the scenario name exactly to confirm the delete."})
    request_id = getattr(request.state, "request_id", "request-unavailable")
    try:
        outcome = delete_scenario_cascade(scenario_id, delete_results=payload.delete_results)
    except ValueError as exc:
        add_audit_event(action="scenario_deleted_by_general", outcome="refused", reason=str(exc), request_id=request_id, actor_id=context.user.id,
                        actor_name=context.user.full_name, target_type="scenario", target_id=scenario_id, metadata={"title": preview["title"]})
        raise HTTPException(status_code=409, detail={"code": "delete_blocked", "message": str(exc), "blockers": preview["blockers"]}) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Scenario not found.") from exc
    add_audit_event(action="scenario_deleted_by_general", outcome="success", reason=f"GENERAL deleted scenario {outcome['title']!r} with all of its records.",
                    request_id=request_id, actor_id=context.user.id, actor_name=context.user.full_name, target_type="scenario", target_id=scenario_id,
                    metadata={"title": outcome["title"], "counts": outcome["counts"], "content_sha256": outcome["content_hash"], "results_deleted": payload.delete_results})
    return outcome
