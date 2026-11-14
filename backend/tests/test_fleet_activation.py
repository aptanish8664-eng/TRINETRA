"""Mocked tests for WSL bridge adoption and structured fleet-activation failures.

No WSL, PX4 or Gazebo process is started; process discovery uses fake /proc trees.
"""
import importlib.util
from pathlib import Path

import pytest

from app.telemetry.fleet_manager import build_fleet_manager_command

_spec = importlib.util.spec_from_file_location(
    "wsl_fleet_manager_activation", Path(__file__).parents[1] / "tools" / "wsl_fleet_manager.py",
)
assert _spec and _spec.loader
_manager = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_manager)


def _fake_proc(root, pid, *args):
    (root / str(pid)).mkdir()
    (root / str(pid) / "cmdline").write_bytes(b"\0".join(item.encode() for item in args) + b"\0")


def test_default_gateway_is_parsed_from_the_route_table(tmp_path):
    route = tmp_path / "route"
    route.write_text("Iface\tDestination\tGateway\tFlags\n"
                     "eth0\t00000000\t015017AC\t0003\neth0\t005017AC\t00000000\t0001\n", encoding="ascii")
    assert _manager.windows_host_address(route) == "172.23.80.1"
    assert _manager.windows_host_address(tmp_path / "missing") is None


def test_running_bridge_is_adopted_and_never_duplicated(tmp_path):
    _fake_proc(tmp_path, 321, "python3", str(_manager.TELEMETRY_BRIDGE_SCRIPT), "--windows-host", "172.23.80.1")

    def forbidden(*args, **kwargs):
        raise AssertionError("a duplicate bridge must not be started")

    result = _manager.ensure_bridge("telemetry", _manager.TELEMETRY_BRIDGE_SCRIPT, [],
                                    proc_root=tmp_path, spawn=forbidden)
    assert result == {"status": "running", "pid": 321, "adopted": True}


def test_missing_bridge_is_started_once_and_early_exit_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(_manager.os, "getuid", lambda: 1000, raising=False)
    launched = []

    class Alive:
        pid = 777
        returncode = None

        def poll(self):
            return None

    def spawn(command, **kwargs):
        launched.append(command)
        assert kwargs["start_new_session"] is True and "shell" not in kwargs
        return Alive()

    started = _manager.ensure_bridge("pose", _manager.POSE_BRIDGE_SCRIPT, ["--windows-host", "172.23.80.1"],
                                     proc_root=tmp_path, settle_s=0.2, spawn=spawn)
    assert started["status"] == "started" and len(launched) == 1
    assert launched[0][1] == str(_manager.POSE_BRIDGE_SCRIPT)

    class Dead:
        pid = 778
        returncode = 1

        def poll(self):
            return 1

    failed = _manager.ensure_bridge("pose", _manager.POSE_BRIDGE_SCRIPT, [], proc_root=tmp_path,
                                    settle_s=0.2, spawn=lambda *a, **k: Dead())
    assert failed["status"] == "failed"


