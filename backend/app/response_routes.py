"""Operator responses to a GPS threat (Part 4): real single-aircraft actions in the simulator.

Software-only: every command goes through the existing Mission Control paths (the onboard executor link to the managed PX4 SITL
instance). Nothing is sent to a real aircraft. Each response is chosen by an operator, confirmed, executed through the same code as
the matching Mission Control button, audited together with the live score, tier and evidence the server itself holds at that moment,
and stored as a research decision (which the live experiment run record lists as an operator response).

Single-aircraft actions: acknowledge (no command), hold (pause the mission), resume, return to launch (abort), land here.
Restart is the existing Restart-at-mission-start flow (the card opens it). Multi-aircraft actions (hand over, reassign) are listed but
disabled: they need more than one active aircraft.
"""
from __future__ import annotations

import time
from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import execution_routes as execution
from . import research_routes
from .auth_routes import require_csrf, require_permission
from .authorization import Permission
from .persistence.database import active_mission_execution, add_audit_event, record_research_decision
from .research import continuity
from .research_routes import DecisionRequest, check_handover
from .security import AuthContext

router = APIRouter()
LABELS = {
    "acknowledge": "Acknowledge and continue", "hold": "Hold (pause the mission)", "resume": "Resume the mission",
    "return_to_launch": "Return to launch", "land_here": "Land here", "restart": "Restart the simulation at the mission start",
    "handover_to_standby": "Hand over to a standby aircraft",
}
MEANING = {
    "acknowledge": "Records that you have seen the threat and want to carry on. No command is sent to the aircraft.",
    "hold": "Pauses the mission: the aircraft stops and holds its position (simulation only). You can resume afterwards.",
    "resume": "Resumes a paused mission from where it stopped.",
    "return_to_launch": "Aborts the mission and flies the aircraft back to launch (simulation only). This ends the run.",
    "land_here": "Lands the aircraft where it is now and disarms it. It does not return to launch.",
    "restart": "Stops the simulation and starts it again at the mission start. The run ends and the aircraft is reset.",
    "handover_to_standby": "Hands the task to another aircraft.",
}
EXECUTABLE = ("acknowledge", "hold", "resume", "return_to_launch", "land_here", "handover_to_standby", "switch_primary")
MULTI_REASON = "Needs more than one active aircraft."
HANDOVER_START_TOLERANCE_M = 500.0
LABELS["switch_primary"] = "Switch primary to another flying aircraft"
MEANING["switch_primary"] = ("Swaps the roles of this primary and a decoy that is flying: the decoy becomes the primary and this aircraft becomes a decoy. "
                             "No command is sent to either aircraft; only the roles the threat monitor uses change (for this flight).")
MEANING["handover_to_standby"] = ("Sends this aircraft back to launch and starts the chosen standby aircraft on its OWN planned route. The standby does not continue "
                                  "from where this aircraft stopped; the roles are swapped (the standby becomes the primary).")


class ResponseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aircraft_id: str = Field(pattern=r"^px4-[1-9][0-9]{0,2}$")
    action: Literal["acknowledge", "hold", "resume", "return_to_launch", "land_here", "handover_to_standby", "switch_primary"]
    candidate: Optional[str] = Field(default=None, pattern=r"^px4-[1-9][0-9]{0,2}$")
    confirm: bool = False


def _live_row(context: AuthContext, aircraft_id: str) -> Optional[dict[str, Any]]:
    if research_routes.telemetry_store is None:
        return None
    return next((item for item in research_routes.live_payload(context)["aircraft"] if item["aircraft_id"] == aircraft_id), None)


def _vehicle(aircraft_id: str) -> Optional[dict[str, Any]]:
    from .fleet_routes import _fleet_snapshot
    return next((item for item in _fleet_snapshot()[0] if item.get("id") == aircraft_id), None)


def _flying_count(context: AuthContext) -> int:
    if research_routes.telemetry_store is None:
        return 0
    return sum(1 for item in research_routes.live_payload(context)["aircraft"] if item["state"] in ("live", "warming_up", "link_lost"))


def _roles(context: AuthContext) -> dict[str, str]:
    return research_routes._deployment_roles(context)


def _vehicles() -> list[dict[str, Any]]:
    from .fleet_routes import _fleet_snapshot
    return _fleet_snapshot()[0]


