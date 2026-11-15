"""Start in simulation: run a deployed schema-2 mission on the managed PX4 SITL instance, with Pause / Resume / Abort.

SITL only. The backend never opens a MAVLink socket: it runs the allowlisted WSL executor with a fixed action and a
mission built from validated numbers (see app/mission_execution/items.py and backend/tools/wsl_mission_executor.py).

Role rules, enforced here and mirrored in the UI:
* GENERAL may start directly.
* OPERATOR may start only a version a General approved (per version: pending, rejected, changes-requested or
  never-submitted versions are refused with 403).
* Nobody may start a superseded deployment (409).
Abort is a safety action: any account that may execute scenarios and can see the mission may abort a running execution.

State is read from the executor's status and event files whenever an execution is read (throttled), and every
event is stored with the executor's own timestamp, so the record is complete even if no browser was open.
Attack experiments are never read here and never appear in any response.
"""
from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .auth_routes import require_csrf, require_permission
from .authorization import Permission, has_permission
from .mission_execution.ground import IN_THE_AIR, ground_state
from .mission_execution.items import MissionItemsError, build_mission, instance_for_aircraft
from .mission_planning.generators import PlanningLimits
from .mission_planning.schemas import MissionPlan
from .mission_planning.start import HOME_TOLERANCE_M
from .persistence.database import (
    active_mission_execution, add_audit_event, add_mission_execution_events, create_mission_execution, get_mission_deployment,
    get_mission_execution, get_scenario, latest_mission_execution, list_mission_execution_events, update_mission_execution,
)
import re
from .security import AuthContext
from .telemetry.mission_executor import invoke_mission_executor

router = APIRouter()

BATTERY_MIN_PCT = 40.0
TELEMETRY_FRESH_S = 3.0
EXECUTOR_STALE_S = 10.0
SYNC_MIN_INTERVAL_S = 0.7
START_CONFIRMATION = "This runs your route in the PX4/Gazebo simulation. The aircraft will arm, take off and fly the plan."
FINAL_STATES = frozenset({"completed", "aborted", "failed"})
FLYING_STATES = frozenset({"executing", "pausing", "paused", "resuming", "returning", "failsafe"})
_last_sync: dict[str, float] = {}


class StartRequest(BaseModel):
    deployment_id: str = Field(min_length=1, max_length=64)
    confirm: bool = False


def _error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _include_all(context: AuthContext) -> bool:
    return has_permission(context.user.role, Permission.MISSION_READ_ALL)


def _load_deployment(context: AuthContext, deployment_id: str) -> tuple[dict, dict, Optional[MissionPlan]]:
    """The deployment, its scenario (only if the caller may read it) and its schema-2 plan (None for legacy)."""
    deployment = get_mission_deployment(deployment_id)
    scenario = get_scenario(deployment["scenario_id"], context.user.id, include_all=_include_all(context)) if deployment else None
    if deployment is None or scenario is None:
        raise _error(404, "deployment_not_found", "The deployment does not exist.")
    intent = deployment.get("intent") or {}
    plan = None
    if intent.get("schema_version") == 2 and intent.get("mission_plan") is not None:
        try:
            plan = MissionPlan.model_validate(intent["mission_plan"])
        except Exception:
            plan = None
    return deployment, scenario, plan