def _patch_manager(monkeypatch, tmp_path, bridges):
    monkeypatch.setattr(_manager, "discover_px4_instances", lambda root, proc_root=None: {0: 4242})
    monkeypatch.setattr(_manager, "_available_memory_kib", lambda: 8 * 1024 * 1024)
    monkeypatch.setattr(_manager, "_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(_manager, "_spawn", lambda *a, **k: (_ for _ in ()).throw(AssertionError("duplicate PX4")))
    monkeypatch.setattr(_manager, "ensure_bridges", lambda existing, ports, proc_root=None: bridges)
    monkeypatch.setattr(_manager, "_environment_report", lambda proc_root=None: {
        "wsl_address": "172.23.85.230", "windows_host": "172.23.80.1", "gazebo_server": "running"})


def test_activation_adopts_running_instance_zero_without_starting_a_duplicate(tmp_path, monkeypatch):
    _patch_manager(monkeypatch, tmp_path, {"telemetry": {"status": "started", "pid": 1},
                                           "pose": {"status": "running", "pid": 2}})
    result = _manager.activate_fleet(1, px4_root=tmp_path)
    assert result["instances"][0]["status"] == "process_present"
    assert result["failure"] is None and result["bridges"]["telemetry"]["status"] == "started"


def test_bridge_start_failure_is_reported_by_name(tmp_path, monkeypatch):
    _patch_manager(monkeypatch, tmp_path, {"telemetry": {"status": "failed", "detail": "Address already in use"},
                                           "pose": {"status": "running", "pid": 2}})
    result = _manager.activate_fleet(1, px4_root=tmp_path)
    assert result["failure_code"] == "bridge_start_failed"
    assert "telemetry bridge: Address already in use" in result["failure"]


def test_bridge_ports_are_validated_and_passed_as_fixed_arguments():
    command = build_fleet_manager_command("activate", count=1,
                                          ports={"telemetry_forward": 14600, "pose_source": 14611})
    assert command[-4:] == ["--telemetry-forward-port", "14600", "--pose-source-port", "14611"]
    with pytest.raises(ValueError, match="bridge ports"):
        build_fleet_manager_command("activate", count=1, ports={"pose_forward": 80})


def _good_process(**overrides):
    base = {"instances": [{"instance": 0, "status": "process_present"}], "failure": None, "failure_code": None,
            "bridges": {"telemetry": {"status": "running"}, "pose": {"status": "running"}},
            "wsl_address": "172.23.85.230", "gazebo_server": "running", "manager": "px4_wsl_fleet_v1"}
    base.update(overrides)
    return base


_ROW_MISSING = [{"aircraft_id": "px4-1", "px4_status": "not_seen", "gazebo_status": "not_seen", "verified": False}]
_ROW_OK = [{"aircraft_id": "px4-1", "px4_status": "connected", "gazebo_status": "connected", "verified": True}]


def _classify(process, rows, *, final):
    from app.fleet_routes import build_activation_checks, classify_activation_failure

    checks = build_activation_checks(
        1, process, rows, receivers={"px4": True, "gazebo": True},
        configured_ips={"px4_telemetry": "172.23.85.230", "gazebo_pose": "172.23.85.230"})
    return checks, classify_activation_failure(1, process, checks, final=final)


def test_failure_classification_uses_accurate_status_codes():
    checks, failure = _classify({"failure": "WSL fleet manager could not complete: OSError", "manager": "x"},
                                _ROW_MISSING, final=False)
    assert failure[:2] == (503, "wsl_manager_unavailable") and checks["wsl"]["status"] == "unavailable"
    _, failure = _classify(_good_process(
        failure="telemetry bridge: boom", failure_code="bridge_start_failed",
        bridges={"telemetry": {"status": "failed", "detail": "boom"}, "pose": {"status": "running"}}),
        _ROW_MISSING, final=False)
    assert failure[:2] == (502, "bridge_start_failed")
    checks, failure = _classify(_good_process(wsl_address="172.23.99.9"), _ROW_MISSING, final=False)
    assert failure[:2] == (409, "source_ip_mismatch") and checks["source_ip"]["status"] == "mismatch"
    checks, failure = _classify(_good_process(), _ROW_MISSING, final=True)
    assert failure[:2] == (504, "verification_timeout")
    assert checks["verification"]["unverified"][0]["px4"] == "not_seen"
    checks, failure = _classify(_good_process(), _ROW_OK, final=True)
    assert failure is None and checks["verification"]["status"] == "ok"
    assert {"wsl", "gazebo", "px4", "telemetry_bridge", "pose_bridge", "receivers", "verification"} <= set(checks)


def _enable_receivers(monkeypatch, px4=True):
    from app import main

    monkeypatch.setattr(main, "px4_udp_transport", object() if px4 else None)
    monkeypatch.setattr(main, "gazebo_pose_udp_transport", object())


def test_activate_endpoint_returns_structured_503_when_receivers_are_unbound(general_client, monkeypatch):
    client, _ = general_client
    _enable_receivers(monkeypatch, px4=False)
    response = client.post("/api/fleet/activate", json={"required_uav_count": 1})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "receivers_unavailable" and detail["verified"] is False
    assert detail["checks"]["receivers"]["px4"] is False and detail["checks"]["receivers"]["gazebo"] is True


def test_activate_endpoint_reports_missing_bridge_as_502_without_waiting(general_client, monkeypatch):
    client, _ = general_client
    _enable_receivers(monkeypatch)
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", lambda *a, **k: _good_process(
        failure="pose bridge: gz not found", failure_code="bridge_start_failed",
        bridges={"telemetry": {"status": "running"}, "pose": {"status": "failed", "detail": "gz not found"}}))
    monkeypatch.setattr("app.fleet_routes._verified_fleet", lambda count: ([], _ROW_MISSING))
    response = client.post("/api/fleet/activate", json={"required_uav_count": 1})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "bridge_start_failed"
    assert detail["checks"]["pose_bridge"] == {"status": "failed", "detail": "gz not found"}
    assert detail["checks"]["telemetry_bridge"]["status"] == "running"


def test_activate_endpoint_succeeds_only_when_telemetry_and_pose_are_both_verified(general_client, monkeypatch):
    client, _ = general_client
    _enable_receivers(monkeypatch)
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", lambda *a, **k: _good_process())
    row = {**_ROW_OK[0], "instance_number": 0, "system_id": 1, "gazebo_model": "x500_0"}
    monkeypatch.setattr("app.fleet_routes._verified_fleet", lambda count: ([row], [row]))
    response = client.post("/api/fleet/activate", json={"required_uav_count": 1})
    assert response.status_code == 200
    assert response.json()["verified"] is True and response.json()["checks"]["verification"]["status"] == "ok"


def test_gazebo_server_is_detected_from_a_space_joined_process_title(tmp_path):
    _fake_proc(tmp_path, 666, "gz sim --verbose=1 -r -s /worlds/default.sdf")
    _fake_proc(tmp_path, 667, "gz sim -g")
    assert _manager.gazebo_server_running(tmp_path) is True
    only_gui = tmp_path / "gui"
    only_gui.mkdir()
    _fake_proc(only_gui, 667, "gz", "sim", "-g")
    assert _manager.gazebo_server_running(only_gui) is False
