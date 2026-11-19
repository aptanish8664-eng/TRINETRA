"""Operator control (Part E): the operator is always in control.

Everything here is available at ANY time to an authorised user (not only when a threat is detected). The system may SUGGEST an action when the detector's probability passes a
threshold (app/research/suggestions.py), but it never takes a new automatic action: every action is a button the operator presses and confirms. Every action does what it says in the
simulator (PX4 SITL), reports its real result or its real refusal reason, and is written to the audit log (who, when, what, which aircraft, result) and to the run's story.

GET  /api/mission-control/threat-summary           headline, probability, suggestion and the attack story for the deployment
POST /api/mission-control/aircraft/{id}/continue-monitoring    acknowledge, keep flying, keep monitoring, optional reminder threshold
GET  /api/mission-control/aircraft/{id}/resume-preview         which waypoint the aircraft would rejoin its route at, and why
POST /api/mission-control/aircraft/{id}/resume-on-track        put the aircraft back on its mission from that point
GET  /api/mission-control/aircraft/{id}/reassign-options       every other aircraft with role, state, battery, link and distance from the task; not-ready ones flagged
POST /api/mission-control/aircraft/{id}/reassign               hand the remaining route to a chosen aircraft, and decide what the original does
"""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from . import execution_routes as execution
from . import research_routes
from .auth_routes import require_csrf, require_permission
from .authorization import Permission
from .mission_planning.geometry import distance_m
from .persistence.database import active_mission_execution, add_audit_event, deployment_mission_executions, record_research_decision, update_mission_execution
from .research import continuity, suggestions
from .security import AuthContext
from .telemetry.mission_executor import invoke_mission_executor

router = APIRouter()
_ID = r"^px4-[1-9][0-9]{0,2}$"
MIN_GPS_FIX = 3
AIRBORNE_ABOVE_M = 2.0
STORY_MAX = 300
SERIES_MAX = 600
# Rejoin at the nearest waypoint of the remaining route instead of the next one when it is at least this much closer (metres).
NEAREST_ADVANTAGE_M = 50.0
# ...but only among the next few waypoints: a route that loops back near its start must never make an aircraft skip most of its mission.
REJOIN_WINDOW = 3

_lock = threading.Lock()
_story: dict[str, list[dict[str, Any]]] = {}                    # deployment -> events, oldest first
_series: dict[str, dict[str, list[tuple[float, float]]]] = {}    # deployment -> aircraft -> (wall time, probability)
_levels: dict[tuple[str, str], str] = {}                        # (deployment, aircraft) -> last level seen
_acks: dict[tuple[str, str], dict[str, Any]] = {}               # (deployment, aircraft) -> acknowledgement with its reminder threshold
# The route an aircraft is ACTUALLY flying when it is not its own planned one (a reassigned remaining route, or a route resumed from the air): (deployment, aircraft) -> {"run_id", "aircraft"}.
# Held in memory like the role changes; it is used only while the run it was made for is the active run.
_flown: dict[tuple[str, str], dict[str, Any]] = {}


