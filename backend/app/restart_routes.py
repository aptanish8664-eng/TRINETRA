"""Restart at mission start / Switch and restart at the new start: one background job with a status per step.

POST /api/mission-control/restart       start a restart of the simulation at a deployment's mission start (202, job view)
GET  /api/mission-control/restart/{id}  the job: status running | succeeded | failed, steps, the failed step and reason, can_retry
GET  /api/mission-control/restart       the newest job (so a reloaded page can pick up a restart in progress)

POST /api/mission-control/restart/{id}/retry-missing   restart only the aircraft that did not come up (the ready ones are left running)
POST /api/mission-control/restart/{id}/continue        accept a partial result: continue with the ready aircraft (the others stay "not available")

Available in every aircraft state (idle, armed on the ground, flying). Any account that may execute scenarios may restart (like Abort, no
approval). `purpose: "switch"` is the one-click mission switch: the deployment is the NEW one and the caller must be allowed to start it
(an OPERATOR needs an approved version). One restart at a time. The job reports every aircraft (starting, telemetry_ok, pose_ok, ready, failed) and
is reported failed only when NOTHING changed for JOB_STUCK_S (progress, not a fixed timer), so the UI is never left on "Restarting". When only some
aircraft came up the job ends `partial`, with what is ready and what is missing. After a failure the steps that did not run say "skipped" (not run),
never RUNNING. SITL only; no flight command is sent.
"""
from __future__ import annotations

from datetime import datetime, timezone
import threading
import time
from typing import Any, Literal, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth_routes import require_csrf, require_permission
from .authorization import Permission
from .mission_execution.restart import REASONS, new_steps, restart_simulation, summary_line
from .mission_planning.start import mission_start
from .persistence.database import add_audit_event
from .security import AuthContext
from .telemetry.fleet_manager import FLEET_CAPACITY

router = APIRouter()

RESTART_CONFIRMATION = "This restarts PX4 and Gazebo at the mission start (about 20 s). A flight in progress ends."
SWITCH_CONFIRMATION = ("The current flight ends, and PX4 and Gazebo restart at the NEW mission's start (about 20 s). "
                       "Then the new deployment is shown, with the aircraft at its start, disarmed and ready to Start.")
JOB_STUCK_S = 150.0          # nothing at all changed in a running job for this long (a hung manager call); every progress report resets it

_guard = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
_state: dict[str, Optional[str]] = {"active": None, "latest": None}


class RestartRequest(BaseModel):
    deployment_id: str = Field(min_length=1, max_length=64)
    confirm: bool = False
    purpose: Literal["restart", "switch"] = "restart"


def _error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _touch(job: dict[str, Any]) -> None:
    job["last_change_mono"] = time.monotonic()


def _skip_remaining(job: dict[str, Any]) -> None:
    """After a failure every later step that never ran says so (skipped = not run); it is never left on running or pending."""
    failed = False
    for item in job["steps"]:
        if item["status"] == "failed":
            failed = True
        elif failed and item["status"] in ("pending", "running"):
            item.update(status="skipped", detail="Not run: an earlier step failed.")


def _view(job: dict[str, Any]) -> dict[str, Any]:
    status = job["status"]
    if status == "running" and time.monotonic() - job.get("last_change_mono", job["started_mono"]) > JOB_STUCK_S:
        with _guard:
            if job["status"] == "running":
                running = next((item for item in job["steps"] if item["status"] == "running"), None)
                job.update(status="failed", failure={"step": running["key"] if running else "executor", "code": "restart_timeout",
                                                     "message": f"The restart did not finish within {JOB_STUCK_S:g} s and was given up."}, finished_at=_now_iso())
                if running:
                    running.update(status="failed", detail=job["failure"]["message"])
                _skip_remaining(job)
                if _state["active"] == job["id"]:
                    _state["active"] = None
    aircraft = job.get("aircraft") or []
    return {key: job[key] for key in ("id", "status", "purpose", "deployment_id", "scenario_id", "scenario_version_id", "steps", "failure", "started_at", "finished_at",
                                      "started_by_name", "reason", "result")} | {
        "aircraft": aircraft, "requested_count": job.get("requested", 0), "ready_count": sum(1 for row in aircraft if row["state"] == "ready"),
        "summary": summary_line(aircraft) if aircraft else "", "partial": job["status"] == "partial", "accepted_partial": bool(job.get("accepted")),
        "missing": [row["aircraft_id"] for row in aircraft if row["state"] != "ready"] if job["status"] in ("partial", "failed") else [],
        "can_retry_missing": job["status"] == "partial" and not job.get("accepted"), "can_continue": job["status"] == "partial" and not job.get("accepted"),
        "retry_of": job.get("retry_of"), "can_retry": job["status"] == "failed", "elapsed_s": round((time.monotonic() - job["started_mono"]) if job["status"] == "running" else job["elapsed_s"], 1),
                                                                                    "simulation_only": True}


