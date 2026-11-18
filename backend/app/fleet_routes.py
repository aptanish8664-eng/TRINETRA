from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from .auth_routes import require_csrf, require_permission
from .authorization import Permission, has_permission
from .mission_planning.schemas import MissionPlan
from .mission_planning.start import (
    FAR_FROM_AREA_M, HOME_TOLERANCE_M, RELOCATION_NOTICE_M, distance_between_m, mission_start,
)
from .mission_planning.validation import start_to_area_distance_m
from .persistence.database import add_audit_event, get_scenario
from .security import AuthContext
from .telemetry.fleet_manager import FLEET_CAPACITY, invoke_fleet_manager
from .mission_planning.quick import capacity_message, capacity_options

router = APIRouter()

# How long the verification (PX4 telemetry + Gazebo pose of every aircraft) waits WITHOUT any further aircraft coming up. It is a stall window, not a fixed
# overall timer: while aircraft keep coming up the wait goes on (the whole restart is capped by restart.start_budget_s).
RELOCATION_VERIFY_TIMEOUT_S = 120.0


class FleetActivationRequest(BaseModel):
    required_uav_count: int = Field(ge=1)       # the capacity rule (capacity_exceeded, with the plain sentence) is checked in the route
    # Optional saved version whose mission start becomes the home of any instance this request has to START.
    # A running instance is never restarted here; use POST /api/fleet/relocate for that.
    scenario_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    scenario_version_id: Optional[str] = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def version_pair(self) -> "FleetActivationRequest":
        if (self.scenario_id is None) != (self.scenario_version_id is None):
            raise ValueError("scenario_id and scenario_version_id must be given together")
        return self


class FleetRelocationRequest(BaseModel):
    scenario_id: str = Field(min_length=1, max_length=64)
    scenario_version_id: str = Field(min_length=1, max_length=64)
    confirm: bool = False


class FleetStopRequest(BaseModel):
    instance_numbers: list[int] = Field(min_length=1, max_length=FLEET_CAPACITY)

    def unique_instances(self) -> list[int]:
        if len(set(self.instance_numbers)) != len(self.instance_numbers) or any(
            item < 0 or item >= FLEET_CAPACITY for item in self.instance_numbers
        ):
            raise ValueError("instance_numbers must be unique IDs within configured capacity")
        return self.instance_numbers


def _fleet_snapshot() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from .main import gazebo_pose_store, px4_telemetry_store

    vehicles = px4_telemetry_store.snapshot(include_trails=False)["vehicles"]
    models = gazebo_pose_store.snapshot()["models"]
    return vehicles, models