def role_rule(context: AuthContext, scenario: dict, deployment: dict) -> dict[str, Any]:
    """Who may start this exact version. Returned as data so the UI can show the same sentence beside the button."""
    role = context.user.role
    versions = scenario.get("versions", [])
    version = next((item for item in versions if item["id"] == deployment["scenario_version_id"]), None)
    latest = versions[0] if versions else None
    number = version["version_number"] if version else "?"
    if role not in ("GENERAL", "OPERATOR"):
        return {"allowed": False, "status": 403, "code": "role_not_allowed", "message": "This account role cannot start missions."}
    if version is None or latest is None or latest["id"] != version["id"]:
        return {"allowed": False, "status": 409, "code": "version_superseded",
                "message": f"Version {number} was superseded by version {latest['version_number'] if latest else '?'}. Deploy the latest version before starting."}
    if role == "GENERAL":
        return {"allowed": True, "status": 200, "code": "general", "message": "General accounts deploy and start directly."}
    approval = (version.get("approval") or {}).get("status")
    if approval == "APPROVED":
        return {"allowed": True, "status": 200, "code": "approved", "message": f"A General approved version {number}."}
    messages = {
        None: (f"approval_required", f"Operators need a General's approval of this exact version. Submit version {number} for review in Scenario Lab."),
        "PENDING": ("approval_pending", f"Version {number} is waiting for a General's review. Start stays disabled until it is approved."),
        "REJECTED": ("approval_rejected", f"Version {number} was rejected by a General. Save a new version and submit it for review."),
        "CHANGES_REQUESTED": ("approval_rejected", f"A General requested changes to version {number}. Save a new version and submit it for review."),
    }
    code, message = messages.get(approval, ("approval_required", f"Version {number} is not approved."))
    return {"allowed": False, "status": 403, "code": code, "message": message}


def _check(key: str, label: str, ok: bool, detail: str, **extra: Any) -> dict[str, Any]:
    return {"key": key, "label": label, "ok": bool(ok), "detail": detail, **extra}