def _worker(job: dict[str, Any], plan: Any, home: dict[str, Any], requested: int, context: AuthContext, before: dict[str, Any],
            instances: Optional[list[int]] = None, stop_first: bool = True) -> None:
    def progress(key: str, status: str, detail: str = "") -> None:
        for item in job["steps"]:
            if item["key"] == key and (item["status"], item["detail"]) != (status, detail):
                item["status"], item["detail"] = status, detail
                _touch(job)

    def on_aircraft(rows: list[dict[str, Any]]) -> None:
        if [(row["aircraft_id"], row["state"]) for row in rows] != [(row["aircraft_id"], row["state"]) for row in job.get("aircraft") or []]:
            _touch(job)
        job["aircraft"] = rows

    try:
        result = restart_simulation(plan, home, requested, correlation_id=job["id"], purpose=job["purpose"], context=context, progress=progress, on_aircraft=on_aircraft,
                                    instances=instances, stop_first=stop_first)
    except Exception as error:   # a bug must not leave the UI on "Restarting"
        result = {"ok": False, "failed_step": "executor", "failure": (500, "restart_error", f"The restart stopped unexpectedly: {type(error).__name__}")}
    with _guard:
        if job["status"] != "running":
            return                                          # the view already gave this job up as stuck
        job["elapsed_s"] = time.monotonic() - job["started_mono"]
        job["finished_at"] = _now_iso()
        if result.get("aircraft"):
            job["aircraft"] = result["aircraft"]
        if result["ok"]:
            job.update(status="succeeded", failure=None, result={key: result.get(key) for key in (
                "mission_start", "max_distance_to_start_m", "armed", "ended_executions", "killed_processes", "elapsed_s", "activated_uav_count")})
        elif result.get("partial"):
            status_code, code, message = result["failure"]
            job.update(status="partial", failure={"step": result.get("failed_step"), "code": code, "message": message, "http_status": status_code}, result={
                "mission_start": result.get("mission_start"), "ready": result.get("ready"), "missing": result.get("missing"), "ended_executions": result.get("ended")})
        else:
            status_code, code, message = result["failure"]
            job.update(status="failed", failure={"step": result.get("failed_step"), "code": code, "message": message, "http_status": status_code}, result=None)
        _skip_remaining(job)
        if _state["active"] == job["id"]:
            _state["active"] = None
    outcome = "success" if result["ok"] else "partial" if result.get("partial") else "failed"
    add_audit_event(action="simulation_restart" if job["purpose"] == "restart" else "simulation_switch_restart", outcome=outcome,
                    reason=("Simulation restarted at the mission start (" + REASONS[job["purpose"]] + ").") if result["ok"] else result["failure"][2],
                    request_id=job["id"], actor_id=context.user.id, actor_name=context.user.full_name, target_type="mission_deployment", target_id=job["deployment_id"],
                    metadata={"purpose": job["purpose"], "aircraft_before": before, "failed_step": result.get("failed_step"), "requested": requested, "retry_of": job.get("retry_of"),
                              "aircraft": [{"aircraft_id": row["aircraft_id"], "state": row["state"]} for row in job.get("aircraft") or []],
                              "ended_executions": result.get("ended_executions"), "killed_processes": result.get("killed_processes"), "home": {k: home[k] for k in ("latitude", "longitude", "altitude_m")}})


def _launch(context: AuthContext, deployment: dict[str, Any], plan: Any, purpose: str, *, instances: Optional[list[int]] = None, stop_first: bool = True,
            retry_of: Optional[str] = None) -> dict[str, Any]:
    """Create the job and start its worker thread. One restart at a time."""
    from .execution_routes import _aircraft_before
    from .fleet_routes import _fleet_snapshot
    from .mission_execution.ground import ground_state

    home = mission_start(plan)
    if home is None:
        raise _error(422, "mission_start_unavailable", "This plan has no take-off point yet. Pick the primary aircraft's start in Scenario Lab and save.")
    requested = plan.required_uav_count or len(plan.aircraft) or 1
    vehicles = _fleet_snapshot()[0]
    primary = next((item for item in vehicles if item.get("id") == (plan.aircraft[0].uav_id if plan.aircraft else "px4-1")), None)
    before = _aircraft_before(primary, ground_state(primary), None)
    with _guard:
        active = _jobs.get(_state["active"] or "")
        if active is not None and active["status"] == "running" and time.monotonic() - active.get("last_change_mono", active["started_mono"]) <= JOB_STUCK_S:
            raise _error(409, "restart_in_progress", "A restart is already running. Wait for it to finish.", job_id=active["id"])
        job = {"id": uuid.uuid4().hex, "status": "running", "purpose": purpose, "deployment_id": deployment["id"], "scenario_id": deployment["scenario_id"],
               "scenario_version_id": deployment["scenario_version_id"], "steps": new_steps(), "failure": None, "started_at": _now_iso(), "finished_at": None,
               "started_by_name": context.user.full_name, "reason": REASONS[purpose], "result": None, "started_mono": time.monotonic(), "last_change_mono": time.monotonic(),
               "elapsed_s": 0.0, "requested": requested, "aircraft": [], "retry_of": retry_of}
        _jobs[job["id"]] = job
        for old in list(_jobs)[:-20]:
            _jobs.pop(old, None)
        _state["active"] = _state["latest"] = job["id"]
    threading.Thread(target=_worker, args=(job, plan, home, requested, context, before, instances, stop_first), name="trinetra-restart", daemon=True).start()
    return _view(job)


