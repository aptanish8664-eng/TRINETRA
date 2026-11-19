"""Compact, cheap answers for Mission Control (read-only, no trails, no WSL calls).

GET /api/mission-control/aircraft-summary?deployment_id=   one small row per planned aircraft: role, link, state, battery, altitude, position, Gazebo pose, verified or the reason it is not
GET /api/fleet/capacity?count=N                              would this computer cope with N simulated aircraft (see app/capacity.py)

Why: the page used to poll several full per-aircraft payloads and run the pre-start checks (each of which started a Python process in WSL) every few seconds. This answer is built
from the two in-memory telemetry stores only, so it costs well under a millisecond per aircraft.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from . import config
from .auth_routes import require_permission
from .authorization import Permission
from .capacity import assess
from .security import AuthContext

router = APIRouter()


def _link(vehicle: Optional[dict[str, Any]]) -> str:
    return "not_seen" if vehicle is None else str(vehicle.get("status") or "unknown")


def not_verified_reason(vehicle: Optional[dict[str, Any]], model: Optional[dict[str, Any]], *, in_simulation: bool) -> Optional[str]:
    """The one sentence that says why an aircraft is not verified (None when it is). Verified = telemetry connected with a valid position AND a connected Gazebo pose."""
    if not in_simulation:
        return "Not part of the running simulation (no PX4 instance has been started for it)."
    if vehicle is None:
        return "No PX4 telemetry has arrived for this aircraft."
    if vehicle.get("status") != "connected":
        age = vehicle.get("heartbeat_age_seconds")
        return f"PX4 telemetry is {vehicle.get('status')}" + ("" if age is None else f" (last heartbeat {age:.1f} s ago)") + "."
    position = vehicle.get("position")
    if not position or position.get("valid") is not True:
        return "PX4 has no valid GPS position yet."
    if model is None:
        return "No Gazebo pose has arrived for this aircraft."
    if model.get("status") != "connected":
        return f"The Gazebo pose is {model.get('status')} (last heard {model.get('telemetry_age_seconds')} s ago)."
    return None


def build_summary(plan_aircraft: list[dict[str, Any]], vehicles: list[dict[str, Any]], models: list[dict[str, Any]], executions: dict[str, dict[str, Any]], *, running_count: int) -> dict[str, Any]:
    by_system = {int(vehicle["system_id"]): vehicle for vehicle in vehicles}
    by_model = {model["model_name"]: model for model in models}
    rows = []
    for index, item in enumerate(plan_aircraft):
        uav_id = item["uav_id"]
        system_id = int(uav_id.split("-")[1])
        vehicle, model = by_system.get(system_id), by_model.get(f"x500_{system_id - 1}")
        position = (vehicle or {}).get("position") or {}
        battery = (vehicle or {}).get("battery") or {}
        execution = executions.get(uav_id)
        reason = not_verified_reason(vehicle, model, in_simulation=system_id <= running_count or vehicle is not None or model is not None)
        pose = (model or {}).get("position_m") or {}
        rows.append({
            "aircraft_id": uav_id, "role": item.get("role"), "plan_role": item.get("plan_role"), "link": _link(vehicle), "gazebo": "not_seen" if model is None else model.get("status"),
            "armed": (vehicle or {}).get("armed"), "mode": ((vehicle or {}).get("heartbeat") or {}).get("flight_mode"), "landed_state": ((vehicle or {}).get("landed_state") or {}).get("name"),
            "battery_percent": battery.get("remaining_percent"), "altitude_m": position.get("relative_altitude_m"),
            "latitude": position.get("latitude"), "longitude": position.get("longitude"), "gps_valid": position.get("valid"),
            "pose_m": {"x": pose.get("x"), "y": pose.get("y"), "z": pose.get("z")} if pose else None,
            "planned_start": item.get("start"), "execution_state": (execution or {}).get("state"), "route_reached": (execution or {}).get("route_reached"), "route_total": (execution or {}).get("route_total"),
            "verified": reason is None, "not_verified_reason": reason,
            "telemetry_age_s": (vehicle or {}).get("telemetry_age_seconds"),
        })
    verified = [row["aircraft_id"] for row in rows if row["verified"]]
    with_mission = [row["aircraft_id"] for row in rows]
    return {"aircraft": rows, "counts": {"with_mission": len(with_mission), "verified": len(verified), "not_verified": [{"aircraft_id": row["aircraft_id"], "reason": row["not_verified_reason"]} for row in rows if not row["verified"]]},
            "simulation_only": True}


@router.get("/api/mission-control/aircraft-summary")
def aircraft_summary(deployment_id: str = Query(min_length=1, max_length=64), context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    from .execution_routes import _load_deployment
    from .main import gazebo_pose_store, px4_telemetry_store
    from .persistence.database import deployment_mission_executions

    from .research_routes import ROLE_OVERRIDES

    deployment, _scenario, plan = _load_deployment(context, deployment_id)
    overrides = ROLE_OVERRIDES.get(deployment["id"], {})       # roles changed during the flight (reassign, switch primary) win over the plan's
    plan_aircraft = []
    for item in (plan.aircraft if plan is not None else []):
        start = item.start_location.model_dump() if getattr(item, "start_location", None) else None
        plan_aircraft.append({"uav_id": item.uav_id, "role": overrides.get(item.uav_id, item.role), "plan_role": item.role, "start": start})
    vehicles = px4_telemetry_store.snapshot(include_trails=False)["vehicles"]
    models = gazebo_pose_store.snapshot(include_trails=False)["models"]
    executions = {row["aircraft_id"]: {"state": row["state"], "route_reached": (row.get("progress") or {}).get("route_reached"), "route_total": (row.get("progress") or {}).get("route_total")}
                  for row in deployment_mission_executions(deployment["id"])}
    # An aircraft is "in the simulation" when PX4 or Gazebo has heard of it; the count of started instances is not known here, so rely on what arrived.
    summary = build_summary(plan_aircraft, vehicles, models, executions, running_count=0)
    summary["deployment_id"] = deployment["id"]
    summary["safe_maximum"] = config.SIM_MAX_AIRCRAFT
    return summary


@router.get("/api/fleet/capacity")
def fleet_capacity(count: int = Query(ge=1, le=64), context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    from .main import px4_telemetry_store

    running = sum(1 for vehicle in px4_telemetry_store.snapshot(include_trails=False)["vehicles"] if vehicle.get("status") in {"connected", "stale"})
    return assess(count, running=running)