def run_preflight(context: AuthContext, deployment: dict, scenario: dict, plan: Optional[MissionPlan]) -> dict[str, Any]:
    """Every pre-start check with pass/fail and a plain sentence. Reads state only; commands nothing."""
    from .fleet_routes import _fleet_snapshot, _verified_fleet, simulation_location
    from .scenario_routes import _plan_readiness
    from .telemetry.fleet_manager import invoke_fleet_manager

    rule = role_rule(context, scenario, deployment)
    checks: list[dict[str, Any]] = []
    superseded = rule["code"] == "version_superseded"
    if plan is None:
        checks.append(_check("plan_valid", "Deployed version is the latest and validated", False,
                             "This is a legacy (schema 1) deployment without a generated route. Plan and deploy a schema-2 mission."))
        checks.append(_check("role", "Role rule", rule["allowed"], rule["message"], code=rule["code"]))
        return {"deployment_id": deployment["id"], "can_start": False, "role_rule": rule, "checks": checks,
                "blocking": [item["detail"] for item in checks if not item["ok"]], "confirmation": START_CONFIRMATION}
    issues = _plan_readiness(plan)
    checks.append(_check(
        "plan_valid", "Deployed version is the latest and validated", not superseded and not issues,
        rule["message"] if superseded else (f"The plan no longer passes validation: {issues[0].message}" if issues
                                            else f"Version {deployment.get('scenario_version_number') or ''} is the latest version and passes validation.".replace("  ", " "))))
    checks.append(_check("role", "Role rule", rule["allowed"] or superseded, rule["message"] if not superseded else "Checked once the latest version is deployed.", code=rule["code"]))
    aircraft = next((item for item in plan.aircraft if item.role == "primary"), plan.aircraft[0] if plan.aircraft else None)
    checks.append(_check("single_aircraft", "One aircraft", len(plan.aircraft) == 1,
                         "The plan has one aircraft." if len(plan.aircraft) == 1 else
                         f"The plan has {len(plan.aircraft)} aircraft. Starting more than one aircraft is not built yet; deploy a one-aircraft plan."))
    uav_id = aircraft.uav_id if aircraft else "px4-1"
    system_id = int(uav_id[4:]) if uav_id[4:].isdigit() else 1
    process_state = invoke_fleet_manager("status", timeout_seconds=12)
    instances = {int(item.get("system_id", -1)): item for item in process_state.get("instances", []) if isinstance(item, dict)} if not process_state.get("failure") else {}
    verified, _rows = _verified_fleet(max(system_id, 1))
    is_verified = system_id in instances and any(item["aircraft_id"] == uav_id for item in verified)
    checks.append(_check("fleet_verified", "Fleet verified", is_verified,
                         f"{uav_id}: PX4 process, telemetry and Gazebo pose are verified." if is_verified else
                         (f"WSL fleet manager: {process_state['failure']}" if process_state.get("failure") else
                          f"{uav_id} is not fully verified (PX4 process, telemetry and Gazebo pose). Run Activate and verify in Scenario Lab.")))
    managed = bool(instances.get(system_id, {}).get("managed"))
    checks.append(_check("managed_instance", "Managed SITL instance", managed,
                         f"{uav_id} is the x500 SITL instance started by the TRINETRA fleet manager." if managed else
                         f"{uav_id} was not started by the TRINETRA fleet manager, so it is not commanded. Use Activate and verify or Move simulation to mission start."))
    vehicles, _models = _fleet_snapshot()
    vehicle = next((item for item in vehicles if item.get("id") == uav_id), None)
    age = (vehicle or {}).get("telemetry_age_seconds")
    fresh = bool(vehicle and vehicle.get("status") == "connected" and isinstance(age, (int, float)) and age <= TELEMETRY_FRESH_S)
    checks.append(_check("telemetry_fresh", "Telemetry fresh", fresh,
                         f"Last telemetry {age:.1f} s ago." if fresh else
                         ("No telemetry from the aircraft." if not vehicle else f"Telemetry is {vehicle.get('status')} (last data {age if age is not None else '?'} s ago; needs {TELEMETRY_FRESH_S:g} s or newer).")))
    gps = (vehicle or {}).get("gps_raw") or {}
    gps_ok = bool(gps.get("valid") and int(gps.get("fix_type") or 0) >= 3)
    checks.append(_check("gps_fix", "GPS fix", gps_ok, f"3D fix (type {gps.get('fix_type')}, {gps.get('satellites_visible', '?')} satellites)." if gps_ok else "No valid 3D GPS fix is reported."))
    position = (vehicle or {}).get("position") or {}
    velocity = (vehicle or {}).get("velocity") or {}
    armed = (vehicle or {}).get("armed")
    relative = position.get("relative_altitude_m")
    speed = velocity.get("ground_speed_m_s")
    landed = isinstance(relative, (int, float)) and abs(relative) < 1.5 and (not isinstance(speed, (int, float)) or speed < 0.7)
    checks.append(_check("disarmed_landed", "Disarmed and landed", armed is False and landed,
                         "The aircraft is disarmed and on the ground." if armed is False and landed else
                         ("The aircraft is armed." if armed else "The aircraft state is unknown or it is not on the ground (relative altitude "
                          f"{relative if relative is not None else '?'} m).")))
    location = simulation_location(plan, vehicles)
    distance = location["max_distance_to_start_m"]
    at_start = distance is not None and distance <= HOME_TOLERANCE_M
    checks.append(_check("at_mission_start", "At the mission start", at_start,
                         f"The aircraft is {distance:.0f} m from the mission start (limit {HOME_TOLERANCE_M:g} m)." if at_start else
                         (f"The aircraft is {distance / 1000:.1f} km from the mission start (limit {HOME_TOLERANCE_M:g} m). Use Move simulation to mission start."
                          if distance is not None else "The aircraft position is unknown, so its distance from the mission start cannot be checked."),
                         offer_relocation=not at_start and distance is not None))
    battery = ((vehicle or {}).get("battery") or {}).get("remaining_percent")
    battery_ok = isinstance(battery, (int, float)) and battery >= BATTERY_MIN_PCT
    checks.append(_check("battery", f"Battery at least {BATTERY_MIN_PCT:g}%", battery_ok,
                         f"Battery {battery:.0f}%." if isinstance(battery, (int, float)) else "No battery reading."))
    running = active_mission_execution(uav_id)
    if running is not None:
        running = sync_execution(running, force=True)
    busy = bool(running and running["active"])
    checks.append(_check("no_execution_running", "No other execution running", not busy,
                         f"An execution is already {running['state']} on {uav_id}." if busy else f"Nothing is running on {uav_id}."))
    limits = PlanningLimits.from_config()
    route = aircraft.route if aircraft else None
    within = bool(route and route.duration_s <= limits.max_flight_s and route.length_m <= limits.max_flight_distance_m)
    checks.append(_check("endurance", "Route within the endurance limit", within,
                         (f"About {route.duration_s / 60:.1f} min and {route.length_m / 1000:.2f} km; the aircraft limit is {limits.max_flight_s / 60:g} min and {limits.max_flight_distance_m / 1000:g} km."
                          if route else "The aircraft has no generated route.")))
    blocking = [rule["message"]] if not rule["allowed"] else []
    blocking += [item["detail"] for item in checks if not item["ok"] and item["detail"] not in blocking]
    return {"deployment_id": deployment["id"], "aircraft_id": uav_id, "can_start": rule["allowed"] and all(item["ok"] for item in checks),
            "role_rule": rule, "checks": checks, "blocking": blocking, "confirmation": START_CONFIRMATION,
            "estimated_duration_s": route.duration_s if route else None, "route_waypoints": len(route.waypoints) if route else 0}