def _verified_fleet(count: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    vehicles, models = _fleet_snapshot()
    by_system = {int(vehicle["system_id"]): vehicle for vehicle in vehicles}
    by_model = {model["model_name"]: model for model in models}
    verified = []
    states = []
    for instance in range(count):
        system_id = instance + 1
        model_name = f"x500_{instance}"
        vehicle = by_system.get(system_id)
        model = by_model.get(model_name)
        px4_ok = bool(vehicle and vehicle.get("status") == "connected"
                      and vehicle.get("position") and vehicle["position"].get("valid") is True)
        gazebo_ok = bool(model and model.get("status") == "connected")
        state = {"aircraft_id": f"px4-{system_id}", "instance_number": instance,
                 "system_id": system_id, "gazebo_model": model_name,
                 "px4_status": vehicle.get("status") if vehicle else "not_seen",
                 "gazebo_status": model.get("status") if model else "not_seen",
                 "verified": px4_ok and gazebo_ok}
        states.append(state)
        if state["verified"]:
            verified.append(state)
    return verified, states


def _check(status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "detail": detail, **extra}


def _version_plan(context: AuthContext, scenario_id: str, version_id: str) -> tuple[dict[str, Any], dict[str, Any], MissionPlan]:
    """The schema-2 plan of one saved version the caller may read, or a structured error."""
    from .schemas import ScenarioIntent

    scenario = get_scenario(scenario_id, context.user.id,
                            include_all=has_permission(context.user.role, Permission.MISSION_READ_ALL))
    version = next((item for item in (scenario or {}).get("versions", []) if item["id"] == version_id), None)
    if scenario is None or version is None:
        raise HTTPException(status_code=404, detail={"code": "version_not_found", "message": "The scenario version does not exist."})
    try:
        plan = ScenarioIntent.model_validate(version.get("intent") or {}).mission_plan
    except Exception as exc:
        raise HTTPException(status_code=422, detail={"code": "mission_plan_invalid", "message": "The stored mission plan is invalid."}) from exc
    if plan is None:
        raise HTTPException(status_code=422, detail={
            "code": "version_not_schema_2",
            "message": "This is a legacy version without a route plan, so it has no mission start. Plan it again in Scenario Lab."})
    return scenario, version, plan


def simulation_location(plan: MissionPlan, vehicles: list[dict[str, Any]]) -> dict[str, Any]:
    """Where the plan's aircraft are relative to the mission start and area. Reads telemetry only; decides nothing."""
    start = mission_start(plan)
    planned = set(plan.selected_uav_ids) or {item.uav_id for item in plan.aircraft}
    rows = []
    for vehicle in sorted(vehicles, key=lambda item: int(item.get("system_id", 0))):
        if planned and vehicle.get("id") not in planned:
            continue
        position = vehicle.get("position") or {}
        usable = vehicle.get("status") == "connected" and position.get("valid") is True
        point = {"latitude": position.get("latitude"), "longitude": position.get("longitude")} if usable else None
        rows.append({
            "aircraft_id": vehicle.get("id"), "status": vehicle.get("status"),
            "latitude": point["latitude"] if point else None, "longitude": point["longitude"] if point else None,
            "distance_to_start_m": round(distance_between_m(point, start), 1) if point and start else None,
            "distance_to_area_m": round(_distance_to_area_m(plan.operating_area, point), 1)
            if point and plan.operating_area is not None else None,
        })
    to_start = [row["distance_to_start_m"] for row in rows if row["distance_to_start_m"] is not None]
    to_area = [row["distance_to_area_m"] for row in rows if row["distance_to_area_m"] is not None]
    worst_start = max(to_start) if to_start else None
    worst_area = max(to_area) if to_area else None
    recommended = worst_start is not None and worst_start > RELOCATION_NOTICE_M
    far = worst_area is not None and worst_area > FAR_FROM_AREA_M
    if start is None:
        message = "This plan has no take-off point yet, so the simulation location cannot be compared."
    elif worst_start is None:
        message = "No aircraft of this plan reports a valid position, so its distance from the mission start is unknown."
    elif far:
        message = (f"The aircraft is about {worst_area / 1000:.0f} km from the mission area. The simulation is running at a "
                   "different location than this mission. Use Restart at mission start.")
    elif recommended:
        message = (f"The aircraft is {worst_start / 1000:.1f} km from the mission start. "
                   "Use Restart at mission start to restart the simulation there.")
    else:
        message = f"The aircraft is {worst_start:.0f} m from the mission start."
    return {"mission_start": start, "aircraft": rows, "max_distance_to_start_m": worst_start,
            "max_distance_to_area_m": worst_area, "position_known": worst_start is not None,
            "relocation_recommended": recommended, "far_from_mission_area": far, "message": message,
            "thresholds": {"relocation_notice_m": RELOCATION_NOTICE_M, "far_from_area_m": FAR_FROM_AREA_M,
                           "home_tolerance_m": HOME_TOLERANCE_M}}


def _geo(point: dict[str, Any]):
    from .mission_planning.schemas import GeoPoint

    return GeoPoint(latitude=point["latitude"], longitude=point["longitude"])


def _distance_to_area_m(area, point: dict[str, Any]) -> float:
    """Distance from a position to the operating area, correct at any range.

    The planner's own start-to-area distance works on the local tangent plane, which is exact near the area but
    understates distances of thousands of kilometres. Near the area it is used as is; far away the great-circle
    distance to the circle (or to the nearest polygon vertex) is used instead.
    """
    if area.kind == "circle" and area.centre is not None and area.radius_m is not None:
        reference = [{"latitude": area.centre.latitude, "longitude": area.centre.longitude}]
        margin = area.radius_m
    else:
        reference = [{"latitude": vertex.latitude, "longitude": vertex.longitude} for vertex in area.vertices]
        margin = 0.0
    great_circle = min(distance_between_m(point, item) for item in reference) if reference else 0.0
    if great_circle < 20_000:
        return start_to_area_distance_m(area, _geo(point))
    return max(0.0, great_circle - margin)


def build_activation_checks(requested: int, process_result: dict[str, Any], rows: list[dict[str, Any]], *,
                            receivers: dict[str, bool], configured_ips: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Per-dependency statuses for one activation attempt; every value is a measured fact or 'unknown'."""
    wrapper_failure = bool(process_result.get("failure")) and not process_result.get("failure_code") \
        and "bridges" not in process_result
    bridges = process_result.get("bridges") or {}
    wsl_address = process_result.get("wsl_address")
    checks: dict[str, dict[str, Any]] = {}
    checks["wsl"] = _check("unavailable", str(process_result.get("failure")), ) if wrapper_failure else _check(
        "ok", "WSL fleet manager responded.", address=wsl_address, windows_host=process_result.get("windows_host"))
    gazebo_server = process_result.get("gazebo_server", "unknown")
    pose_seen = [row for row in rows if row["gazebo_status"] != "not_seen"]
    pose_live = [row for row in rows if row["gazebo_status"] == "connected"]
    # The manager samples the Gazebo process the moment it returns, which is before a PX4 instance it has just
    # started has launched Gazebo. A model pose arriving now is the later, direct measurement, so it wins.
    if gazebo_server == "running":
        checks["gazebo"] = _check("ok", "Gazebo server process is running.", models_seen=len(pose_seen))
    elif pose_live:
        checks["gazebo"] = _check("ok", "Gazebo is publishing model poses (it was still starting when the fleet manager reported).",
                                  models_seen=len(pose_seen))
    else:
        checks["gazebo"] = _check(gazebo_server, "Gazebo server process was not found and no model pose has arrived.",
                                  models_seen=len(pose_seen))
    present = [item for item in process_result.get("instances", []) if item.get("status") in {"process_present", "process_started"}]
    px4_ok = len(present) >= requested
    checks["px4"] = _check(
        "ok" if px4_ok else "failed" if process_result.get("failure_code") == "sitl_start_failed" else "missing",
        f"{len(present)} of {requested} PX4 SITL process(es) present"
        + (f" (adopted: {sum(i['status'] == 'process_present' for i in present)}, started: "
           f"{sum(i['status'] == 'process_started' for i in present)})." if present else "."))
    for key, name in (("telemetry", "telemetry_bridge"), ("pose", "pose_bridge")):
        item = bridges.get(key)
        if item is None:
            checks[name] = _check("unknown", "The manager did not report this bridge.")
        elif item.get("restarted"):   # a running bridge that sent to the wrong place was replaced: say what was wrong
            checks[name] = _check(item["status"], f"Bridge restarted by the manager because it was misconfigured: {item.get('replaced')}.")
        else:
            detail = item.get("detail") or {
                "running": "Bridge process already running (adopted).", "started": "Bridge process started by the manager.",
                "not_running": "Bridge process is not running."}.get(item["status"], item["status"])
            if item.get("mirror_ports"):
                detail += " It also mirrors to " + ", ".join(str(port) for port in item["mirror_ports"]) + " (another receiver)."
            checks[name] = _check(item["status"], detail)
    checks["receivers"] = _check(
        "ok" if all(receivers.values()) else "unavailable",
        "Both backend UDP receivers are bound." if all(receivers.values()) else "A backend UDP receiver is not bound.",
        px4=receivers["px4"], gazebo=receivers["gazebo"])
    mismatched = {name: ip for name, ip in configured_ips.items() if ip and wsl_address and ip != wsl_address}
    checks["source_ip"] = _check(
        "mismatch" if mismatched else "ok" if wsl_address else "unknown",
        (f"WSL address {wsl_address} differs from the configured allowed source IP for: "
         + ", ".join(f"{k} ({v})" for k, v in mismatched.items()) + ".") if mismatched
        else "WSL address matches the configured allowed source IP." if wsl_address else "WSL address was not reported.",
        wsl_address=wsl_address, configured=configured_ips)
    verified = sum(bool(row["verified"]) for row in rows)
    # A bridge that sends to the wrong port (or could not be restarted) is WHY nothing is verified: say so instead of "pending".
    bridge_causes = [checks[name]["detail"] for name in ("telemetry_bridge", "pose_bridge") if checks[name]["status"] in {"mismatch", "failed"}]
    blocked = verified < requested and bool(bridge_causes)
    checks["verification"] = _check(
        "ok" if verified >= requested else "blocked" if blocked else "pending",
        f"{verified} of {requested} UAV(s) have PX4 telemetry and a Gazebo pose." + (" Cause: " + " ".join(bridge_causes) if blocked else ""),
        verified=verified, required=requested,
        unverified=[{"aircraft_id": r["aircraft_id"], "px4": r["px4_status"], "gazebo": r["gazebo_status"]}
                    for r in rows if not r["verified"]])
    return checks


def classify_activation_failure(requested: int, process_result: dict[str, Any],
                                checks: dict[str, dict[str, Any]], *, final: bool) -> tuple[int, str, str] | None:
    """Return (HTTP status, machine code, message) for a known failure, else None. `final` adds the timeout case."""
    if checks["wsl"]["status"] == "unavailable":
        return 503, "wsl_manager_unavailable", f"The WSL fleet manager could not be reached: {checks['wsl']['detail']}"
    code = process_result.get("failure_code")
    if code == "insufficient_resources":
        return 503, "insufficient_resources", f"WSL does not have enough resources: {process_result.get('failure')}"
    if code == "sitl_start_failed":
        return 502, "sitl_start_failed", f"PX4 SITL did not start: {process_result.get('failure')}"
    if code == "bridge_start_failed":
        return 502, "bridge_start_failed", f"A WSL bridge could not be started: {process_result.get('failure')}"
    if code == "instance_not_managed":
        return 409, "instance_not_managed", str(process_result.get("failure"))
    if code == "stop_failed":
        return 502, "stop_failed", f"The simulation could not be stopped cleanly: {process_result.get('failure')}"
    if checks["source_ip"]["status"] == "mismatch":
        return 409, "source_ip_mismatch", (checks["source_ip"]["detail"]
            + " Update the allowed source IP in the backend configuration; source validation was not relaxed.")
    if process_result.get("failure"):
        return 502, "manager_failed", f"The WSL fleet manager reported: {process_result.get('failure')}"
    wrong = [checks[name]["detail"] for name in ("telemetry_bridge", "pose_bridge") if checks[name]["status"] == "mismatch"]
    if wrong:
        return 502, "bridge_port_mismatch", " ".join(wrong)
    if final and checks["verification"]["status"] != "ok":
        return 504, "verification_timeout", (
            f"PX4 and Gazebo are running, but only {checks['verification']['verified']} of {requested} UAV(s) "
            "produced both PX4 telemetry and a Gazebo pose before the timeout. See the per-check statuses.")
    return None


def _structured_failure(status: int, code: str, message: str, checks: dict[str, Any], **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, "checks": checks,
                                                     "verified": False, **extra})


def _audit(request: Request, context: AuthContext, action: str, metadata: dict[str, Any]) -> None:
    add_audit_event(action=action, outcome="success", reason="Authenticated software-only SITL fleet operation.",
                    request_id=getattr(request.state, "request_id", "request-unavailable"),
                    actor_id=context.user.id, actor_name=context.user.full_name,
                    target_type="px4_sitl_fleet", target_id="fleet", metadata=metadata)


@router.get("/api/fleet/status")
def fleet_status(
    context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)),
) -> dict[str, Any]:
    vehicles, models = _fleet_snapshot()
    processes = invoke_fleet_manager("status", timeout_seconds=12)
    process_instances = processes.get("instances", []) if not processes.get("failure") else []
    by_system = {int(item["system_id"]): item for item in process_instances}
    present_ids = set(by_system)
    verified, rows = _verified_fleet(FLEET_CAPACITY)
    for row in rows:
        row["process_status"] = "present" if row["system_id"] in present_ids else "not_seen"
        row["verified"] = row["verified"] and row["system_id"] in present_ids
        row["home"] = by_system.get(row["system_id"], {}).get("home")
        row["managed"] = by_system.get(row["system_id"], {}).get("managed")
        row["profile"] = by_system.get(row["system_id"], {}).get("profile")
    first = by_system.get(1)
    return {"capacity": FLEET_CAPACITY, "verified_count": sum(bool(row["verified"]) for row in rows),
            # Where the running simulation was started: an explicit home, or the stock PX4 world origin.
            "simulation_home": first.get("home") if first else None,
            "simulation_home_source": "unknown" if first is None else "px4_home_env" if first.get("home") else "px4_default_world",
            # The long-range SITL parameters the running instance was started with (None: the manager set none).
            "long_range_profile": first.get("profile") if first else None,
            "instances": rows, "px4_vehicle_records": len(vehicles), "gazebo_model_records": len(models),
            "manager_error": processes.get("failure"), "bridges": processes.get("bridges"),
            "gazebo_server": processes.get("gazebo_server"), "wsl_address": processes.get("wsl_address")}


@router.post("/api/fleet/activate")
def activate_fleet(
    payload: FleetActivationRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.SCENARIO_CREATE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    from . import main as app_main
    from .config import GAZEBO_POSE_UDP_ALLOWED_SOURCE_IP, PX4_UDP_ALLOWED_SOURCE_IP

    receivers = {"px4": app_main.px4_udp_transport is not None,
                 "gazebo": app_main.gazebo_pose_udp_transport is not None}
    configured_ips = {"px4_telemetry": PX4_UDP_ALLOWED_SOURCE_IP, "gazebo_pose": GAZEBO_POSE_UDP_ALLOWED_SOURCE_IP}
    requested = payload.required_uav_count
    if requested > FLEET_CAPACITY:
        message = capacity_message(requested, FLEET_CAPACITY)
        raise _structured_failure(409, "capacity_exceeded", message,
                                  {"capacity": _check("blocked", message, requested=requested, capacity=FLEET_CAPACITY)},
                                  required_uav_count=requested, capacity=FLEET_CAPACITY, options=capacity_options(requested, FLEET_CAPACITY))
    if not all(receivers.values()):
        raise _structured_failure(
            503, "receivers_unavailable",
            "The PX4 telemetry and Gazebo pose UDP receivers must both be enabled and bound before fleet activation.",
            {"receivers": _check("unavailable", "A backend UDP receiver is not bound.",
                                 px4=receivers["px4"], gazebo=receivers["gazebo"])},
            required_uav_count=requested)
    correlation_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    home = None
    if payload.scenario_id and payload.scenario_version_id:
        home = mission_start(_version_plan(context, payload.scenario_id, payload.scenario_version_id)[2])
    staged = requested >= 2
    if staged:
        # Several aircraft: the manager's staged start (Gazebo server first, PX4 in small batches, each waited for), followed by its progress. Running instances are adopted.
        import sys
        from .mission_execution.restart import follow_staged_start, legacy_process_result

        followed = follow_staged_start(sys.modules[__name__], requested, home, correlation_id, instances=None, stop_first=False, step=lambda *args: None, on_aircraft=None)
        process_result = legacy_process_result(followed["state"], followed["answer"], requested)
        if (followed["state"].get("result") or {}).get("partial"):
            process_result["failure"], process_result["failure_code"] = None, None          # the verification below reports which aircraft are missing
    else:
        process_result = invoke_fleet_manager("activate", count=requested, request_id=correlation_id, timeout_seconds=35,
                                              **({"home": home} if home else {}))
    verified, rows = _verified_fleet(requested)
    checks = build_activation_checks(requested, process_result, rows, receivers=receivers, configured_ips=configured_ips)
    failure = classify_activation_failure(requested, process_result, checks, final=False)
    window = float(RELOCATION_VERIFY_TIMEOUT_S) if staged else 45.0
    last_change, best = time.monotonic(), len(verified)
    while failure is None and len(verified) < requested and time.monotonic() - last_change < window:
        time.sleep(0.5)
        verified, rows = _verified_fleet(requested)
        if len(verified) > best:
            best, last_change = len(verified), time.monotonic()           # one more aircraft came up: keep waiting
    if failure is None:
        checks = build_activation_checks(requested, process_result, rows, receivers=receivers, configured_ips=configured_ips)
        failure = classify_activation_failure(requested, process_result, checks, final=True)
    if process_result.get("profile_mismatch"):
        checks["long_range_profile"] = _check(
            "differs", "A running instance was started with different long-range parameters and was not restarted. "
            "Use Restart at mission start to restart it with the current profile.", aircraft=process_result["profile_mismatch"])
    if home is not None:
        mismatch = process_result.get("home_mismatch") or []
        checks["simulation_home"] = _check(
            "differs" if mismatch else "ok",
            ("The running simulation was started at a different home and was not restarted. "
             "Use Restart at mission start to restart it there.") if mismatch
            else "Instances started by this request use the mission start as their home.",
            requested=home, aircraft=mismatch)
    body = {"required_uav_count": requested, "activated_uav_count": len(verified),
            "assigned_uav_count": 0, "vehicles": verified, "instances": rows,
            "request_id": correlation_id, "checks": checks,
            "manager_error": process_result.get("failure"),
            "verified": len(verified) == requested and failure is None}
    if failure is not None:
        status_code, code, message = failure
        raise _structured_failure(status_code, code, message, checks, request_id=correlation_id,
                                  required_uav_count=requested, activated_uav_count=len(verified),
                                  instances=rows, manager_error=process_result.get("failure"))
    _audit(request, context, "px4_sitl_fleet_activated", {
        "request_id": body["request_id"], "required_uav_count": requested,
        "activated_uav_count": len(verified),
        "system_ids": [item["system_id"] for item in verified],
    })
    return body


@router.post("/api/fleet/stop-managed")
def stop_managed_fleet(
    payload: FleetStopRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.SCENARIO_CREATE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        instances = payload.unique_instances()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    correlation_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    result = invoke_fleet_manager("stop", instances=instances, request_id=correlation_id, timeout_seconds=15)
    _audit(request, context, "px4_sitl_managed_fleet_stopped", {
        "request_id": getattr(request.state, "request_id", "request-unavailable"),
        "requested_instances": instances,
        "stopped_instances": result.get("stopped_instances", []),
    })
    return result


@router.get("/api/fleet/simulation-location")
def read_simulation_location(
    scenario_id: str = Query(min_length=1, max_length=64),
    scenario_version_id: str = Query(min_length=1, max_length=64),
    context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)),
) -> dict[str, Any]:
    """Distance of the plan's aircraft from the mission start and area, from current telemetry. Read-only."""
    _scenario, _version, plan = _version_plan(context, scenario_id, scenario_version_id)
    vehicles, _models = _fleet_snapshot()
    return {"scenario_id": scenario_id, "scenario_version_id": scenario_version_id, **simulation_location(plan, vehicles)}


def _home_check(plan: MissionPlan, home: dict[str, Any]) -> dict[str, Any]:
    location = simulation_location(plan, _fleet_snapshot()[0])
    expected = set(plan.selected_uav_ids) or {item.uav_id for item in plan.aircraft}
    known = {row["aircraft_id"]: row["distance_to_start_m"] for row in location["aircraft"] if row["distance_to_start_m"] is not None}
    missing = sorted(expected - set(known))
    worst = max(known.values()) if known else None
    ok = not missing and worst is not None and worst <= HOME_TOLERANCE_M
    if ok:
        detail = f"Every aircraft reports a position within {HOME_TOLERANCE_M:g} m of the mission start (largest {worst:.1f} m)."
    elif worst is not None and worst > HOME_TOLERANCE_M:
        detail = f"An aircraft reports a position {worst:.0f} m from the mission start; the limit is {HOME_TOLERANCE_M:g} m."
    else:
        detail = f"No valid position yet from: {', '.join(missing) or 'the aircraft'}."
    return _check("ok" if ok else "pending" if worst is None or missing else "failed", detail, requested=home,
                  max_distance_m=worst, aircraft=location["aircraft"])


@router.post("/api/fleet/relocate")
def relocate_simulation(
    payload: FleetRelocationRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.SCENARIO_CREATE)),
    _csrf: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Restart the managed PX4/Gazebo simulation at the saved version's mission start. Sends no flight command."""
    from . import main as app_main
    from .config import GAZEBO_POSE_UDP_ALLOWED_SOURCE_IP, PX4_UDP_ALLOWED_SOURCE_IP

    if not payload.confirm:
        raise HTTPException(status_code=422, detail={
            "code": "confirmation_required",
            "message": "Confirm the restart first: it stops and restarts the PX4 and Gazebo simulation."})
    _scenario, _version, plan = _version_plan(context, payload.scenario_id, payload.scenario_version_id)
    home = mission_start(plan)
    if home is None:
        raise HTTPException(status_code=422, detail={
            "code": "mission_start_unavailable",
            "message": "This plan has no take-off point yet. Pick the primary aircraft's start in Scenario Lab and save."})
    receivers = {"px4": app_main.px4_udp_transport is not None,
                 "gazebo": app_main.gazebo_pose_udp_transport is not None}
    configured_ips = {"px4_telemetry": PX4_UDP_ALLOWED_SOURCE_IP, "gazebo_pose": GAZEBO_POSE_UDP_ALLOWED_SOURCE_IP}
    requested = plan.required_uav_count or len(plan.aircraft) or 1
    if not all(receivers.values()):
        raise _structured_failure(
            503, "receivers_unavailable",
            "The PX4 telemetry and Gazebo pose UDP receivers must both be enabled and bound before the simulation is moved.",
            {"receivers": _check("unavailable", "A backend UDP receiver is not bound.",
                                 px4=receivers["px4"], gazebo=receivers["gazebo"])},
            required_uav_count=requested, relocated=False, simulation_stopped=False)
    from .mission_execution.restart import restart_simulation

    # A restart is available in every aircraft state: it ends an active run (reason "simulation restarted"), stops the executor and restarts PX4 and Gazebo.
    correlation_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    result = restart_simulation(plan, home, requested, correlation_id=correlation_id, purpose="restart", context=context)
    if not result["ok"]:
        status_code, code, message = result["failure"]
        if result.get("partial"):
            status_code = 502                                   # this older endpoint has no "partial" answer: some aircraft came up, the rest did not
        stopped = bool(result.get("simulation_stopped"))
        raise _structured_failure(status_code, code, message, result.get("checks", {}), request_id=correlation_id, required_uav_count=requested,
                                  activated_uav_count=0, instances=result.get("instances", []), manager_error=message, relocated=False, mission_start=home,
                                  simulation_stopped=stopped, failed_step=result.get("failed_step"),
                                  recovery="Press Restart at mission start again, or Activate and verify, to start the simulation." if stopped else "Nothing was stopped. Fix the reported check and retry.")
    verified, rows, checks, process_result = result["vehicles"], result["instances"], result["checks"], {"stopped_instances": result["stopped_instances"]}
    _audit(request, context, "px4_sitl_simulation_relocated", {
        "request_id": correlation_id, "scenario_id": payload.scenario_id, "scenario_version_id": payload.scenario_version_id,
        "home": {key: home[key] for key in ("latitude", "longitude", "altitude_m")},
        "stopped_instances": process_result.get("stopped_instances", []), "system_ids": [item["system_id"] for item in verified],
        "ended_executions": result["ended_executions"],
    })
    return {"relocated": True, "verified": True, "mission_start": home, "required_uav_count": requested,
            "activated_uav_count": len(verified), "vehicles": verified, "instances": rows, "checks": checks,
            "max_distance_to_start_m": checks["simulation_home"].get("max_distance_m"), "request_id": correlation_id,
            "commands_sent_to_aircraft": False,
            "note": "The PX4 and Gazebo simulation was restarted at the mission start. No arm, take-off or mission command was sent."}