def reset_for_tests() -> None:
    with _lock:
        _story.clear(); _series.clear(); _levels.clear(); _acks.clear(); _flown.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def story_add(deployment_id: str, kind: str, text: str, *, aircraft: Optional[str] = None, by: Optional[str] = None, data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """One sentence of the run's story, in the order things happened."""
    event = {"at": _now_iso(), "t": time.time(), "kind": kind, "aircraft_id": aircraft, "by": by, "text": text, "data": data or {}}
    with _lock:
        items = _story.setdefault(deployment_id, [])
        items.append(event)
        del items[:-STORY_MAX]
    return event


def story_for(deployment_id: str) -> list[dict[str, Any]]:
    with _lock:
        return [dict(item) for item in _story.get(deployment_id, [])]


def _error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


# ---------------------------------------------------------------------------------------------------- shared reading helpers
def _vehicles() -> list[dict[str, Any]]:
    from .fleet_routes import _fleet_snapshot
    return _fleet_snapshot()[0]


def _vehicle(aircraft_id: str) -> Optional[dict[str, Any]]:
    return next((item for item in _vehicles() if item.get("id") == aircraft_id), None)


def _live_rows(context: AuthContext) -> list[dict[str, Any]]:
    return research_routes.live_payload(context)["aircraft"] if research_routes.telemetry_store is not None else []


def _plan_and_roles(context: AuthContext, deployment_id: str):
    deployment, scenario, plan = execution._load_deployment(context, deployment_id)
    roles = {item.uav_id: item.role for item in (plan.aircraft if plan is not None else [])}
    roles.update(research_routes.ROLE_OVERRIDES.get(deployment["id"], {}))
    return deployment, scenario, plan, roles


def _own_route(plan: Any, aircraft_id: str, deployment_id: Optional[str] = None) -> Any:
    """The aircraft's plan with the route it is flying now: its own planned route, or the derived one it was given by a reassign or a resume from the air."""
    if deployment_id is not None:
        flown = _flown.get((deployment_id, aircraft_id))
        active = active_mission_execution(aircraft_id)
        if flown is not None and active is not None and active["id"] == flown["run_id"]:
            return flown["aircraft"]
    aircraft = next((item for item in (plan.aircraft if plan is not None else []) if item.uav_id == aircraft_id), None)
    if aircraft is None or aircraft.route is None or len(aircraft.route.waypoints) < 2:
        raise _error(409, "no_route", f"{aircraft_id} has no planned route in this deployment.")
    return aircraft


def _progress_of(deployment_id: str, aircraft_id: str) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """(the active run or None, the progress of the newest run of this aircraft)."""
    active = active_mission_execution(aircraft_id)
    if active is not None:
        active = execution.sync_execution(active, force=True)
        return active, active.get("progress") or {}
    newest = next((row for row in deployment_mission_executions(deployment_id) if row["aircraft_id"] == aircraft_id), None)
    return None, (newest or {}).get("progress") or {}


RUN_START_WAIT_S = 40.0


def _await_executing(run_id: str, seconds: float = RUN_START_WAIT_S) -> tuple[bool, str, str]:
    """Wait until a run really is flying (or has failed). A hand-over is only reported as done once the taker's run is executing: (ok, state, reason)."""
    from .persistence.database import get_mission_execution
    deadline = time.monotonic() + seconds
    state, reason = "starting", ""
    while time.monotonic() < deadline:
        current = get_mission_execution(run_id)
        if current is not None:
            current = execution.sync_execution(current, force=True)
            state, reason = current["state"], current.get("reason") or ""
            if state == "executing":
                return True, state, reason
            if state in ("failed", "aborted", "completed", "link_lost"):
                return False, state, reason
        time.sleep(1.0)
    return False, state, reason or f"the run was still {state} after {seconds:g} s"


def _is_airborne(vehicle: Optional[dict[str, Any]]) -> Optional[bool]:
    if vehicle is None:
        return None
    relative = ((vehicle.get("position") or {}).get("relative_altitude_m"))
    if not isinstance(relative, (int, float)):
        return None
    return bool(vehicle.get("armed")) and relative > AIRBORNE_ABOVE_M


# ---------------------------------------------------------------------------------------------------- the rejoin point
def rejoin_point(waypoints: list[Any], position: Optional[tuple[float, float]], route_current: Optional[int], *, own_next_only: bool = False) -> dict[str, Any]:
    """Where an aircraft would rejoin its route: its own next waypoint, or (for a fresh mission flown from the air) the nearest of the next few waypoints when that is clearly closer.
    `own_next_only` is for a PAUSED run: PX4 continues towards the waypoint it was already flying to, so that is the rejoin waypoint.

    `route_current` is the 1-based number of the next waypoint the executor reported (None: the route has not started). The take-off waypoint (index 0) is never a rejoin point.
    """
    last = len(waypoints) - 1
    next_index = min(max(1, (route_current or 1) - 1 if route_current else 1), last)
    if own_next_only:
        chosen = next_index
        near = None if position is None else distance_m(position, (waypoints[next_index].latitude, waypoints[next_index].longitude))
        why = "the waypoint it was already flying to" + ("" if near is None else f" ({near:.0f} m away)")
    elif position is None:
        chosen, why = next_index, "its own next waypoint (its position is unknown)"
    else:
        distances = {index: distance_m(position, (waypoints[index].latitude, waypoints[index].longitude)) for index in range(next_index, min(last, next_index + REJOIN_WINDOW) + 1)}
        nearest = min(distances, key=distances.get)
        if distances[nearest] + NEAREST_ADVANTAGE_M < distances[next_index]:
            chosen, why = nearest, f"the nearest of its next waypoints ({distances[nearest]:.0f} m away; its own next waypoint is {distances[next_index]:.0f} m away)"
        else:
            chosen, why = next_index, f"its own next waypoint ({distances[next_index]:.0f} m away)"
    target = waypoints[chosen]
    return {"index": chosen, "waypoint_number": chosen + 1, "of": len(waypoints), "latitude": target.latitude, "longitude": target.longitude, "altitude_m": target.altitude_m,
            "distance_m": None if position is None else round(distance_m(position, (target.latitude, target.longitude)), 1), "why": why, "is_landing": target.action == "land",
            "remaining_waypoints": len(waypoints) - chosen}


def remaining_route(aircraft: Any, from_index: int, start: tuple[float, float], start_altitude_m: Optional[float]) -> Any:
    """A copy of this aircraft's plan whose route is: take-off at `start` (the aircraft's own position), then the waypoints from `from_index` to the end (the landing)."""
    original = aircraft.route.waypoints
    tail = [item for item in original[from_index:] if item.action != "takeoff"]
    if not tail or tail[-1].action != "land":
        raise _error(409, "route_not_flyable", "The remaining route does not end with a landing, so it cannot be flown.")
    altitude = start_altitude_m if isinstance(start_altitude_m, (int, float)) and start_altitude_m > 1 else tail[0].altitude_m
    first = type(original[0]).model_validate({"seq": 0, "latitude": start[0], "longitude": start[1], "altitude_m": max(2.0, float(altitude)), "speed_mps": tail[0].speed_mps,
                                              "hover_s": 0.0, "action": "takeoff"})
    renumbered = [first] + [item.model_copy(update={"seq": index + 1}) for index, item in enumerate(tail)]
    return aircraft.model_copy(update={"route": aircraft.route.model_copy(update={"waypoints": renumbered})})


def _position(vehicle: Optional[dict[str, Any]]) -> Optional[tuple[float, float]]:
    position = (vehicle or {}).get("position") or {}
    if position.get("valid") is True and isinstance(position.get("latitude"), (int, float)) and isinstance(position.get("longitude"), (int, float)):
        return float(position["latitude"]), float(position["longitude"])
    return None


# ---------------------------------------------------------------------------------------------------- takers (who can take over)
def taker_problems(vehicle: Optional[dict[str, Any]], run: Optional[dict[str, Any]]) -> list[str]:
    """Reasons an aircraft cannot take a route over right now (empty list: ready)."""
    if vehicle is None:
        return ["no telemetry has been received from this aircraft"]
    problems = []
    if vehicle.get("status") != "connected":
        problems.append(f"its telemetry is {vehicle.get('status')}")
    gps = vehicle.get("gps_raw") or {}
    if not (gps.get("valid") and int(gps.get("fix_type") or 0) >= MIN_GPS_FIX):
        problems.append("it has no valid 3D GPS fix")
    battery = (vehicle.get("battery") or {}).get("remaining_percent")
    minimum = float(research_routes.MIN_HANDOVER_BATTERY_PCT)
    if not isinstance(battery, (int, float)):
        problems.append("its battery level is unknown")
    elif battery < minimum:
        problems.append(f"its battery is {battery:.0f}%, below the {minimum:.0f}% minimum")
    if _position(vehicle) is None:
        problems.append("it has no valid position")
    return problems


def candidate_rows(context: AuthContext, deployment_id: str, original_id: str, target: Optional[tuple[float, float]]) -> list[dict[str, Any]]:
    deployment, _scenario, plan, roles = _plan_and_roles(context, deployment_id)
    live = {row["aircraft_id"]: row for row in _live_rows(context)}
    vehicles = {item.get("id"): item for item in _vehicles()}
    in_plan = {item.uav_id: item for item in (plan.aircraft if plan is not None else [])}
    rows = []
    for aircraft_id in sorted(set(in_plan) | {key for key in vehicles if key}):
        if aircraft_id == original_id:
            continue
        vehicle = vehicles.get(aircraft_id)
        run = active_mission_execution(aircraft_id)
        problems = taker_problems(vehicle, run)
        if aircraft_id not in in_plan:
            problems.append("it is not part of this deployed plan")
        elif in_plan[aircraft_id].route is None:
            problems.append("it has no planned route")
        airborne = _is_airborne(vehicle)
        position = _position(vehicle)
        rows.append({"aircraft_id": aircraft_id, "role": roles.get(aircraft_id), "state": "airborne" if airborne else "on the ground" if airborne is False else "unknown",
                     "has_active_run": run is not None, "run_state": (run or {}).get("state"), "battery_percent": ((vehicle or {}).get("battery") or {}).get("remaining_percent"),
                     "link": (vehicle or {}).get("status", "not_seen"), "distance_from_task_m": None if position is None or target is None else round(distance_m(position, target), 1),
                     "monitor_state": (live.get(aircraft_id) or {}).get("state"), "ready": not problems, "not_ready_reasons": problems,
                     "verified": not problems})
    rows.sort(key=lambda item: (not item["ready"], item["distance_from_task_m"] if item["distance_from_task_m"] is not None else 1e12, item["aircraft_id"]))
    return rows


# ---------------------------------------------------------------------------------------------------- threat summary and the story
def _observe(deployment_id: str, rows: list[dict[str, Any]], who: str) -> None:
    now = time.time()
    for row in rows:
        assessment = row.get("assessment") or {}
        probability = assessment.get("anomaly_confidence")
        if not isinstance(probability, (int, float)):
            continue
        aircraft = row["aircraft_id"]
        with _lock:
            series = _series.setdefault(deployment_id, {}).setdefault(aircraft, [])
            if not series or now - series[-1][0] >= 1.5:
                series.append((now, float(probability)))
                del series[:-SERIES_MAX]
            previous = _levels.get((deployment_id, aircraft), "clear")
        level = suggestions.level_for(float(probability))
        if level != previous:
            with _lock:
                _levels[(deployment_id, aircraft)] = level
            rising = suggestions.LEVELS.index(level) > suggestions.LEVELS.index(previous)
            story_add(deployment_id, f"level_{level}", (f"The monitor's probability for {aircraft} rose to {probability:.0%}: level {suggestions.HEADLINES[level]}." if rising
                                                            else f"The monitor's probability for {aircraft} fell to {probability:.0%}: level {suggestions.HEADLINES[level]}."),
                      aircraft=aircraft, data={"probability": round(float(probability), 3), "tier": assessment.get("tier")})


def _takers_for(context: AuthContext, deployment_id: str, aircraft_id: str, target: Optional[tuple[float, float]]) -> list[str]:
    try:
        return [item["aircraft_id"] for item in candidate_rows(context, deployment_id, aircraft_id, target) if item["ready"]]
    except HTTPException:
        return []


@router.get("/api/mission-control/threat-summary")
def threat_summary(deployment_id: str = Query(min_length=1, max_length=64), context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    deployment, _scenario, plan, roles = _plan_and_roles(context, deployment_id)
    rows = [row for row in _live_rows(context) if row.get("assessment")]
    _observe(deployment["id"], rows, context.user.full_name)
    best = max(rows, key=lambda row: row["assessment"].get("anomaly_confidence") or 0.0, default=None)
    probability = float(best["assessment"]["anomaly_confidence"]) if best else None
    level = suggestions.level_for(probability)
    aircraft_id = best["aircraft_id"] if best else None
    # Reminders are per aircraft: every acknowledged aircraft is checked against ITS OWN probability, so acknowledging one aircraft never hides another.
    raised_again = False
    probabilities = {row["aircraft_id"]: float(row["assessment"]["anomaly_confidence"]) for row in rows if isinstance((row.get("assessment") or {}).get("anomaly_confidence"), (int, float))}
    for (dep_key, acked), entry in list(_acks.items()):
        if dep_key != deployment["id"] or acked not in probabilities or probabilities[acked] < entry["reminder_probability"]:
            continue
        raised_again = True
        story_add(deployment["id"], "reminder_raised", f"The probability for {acked} reached {probabilities[acked]:.0%}, above the reminder you set ({entry['reminder_probability']:.0%}): the suggestion is raised again.",
                  aircraft=acked, data={"probability": round(probabilities[acked], 3), "reminder": entry["reminder_probability"]})
        with _lock:
            _acks.pop((dep_key, acked), None)
    ack = _acks.get((deployment["id"], aircraft_id or ""))
    suggestion = None
    if best is not None and level != "clear" and ack is None:
        route_target = None
        try:
            aircraft = _own_route(plan, aircraft_id, deployment["id"])
            _active, progress = _progress_of(deployment["id"], aircraft_id)
            route_target = rejoin_point(list(aircraft.route.waypoints), _position(_vehicle(aircraft_id)), progress.get("route_current"), own_next_only=True)
            route_target = (route_target["latitude"], route_target["longitude"])
        except HTTPException:
            route_target = None
        takers = _takers_for(context, deployment["id"], aircraft_id, route_target) if roles.get(aircraft_id) in ("primary", "decoy") else []
        suggestion = suggestions.suggestion(level, aircraft_id, probability, [str(item.get("text", "")) for item in (best["assessment"].get("evidence") or [])[:2]], takers)
    with _lock:
        series = list(_series.get(deployment["id"], {}).get(aircraft_id or "", []))[-120:]
    return {"deployment_id": deployment["id"], "level": level, "headline": suggestions.HEADLINES[level], "probability": None if probability is None else round(probability, 3),
            "aircraft_id": aircraft_id, "tier": (best or {}).get("assessment", {}).get("tier"), "threat_active": level in ("suspected", "confirmed"),
            "suggestion": suggestion, "acknowledged": None if ack is None else {"at": ack["at"], "by": ack["by"], "reminder_probability": ack["reminder_probability"]}, "raised_again": raised_again,
            "acknowledgements": [{"aircraft_id": key[1], "at": value["at"], "by": value["by"], "reminder_probability": value["reminder_probability"]} for key, value in _acks.items() if key[0] == deployment["id"]],
            "thresholds": {"watch": suggestions.SUGGEST_WATCH, "act": suggestions.SUGGEST_ACT, "confirmed": suggestions.CONFIRMED_AT}, "explanation": suggestions.EXPLANATION,
            "probability_series": [{"t": round(t, 1), "p": round(p, 3)} for t, p in series], "story": story_for(deployment["id"]), "simulation_only": True,
            "monitor_ready": bool(rows), "note": "The probability is the detector's anomaly confidence. Suggestions are only buttons: nothing is ever pressed for you."}


# ---------------------------------------------------------------------------------------------------- audit helper
def _audit(request: Request, context: AuthContext, action: str, aircraft_id: str, outcome: str, reason: str, metadata: dict[str, Any]) -> None:
    add_audit_event(action=action, outcome=outcome, reason=reason, request_id=getattr(request.state, "request_id", "request-unavailable"), actor_id=context.user.id,
                    actor_name=context.user.full_name, target_type="aircraft", target_id=aircraft_id, metadata={"simulation_only": True, **metadata})


def _decision(context: AuthContext, aircraft_id: str, action: str, candidate: Optional[str], detail: str, row: Optional[dict[str, Any]]) -> None:
    assessment = (row or {}).get("assessment") or {}
    record_research_decision(context.user.id, context.user.username, aircraft_id, action[:40], candidate, "live_telemetry", detail[:120], assessment.get("tier"), assessment.get("threat_score"))


# ---------------------------------------------------------------------------------------------------- continue monitoring
class ContinueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deployment_id: str = Field(min_length=1, max_length=64)
    reminder_probability: Optional[float] = Field(default=None, ge=0.05, le=0.99)


@router.post("/api/mission-control/aircraft/{aircraft_id}/continue-monitoring")
def continue_monitoring(aircraft_id: str, payload: ContinueRequest, request: Request, context: AuthContext = Depends(require_permission(Permission.SCENARIO_EXECUTE)),
                        _csrf: AuthContext = Depends(require_csrf)) -> dict:
    """Acknowledge the suggestion or alert: nothing changes on the aircraft, the monitor keeps running, the decision is recorded, and the page raises it again if the probability reaches the reminder."""
    execution._AIRCRAFT_ID.fullmatch(aircraft_id) or (_ for _ in ()).throw(_error(422, "aircraft_invalid", "The aircraft id must look like px4-1."))
    deployment, _scenario, _plan, _roles = _plan_and_roles(context, payload.deployment_id)
    rows = {row["aircraft_id"]: row for row in _live_rows(context)}
    row = rows.get(aircraft_id)
    probability = ((row or {}).get("assessment") or {}).get("anomaly_confidence")
    reminder = payload.reminder_probability if payload.reminder_probability is not None else suggestions.next_reminder(probability if isinstance(probability, (int, float)) else None)
    entry = {"at": _now_iso(), "by": context.user.full_name, "reminder_probability": reminder if reminder is not None else 1.01, "probability_then": probability}
    with _lock:
        _acks[(deployment["id"], aircraft_id)] = entry
    text = (f"{context.user.full_name} chose to keep flying and keep monitoring {aircraft_id} (probability {probability:.0%}); it will be raised again at {reminder:.0%}." if isinstance(probability, (int, float)) and reminder is not None
            else f"{context.user.full_name} chose to keep flying and keep monitoring {aircraft_id}.")
    story_add(deployment["id"], "operator_continue_monitoring", text, aircraft=aircraft_id, by=context.user.full_name, data={"reminder": reminder, "probability": probability})
    _decision(context, aircraft_id, "continue_monitor", None, "acknowledged: keep flying, keep monitoring", row)
    _audit(request, context, "continue_monitoring", aircraft_id, "success", "Acknowledged; the aircraft keeps flying and the monitor keeps running.", {"reminder_probability": reminder, "probability": probability})
    return {"aircraft_id": aircraft_id, "outcome": "acknowledged", "reminder_probability": reminder, "message": text, "command_sent": False, "simulation_only": True}


# ---------------------------------------------------------------------------------------------------- resume on track
def _resume_context(context: AuthContext, aircraft_id: str, deployment_id: str):
    deployment, scenario, plan, _roles = _plan_and_roles(context, deployment_id)
    aircraft = _own_route(plan, aircraft_id, deployment["id"])
    run, progress = _progress_of(deployment["id"], aircraft_id)
    vehicle = _vehicle(aircraft_id)
    rejoin = rejoin_point(list(aircraft.route.waypoints), _position(vehicle), progress.get("route_current"), own_next_only=run is not None and run.get("state") == "paused")
    return deployment, scenario, plan, aircraft, run, progress, vehicle, rejoin


@router.get("/api/mission-control/aircraft/{aircraft_id}/resume-preview")
def resume_preview(aircraft_id: str, deployment_id: str = Query(min_length=1, max_length=64), context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    deployment, _scenario, _plan, _aircraft, run, progress, vehicle, rejoin = _resume_context(context, aircraft_id, deployment_id)
    state = (run or {}).get("state")
    airborne = _is_airborne(vehicle)
    if run is not None and state == "paused":
        how, allowed, reason = "The paused mission continues; PX4 resumes towards this waypoint.", True, None
    elif run is not None:
        how, allowed, reason = "", False, f"{aircraft_id} is already flying its mission (state {state}); there is nothing to resume."
    elif airborne:
        how, allowed, reason = "A new mission is uploaded that flies the remaining waypoints from the aircraft's present position and then lands.", True, None
    else:
        how, allowed, reason = "", False, f"{aircraft_id} is on the ground with no active run. Use Start in simulation, or reassign the route to another aircraft."
    return {"aircraft_id": aircraft_id, "deployment_id": deployment["id"], "run_state": state, "airborne": airborne, "rejoin": rejoin, "how": how, "allowed": allowed, "reason": reason,
            "route_reached": progress.get("route_reached"), "route_total": progress.get("route_total"), "simulation_only": True}


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deployment_id: str = Field(min_length=1, max_length=64)
    confirm: bool = False


@router.post("/api/mission-control/aircraft/{aircraft_id}/resume-on-track")
def resume_on_track(aircraft_id: str, payload: ResumeRequest, request: Request, context: AuthContext = Depends(require_permission(Permission.SCENARIO_EXECUTE)),
                    _csrf: AuthContext = Depends(require_csrf)) -> dict:
    if not payload.confirm:
        raise _error(422, "confirmation_required", "Confirm the rejoin waypoint first (see the preview).")
    deployment, scenario, plan, aircraft, run, progress, vehicle, rejoin = _resume_context(context, aircraft_id, payload.deployment_id)
    where = f"waypoint {rejoin['waypoint_number']} of {rejoin['of']}"
    try:
        if run is not None and run.get("state") == "paused":
            view = execution._control("resume", run["id"], request, context)
            result = f"{aircraft_id} resumed its mission towards {where}."
        elif run is not None:
            raise _error(409, "already_on_track", f"{aircraft_id} is already flying its mission (state {run.get('state')}).")
        elif _is_airborne(vehicle):
            position = _position(vehicle)
            derived = remaining_route(aircraft, rejoin["index"], position, (vehicle.get("position") or {}).get("relative_altitude_m"))
            started = execution._start_one(request, context, deployment, derived, in_air=True)
            _flown[(deployment["id"], aircraft_id)] = {"run_id": started["id"], "aircraft": derived}
            flying, state, reason = _await_executing(started["id"])
            if not flying:
                raise _error(502, "resume_failed", f"The new mission on {aircraft_id} did not start flying ({state}): {reason}")
            view = started
            result = f"{aircraft_id} was put back on its route: a new mission from its present position flies {where} to the end (run {started['id'][:8]})."
        else:
            raise _error(409, "on_the_ground", f"{aircraft_id} is on the ground with no active run, so there is no route to rejoin.")
    except HTTPException as error:
        message = error.detail.get("message") if isinstance(error.detail, dict) else str(error.detail)
        _audit(request, context, "resume_on_track", aircraft_id, "refused", str(message), {"rejoin": rejoin})
        story_add(deployment["id"], "operator_resume_refused", f"{context.user.full_name} tried to resume {aircraft_id} on track: {message}", aircraft=aircraft_id, by=context.user.full_name)
        raise
    story_add(deployment["id"], "operator_resume_on_track", f"{context.user.full_name} put {aircraft_id} back on track: {result}", aircraft=aircraft_id, by=context.user.full_name, data={"rejoin": rejoin})
    _audit(request, context, "resume_on_track", aircraft_id, "success", result, {"rejoin": rejoin})
    return {"aircraft_id": aircraft_id, "outcome": "executed", "message": result, "rejoin": rejoin, "execution": view if isinstance(view, dict) and "state" in view else None, "simulation_only": True}


# ---------------------------------------------------------------------------------------------------- reassign
def _reassign_context(context: AuthContext, aircraft_id: str, deployment_id: str):
    deployment, scenario, plan, roles = _plan_and_roles(context, deployment_id)
    aircraft = _own_route(plan, aircraft_id, deployment["id"])
    run, progress = _progress_of(deployment["id"], aircraft_id)
    vehicle = _vehicle(aircraft_id)
    # The taker continues from the original's OWN next waypoint: nothing the original has not flown yet is skipped.
    rejoin = rejoin_point(list(aircraft.route.waypoints), _position(vehicle), progress.get("route_current"), own_next_only=True)
    return deployment, scenario, plan, roles, aircraft, run, progress, vehicle, rejoin


@router.get("/api/mission-control/aircraft/{aircraft_id}/reassign-options")
def reassign_options(aircraft_id: str, deployment_id: str = Query(min_length=1, max_length=64), context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    deployment, _scenario, _plan, roles, _aircraft, run, _progress, _vehicle_row, rejoin = _reassign_context(context, aircraft_id, deployment_id)
    rows = candidate_rows(context, deployment["id"], aircraft_id, (rejoin["latitude"], rejoin["longitude"]))
    return {"aircraft_id": aircraft_id, "role": roles.get(aircraft_id), "run_state": (run or {}).get("state"), "rejoin": rejoin, "candidates": rows,
            "original_choices": [{"value": "hold", "label": "Hold (pause the mission)", "meaning": "The original aircraft stops and hovers; you can resume it later."},
                                 {"value": "return_to_launch", "label": "Return to launch", "meaning": "The original aircraft flies back to its launch point and the run ends."},
                                 {"value": "land", "label": "Land here", "meaning": "The original aircraft lands where it is and disarms."},
                                 {"value": "decoy", "label": "Continue as decoy", "meaning": "The original aircraft keeps flying its own route as a decoy; its role changes."}],
            "what_happens": ("The chosen aircraft takes off if it is on the ground, then flies the remaining waypoints of this route from the rejoin waypoint to the landing. "
                             "The original aircraft does what you choose. Roles are swapped and the plan's audit trail records who did this."), "simulation_only": True}


class ReassignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deployment_id: str = Field(min_length=1, max_length=64)
    taker: str = Field(pattern=_ID)
    original: Literal["hold", "return_to_launch", "land", "decoy"]
    confirm: bool = False


@router.post("/api/mission-control/aircraft/{aircraft_id}/reassign")
def reassign(aircraft_id: str, payload: ReassignRequest, request: Request, context: AuthContext = Depends(require_permission(Permission.SCENARIO_EXECUTE)),
             _csrf: AuthContext = Depends(require_csrf)) -> dict:
    if not payload.confirm:
        raise _error(422, "confirmation_required", "Confirm the handover first (see what will happen to both aircraft).")
    taker = payload.taker
    if taker == aircraft_id:
        raise _error(409, "same_aircraft", "An aircraft cannot take over from itself.")
    deployment, scenario, plan, roles, original_plan, run, progress, original_vehicle, rejoin = _reassign_context(context, aircraft_id, payload.deployment_id)
    rows = {item["aircraft_id"]: item for item in candidate_rows(context, deployment["id"], aircraft_id, (rejoin["latitude"], rejoin["longitude"]))}
    chosen = rows.get(taker)
    if chosen is None:
        raise _error(409, "taker_unknown", f"{taker} is not an aircraft of this simulation.")
    if not chosen["ready"]:
        raise _error(409, "taker_not_ready", f"{taker} cannot take over: " + "; ".join(chosen["not_ready_reasons"]) + ".", reasons=chosen["not_ready_reasons"])
    taker_plan = next(item for item in plan.aircraft if item.uav_id == taker)
    taker_vehicle = _vehicle(taker)
    taker_run = active_mission_execution(taker)
    base = remaining_route(original_plan, rejoin["index"], _position(taker_vehicle), (taker_vehicle.get("position") or {}).get("relative_altitude_m") if _is_airborne(taker_vehicle) else None)
    derived = taker_plan.model_copy(update={"route": base.route})
    detail: dict[str, Any] = {"taker": taker, "original": payload.original, "rejoin": rejoin, "taker_was": chosen["state"]}
    steps: list[str] = []
    try:
        if taker_run is not None:                              # an airborne taker with its own run: hold it, then release its executor (PX4 stays in Hold) so the new route can be sent
            taker_run = execution.sync_execution(taker_run, force=True)
            if taker_run.get("state") not in ("paused",):
                execution._control("pause", taker_run["id"], request, context)
                steps.append(f"{taker} was held")
            ended = invoke_mission_executor("end", instance=taker_run["instance_number"], timeout_seconds=20)
            if ended.get("failure"):
                raise _error(502, "taker_release_failed", f"{taker}'s current run could not be released: {ended['failure']}")
            update_mission_execution(taker_run["id"], state="aborted", reason=f"Released so {taker} could take over {aircraft_id}'s route (reassign).", finished=True)
            steps.append(f"{taker}'s previous run was ended")
        started = execution._start_one(request, context, deployment, derived, in_air=bool(_is_airborne(taker_vehicle)))       # FIRST the taker: if it cannot start, nothing about the original has changed
        _flown[(deployment["id"], taker)] = {"run_id": started["id"], "aircraft": derived}
        steps.append(f"{taker} started the remaining route (run {started['id'][:8]})")
        flying, state, reason = _await_executing(started["id"])
        if not flying:
            raise _error(502, "taker_not_flying", f"{taker}'s new run did not start flying ({state}): {reason}")
        steps.append(f"{taker} is flying it (state {state})")
    except HTTPException as error:
        message = error.detail.get("message") if isinstance(error.detail, dict) else str(error.detail)
        _audit(request, context, "reassign", aircraft_id, "refused", f"{taker} did not start: {message}", detail)
        story_add(deployment["id"], "operator_reassign_failed", f"{context.user.full_name} tried to hand {aircraft_id}'s route to {taker}, but it could not start: {message} {aircraft_id} was not changed.", aircraft=aircraft_id, by=context.user.full_name, data=detail)
        raise HTTPException(status_code=error.status_code, detail={"code": (error.detail or {}).get("code", "taker_start_failed") if isinstance(error.detail, dict) else "taker_start_failed",
                                                                   "message": f"{taker} could not start the route, so nothing was changed on {aircraft_id}: {message}", "steps_done": steps}) from error
    original_result = ""
    try:
        if payload.original == "hold":
            if run is not None and run.get("state") != "paused":
                execution._control("pause", run["id"], request, context)
            original_result = f"{aircraft_id} is holding"
        elif payload.original == "return_to_launch":
            if run is not None:
                execution._control("abort", run["id"], request, context)
            original_result = f"{aircraft_id} was sent back to launch"
        elif payload.original == "land":
            execution.land_aircraft(aircraft_id, execution.LandRequest(confirm=True), request, context, context)
            original_result = f"{aircraft_id} is landing where it is"
        else:
            original_result = f"{aircraft_id} keeps flying its own route as a decoy"
    except HTTPException as error:
        message = error.detail.get("message") if isinstance(error.detail, dict) else str(error.detail)
        original_result = f"{aircraft_id} could not be put in the state you chose ({payload.original.replace('_', ' ')}): {message}"
        detail["original_failed"] = True
    new_original_role = "decoy" if payload.original == "decoy" else "standby"
    taker_role = roles.get(aircraft_id) or "primary"
    research_routes.ROLE_OVERRIDES.setdefault(deployment["id"], {}).update({taker: taker_role, aircraft_id: new_original_role})
    research_routes._roles_cache.clear()
    continuity.watch("handover", aircraft_id, taker, deployment["id"], (run or {}).get("id"), started["id"], context.user.full_name)
    message = f"{taker} took over {aircraft_id}'s route from waypoint {rejoin['waypoint_number']} of {rejoin['of']} as {taker_role}; {original_result}."
    story_add(deployment["id"], "operator_reassign", f"{context.user.full_name} handed {aircraft_id}'s route to {taker}: {message}", aircraft=aircraft_id, by=context.user.full_name, data=detail)
    _audit(request, context, "reassign", aircraft_id, "success", message, {**detail, "steps": steps, "original_result": original_result, "new_roles": {taker: taker_role, aircraft_id: new_original_role}})
    _decision(context, aircraft_id, "response_reassign", taker, f"executed: {message}", {"assessment": None})
    return {"aircraft_id": aircraft_id, "taker": taker, "outcome": "executed", "message": message, "steps": steps, "rejoin": rejoin, "original_result": original_result,
            "new_roles": {taker: taker_role, aircraft_id: new_original_role}, "execution": started, "simulation_only": True}