# --- reading the executor ------------------------------------------------------------------------------------------

def _event_time(epoch: Any) -> datetime:
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError, OverflowError):
        return datetime.now(timezone.utc).replace(tzinfo=None)


def _backend_event(execution_id: str, kind: str, message: str, context: Optional[AuthContext] = None, data: Optional[dict] = None) -> None:
    add_mission_execution_events(execution_id, [{
        "source": "user" if context else "backend", "type": kind, "message": message, "data": data or {},
        "actor_id": context.user.id if context else None, "actor_name": context.user.full_name if context else None}])


def sync_execution(execution: dict, *, force: bool = False) -> dict:
    """Bring one active execution up to date from the executor's status and event files."""
    if not execution["active"]:
        return execution
    execution_id = execution["id"]
    now = time.monotonic()
    if not force and now - _last_sync.get(execution_id, 0.0) < SYNC_MIN_INTERVAL_S:
        return execution
    _last_sync[execution_id] = now
    progress = dict(execution["progress"])
    wall = time.time()
    created = datetime.fromisoformat(execution["created_at"].rstrip("Z")).replace(tzinfo=timezone.utc).timestamp()
    last_contact = float(progress.get("last_contact") or created)
    result = invoke_mission_executor("status", instance=execution["instance_number"], events_after=execution["last_event_index"], timeout_seconds=12)
    status = result.get("status") if not result.get("failure") else None
    if not isinstance(status, dict) or status.get("execution_id") != execution_id:
        if wall - last_contact <= EXECUTOR_STALE_S:
            return execution  # a slow start or one missed poll is not a lost link
        if result.get("failure"):
            if execution["state"] != "link_lost":
                _backend_event(execution_id, "link_lost", f"Link lost: the mission executor has not answered for more than {EXECUTOR_STALE_S:g} s ({result['failure']}).")
            return update_mission_execution(execution_id, state="link_lost", reason=f"The mission executor cannot be reached: {result['failure']}") or execution
        _backend_event(execution_id, "failed", "The mission executor has no record of this run.")
        return update_mission_execution(execution_id, state="failed", reason="The mission executor has no record of this run.", finished=True) or execution
    events = [item for item in result.get("events", []) if isinstance(item, dict) and isinstance(item.get("index"), int)]
    if events:
        add_mission_execution_events(execution_id, [{
            "source": "executor", "type": str(item.get("type", "event")), "message": str(item.get("message", "")),
            "data": item.get("data") if isinstance(item.get("data"), dict) else {}, "executor_index": item["index"],
            "occurred_at": _event_time(item.get("t"))} for item in events])
    for key in ("uploaded", "armed", "px4_mode", "current_item", "items", "route_total", "route_reached", "route_current", "percent",
                "px4_heartbeat_age_s", "gcs_heartbeats_sent", "armed_at", "px4_messages"):
        if key in status:
            progress[key] = status[key]
    progress["last_contact"] = wall
    state, reason, final = str(status.get("state", execution["state"])), str(status.get("reason", "")), bool(status.get("terminal"))
    reported = float(status.get("updated_at") or 0.0)
    executor_now = float(result.get("now") or wall)
    if not final and not result.get("daemon_alive"):
        state, reason, final = "link_lost", "The mission executor process stopped before the run finished.", True
        _backend_event(execution_id, "link_lost", "Link lost: the mission executor process stopped before the run finished.")
    elif not final and executor_now - reported > EXECUTOR_STALE_S:
        if execution["state"] != "link_lost":
            _backend_event(execution_id, "link_lost", f"Link lost: the mission executor has not reported for more than {EXECUTOR_STALE_S:g} s.")
        state, reason = "link_lost", f"The mission executor has not reported for more than {EXECUTOR_STALE_S:g} s."
    if final:
        progress["finished_wall"] = wall
    last_index = max([execution["last_event_index"]] + [item["index"] for item in events])
    return update_mission_execution(execution_id, state=state, reason=reason, progress=progress, last_event_index=last_index, finished=final) or execution