def _candidates(context: AuthContext, aircraft_id: str, kind: str) -> list[dict[str, Any]]:
    """Other aircraft that could take over (`handover`: a standby on the ground with enough battery) or swap roles (`switch`: a flying decoy)."""
    roles = _roles(context)
    live = {item["aircraft_id"]: item for item in (research_routes.live_payload(context)["aircraft"] if research_routes.telemetry_store is not None else [])}
    minimum = float(research_routes.MIN_HANDOVER_BATTERY_PCT)
    rows = []
    for vehicle in _vehicles() or []:
        other = vehicle.get("id")
        if other == aircraft_id:
            continue
        role = roles.get(other)
        battery = (vehicle.get("battery") or {}).get("remaining_percent")
        connected = vehicle.get("status") == "connected"
        flying = bool(vehicle.get("armed")) or (live.get(other) or {}).get("state") in ("live", "warming_up")
        if kind == "handover":
            reason = None if role == "standby" else f"role is {role or 'unknown'}, not standby"
            reason = reason or (None if connected else "no telemetry")
            reason = reason or (None if not flying else "it is already flying")
            reason = reason or (None if isinstance(battery, (int, float)) else "no battery reading")
            reason = reason or (None if battery >= minimum else f"battery {battery:.0f}% is below the {minimum:.0f}% minimum")
        else:
            reason = None if role == "decoy" else f"role is {role or 'unknown'}, not decoy"
            reason = reason or (None if connected and flying else "it is not flying")
        rows.append({"aircraft_id": other, "role": role, "battery_pct": battery if isinstance(battery, (int, float)) else None, "eligible": reason is None, "reason": reason})
    return sorted(rows, key=lambda item: item["aircraft_id"])


def options_for(context: AuthContext, aircraft_id: str) -> list[dict[str, Any]]:
    """Every response with its exact reason when it is disabled. Read-only; never sends a command."""
    row = _live_row(context, aircraft_id)
    run = active_mission_execution(aircraft_id)
    controls = execution._full_view(context, run)["controls"] if run is not None else None
    vehicle = _vehicle(aircraft_id)
    no_run = f"No mission run is active on {aircraft_id}."

    def option(action: str, enabled: bool, reason: Optional[str], **extra: Any) -> dict[str, Any]:
        return {"action": action, "label": LABELS[action], "meaning": MEANING[action], "enabled": enabled, "reason": None if enabled else reason, **extra}

    rows = [option("acknowledge", row is not None and row.get("assessment") is not None, "There is no live assessment for this aircraft yet.")]
    for action, key in (("hold", "pause"), ("resume", "resume"), ("return_to_launch", "abort")):
        entry = (controls or {}).get(key)
        rows.append(option(action, bool(entry and entry["allowed"]), no_run if controls is None else (entry or {}).get("reason") or no_run))
    land_block = execution.land_decision(vehicle)
    land_entry = (controls or {}).get("land")
    land_ok = land_block is None and (land_entry is None or land_entry["allowed"])
    rows.append(option("land_here", land_ok, land_block[2] if land_block else (land_entry or {}).get("reason") or "Landing is not possible now."))
    rows.append(option("restart", True, None, runs_in_ui=True))
    others = [item for item in (_vehicles() or []) if item.get("id") != aircraft_id and item.get("status") == "connected"]
    role = _roles(context).get(aircraft_id)
    handover = _candidates(context, aircraft_id, "handover")
    ready = [item for item in handover if item["eligible"]]
    rows.append(option("handover_to_standby", bool(ready) and run is not None,
                       MULTI_REASON if not others else (no_run if run is None else "No standby aircraft is ready: " + "; ".join(f"{item['aircraft_id']} ({item['reason']})" for item in handover)),
                       multi_aircraft=True, candidates=handover))
    switch = _candidates(context, aircraft_id, "switch")
    ready_switch = [item for item in switch if item["eligible"]]
    rows.append(option("switch_primary", role == "primary" and bool(ready_switch),
                       MULTI_REASON if not others else ("Only the primary can switch." if role != "primary" else "No flying decoy to swap with: " + "; ".join(f"{item['aircraft_id']} ({item['reason']})" for item in switch)),
                       multi_aircraft=True, candidates=switch))
    return rows