@router.post("/api/mission-control/restart", status_code=202)
def start_restart(payload: RestartRequest, request: Request, context: AuthContext = Depends(require_permission(Permission.SCENARIO_EXECUTE)),
                  _csrf: AuthContext = Depends(require_csrf)) -> dict:
    from .execution_routes import _load_deployment, role_rule

    confirmation = SWITCH_CONFIRMATION if payload.purpose == "switch" else RESTART_CONFIRMATION
    if not payload.confirm:
        raise _error(422, "confirmation_required", f"Confirm first. {confirmation}", confirmation=confirmation)
    deployment, scenario, plan = _load_deployment(context, payload.deployment_id)
    if plan is None:
        raise _error(422, "version_not_schema_2", "This is a legacy deployment without a route plan, so it has no mission start. Plan it again in Scenario Lab.")
    if payload.purpose == "switch":
        rule = role_rule(context, scenario, deployment)
        if not rule["allowed"]:
            raise _error(rule["status"], rule["code"], rule["message"])
    requested = plan.required_uav_count or len(plan.aircraft) or 1
    if requested > FLEET_CAPACITY:
        from .mission_planning.quick import capacity_message, capacity_options
        raise _error(409, "capacity_exceeded", capacity_message(requested, FLEET_CAPACITY), capacity=FLEET_CAPACITY, required_uav_count=requested, options=capacity_options(requested, FLEET_CAPACITY))
    return _launch(context, deployment, plan, payload.purpose)


@router.post("/api/mission-control/restart/{job_id}/retry-missing", status_code=202)
def retry_missing(job_id: str, request: Request, context: AuthContext = Depends(require_permission(Permission.SCENARIO_EXECUTE)),
                  _csrf: AuthContext = Depends(require_csrf)) -> dict:
    """Restart only the aircraft that did not come up; the ready ones keep running and nothing is stopped."""
    from .execution_routes import _load_deployment

    job = _jobs.get(job_id)
    if job is None:
        raise _error(404, "restart_not_found", "The restart does not exist.")
    if job["status"] != "partial" or job.get("accepted"):
        raise _error(409, "nothing_to_retry", "Only a restart that ended with some aircraft missing can be retried this way.")
    deployment, _scenario, plan = _load_deployment(context, job["deployment_id"])
    missing = [int(row["aircraft_id"][4:]) - 1 for row in job.get("aircraft") or [] if row["state"] != "ready"]
    if not missing:
        raise _error(409, "nothing_to_retry", "Every aircraft is ready.")
    return _launch(context, deployment, plan, job["purpose"], instances=missing, stop_first=False, retry_of=job_id)


@router.post("/api/mission-control/restart/{job_id}/continue")
def continue_with_ready(job_id: str, request: Request, context: AuthContext = Depends(require_permission(Permission.SCENARIO_EXECUTE)),
                        _csrf: AuthContext = Depends(require_csrf)) -> dict:
    """Accept a partial result: the ready aircraft are used, the others stay not available. Nothing is started or stopped."""
    job = _jobs.get(job_id)
    if job is None:
        raise _error(404, "restart_not_found", "The restart does not exist.")
    if job["status"] != "partial":
        raise _error(409, "nothing_to_accept", "Only a restart that ended with some aircraft missing can be continued with the ready aircraft.")
    with _guard:
        job["accepted"] = True
    ready = [row["aircraft_id"] for row in job.get("aircraft") or [] if row["state"] == "ready"]
    missing = [row["aircraft_id"] for row in job.get("aircraft") or [] if row["state"] != "ready"]
    add_audit_event(action="simulation_restart_partial_accepted", outcome="success", reason=f"Continued with {', '.join(ready)}; {', '.join(missing)} not available.",
                    request_id=getattr(request.state, "request_id", job_id), actor_id=context.user.id, actor_name=context.user.full_name, target_type="mission_deployment",
                    target_id=job["deployment_id"], metadata={"job_id": job_id, "ready": ready, "missing": missing})
    return _view(job)


@router.get("/api/mission-control/restart")
def latest_restart(context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    job = _jobs.get(_state["latest"] or "")
    return {"job": _view(job) if job is not None else None}


@router.get("/api/mission-control/restart/{job_id}")
def read_restart(job_id: str, context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise _error(404, "restart_not_found", "The restart does not exist.")
    return _view(job)