def _controls(context: AuthContext, execution: dict, rule: Optional[dict]) -> dict[str, dict[str, Any]]:
    """Whether the caller may pause / resume / abort right now, each with the exact reason when not."""
    state, active = execution["state"], execution["active"]
    may_execute = has_permission(context.user.role, Permission.SCENARIO_EXECUTE)

    def entry(allowed: bool, reason: str) -> dict[str, Any]:
        return {"allowed": allowed, "reason": None if allowed else reason}

    role_block = None if (rule is None or rule["allowed"] or rule["code"] == "version_superseded") else rule["message"]
    if not may_execute:
        blocked = entry(False, "This account role cannot control missions.")
        return {"pause": blocked, "resume": blocked, "abort": blocked}
    finished = f"The run is already {state}."
    return {
        "pause": entry(active and state == "executing" and not role_block, role_block or (finished if not active else f"Pause is possible while executing; the run is {state}.")),
        "resume": entry(active and state == "paused" and not role_block, role_block or (finished if not active else f"Resume is possible while paused; the run is {state}.")),
        "abort": entry(active, finished),
    }


def _view(context: AuthContext, execution: dict, *, rule: Optional[dict] = None, estimated_s: Optional[float] = None) -> dict[str, Any]:
    progress = execution["progress"]
    state = execution["state"]
    armed_at = progress.get("armed_at")
    end = progress.get("finished_wall") if not execution["active"] else time.time()
    elapsed = max(0.0, float(end) - float(armed_at)) if isinstance(armed_at, (int, float)) and isinstance(end, (int, float)) else None
    stage = ("completed" if state == "completed" else "aborted" if state == "aborted" else "failed" if state == "failed"
             else "link_lost" if state == "link_lost" else "executing" if state in FLYING_STATES else "uploaded" if progress.get("uploaded") else "starting")
    return {
        "id": execution["id"], "deployment_id": execution["deployment_id"], "scenario_id": execution["scenario_id"],
        "scenario_version_id": execution["scenario_version_id"], "aircraft_id": execution["aircraft_id"],
        "state": state, "stage": stage, "reason": execution["reason"], "active": execution["active"],
        "started_by_name": execution["started_by_name"], "started_by_role": execution["started_by_role"],
        "created_at": execution["created_at"], "updated_at": execution["updated_at"], "finished_at": execution["finished_at"],
        "uploaded": bool(progress.get("uploaded")), "armed": bool(progress.get("armed")), "px4_mode": progress.get("px4_mode"),
        "route_total": progress.get("route_total"), "route_reached": progress.get("route_reached"), "route_current": progress.get("route_current"),
        "percent": progress.get("percent"), "elapsed_s": round(elapsed, 1) if elapsed is not None else None, "estimated_s": estimated_s,
        "px4_heartbeat_age_s": progress.get("px4_heartbeat_age_s"), "px4_messages": progress.get("px4_messages") or [],
        "controls": _controls(context, execution, rule), "simulation_only": True,
    }


def _full_view(context: AuthContext, execution: dict) -> dict[str, Any]:
    rule, estimated = None, None
    deployment = get_mission_deployment(execution["deployment_id"])
    scenario = get_scenario(execution["scenario_id"], context.user.id, include_all=_include_all(context))
    if deployment and scenario:
        rule = role_rule(context, scenario, deployment)
        plan = ((deployment.get("intent") or {}).get("mission_plan") or {})
        aircraft = next((item for item in plan.get("aircraft", []) if item.get("uav_id") == execution["aircraft_id"]), None)
        estimated = ((aircraft or {}).get("route") or {}).get("duration_s")
    return _view(context, execution, rule=rule, estimated_s=estimated)


