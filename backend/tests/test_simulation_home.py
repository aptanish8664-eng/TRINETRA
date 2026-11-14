"""Simulation home: one mission-start definition, validated numbers only, and an explicit, checked relocation.

No WSL, PX4 or Gazebo process is started here: the manager is exercised against fake /proc trees and the HTTP
routes against a mocked manager and telemetry snapshot. The live check is recorded in docs/OVERNIGHT_RUN_LOG.md.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.mission_planning.start import (
    DEFAULT_HOME_ALTITUDE_M, FAR_FROM_AREA_M, HOME_TOLERANCE_M, RELOCATION_NOTICE_M, distance_between_m, mission_start,
    takeoff_point, validate_home,
)
from app.mission_planning.validation import validate_plan
from app.telemetry.fleet_manager import build_fleet_manager_command
from test_mission_planning import LIMITS, make_plan, offset
from test_mission_workflow_e2e import body as plan_body, complete_plan, flight  # noqa: F401  (flight is a fixture)

_spec = importlib.util.spec_from_file_location(
    "wsl_fleet_manager_home", Path(__file__).parents[1] / "tools" / "wsl_fleet_manager.py")
assert _spec and _spec.loader
_manager = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_manager)

ZURICH = {"latitude": 47.397971, "longitude": 8.546164}


# --- the single start definition --------------------------------------------------------------------------------

def test_mission_start_is_the_primary_aircrafts_takeoff_point():
    plan = complete_plan(2)
    standby_first = plan.model_copy(update={"aircraft": [
        plan.aircraft[1].model_copy(update={"start_location": offset(500, 500), "route": None, "confirmed": False}), plan.aircraft[0]]})
    start = mission_start(standby_first)
    assert start["uav_id"] == "px4-1" and start["role"] == "primary"  # the primary, not the first row
    primary = plan.aircraft[0]
    assert (start["latitude"], start["longitude"]) == (primary.start_location.latitude, primary.start_location.longitude)
    first_waypoint = primary.route.waypoints[0]
    assert first_waypoint.action == "takeoff"
    assert distance_between_m(start, {"latitude": first_waypoint.latitude, "longitude": first_waypoint.longitude}) < 0.01
    assert start["altitude_m"] == DEFAULT_HOME_ALTITUDE_M
    assert takeoff_point(primary.model_copy(update={"start_location": None})).latitude == first_waypoint.latitude
    assert mission_start(None) is None
    assert mission_start(plan.model_copy(update={"aircraft": [], "selected_uav_ids": [], "activated_uav_ids": []})) is None


def test_max_distance_validation_measures_from_the_same_takeoff_point():
    plan = make_plan(plan_extra={"constraints": {"max_distance_from_start_m": 250, "max_altitude_m": 100}})
    issues = validate_plan(plan, LIMITS, complete=False)
    assert issues == []  # nothing generated yet: drafts are not checked
    confirmed = plan.model_copy(update={"aircraft": [plan.aircraft[0].model_copy(update={"confirmed": True})]})
    found = [item for item in validate_plan(confirmed, LIMITS, complete=False) if item.code == "route_too_far"]
    assert found and "283 m from the start" in found[0].message  # the farthest waypoint is 200 m north, 200 m east
    start = takeoff_point(plan.aircraft[0])
    assert round(distance_between_m(start, offset(200, 200))) == 283


def test_home_numbers_are_range_checked_before_they_reach_a_command():
    assert validate_home(12.9716, 77.5946, 488) == (12.9716, 77.5946, 488.0)
    for bad in ((91, 0, 0), (0, 181, 0), (0, 0, 99999), (float("nan"), 0, 0), ("12", 77, 0), (True, 0, 0), (None, 0, 0)):
        with pytest.raises(ValueError):
            validate_home(*bad)
    command = build_fleet_manager_command("activate", count=1, home={"latitude": -33.5, "longitude": 151.25, "altitude_m": 10})
    assert command[-3:] == ["--home-lat=-33.5000000", "--home-lon=151.2500000", "--home-alt=10.00"]
    relocate = build_fleet_manager_command("relocate", count=2, home={"latitude": 12.9716, "longitude": 77.5946, "altitude_m": 488})
    assert relocate[6] == "relocate" and relocate[-3] == "--home-lat=12.9716000"
    with pytest.raises(ValueError, match="requires a simulation home"):
        build_fleet_manager_command("relocate", count=1)
    with pytest.raises(ValueError, match="out of range"):
        build_fleet_manager_command("activate", count=1, home={"latitude": 120, "longitude": 0, "altitude_m": 0})
    with pytest.raises(ValueError):
        build_fleet_manager_command("activate", count=1, home={"latitude": "12; rm -rf /", "longitude": 0, "altitude_m": 0})
    with pytest.raises(ValueError, match="only accepted"):
        build_fleet_manager_command("status", home={"latitude": 1, "longitude": 1, "altitude_m": 1})


# --- WSL manager (fake /proc) -----------------------------------------------------------------------------------

def _fake_proc(root, pid, *args, env: dict | None = None):
    (root / str(pid)).mkdir(parents=True)
    (root / str(pid) / "cmdline").write_bytes(b"\0".join(item.encode() for item in args) + b"\0")
    (root / str(pid) / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in (env or {}).items()) + b"\0")


def test_manager_builds_the_px4_home_environment_from_validated_numbers(tmp_path):
    home = _manager.validate_home(12.9716, 77.5946, 488)
    assert _manager.home_environment(home) == {"PX4_HOME_LAT": "12.9716000", "PX4_HOME_LON": "77.5946000", "PX4_HOME_ALT": "488.00"}
    with pytest.raises(ValueError):
        _manager.validate_home(200, 0, 0)
    _fake_proc(tmp_path, 10, "px4", env={"PX4_HOME_LAT": "12.9716000", "PX4_HOME_LON": "77.5946000", "PX4_HOME_ALT": "488.00"})
    _fake_proc(tmp_path, 11, "px4", env={"PX4_SIM_MODEL": "gz_x500"})
    assert _manager.instance_home(10, tmp_path) == home
    assert _manager.instance_home(11, tmp_path) is None  # stock world origin
    assert _manager.same_home(home, dict(home)) and not _manager.same_home(home, None) and _manager.same_home(None, None)


def test_spawn_sets_only_the_three_home_variables_and_never_inherits_them(tmp_path, monkeypatch):
    build = tmp_path / "build/px4_sitl_default"
    (build / "bin").mkdir(parents=True)
    (build / "bin/px4").write_text("")
    (build / "etc").mkdir()
    captured = {}

    def popen(command, **kwargs):
        captured.update(command=command, env=kwargs["env"])
        return object()

    monkeypatch.setattr(_manager.subprocess, "Popen", popen)
    monkeypatch.setenv("PX4_HOME_LAT", "1.0")  # a stray shell value must not leak into the simulation
    _manager._spawn(tmp_path, 0)
    assert not any(name in captured["env"] for name in _manager.HOME_ENV)
    _manager._spawn(tmp_path, 1, _manager.validate_home(12.9716, 77.5946, 488))
    assert {name: captured["env"][name] for name in _manager.HOME_ENV} == {
        "PX4_HOME_LAT": "12.9716000", "PX4_HOME_LON": "77.5946000", "PX4_HOME_ALT": "488.00"}
    assert captured["command"][1:3] == ["-i", "1"] and captured["env"]["PX4_GZ_WORLD"] == "default"


def test_gazebo_processes_of_this_checkout_are_found_and_others_ignored(tmp_path):
    root = tmp_path / "px4"
    proc = tmp_path / "proc"
    worlds = (root / "Tools/simulation/gz/worlds").resolve()
    _fake_proc(proc, 500, f"gz sim --verbose=1 -r -s {worlds}/default.sdf")
    _fake_proc(proc, 501, "gz sim -g")
    _fake_proc(proc, 502, "gz", "sim", "-r", "-s", "/somewhere/else/world.sdf")
    _fake_proc(proc, 503, "python3", "gz_helper.py")
    assert _manager.discover_gazebo_processes(root, proc) == {"server": [500], "gui": [501]}


def _relocation_world(monkeypatch, tmp_path, *, existing, owned):
    calls = {"terminated": [], "gazebo": 0, "activate": None}
    state = {"instances": dict(existing)}
    monkeypatch.setattr(_manager, "discover_px4_instances", lambda root, proc_root=None: dict(state["instances"]))
    monkeypatch.setattr(_manager, "_state_path", lambda: tmp_path / "state.json")
    _manager._save_owned(tmp_path / "state.json", owned)

    def terminate(pid, still_running, **_):
        calls["terminated"].append(pid)
        state["instances"] = {k: v for k, v in state["instances"].items() if v != pid}
        return True

    monkeypatch.setattr(_manager, "_terminate", terminate)
    monkeypatch.setattr(_manager, "stop_gazebo", lambda root, proc_root=None: calls.__setitem__("gazebo", calls["gazebo"] + 1) or {"stopped": [900, 901], "failed": []})
    monkeypatch.setattr(_manager, "inspect_bridges", lambda proc_root=None: {"telemetry": {"status": "running"}, "pose": {"status": "running"}})
    monkeypatch.setattr(_manager, "_environment_report", lambda proc_root=None: {"wsl_address": "172.23.85.230", "windows_host": "172.23.80.1", "gazebo_server": "running"})
    monkeypatch.setattr(_manager.time, "sleep", lambda _s: None)

    def activate(count, **kwargs):
        calls["activate"] = (count, kwargs.get("home"))
        return {"requested_count": count, "instances": [{"instance": 0, "status": "process_started", "home": kwargs.get("home")}],
                "failure": None, "failure_code": None, "home_requested": kwargs.get("home"), "home_mismatch": []}

    monkeypatch.setattr(_manager, "activate_fleet", activate)
    return calls


def test_relocate_stops_managed_instances_and_gazebo_then_starts_at_the_home(tmp_path, monkeypatch):
    calls = _relocation_world(monkeypatch, tmp_path, existing={0: 4242}, owned={0: 4242})
    home = {"latitude": 12.9716, "longitude": 77.5946, "altitude_m": 488.0}
    result = _manager.relocate_fleet(1, home, px4_root=tmp_path)
    assert calls["terminated"] == [4242] and calls["gazebo"] == 1 and calls["activate"] == (1, home)
    assert result["relocated"] is True and result["stopped_instances"] == [0] and result["gazebo_stopped"] == [900, 901]
    assert _manager._load_owned(tmp_path / "state.json") == {}  # the stopped pid is no longer claimed


def test_relocate_refuses_an_instance_it_did_not_start_and_touches_nothing(tmp_path, monkeypatch):
    calls = _relocation_world(monkeypatch, tmp_path, existing={0: 4242}, owned={})
    result = _manager.relocate_fleet(1, {"latitude": 12.9716, "longitude": 77.5946, "altitude_m": 488.0}, px4_root=tmp_path)
    assert result["failure_code"] == "instance_not_managed" and result["relocated"] is False
    assert "were not started by the TRINETRA fleet manager" in result["failure"]
    assert calls["terminated"] == [] and calls["gazebo"] == 0 and calls["activate"] is None


def test_activation_never_restarts_a_running_instance_for_a_different_home(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    _fake_proc(proc, 4242, "px4", env={"PX4_SIM_MODEL": "gz_x500"})
    monkeypatch.setattr(_manager, "discover_px4_instances", lambda root, proc_root=None: {0: 4242})
    monkeypatch.setattr(_manager, "_available_memory_kib", lambda: 8 * 1024 * 1024)
    monkeypatch.setattr(_manager, "_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(_manager, "_spawn", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not restart")))
    monkeypatch.setattr(_manager, "ensure_bridges", lambda existing, ports, proc_root=None: {"telemetry": {"status": "running"}, "pose": {"status": "running"}})
    monkeypatch.setattr(_manager, "_environment_report", lambda proc_root=None: {"wsl_address": "x", "windows_host": "y", "gazebo_server": "running"})
    result = _manager.activate_fleet(1, px4_root=tmp_path, proc_root=proc, home={"latitude": 12.9716, "longitude": 77.5946, "altitude_m": 488.0})
    assert result["failure"] is None and result["home_mismatch"] == ["px4-1"] and result["instances"][0]["home"] is None


# --- HTTP routes (mocked manager and telemetry) -------------------------------------------------------------------

def _vehicle(latitude, longitude, *, armed=False, status="connected", system_id=1):
    return {"id": f"px4-{system_id}", "system_id": system_id, "status": status, "armed": armed,
            "position": {"latitude": latitude, "longitude": longitude, "valid": True}}


def _saved(client, count=1):
    response = client.post("/api/scenarios", json=plan_body(complete_plan(count), f"Home plan {count}"))
    assert response.status_code == 201, response.text
    return response.json()["id"], response.json()["versions"][0]["id"]


def _receivers(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "px4_udp_transport", object())
    monkeypatch.setattr(main, "gazebo_pose_udp_transport", object())


def test_simulation_location_reports_distance_and_the_two_notices(general_client, flight, monkeypatch):
    client, _ = general_client
    scenario_id, version_id = _saved(client)
    start = offset()
    query = f"/api/fleet/simulation-location?scenario_id={scenario_id}&scenario_version_id={version_id}"
    monkeypatch.setattr("app.fleet_routes._fleet_snapshot", lambda: ([_vehicle(**ZURICH)], []))
    far = client.get(query).json()
    assert far["mission_start"]["uav_id"] == "px4-1" and abs(far["mission_start"]["latitude"] - start["latitude"]) < 1e-9
    assert far["relocation_recommended"] is True and far["far_from_mission_area"] is True
    assert 7_300_000 < far["max_distance_to_start_m"] < 7_450_000  # Zurich to Bengaluru, great circle (about 7,378 km)
    # The area distance is the same great circle less the 400 m radius, not the shorter tangent-plane figure.
    assert abs(far["max_distance_to_start_m"] - far["max_distance_to_area_m"]) < 1_000
    assert "about 7378 km from the mission area" in far["message"] or "about 7377 km from the mission area" in far["message"]
    assert "different location" in far["message"] and "Move simulation to mission start" in far["message"]
    assert far["thresholds"] == {"relocation_notice_m": RELOCATION_NOTICE_M, "far_from_area_m": FAR_FROM_AREA_M, "home_tolerance_m": HOME_TOLERANCE_M}
    monkeypatch.setattr("app.fleet_routes._fleet_snapshot", lambda: ([_vehicle(**offset(3000, 0))], []))
    near_area = client.get(query).json()
    assert near_area["relocation_recommended"] is True and near_area["far_from_mission_area"] is False
    assert "3.0 km from the mission start" in near_area["message"]
    monkeypatch.setattr("app.fleet_routes._fleet_snapshot", lambda: ([_vehicle(**offset(5, 5))], []))
    there = client.get(query).json()
    assert there["relocation_recommended"] is False and there["max_distance_to_start_m"] < 10
    monkeypatch.setattr("app.fleet_routes._fleet_snapshot", lambda: ([_vehicle(**ZURICH, status="stale")], []))
    unknown = client.get(query).json()
    assert unknown["position_known"] is False and unknown["relocation_recommended"] is False and "unknown" in unknown["message"]
    assert client.get("/api/fleet/simulation-location?scenario_id=nope&scenario_version_id=nope").status_code == 404


def test_relocate_needs_confirmation_role_csrf_and_disarmed_aircraft(general_client, flight, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    client, _ = general_client
    scenario_id, version_id = _saved(client)
    body = {"scenario_id": scenario_id, "scenario_version_id": version_id}
    called = []
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", lambda *a, **k: called.append((a, k)) or {})
    assert TestClient(app).post("/api/fleet/relocate", json={**body, "confirm": True}).status_code == 401
    unconfirmed = client.post("/api/fleet/relocate", json=body)
    assert unconfirmed.status_code == 422 and unconfirmed.json()["detail"]["code"] == "confirmation_required"
    no_receivers = client.post("/api/fleet/relocate", json={**body, "confirm": True})
    assert no_receivers.status_code == 503 and no_receivers.json()["detail"]["code"] == "receivers_unavailable"
    _receivers(monkeypatch)
    monkeypatch.setattr("app.fleet_routes._fleet_snapshot", lambda: ([_vehicle(**ZURICH, armed=True)], []))
    armed = client.post("/api/fleet/relocate", json={**body, "confirm": True})
    assert armed.status_code == 409 and armed.json()["detail"]["code"] == "aircraft_armed"
    assert armed.json()["detail"]["simulation_stopped"] is False
    assert called == []  # nothing reached the manager in any refused case
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/fleet/relocate", json={**body, "confirm": True}).status_code == 403
    client.headers["X-CSRF-Token"] = csrf


def _manager_ok(home_ok=True, **overrides):
    base = {"instances": [{"instance": 0, "status": "process_started"}], "failure": None, "failure_code": None,
            "bridges": {"telemetry": {"status": "running"}, "pose": {"status": "running"}}, "wsl_address": "172.23.85.230",
            "gazebo_server": "running", "relocated": True, "stopped_instances": [0], "gazebo_stopped": [900], "manager": "px4_wsl_fleet_v1"}
    base.update(overrides)
    return base


def test_relocate_succeeds_only_when_the_reported_position_is_at_the_mission_start(general_client, flight, monkeypatch):
    client, _ = general_client
    scenario_id, version_id = _saved(client)
    _receivers(monkeypatch)
    seen = {}

    def manager(action, **kwargs):
        seen.update(action=action, **kwargs)
        return _manager_ok()

    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", manager)
    monkeypatch.setattr("app.fleet_routes._fleet_snapshot", lambda: ([_vehicle(**offset(2, 1))], []))
    row = {"aircraft_id": "px4-1", "instance_number": 0, "system_id": 1, "gazebo_model": "x500_0", "px4_status": "connected", "gazebo_status": "connected", "verified": True}
    monkeypatch.setattr("app.fleet_routes._verified_fleet", lambda count: ([row], [row]))
    response = client.post("/api/fleet/relocate", json={"scenario_id": scenario_id, "scenario_version_id": version_id, "confirm": True})
    assert response.status_code == 200, response.text
    payload = response.json()
    start = offset()
    assert seen["action"] == "relocate" and seen["count"] == 1
    assert abs(seen["home"]["latitude"] - start["latitude"]) < 1e-9 and abs(seen["home"]["longitude"] - start["longitude"]) < 1e-9
    assert payload["relocated"] is True and payload["commands_sent_to_aircraft"] is False and payload["max_distance_to_start_m"] < 5
    assert payload["checks"]["simulation_home"]["status"] == "ok" and payload["checks"]["verification"]["status"] == "ok"
    assert {"wsl", "gazebo", "px4", "telemetry_bridge", "pose_bridge", "receivers", "verification", "simulation_home"} <= set(payload["checks"])
    assert "No arm, take-off or mission command was sent" in payload["note"]


def test_relocate_reports_structured_failures_and_how_to_recover(general_client, flight, monkeypatch):
    client, _ = general_client
    scenario_id, version_id = _saved(client)
    _receivers(monkeypatch)
    body = {"scenario_id": scenario_id, "scenario_version_id": version_id, "confirm": True}
    row = {"aircraft_id": "px4-1", "instance_number": 0, "system_id": 1, "gazebo_model": "x500_0", "px4_status": "connected", "gazebo_status": "connected", "verified": True}
    monkeypatch.setattr("app.fleet_routes._verified_fleet", lambda count: ([row], [row]))
    monkeypatch.setattr("app.fleet_routes._fleet_snapshot", lambda: ([_vehicle(**ZURICH)], []))
    # 1. the instance is not the manager's: nothing was stopped
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", lambda *a, **k: _manager_ok(
        failure="PX4 instance(s) 0 were not started by the TRINETRA fleet manager, so they were left running. Stop them in WSL, then try again.",
        failure_code="instance_not_managed", relocated=False, stopped_instances=[], gazebo_stopped=[], instances=[]))
    refused = client.post("/api/fleet/relocate", json=body)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "instance_not_managed"
    assert refused.json()["detail"]["simulation_stopped"] is False and "Nothing was stopped" in refused.json()["detail"]["recovery"]
    # 2. restarted, but PX4 still reports the old place: the home was not applied
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", lambda *a, **k: _manager_ok())
    monkeypatch.setattr("app.fleet_routes.RELOCATION_VERIFY_TIMEOUT_S", 0.0)
    wrong = client.post("/api/fleet/relocate", json=body)
    detail = wrong.json()["detail"]
    assert wrong.status_code == 502 and detail["code"] == "home_not_applied" and detail["relocated"] is False
    assert detail["checks"]["simulation_home"]["status"] == "failed" and detail["simulation_stopped"] is True
    assert "Activate and verify" in detail["recovery"]
    # 3. restarted, but nothing verified before the timeout
    monkeypatch.setattr("app.fleet_routes._verified_fleet", lambda count: ([], [{**row, "px4_status": "not_seen", "gazebo_status": "not_seen", "verified": False}]))
    monkeypatch.setattr("app.fleet_routes._fleet_snapshot", lambda: ([], []))
    timeout = client.post("/api/fleet/relocate", json=body)
    assert timeout.status_code == 504 and timeout.json()["detail"]["code"] == "verification_timeout"
    assert timeout.json()["detail"]["checks"]["simulation_home"]["status"] == "pending"


def test_relocate_rejects_a_legacy_version_and_a_plan_without_a_start(general_client, flight, monkeypatch):
    from app.persistence.database import create_scenario
    from test_mission_workflow_e2e import CONFIG

    client, user = general_client
    _receivers(monkeypatch)
    legacy = create_scenario(user.id, "Legacy", CONFIG, {
        "mission_objectives": ["x"], "mission_description": "legacy", "proposed_tasks": [], "uav_role_suggestions": [], "operating_area_notes": "",
        "environment_notes": "", "constraints": [], "geographic_mission": {"objective": "inspection", "uav_assignments": [], "survey_zones": [], "constraints": [], "ai_recommendations": []}})
    response = client.post("/api/fleet/relocate", json={"scenario_id": legacy["id"], "scenario_version_id": legacy["versions"][0]["id"], "confirm": True})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "version_not_schema_2"
    draft = make_plan(aircraft_extra={"start_location": None}, plan_extra={"workflow_stage": "aircraft"})
    saved = client.post("/api/scenarios", json=plan_body(draft, "No start yet")).json()
    missing = client.post("/api/fleet/relocate", json={"scenario_id": saved["id"], "scenario_version_id": saved["versions"][0]["id"], "confirm": True})
    assert missing.status_code == 422 and missing.json()["detail"]["code"] == "mission_start_unavailable"


def test_activation_with_a_saved_version_passes_its_mission_start_and_reports_a_running_mismatch(general_client, flight, monkeypatch):
    client, _ = general_client
    scenario_id, version_id = _saved(client)
    _receivers(monkeypatch)
    seen = {}

    def manager(action, **kwargs):
        seen.update(kwargs)
        return _manager_ok(instances=[{"instance": 0, "status": "process_present"}], home_mismatch=["px4-1"])

    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", manager)
    row = {"aircraft_id": "px4-1", "instance_number": 0, "system_id": 1, "gazebo_model": "x500_0", "px4_status": "connected", "gazebo_status": "connected", "verified": True}
    monkeypatch.setattr("app.fleet_routes._verified_fleet", lambda count: ([row], [row]))
    response = client.post("/api/fleet/activate", json={"required_uav_count": 1, "scenario_id": scenario_id, "scenario_version_id": version_id})
    assert response.status_code == 200, response.text
    assert abs(seen["home"]["latitude"] - offset()["latitude"]) < 1e-9
    check = response.json()["checks"]["simulation_home"]
    assert check["status"] == "differs" and "was not restarted" in check["detail"] and check["aircraft"] == ["px4-1"]
    seen.clear()
    plain = client.post("/api/fleet/activate", json={"required_uav_count": 1})
    assert plain.status_code == 200 and "home" not in seen and "simulation_home" not in plain.json()["checks"]
    assert client.post("/api/fleet/activate", json={"required_uav_count": 1, "scenario_id": scenario_id}).status_code == 422


def test_fleet_status_reports_where_the_simulation_was_started(general_client, monkeypatch):
    client, _ = general_client
    home = {"latitude": 12.9716, "longitude": 77.5946, "altitude_m": 488.0}
    monkeypatch.setattr("app.fleet_routes._fleet_snapshot", lambda: ([], []))
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", lambda *a, **k: {"instances": [{"system_id": 1, "home": home, "managed": True}]})
    status = client.get("/api/fleet/status").json()
    assert status["simulation_home"] == home and status["simulation_home_source"] == "px4_home_env"
    assert status["instances"][0]["home"] == home and status["instances"][0]["managed"] is True
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", lambda *a, **k: {"instances": [{"system_id": 1, "home": None, "managed": True}]})
    assert client.get("/api/fleet/status").json()["simulation_home_source"] == "px4_default_world"
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", lambda *a, **k: {"instances": []})
    assert client.get("/api/fleet/status").json()["simulation_home_source"] == "unknown"


def test_gazebo_check_uses_the_arriving_pose_when_the_manager_sampled_before_gazebo_started():
    """Seen live: activation and relocation reported Gazebo "not_running" although its model pose was arriving."""
    from app.fleet_routes import build_activation_checks

    process = _manager_ok(gazebo_server="not_running")
    row = {"aircraft_id": "px4-1", "px4_status": "connected", "gazebo_status": "connected", "verified": True}
    checks = build_activation_checks(1, process, [row], receivers={"px4": True, "gazebo": True},
                                     configured_ips={"px4_telemetry": "172.23.85.230", "gazebo_pose": "172.23.85.230"})
    assert checks["gazebo"]["status"] == "ok" and "publishing model poses" in checks["gazebo"]["detail"]
    silent = build_activation_checks(1, process, [{**row, "gazebo_status": "not_seen", "verified": False}],
                                     receivers={"px4": True, "gazebo": True}, configured_ips={"px4_telemetry": "", "gazebo_pose": ""})
    assert silent["gazebo"]["status"] == "not_running" and "no model pose has arrived" in silent["gazebo"]["detail"]
    running = build_activation_checks(1, _manager_ok(), [row], receivers={"px4": True, "gazebo": True},
                                      configured_ips={"px4_telemetry": "", "gazebo_pose": ""})
    assert running["gazebo"] == {"status": "ok", "detail": "Gazebo server process is running.", "models_seen": 1}