@router.get("/api/research/responses")
def response_options(aircraft_id: str, context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    """The response options for one aircraft, each enabled or disabled with its reason. Read-only."""
    if not execution._AIRCRAFT_ID.fullmatch(aircraft_id):
        raise HTTPException(status_code=422, detail={"code": "aircraft_invalid", "message": "The aircraft id must look like px4-1."})
    return {"aircraft_id": aircraft_id, "options": options_for(context, aircraft_id), "simulation_only": True,
            "note": "Every command acts on the simulated aircraft only, through the same path as the matching Mission Control button."}


@router.get("/api/research/reconfigurations")
def reconfigurations(_context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    """Mission continuity after hand-overs and primary switches: what the aircraft really did afterwards. Read-only."""
    return {"reconfigurations": continuity.list_items(), "simulation_only": True,
            "note": "Outcome is completed, degraded or failed from measured run states and telemetry; verifying while the new primary is still flying."}


def _evidence(row: Optional[dict[str, Any]]) -> list[str]:
    assessment = (row or {}).get("assessment") or {}
    return [str(item.get("text", "")) for item in (assessment.get("evidence") or [])[:3]]


def _record(context: AuthContext, request: Request, body: ResponseRequest, row: Optional[dict[str, Any]], outcome: str, detail: str,
            run: Optional[dict[str, Any]], extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    assessment = (row or {}).get("assessment") or {}
    tier, score = assessment.get("tier"), assessment.get("threat_score")
    decision = record_research_decision(context.user.id, context.user.username, body.aircraft_id, f"response_{body.action}"[:40], body.candidate, "live_telemetry",
                                        f"{outcome}: {detail}"[:120], tier, score)
    add_audit_event(action="threat_response", outcome="success" if outcome in ("executed", "acknowledged") else "refused", reason=detail,
                    request_id=getattr(request.state, "request_id", "request-unavailable"), actor_id=context.user.id, actor_name=context.user.full_name,
                    target_type="aircraft", target_id=body.aircraft_id,
                    metadata={"action": body.action, "tier": tier, "threat_score": score, "evidence": _evidence(row), "execution_id": (run or {}).get("id"),
                              "candidate": body.candidate, "simulation_only": True, **(extra or {})})
    return decision


def _multi(context: AuthContext, request: Request, body: ResponseRequest, run: Optional[dict[str, Any]]) -> tuple[str, str, Optional[dict[str, Any]]]:
    """Hand over to a standby aircraft, or switch the primary with a flying decoy. Everything is checked BEFORE anything is commanded."""
    kind = "handover" if body.action == "handover_to_standby" else "switch"
    if not body.candidate:
        raise HTTPException(status_code=422, detail={"code": "candidate_required", "message": "Choose the other aircraft first."})
    chosen = next((item for item in _candidates(context, body.aircraft_id, kind) if item["aircraft_id"] == body.candidate), None)
    if chosen is None or not chosen["eligible"]:
        raise HTTPException(status_code=409, detail={"code": "candidate_not_eligible", "message": f"{body.candidate} cannot be used: {(chosen or {}).get('reason') or 'it is not in this simulation'}."})
    roles = dict(_roles(context))
    deployment_id = (run or {}).get("deployment_id") or _deployment_of(context)
    if kind == "switch":
        if roles.get(body.aircraft_id) != "primary":
            raise HTTPException(status_code=409, detail={"code": "not_primary", "message": f"{body.aircraft_id} is not the primary."})
        research_routes.ROLE_OVERRIDES.setdefault(deployment_id, {}).update({body.candidate: "primary", body.aircraft_id: "decoy"})
        research_routes._roles_cache.clear()
        other_run = active_mission_execution(body.candidate)
        continuity.watch("switch_primary", body.aircraft_id, body.candidate, deployment_id, (run or {}).get("id"), (other_run or {}).get("id"), context.user.full_name)
        return "executed", f"{body.candidate} is now the primary and {body.aircraft_id} is a decoy (monitor roles only; no command was sent).", None
    if run is None:
        raise HTTPException(status_code=409, detail={"code": "no_active_run", "message": f"No mission run is active on {body.aircraft_id}, so there is nothing to hand over."})
    deployment, scenario, plan = execution._load_deployment(context, run["deployment_id"])
    aircraft = next((item for item in (plan.aircraft if plan is not None else []) if item.uav_id == body.candidate), None)
    if aircraft is None:
        raise HTTPException(status_code=409, detail={"code": "candidate_not_in_plan", "message": f"{body.candidate} is not part of the deployed plan, so it has no route to fly."})
    check_handover(DecisionRequest(aircraft_id=body.aircraft_id, action="handover_to_standby", handover_candidate=body.candidate, source="live_telemetry"), context)
    # A standby waits where it was placed, not at its own route start: a handover accepts it up to 500 m away (it flies to the first waypoint).
    preflight = execution.run_preflight(context, deployment, scenario, plan, aircraft_id=body.candidate, start_tolerance_m=HANDOVER_START_TOLERANCE_M)
    if not preflight["can_start"]:
        raise HTTPException(status_code=409, detail={"code": "standby_not_ready", "message": f"{body.candidate} cannot start: {preflight['blocking'][0]}", "blocking": preflight["blocking"]})
    execution._control("abort", run["id"], request, context)                      # the threatened aircraft returns to launch
    try:
        started = execution._start_one(request, context, deployment, aircraft)
    except HTTPException as error:
        message = error.detail.get("message") if isinstance(error.detail, dict) else str(error.detail)
        raise HTTPException(status_code=502, detail={"code": "standby_start_failed", "message": f"{body.aircraft_id} was sent back to launch, but {body.candidate} did not start: {message}"}) from error
    research_routes.ROLE_OVERRIDES.setdefault(deployment["id"], {}).update({body.candidate: "primary", body.aircraft_id: "standby"})
    research_routes._roles_cache.clear()
    continuity.watch("handover", body.aircraft_id, body.candidate, deployment["id"], run["id"], started["id"], context.user.full_name)
    return "executed", f"{body.aircraft_id} was sent back to launch and {body.candidate} started its own planned route as the new primary (run {started['id'][:8]}).", None


def _deployment_of(context: AuthContext) -> str:
    from .persistence.database import latest_mission_deployment
    deployment = latest_mission_deployment(context.user.id, include_all=True)
    return deployment["id"] if deployment else "none"


@router.post("/api/research/responses")
def respond(request: Request, payload: dict = Body(...), context: AuthContext = Depends(require_permission(Permission.SCENARIO_EXECUTE)),
            _csrf: AuthContext = Depends(require_csrf)) -> dict:
    """Executes one operator-chosen response on one simulated aircraft and records it with the live score, tier and evidence."""
    try:
        body = ResponseRequest.model_validate(payload)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail={"code": "response_invalid", "message": "The response is invalid.",
                                                      "issues": [{"field": ".".join(str(p) for p in item["loc"]), "message": item["msg"]} for item in error.errors()]}) from error
    row = _live_row(context, body.aircraft_id)
    if body.action != "acknowledge" and not body.confirm:
        raise HTTPException(status_code=422, detail={"code": "confirmation_required", "message": f"Confirm first. {MEANING[body.action]}"})
    run = active_mission_execution(body.aircraft_id)
    view: Optional[dict[str, Any]] = None
    try:
        if body.action == "acknowledge":
            if row is None or row.get("assessment") is None:
                raise HTTPException(status_code=409, detail={"code": "no_assessment", "message": "There is no live assessment for this aircraft to acknowledge."})
            outcome, detail = "acknowledged", "The operator acknowledged the threat and chose to continue. No command was sent."
        elif body.action in ("handover_to_standby", "switch_primary"):
            outcome, detail, view = _multi(context, request, body, run)
        elif body.action == "land_here":
            for attempt in range(4):       # a busy host can make the telemetry look stale for a second or two: look again before refusing
                try:
                    result = execution.land_aircraft(body.aircraft_id, execution.LandRequest(confirm=True), request, context, context)
                    break
                except HTTPException as error:
                    transient = isinstance(error.detail, dict) and error.detail.get("code") == "no_telemetry"
                    if not transient or attempt == 3:
                        raise
                    time.sleep(1.0)
            outcome = "executed"
            detail = "Landing commanded at the current position." if not result.get("already_landing") else "The aircraft was already landing."
            view = result.get("execution")
        else:
            if run is None:
                raise HTTPException(status_code=409, detail={"code": "no_active_run", "message": f"No mission run is active on {body.aircraft_id}, so there is nothing to {body.action.replace('_', ' ')}."})
            command = {"hold": "pause", "resume": "resume", "return_to_launch": "abort"}[body.action]
            view = execution._control(command, run["id"], request, context)
            outcome, detail = "executed", {"pause": "The mission was paused (hold).", "resume": "The mission was resumed.", "abort": "The mission was aborted and the aircraft was sent back to launch."}[command]
    except HTTPException as error:
        message = error.detail.get("message") if isinstance(error.detail, dict) else str(error.detail)
        _record(context, request, body, row, "refused", str(message), run)
        raise
    decision = _record(context, request, body, row, outcome, detail, run, {"command_result": detail})
    live_experiments_note = (row or {}).get("assessment") is not None
    return {"aircraft_id": body.aircraft_id, "action": body.action, "outcome": outcome, "executed": outcome == "executed", "message": detail,
            "tier": decision["tier"], "threat_score": decision["threat_score"], "evidence": _evidence(row), "decision": decision,
            "execution": view if isinstance(view, dict) and "state" in view else None, "had_assessment": live_experiments_note,
            "simulation_only": True, "at": time.time()}