def _visible_execution(context: AuthContext, execution_id: str) -> dict:
    execution = get_mission_execution(execution_id)
    if execution is None or get_scenario(execution["scenario_id"], context.user.id, include_all=_include_all(context)) is None:
        raise _error(404, "execution_not_found", "The execution does not exist.")
    return execution


def _audit(request: Request, context: AuthContext, action: str, execution_id: str, reason: str, metadata: Optional[dict] = None) -> None:
    add_audit_event(action=action, outcome="success", reason=reason, request_id=getattr(request.state, "request_id", "request-unavailable"),
                    actor_id=context.user.id, actor_name=context.user.full_name, target_type="mission_execution", target_id=execution_id,
                    metadata=metadata or {})


# --- routes -----------------------------------------------------------------------------------------------------------

@router.get("/api/mission-control/executions/preflight")
def execution_preflight(deployment_id: str = Query(min_length=1, max_length=64),
                        context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    deployment, scenario, plan = _load_deployment(context, deployment_id)
    return run_preflight(context, deployment, scenario, plan)


@router.get("/api/mission-control/executions/latest")
def latest_execution(context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    execution = latest_mission_execution(context.user.id, include_all=_include_all(context))
    if execution is None:
        return {"execution": None}
    return {"execution": _full_view(context, sync_execution(execution))}


@router.post("/api/mission-control/executions", status_code=201)
def start_execution(payload: StartRequest, request: Request,
                    context: AuthContext = Depends(require_permission(Permission.SCENARIO_EXECUTE)),
                    _csrf: AuthContext = Depends(require_csrf)) -> dict:
    deployment, scenario, plan = _load_deployment(context, payload.deployment_id)
    rule = role_rule(context, scenario, deployment)
    if not rule["allowed"]:  # the role rule is answered before anything else is looked at
        raise _error(rule["status"], rule["code"], rule["message"])
    if not payload.confirm:
        raise _error(422, "confirmation_required", f"Confirm the start first. {START_CONFIRMATION}", confirmation=START_CONFIRMATION)
    preflight = run_preflight(context, deployment, scenario, plan)
    if not preflight["can_start"]:
        raise _error(409, "preflight_failed", preflight["blocking"][0] if preflight["blocking"] else "A pre-start check failed.",
                     checks=preflight["checks"], blocking=preflight["blocking"])
    assert plan is not None
    aircraft = next(item for item in plan.aircraft if item.uav_id == preflight["aircraft_id"])
    execution_id = uuid.uuid4().hex
    try:
        mission = build_mission(aircraft, execution_id)
    except MissionItemsError as error:
        raise _error(409, "route_not_flyable", f"The stored route cannot be flown: {error}.") from error
    try:
        execution = create_mission_execution(
            execution_id=execution_id, deployment_id=deployment["id"], scenario_id=deployment["scenario_id"],
            scenario_version_id=deployment["scenario_version_id"], aircraft_id=aircraft.uav_id, instance_number=instance_for_aircraft(aircraft.uav_id),
            user_id=context.user.id, user_name=context.user.full_name, role=context.user.role)
    except ValueError as error:
        raise _error(409, "execution_running", f"Another execution is already running on {aircraft.uav_id}.") from error
    _backend_event(execution_id, "start_requested", f"{context.user.full_name} ({context.user.role}) started the mission in simulation.", context,
                   {"deployment_id": deployment["id"], "mission_items": len(mission["items"]), "route_waypoints": mission["route_total"]})
    result = invoke_mission_executor("start", instance=mission["instance"], mission=mission, timeout_seconds=25)
    if result.get("failure"):
        code = str(result.get("failure_code") or "executor_error")
        message = f"The simulation did not start: {result['failure']}"
        _backend_event(execution_id, "failed", message, data={"code": code})
        update_mission_execution(execution_id, state="failed", reason=message, finished=True)
        status = 409 if code in {"instance_not_managed", "instance_not_running", "execution_running"} else 503 if code in {"pymavlink_missing", "executor_unreachable"} else 502
        raise _error(status, code, message, execution_id=execution_id)
    _audit(request, context, "mission_execution_started", execution_id,
           "Mission started on the managed PX4 SITL instance (software simulation).",
           {"deployment_id": deployment["id"], "scenario_version_id": deployment["scenario_version_id"], "aircraft_id": aircraft.uav_id})
    return _full_view(context, sync_execution(execution, force=True))


@router.get("/api/mission-control/executions/{execution_id}")
def read_execution(execution_id: str, context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    return _full_view(context, sync_execution(_visible_execution(context, execution_id)))


@router.get("/api/mission-control/executions/{execution_id}/events")
def execution_events(execution_id: str, after: int = Query(default=0, ge=0),
                     context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    execution = sync_execution(_visible_execution(context, execution_id))
    return {"execution_id": execution_id, "state": execution["state"], "events": list_mission_execution_events(execution_id, after_id=after)}


def _control(command: str, execution_id: str, request: Request, context: AuthContext) -> dict:
    execution = sync_execution(_visible_execution(context, execution_id), force=True)
    deployment = get_mission_deployment(execution["deployment_id"])
    scenario = get_scenario(execution["scenario_id"], context.user.id, include_all=_include_all(context))
    rule = role_rule(context, scenario, deployment) if deployment and scenario else None
    allowed = _controls(context, execution, rule)[command]
    if not allowed["allowed"]:
        if command != "abort" and rule is not None and not rule["allowed"] and rule["code"] != "version_superseded":
            raise _error(403, rule["code"], rule["message"])
        raise _error(409, "already_finished" if not execution["active"] else f"not_{'executing' if command == 'pause' else 'paused'}", allowed["reason"], state=execution["state"])
    labels = {"pause": "paused the mission (Hold)", "resume": "resumed the mission", "abort": "aborted the mission (Return to launch)"}
    _backend_event(execution_id, f"{command}_requested_by_user", f"{context.user.full_name} ({context.user.role}) {labels[command]}.", context)
    result = invoke_mission_executor(command, instance=execution["instance_number"], execution_id=execution_id, timeout_seconds=20)
    if result.get("failure"):
        code = str(result.get("failure_code") or "executor_error")
        _backend_event(execution_id, f"{command}_failed", f"The {command} request did not reach the executor: {result['failure']}", data={"code": code})
        raise _error(503 if code == "executor_unreachable" else 409, code, f"The {command} request did not reach the executor: {result['failure']}")
    _audit(request, context, f"mission_execution_{command}", execution_id, f"User {labels[command]} on the managed PX4 SITL instance.")
    return {**_full_view(context, sync_execution(get_mission_execution(execution_id) or execution, force=True)), "request": {"command": command, "detail": result.get("detail")}}


@router.post("/api/mission-control/executions/{execution_id}/pause")
def pause_execution(execution_id: str, request: Request, context: AuthContext = Depends(require_permission(Permission.SCENARIO_EXECUTE)),
                    _csrf: AuthContext = Depends(require_csrf)) -> dict:
    return _control("pause", execution_id, request, context)


@router.post("/api/mission-control/executions/{execution_id}/resume")
def resume_execution(execution_id: str, request: Request, context: AuthContext = Depends(require_permission(Permission.SCENARIO_EXECUTE)),
                     _csrf: AuthContext = Depends(require_csrf)) -> dict:
    return _control("resume", execution_id, request, context)


@router.post("/api/mission-control/executions/{execution_id}/abort")
def abort_execution(execution_id: str, request: Request, context: AuthContext = Depends(require_permission(Permission.SCENARIO_EXECUTE)),
                    _csrf: AuthContext = Depends(require_csrf)) -> dict:
    return _control("abort", execution_id, request, context)


# --- disarm (a normal MAVLink disarm, never forced) --------------------------------------------------------------------

_AIRCRAFT_ID = re.compile(r"^px4-[1-5]$")


def disarm_decision(vehicle: Optional[dict[str, Any]], execution: Optional[dict]) -> Optional[tuple[int, str, str]]:
    """None when a disarm may be sent, else (HTTP status, code, reason). The same sentences are shown beside the disabled button."""
    state = ground_state(vehicle)
    if not state["connected"]:
        return 409, "no_telemetry", "No telemetry from this aircraft, so its armed state is unknown."
    if state["armed"] is False:
        return 409, "already_disarmed", "The aircraft is already disarmed."
    if state["armed"] is not True:
        return 409, "armed_state_unknown", "PX4 has not reported whether the aircraft is armed."
    if execution is not None and execution["active"]:
        if execution["state"] in FLYING_STATES:
            return 409, "in_the_air", IN_THE_AIR
        return 409, "execution_active", f"A run is {execution['state']} on this aircraft. Wait for it, or abort it first."
    if not state["known"]:
        return 409, "altitude_unknown", "The aircraft's altitude is unknown, so it cannot be confirmed on the ground."
    if not state["on_ground"]:
        return 409, "in_the_air", IN_THE_AIR
    return None


@router.post("/api/mission-control/aircraft/{aircraft_id}/disarm")
def disarm_aircraft(aircraft_id: str, request: Request, context: AuthContext = Depends(require_permission(Permission.SCENARIO_EXECUTE)),
                    _csrf: AuthContext = Depends(require_csrf)) -> dict:
    """Disarm a managed SITL aircraft that is armed and on the ground and not being flown by an execution. Like Abort it needs no approval."""
    from .fleet_routes import _fleet_snapshot

    if not _AIRCRAFT_ID.fullmatch(aircraft_id):
        raise _error(422, "aircraft_invalid", "The aircraft id must look like px4-1.")
    vehicle = next((item for item in _fleet_snapshot()[0] if item.get("id") == aircraft_id), None)
    running = active_mission_execution(aircraft_id)
    if running is not None:
        running = sync_execution(running, force=True)
    refusal = disarm_decision(vehicle, running)
    if refusal is not None:
        raise _error(*refusal)
    result = invoke_mission_executor("disarm", instance=instance_for_aircraft(aircraft_id), timeout_seconds=25)
    last = latest_mission_execution(context.user.id, include_all=_include_all(context))
    attach = last["id"] if last is not None and last["aircraft_id"] == aircraft_id and not last["active"] else None
    who = f"{context.user.full_name} ({context.user.role})"
    if result.get("failure"):
        code = str(result.get("failure_code") or "executor_error")
        if attach:
            _backend_event(attach, "disarm_failed", f"{who} asked to disarm {aircraft_id}; it did not reach PX4: {result['failure']}", context, {"code": code})
        raise _error(409 if code in {"instance_not_managed", "instance_not_running", "execution_running"} else 503 if code in {"pymavlink_missing", "executor_unreachable"} else 502,
                     code, f"The disarm request did not reach PX4: {result['failure']}")
    messages = [str(item) for item in result.get("px4_messages", [])]
    if not result.get("accepted"):
        reason = f"PX4 refused to disarm ({result.get('ack') or 'no answer'})." + (f" PX4 said: {'; '.join(messages)}" if messages else "")
        if attach:
            _backend_event(attach, "disarm_refused", f"{who} asked to disarm {aircraft_id}: {reason}", context, {"ack": result.get("ack"), "px4_messages": messages})
        add_audit_event(action="aircraft_disarm", outcome="refused", reason=reason, request_id=getattr(request.state, "request_id", "request-unavailable"),
                        actor_id=context.user.id, actor_name=context.user.full_name, target_type="aircraft", target_id=aircraft_id, metadata={"ack": result.get("ack")})
        raise _error(409, "disarm_refused", reason, ack=result.get("ack"), px4_messages=messages)
    if attach:
        _backend_event(attach, "disarmed", f"{who} disarmed {aircraft_id} on the ground (normal disarm, no force).", context, {"px4_messages": messages})
    add_audit_event(action="aircraft_disarm", outcome="success", reason="Normal disarm sent to the managed PX4 SITL aircraft on the ground; PX4 reports disarmed.",
                    request_id=getattr(request.state, "request_id", "request-unavailable"), actor_id=context.user.id, actor_name=context.user.full_name,
                    target_type="aircraft", target_id=aircraft_id, metadata={"forced": False})
    return {"aircraft_id": aircraft_id, "disarmed": True, "armed": False, "forced": False, "px4_messages": messages, "simulation_only": True}
